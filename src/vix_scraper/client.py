"""Stdlib GraphQL HTTP client with retries and backoff."""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vix_scraper.errors import GraphQLComplexityError, GraphQLError
from vix_scraper.models import ScrapeConfig

_UNKNOWN_FIELD_RE = re.compile(
    r"""Cannot query field ["']([A-Za-z_][A-Za-z0-9_]*)["']""",
    re.IGNORECASE,
)
_UNDEFINED_FIELD_RE = re.compile(
    r"""Field ["']([A-Za-z_][A-Za-z0-9_]*)["'].*undefined""",
    re.IGNORECASE,
)
_MAX_FIELD_STRIPS = 16
_OPTIONAL_MODULE_FIELDS = ("items", "collection")


def graphql_error_messages(payload: dict[str, Any]) -> list[str]:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return []
    messages: list[str] = []
    for error in errors:
        if isinstance(error, dict) and error.get("message"):
            messages.append(str(error["message"]))
        elif error:
            messages.append(str(error))
    return messages


def is_complexity_error(messages: list[str] | str) -> bool:
    text = messages if isinstance(messages, str) else " ".join(messages)
    low = text.lower()
    return "complexity" in low and ("over the maximum" in low or "maximum of" in low)


def unknown_graphql_fields(messages: list[str] | str) -> list[str]:
    """Field names rejected by schema validation (order preserved)."""
    texts = [messages] if isinstance(messages, str) else list(messages)
    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for match in _UNKNOWN_FIELD_RE.finditer(text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
        for match in _UNDEFINED_FIELD_RE.finditer(text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def optional_module_fields_to_strip(messages: list[str] | str) -> list[str]:
    """Strip CMS-optional module fields when the schema rejects their shape.

    ``items`` may be absent, a card list, or a connection. A type mismatch must
    drop the ``items`` selection only — never ``edges`` / ``totalCount`` on
    ``contents``. Same for ``collection { totalCount }``.
    """
    texts = [messages] if isinstance(messages, str) else list(messages)
    names: list[str] = []
    seen: set[str] = set()
    for text in texts:
        normalized = text.replace("'", '"')
        low = normalized.lower()
        for field in _OPTIONAL_MODULE_FIELDS:
            quoted = f'"{field}"' in normalized
            unknown = f'cannot query field "{field}"' in low
            mismatch = quoted and (
                "cannot be spread" in low
                or "must have a selection" in low
                or "must not have a selection" in low
                or "unknown argument" in low
            )
            spread_on_connection = (
                field == "items"
                and "cannot be spread" in low
                and "connection" in low
            )
            if unknown or mismatch or spread_on_connection:
                if field not in seen:
                    seen.add(field)
                    names.append(field)
    return names


def items_is_connection_mismatch(messages: list[str] | str) -> bool:
    """True when list-shaped ``items { ... on UiVideoCard }`` hit a Connection type."""
    texts = [messages] if isinstance(messages, str) else list(messages)
    for text in texts:
        low = text.replace("'", '"').lower()
        if "cannot be spread" in low and "connection" in low:
            return True
        if "items" in low and "must have a selection" in low and "connection" in low:
            return True
    return False


def rewrite_list_items_as_connection(query: str) -> str:
    """Wrap list-shaped ``items { card fragments }`` into a pin-list connection.

    Production ``items`` may be ``[UiVideoCard]`` or ``UiVideoCarouselItemConnection``.
    A type mismatch must not drop ``items`` (that falls back to contents.totalCount
    catalog dumps such as Exclusivo x=119). Rewrite the selection to edges/node
    and retry; strip only if the connection shape is also rejected.
    """
    if not query or "items" not in query:
        return query
    out = query
    search_from = 0
    while True:
        idx = out.find("items", search_from)
        if idx < 0:
            break
        if idx > 0 and (out[idx - 1].isalnum() or out[idx - 1] == "_"):
            search_from = idx + 5
            continue
        after_name = idx + 5
        if after_name < len(out) and (out[after_name].isalnum() or out[after_name] == "_"):
            search_from = idx + 5
            continue
        brace = after_name
        while brace < len(out) and out[brace] in " \t\r\n":
            brace += 1
        if brace >= len(out) or out[brace] != "{":
            search_from = idx + 5
            continue
        inner_start = brace + 1
        depth = 1
        k = inner_start
        while k < len(out) and depth:
            if out[k] == "{":
                depth += 1
            elif out[k] == "}":
                depth -= 1
            k += 1
        inner = out[inner_start : k - 1]
        if "edges" in inner or "... on UiVideoCard" not in inner:
            search_from = k
            continue
        wrapped = (
            "{\n"
            "              totalCount\n"
            "              pageInfo {\n"
            "                hasNextPage\n"
            "                endCursor\n"
            "                itemCount\n"
            "              }\n"
            "              edges {\n"
            "                cursor\n"
            "                node {"
            + inner
            + "                }\n"
            "              }\n"
            "            }"
        )
        out = out[:brace] + wrapped + out[k:]
        search_from = brace + len(wrapped)
    return out


def strip_selection_fields(query: str, names: list[str] | set[str]) -> str:
    """Remove GraphQL selection fields by name, including inline fields on one line."""
    drop = {n for n in names if n}
    if not drop or not query:
        return query

    def is_ident_start(char: str) -> bool:
        return char.isalpha() or char == "_"

    def is_ident_char(char: str) -> bool:
        return char.isalnum() or char == "_"

    out: list[str] = []
    i = 0
    n = len(query)
    while i < n:
        char = query[i]
        if char == "#":
            end = query.find("\n", i)
            if end < 0:
                out.append(query[i:])
                break
            out.append(query[i:end])
            i = end
            continue
        if char in "\"'":
            quote = char
            j = i + 1
            while j < n:
                if query[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if query[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(query[i:j])
            i = j
            continue
        if is_ident_start(char):
            j = i + 1
            while j < n and is_ident_char(query[j]):
                j += 1
            ident = query[i:j]
            if ident in drop:
                k = j
                while k < n and query[k] in " \t":
                    k += 1
                if k < n and query[k] == "(":
                    depth = 1
                    k += 1
                    while k < n and depth:
                        if query[k] == "(":
                            depth += 1
                        elif query[k] == ")":
                            depth -= 1
                        k += 1
                    while k < n and query[k] in " \t\r\n":
                        k += 1
                if k < n and query[k] == "{":
                    depth = 1
                    k += 1
                    while k < n and depth:
                        if query[k] == "{":
                            depth += 1
                        elif query[k] == "}":
                            depth -= 1
                        k += 1
                i = k
                continue
            out.append(ident)
            i = j
            continue
        out.append(char)
        i += 1
    return "".join(out)


class GraphQLClient:
    """Reusable POST client for ViX GraphQL. Stdlib only — works on any PC with Python."""

    def __init__(self, config: ScrapeConfig) -> None:
        if not config.endpoint:
            raise GraphQLError("GraphQL endpoint is not configured")
        self.endpoint = config.endpoint
        self.timeout = config.timeout
        self.retries = config.retries
        self._headers = self._build_headers(config)
        self._query_rewrites: dict[str, str] = {}

    def _effective_query(self, query: str) -> str:
        effective = query
        seen: set[str] = set()
        while effective in self._query_rewrites and effective not in seen:
            seen.add(effective)
            effective = self._query_rewrites[effective]
        return effective

    def _remember_rewrite(self, original: str, stripped: str) -> None:
        if stripped and stripped != original:
            self._query_rewrites[original] = stripped

    @staticmethod
    def _build_headers(config: ScrapeConfig) -> dict[str, str]:
        import os

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": config.user_agent,
            "x-vix-app-version": config.app_version,
            "x-vix-device-type": config.device_type,
            "x-vix-platform": config.platform,
        }
        accept_language = (
            (getattr(config, "accept_language", None) or "").strip()
            or (os.getenv("VIX_ACCEPT_LANGUAGE") or "").strip()
            or "es-MX,es;q=0.9"
        )
        country = (
            (getattr(config, "country", None) or "").strip()
            or (os.getenv("VIX_COUNTRY") or "").strip()
            or "MX"
        ).upper()
        if accept_language:
            headers["Accept-Language"] = accept_language
        if country:
            headers["x-vix-country"] = country
            headers["cloudfront-viewer-country"] = country
            headers["x-vix-geo-country"] = country

        # Statsig / device install overrides for gated experiences (e.g. WC cluster).
        installation_id = (
            (getattr(config, "installation_id", None) or "").strip()
            or (os.getenv("VIX_INSTALLATION_ID") or "").strip()
        )
        if installation_id:
            # Primary + common aliases; gateway ignores unknown headers.
            headers["x-vix-installation-id"] = installation_id
            headers["x-installation-id"] = installation_id
            headers["installation-id"] = installation_id

        if config.auth_token:
            headers["Authorization"] = f"Bearer {config.auth_token}"
        if config.x_vix_user_token:
            headers["x-vix-user-token"] = config.x_vix_user_token
        headers.update(config.extra_headers)
        return headers

    def _raise_or_rewrite(
        self,
        *,
        original_query: str,
        effective_query: str,
        errors: list[str],
        http_code: int | None,
        allow_errors: bool,
        payload: dict[str, Any] | None,
    ) -> str | dict[str, Any]:
        """Return rewritten query, payload to return, or raise.

        Returns:
            str: stripped query to retry immediately
            dict: payload to return to caller
        """
        if is_complexity_error(errors):
            joined = "; ".join(errors)
            prefix = f"HTTP {http_code}: " if http_code is not None else ""
            raise GraphQLComplexityError(f"{prefix}{joined}".strip())
        if items_is_connection_mismatch(errors):
            rewritten = rewrite_list_items_as_connection(effective_query)
            if rewritten != effective_query:
                self._remember_rewrite(original_query, rewritten)
                self._remember_rewrite(effective_query, rewritten)
                return rewritten
        unknown = unknown_graphql_fields(errors)
        unknown.extend(
            name
            for name in optional_module_fields_to_strip(errors)
            if name not in unknown
        )
        if unknown:
            stripped = strip_selection_fields(effective_query, unknown)
            if stripped != effective_query:
                self._remember_rewrite(original_query, stripped)
                self._remember_rewrite(effective_query, stripped)
                return stripped
        if allow_errors and payload is not None:
            return payload
        joined = "; ".join(errors) if errors else "GraphQL request failed"
        prefix = f"HTTP {http_code}: " if http_code is not None else "GraphQL errors: "
        raise GraphQLError(prefix + joined)

    def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_errors: bool = False,
    ) -> dict[str, Any]:
        effective = self._effective_query(query)
        vars_obj = variables or {}
        last_error: Exception | None = None
        field_strips = 0
        attempt = 0

        while attempt <= self.retries:
            body_bytes = json.dumps({"query": effective, "variables": vars_obj}).encode("utf-8")
            request = Request(self.endpoint, data=body_bytes, headers=self._headers, method="POST")
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise GraphQLError("GraphQL response is not a JSON object")
                errors = graphql_error_messages(payload)
                if not errors:
                    return payload
                handled = self._raise_or_rewrite(
                    original_query=query,
                    effective_query=effective,
                    errors=errors,
                    http_code=None,
                    allow_errors=allow_errors,
                    payload=payload,
                )
                if isinstance(handled, dict):
                    return handled
                effective = handled
                field_strips += 1
                if field_strips > _MAX_FIELD_STRIPS:
                    raise GraphQLError(
                        "GraphQL schema rejected too many unknown fields to recover"
                    )
                continue
            except GraphQLComplexityError:
                raise
            except HTTPError as exc:
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")
                    payload = json.loads(err_body) if err_body else None
                except Exception:
                    payload = None
                errors = graphql_error_messages(payload) if isinstance(payload, dict) else []
                if errors:
                    try:
                        handled = self._raise_or_rewrite(
                            original_query=query,
                            effective_query=effective,
                            errors=errors,
                            http_code=exc.code,
                            allow_errors=allow_errors,
                            payload=payload if isinstance(payload, dict) else None,
                        )
                    except GraphQLComplexityError:
                        raise
                    except GraphQLError as gql_exc:
                        last_error = gql_exc
                        # Schema/validation 400s: do not sleep-retry the same query.
                        if exc.code < 500:
                            break
                        if attempt >= self.retries:
                            break
                        time.sleep(2**attempt)
                        attempt += 1
                        continue
                    if isinstance(handled, dict):
                        return handled
                    effective = handled
                    field_strips += 1
                    if field_strips > _MAX_FIELD_STRIPS:
                        raise GraphQLError(
                            "GraphQL schema rejected too many unknown fields to recover"
                        )
                    continue
                last_error = GraphQLError(f"HTTP {exc.code}: {err_body[:500] or exc}")
                if exc.code < 500:
                    break
                if attempt >= self.retries:
                    break
                time.sleep(2**attempt)
                attempt += 1
                continue
            except GraphQLError as exc:
                last_error = exc
                break
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(2**attempt)
                attempt += 1
                continue
            attempt += 1

        raise GraphQLError(f"GraphQL request failed for {self.endpoint}: {last_error}") from last_error
