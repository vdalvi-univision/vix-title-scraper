"""Cursor pagination for uiModules and nested carousel contents.

Module walk is two-phase on every page (ODP and WC), not only when PageInfo
looks broken:

1. Probe ``uiModules.totalCount`` (cheap, no edges).
2. List all rails from offset 0: ``uiModules(pagination: { first: totalCount })``
   with no ``after`` and no content fields (``queries/layout_modules.graphql``).
3. Hydrate each module’s contents with the lean layout query, one rail per
   request, so ``$contentPagination.after`` is never shared across 30 modules.

Why WC and not ODP: ``/ondemandplus`` (ungated) typically returns a real module
``endCursor`` after Hero, so a first:1 walk happened to work. ``/ondemandpluswc``
is the Statsig / install-id treatment; the same first:1 layout query can return
Hero + totalCount≈30 + hasNextPage=false + no endCursor. That is cursor
withholding, not a 1-module catalog (WC has succeeded with ~973 titles). We
therefore never use module PageInfo to decide whether rails 2..N exist.

Index-query edge cursors are listing-only. Hydrate with the layout query’s own
``after`` (or ``first: i+1`` when it omits one). Mixing those connections
reprinted a block of rails under new ``carousel_y`` values.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from vix_scraper.client import GraphQLClient, strip_selection_fields
from vix_scraper.errors import GraphQLComplexityError, GraphQLError, PaginationError
from vix_scraper.extractor import (
    content_edge_keys,
    contents_edge_count,
    contents_has_more,
    contents_total_count,
    module_contents_has_more,
    module_layout_connection,
    module_layout_length,
    page_info,
    pending_content_cursors,
    pending_content_module_id,
    ui_modules_edges,
)
from vix_scraper.models import ScrapeConfig
from vix_scraper.util import first_text


# Transport-only ceiling for contents `first` with the lean layout query.
# Fat VideoTitleFields at first ≈ rail length scored complexity 109776 / 100000.
# Identity-only queries/layout.graphql is sized so a typical CMS rail (26–30)
# and a large rail (up to this many items) fit in one request under 100k.
# Ranked/playlist/infinite: product length is contents.totalCount / hasNextPage.
# Editorial: `items` pin list, or the first layout page's contents.edges window.
# Never this constant, and never a magic 30/119.
# If a request still exceeds the budget, PagePaginator halves `first` and
# retries the same cursor, then continues `after`.
LAYOUT_CONTENTS_COMPLEXITY_FIRST = 80
# Backward-compatible alias (not a CMS row-length cap).
LAYOUT_CONTENTS_PAGE_MAX = LAYOUT_CONTENTS_COMPLEXITY_FIRST

# Existence-style probe: totalCount only. Same shape as layout_compare.probe_page_exists.
MODULES_TOTAL_COUNT_QUERY = (
    "query ModulesTotalCount($urlPath: ID!) { "
    "uiPage(urlPath: $urlPath) { urlPath pageName moduleCount: uiModules { totalCount } } }"
)

LayoutRequestKind = Literal["probe", "index", "contents"]


@dataclass(frozen=True, slots=True)
class PageCursor:
    module_after: str | None = None
    content_after: str | None = None
    content_module_id: str | None = None
    # Override uiModules `first` (index walk uses 1, or seen+1 when no cursor).
    module_first: int | None = None
    # Keep only this module in the yielded payload (per-rail contents after).
    slice_module_id: str | None = None
    # Prefer this 0-based edge when id-slice misses (never yield a multi-rail page).
    slice_index: int | None = None
    slice_last: bool = False


@dataclass(frozen=True, slots=True)
class _ModuleRef:
    module_id: str
    cursor: str
    index: int


def layout_request_kind(query: str | None, variables: dict[str, Any] | None) -> LayoutRequestKind:
    """Classify a GraphQL call: totalCount probe, modules index, or contents hydrate."""
    q = query or ""
    vars_ = variables or {}
    if "ModulesTotalCount" in q or "moduleCount: uiModules" in q:
        return "probe"
    if "UiPageModulesIndex" in q:
        return "index"
    if "contentPagination" in vars_:
        return "contents"
    if "uiModulesPagination" in vars_:
        return "index"
    return "probe"


def modules_index_query_from_layout(query: str) -> str:
    """Derive a contents-free modules listing from a layout/request query."""
    stripped = strip_selection_fields(query, {"contents"})
    stripped = re.sub(r",?\s*\$contentPagination:\s*PaginationParams\s*", "", stripped, count=1)
    stripped = re.sub(r"\(\s*,", "(", stripped)
    stripped = re.sub(r",\s*,", ",", stripped)
    stripped = re.sub(r",\s*\)", ")", stripped)
    return stripped


def load_modules_index_query(fallback_query: str = "") -> str:
    """Prefer queries/layout_modules.graphql; else strip contents from the layout query."""
    here = Path(__file__).resolve()
    candidates = [
        Path("queries/layout_modules.graphql"),
        here.parents[2] / "queries" / "layout_modules.graphql",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    if fallback_query.strip():
        return modules_index_query_from_layout(fallback_query)
    return ""


def _module_end_cursor(modules_connection: dict[str, Any]) -> str:
    """Prefer pageInfo.endCursor; fall back to last edge cursor when API omits it."""
    _, cursor = page_info(modules_connection)
    if cursor:
        return cursor
    edges = modules_connection.get("edges")
    if not isinstance(edges, list) or not edges:
        return ""
    last = edges[-1]
    if isinstance(last, dict):
        return first_text(last.get("cursor"))
    return ""


def _modules_total_count(modules_connection: dict[str, Any]) -> int | None:
    raw = modules_connection.get("totalCount")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _payload_ui_page(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    page = data.get("uiPage")
    return page if isinstance(page, dict) else None


def _read_modules_total(payload: dict[str, Any]) -> int | None:
    page = _payload_ui_page(payload)
    if not page:
        return None
    for key in ("moduleCount", "uiModules"):
        conn = page.get(key)
        if isinstance(conn, dict):
            total = _modules_total_count(conn)
            if total is not None:
                return total
    return None


def _edge_module_id(edge: Any) -> str:
    if not isinstance(edge, dict):
        return ""
    node = edge.get("node") or {}
    if not isinstance(node, dict):
        return ""
    return first_text(node.get("id"), node.get("trackingId"))


def _parse_module_index(payload: dict[str, Any]) -> list[_ModuleRef]:
    refs: list[_ModuleRef] = []
    for i, edge in enumerate(ui_modules_edges(payload)):
        cursor = ""
        if isinstance(edge, dict):
            cursor = first_text(edge.get("cursor"))
        refs.append(_ModuleRef(module_id=_edge_module_id(edge), cursor=cursor, index=i))
    return refs


def _dedupe_module_refs(refs: list[_ModuleRef]) -> list[_ModuleRef]:
    """Keep the first listing of each module_id. GraphQL first:totalCount can
    repeat a block of rails; hydrating those again reprints the same row later."""
    seen: set[str] = set()
    out: list[_ModuleRef] = []
    for ref in refs:
        mid = first_text(ref.module_id)
        if mid:
            if mid in seen:
                continue
            seen.add(mid)
        out.append(_ModuleRef(module_id=ref.module_id, cursor=ref.cursor, index=len(out)))
    return out


def _replace_module_edges(payload: dict[str, Any], edges: list[Any]) -> dict[str, Any]:
    page = _payload_ui_page(payload)
    if not page:
        return payload
    conn = page.get("uiModules")
    if not isinstance(conn, dict):
        return payload
    new_conn = dict(conn)
    new_conn["edges"] = edges
    new_page = dict(page)
    new_page["uiModules"] = new_conn
    data = payload.get("data")
    if not isinstance(data, dict):
        return payload
    new_data = dict(data)
    new_data["uiPage"] = new_page
    new_payload = dict(payload)
    new_payload["data"] = new_data
    return new_payload


def _payload_keeping_module(payload: dict[str, Any], module_id: str) -> dict[str, Any]:
    want = first_text(module_id)
    if not want:
        return payload
    kept = [edge for edge in ui_modules_edges(payload) if _edge_module_id(edge) == want]
    if not kept:
        return payload
    return _replace_module_edges(payload, kept[:1])


def _payload_keeping_last_edge(payload: dict[str, Any]) -> dict[str, Any]:
    edges = ui_modules_edges(payload)
    if not edges:
        return payload
    return _replace_module_edges(payload, [edges[-1]])


def _payload_keeping_index(payload: dict[str, Any], index: int) -> dict[str, Any]:
    edges = ui_modules_edges(payload)
    if not edges:
        return payload
    if 0 <= index < len(edges):
        return _replace_module_edges(payload, [edges[index]])
    return _replace_module_edges(payload, [edges[-1]])


def _payload_keeping_unseen(payload: dict[str, Any], seen_ids: set[str]) -> dict[str, Any]:
    """Keep the first module that has not already been hydrated."""
    for edge in ui_modules_edges(payload):
        mid = _edge_module_id(edge)
        if mid and mid not in seen_ids:
            return _replace_module_edges(payload, [edge])
    return payload


def _slice_hydrate_payload(payload: dict[str, Any], cursor: PageCursor) -> dict[str, Any]:
    """Yield one module so contents `after` never applies to sibling rails."""
    if cursor.slice_module_id:
        sliced = _payload_keeping_module(payload, cursor.slice_module_id)
        if len(ui_modules_edges(sliced)) == 1:
            return sliced
    if cursor.slice_index is not None:
        return _payload_keeping_index(payload, cursor.slice_index)
    if cursor.slice_last:
        return _payload_keeping_last_edge(payload)
    edges = ui_modules_edges(payload)
    if len(edges) > 1:
        return _payload_keeping_index(payload, 0)
    return payload


class PagePaginator:
    """Yields GraphQL page payloads until both module and content cursors are exhausted."""

    def __init__(self, client: GraphQLClient, config: ScrapeConfig, query: str) -> None:
        self.client = client
        self.config = config
        self.query = query
        self.last_modules_total_count: int | None = None
        self.last_modules_seen: int = 0
        self._content_emitted: dict[str, int] = {}
        self._content_keys: dict[str, set[str]] = {}
        self._content_new_count: dict[str, int] = {}
        self._content_layout_totals: dict[str, int] = {}
        self._module_index: list[_ModuleRef] | None = None
        self._index_raw_count: int = 0
        self._unique_listed: int = 0
        self.last_skipped_dupes: int = 0
        page_max = int(
            getattr(self.config, "contents_page_max", LAYOUT_CONTENTS_COMPLEXITY_FIRST)
            or LAYOUT_CONTENTS_COMPLEXITY_FIRST
        )
        if getattr(self.config, "contents_first_from_total", False):
            self._contents_first_cap = max(1, page_max)
        else:
            self._contents_first_cap = max(1, int(self.config.page_size or page_max))

    def _unique_emitted(self, module_id: str) -> int:
        """Unique tile keys seen on this module (cursor overlap does not count twice)."""
        keys = self._content_keys.get(module_id)
        if keys:
            return len(keys)
        return self._content_emitted.get(module_id, 0)

    def _remaining_contents_first(self, cursor: PageCursor) -> int | None:
        """API-reported remaining items for this module, or None before totalCount is known."""
        mid = cursor.content_module_id
        if not mid:
            return None
        total = self._content_layout_totals.get(mid)
        if total is None:
            return None
        already = self._unique_emitted(mid)
        left = total - already
        return left if left > 0 else None

    def _content_first(self, cursor: PageCursor) -> int:
        """Per-request contents `first`. Not a CMS row-length cap.

        Layout must not omit PaginationParams (schema default first=10 is not
        the rail). When the module has reported totalCount, send remaining
        unique items (one or few pages per rail). Clamp only to the lean-query
        complexity ceiling; if the server still rejects the query, _execute_page
        halves this value and retries the same cursor.
        """
        cap = max(1, int(self._contents_first_cap))
        remaining = self._remaining_contents_first(cursor)
        if getattr(self.config, "contents_first_from_total", False):
            if remaining is not None:
                # Inclusive `after` may re-emit the last tile; skip_keys drop it.
                # Request one extra so the last new edge is not skipped.
                need = remaining + (1 if cursor.content_after else 0)
                return max(1, min(need, cap))
            return cap
        if getattr(self.config, "paginate_contents", True):
            return max(1, min(int(self.config.page_size or cap), cap))
        if remaining is not None:
            return max(1, min(remaining, cap))
        return cap

    def _variables(self, cursor: PageCursor) -> dict[str, Any]:
        first_n = cursor.module_first if cursor.module_first else self.config.module_page_size
        module_pagination: dict[str, Any] = {"first": max(1, int(first_n))}
        # NEVER send empty PaginationParams. GraphQL PaginationParams.first
        # defaults to 10 when omitted — that is a schema default, not CMS
        # LAYOUTS length, and must not be used as the rail size.
        content_pagination: dict[str, Any] = {"first": self._content_first(cursor)}
        if cursor.module_after:
            module_pagination["after"] = cursor.module_after
        if cursor.content_after:
            content_pagination["after"] = cursor.content_after
        # Do not send unused vars (GraphQL rejects them).
        return {
            "urlPath": self.config.url_path,
            "uiModulesPagination": module_pagination,
            "contentPagination": content_pagination,
        }

    def _execute_page(self, cursor: PageCursor, *, allow_errors: bool) -> dict[str, Any]:
        """Run one GraphQL page; shrink contents `first` if complexity is exceeded."""
        while True:
            try:
                return self.client.execute(
                    self.query,
                    self._variables(cursor),
                    allow_errors=allow_errors,
                )
            except GraphQLComplexityError:
                current_first = self._content_first(cursor)
                if current_first <= 1:
                    raise
                # Retry this same cursor with a smaller page; do not advance `after`.
                self._contents_first_cap = max(1, current_first // 2)
                continue

    def _probe_modules_total(self, *, allow_errors: bool) -> tuple[int | None, bool]:
        """Return (totalCount, page_missing). Does not invent a module cursor."""
        try:
            payload = self.client.execute(
                MODULES_TOTAL_COUNT_QUERY,
                {"urlPath": self.config.url_path},
                allow_errors=allow_errors,
            )
        except GraphQLError:
            if allow_errors:
                return None, True
            raise
        if _payload_ui_page(payload) is None and ((payload.get("data") or {}).get("uiPage") is None):
            return None, True
        return _read_modules_total(payload), False

    def _fetch_module_index(self, total: int, *, allow_errors: bool) -> list[_ModuleRef]:
        """List rails from offset 0 with first=totalCount and no after."""
        query = load_modules_index_query(self.query)
        if not query.strip():
            query = modules_index_query_from_layout(self.query) if self.query else MODULES_TOTAL_COUNT_QUERY
        first_n = max(1, int(total))
        variables = {
            "urlPath": self.config.url_path,
            "uiModulesPagination": {"first": first_n},
        }
        if self.config.debug:
            print(
                f"debug modules_index first={first_n} after=- url={self.config.url_path}",
                file=sys.stderr,
            )
        payload = self.client.execute(query, variables, allow_errors=allow_errors)
        if _payload_ui_page(payload) is None:
            self._index_raw_count = 0
            return []
        raw = _parse_module_index(payload)
        self._index_raw_count = len(raw)
        refs = _dedupe_module_refs(raw)
        listed_total = _read_modules_total(payload)
        if listed_total is not None:
            self.last_modules_total_count = listed_total
        return refs

    def _hydrate_cursor(self, index: int, refs: list[_ModuleRef], layout_after: str) -> PageCursor:
        """Pin one module using the layout query's own cursors.

        Cursors from the contents-free index query are not valid ``after`` values
        on the layout query. Using them replayed a block of rails (same
        ``module_id``, later ``carousel_y``) — e.g. Modo vacaciones at y=14 and y=18.

        Layout ``after`` is often inclusive: first=1 returns the previous rail
        again. That was skipped as a duplicate and the next unique listing was
        never hydrated (ODP abort: seen=45, listed=46). Request first=2 and
        keep ``slice_module_id``.
        """
        slice_id = refs[index].module_id or None
        after = first_text(layout_after)
        if index == 0:
            return PageCursor(
                module_first=1,
                slice_module_id=slice_id,
                slice_index=0,
                slice_last=not slice_id,
            )
        if after:
            # Inclusive after: first=1 is the previous rail. first=2 includes the
            # next one; keep edge 1 when id-slice misses.
            return PageCursor(
                module_after=after,
                module_first=2,
                slice_module_id=slice_id,
                slice_index=1,
                slice_last=True,
            )
        # Layout omitted edge cursors. Re-query from offset 0 with first=seen+1,
        # no after, then keep this rail so contents pagination stays per-module.
        return PageCursor(
            module_first=index + 1,
            slice_module_id=slice_id,
            slice_index=index,
            slice_last=not slice_id,
        )

    def _module_by_id(self, payload: dict[str, Any], module_id: str) -> dict[str, Any]:
        want = first_text(module_id)
        if not want:
            return {}
        for edge in ui_modules_edges(payload):
            module = (edge or {}).get("node") or {}
            if first_text(module.get("id"), module.get("trackingId")) == want:
                return module
        return {}

    def _note_content_progress(self, payload: dict[str, Any], cursor: PageCursor) -> None:
        """Accumulate contents.edges so later pages can stop at layout length / duplicates."""
        if cursor.content_after and cursor.content_module_id:
            module = self._module_by_id(payload, cursor.content_module_id)
            mid = cursor.content_module_id
            conn = module_layout_connection(module)
            n = contents_edge_count(conn)
            keys = [k for k in content_edge_keys(module) if k]
            prev = self._content_keys.setdefault(mid, set())
            new_n = sum(1 for k in keys if k not in prev)
            prev.update(keys)
            self._content_emitted[mid] = self._content_emitted.get(mid, 0) + n
            self._content_new_count[mid] = new_n
            layout_len = module_layout_length(module)
            if layout_len is not None:
                self._content_layout_totals[mid] = layout_len
            return
        for edge in ui_modules_edges(payload):
            module = (edge or {}).get("node") or {}
            mid = first_text(module.get("id"), module.get("trackingId"))
            if not mid:
                continue
            conn = module_layout_connection(module)
            keys = [k for k in content_edge_keys(module) if k]
            self._content_emitted[mid] = contents_edge_count(conn)
            self._content_keys[mid] = set(keys)
            self._content_new_count[mid] = len(keys)
            layout_len = module_layout_length(module)
            if layout_len is not None:
                self._content_layout_totals[mid] = layout_len

    def _content_exhausted(self, payload: dict[str, Any], current: PageCursor, module_id: str) -> bool:
        module = self._module_by_id(payload, module_id) if module_id else {}
        if module and not module_contents_has_more(module):
            return True
        conn = module_layout_connection(module) if module else {}
        if not isinstance(conn, dict):
            conn = {}
        total = self._content_layout_totals.get(module_id)
        if total is None:
            total = module_layout_length(module) if module else None
        if total is None:
            total = contents_total_count(conn)
        already = self._unique_emitted(module_id)
        if total is not None and already >= total:
            return True
        if current.content_after and self._content_new_count.get(module_id, 1) == 0:
            return True
        return not contents_has_more(conn)

    def _next_content_cursor(self, payload: dict[str, Any], current: PageCursor) -> PageCursor | None:
        content_cursors = pending_content_cursors(payload)
        if len(content_cursors) > 1:
            raise PaginationError(
                "Response has multiple independent carousel cursors: "
                + ", ".join(content_cursors.keys())
                + ". Query must paginate one content connection per request "
                "or define one cursor variable per row."
            )
        if not (
            getattr(self.config, "paginate_contents", True)
            or getattr(self.config, "contents_first_from_total", False)
        ):
            return None
        mid = pending_content_module_id(payload) or current.content_module_id or ""
        if content_cursors and mid and not self._content_exhausted(payload, current, mid):
            return PageCursor(
                module_after=current.module_after,
                content_after=next(iter(content_cursors.values())),
                content_module_id=mid,
                module_first=current.module_first,
                slice_module_id=current.slice_module_id,
                slice_index=current.slice_index,
                slice_last=current.slice_last,
            )
        if content_cursors and not mid:
            return PageCursor(
                module_after=current.module_after,
                content_after=next(iter(content_cursors.values())),
                module_first=current.module_first,
                slice_module_id=current.slice_module_id,
                slice_index=current.slice_index,
                slice_last=current.slice_last,
            )
        return None

    def iter_pages(self, *, allow_errors: bool = False) -> Iterator[tuple[int, PageCursor, dict[str, Any]]]:
        total, page_missing = self._probe_modules_total(allow_errors=allow_errors)
        if page_missing:
            if allow_errors:
                return
            raise PaginationError(
                f"{self.config.url_path}: uiPage returned null (modules totalCount probe)."
            )
        if total is None:
            total = 1
        self.last_modules_total_count = total

        try:
            refs = self._fetch_module_index(total, allow_errors=allow_errors)
        except GraphQLError:
            if allow_errors:
                return
            raise
        self._module_index = refs
        self._unique_listed = len(refs)
        if refs:
            if self._index_raw_count >= total:
                self.last_modules_total_count = len(refs)
            else:
                self.last_modules_total_count = max(total, len(refs))
        elif total > 0 and not allow_errors:
            raise PaginationError(
                f"{self.config.url_path}: scrape incomplete "
                f"(seen=0, totalCount={total}, hasNextPage=False) "
                f"but module endCursor is missing."
            )
        elif not refs:
            return

        # Pad only when the listing was truncated (fewer edges than totalCount).
        # If totalCount is high because the API repeated module_ids, do not
        # invent extra hydrations — that reprints the same rails at later y.
        planned = len(refs)
        raw_n = self._index_raw_count
        if raw_n < total:
            extra = [
                _ModuleRef(module_id="", cursor="", index=i) for i in range(planned, total)
            ]
            refs = list(refs) + extra
            self._module_index = refs
        target = len(refs)

        seen: set[PageCursor] = set()
        page_number = 0
        modules_seen = 0
        layout_after = ""
        hydrated_ids: set[str] = set()
        skipped_dupes = 0
        self.last_skipped_dupes = 0

        for i in range(target):
            cursor = self._hydrate_cursor(i, refs, layout_after)
            while True:
                if cursor in seen:
                    raise PaginationError(
                        f"Pagination cycle detected at {cursor}. Re-run with --debug to inspect cursors."
                    )
                seen.add(cursor)
                raw_payload = self._execute_page(cursor, allow_errors=allow_errors)
                payload = _slice_hydrate_payload(raw_payload, cursor)
                if not cursor.content_after and cursor.module_after:
                    unseen = _payload_keeping_unseen(raw_payload, hydrated_ids)
                    if len(ui_modules_edges(unseen)) == 1:
                        sliced_mid = (
                            _edge_module_id(ui_modules_edges(payload)[0])
                            if ui_modules_edges(payload)
                            else ""
                        )
                        if not sliced_mid or sliced_mid in hydrated_ids:
                            payload = unseen
                page_number += 1

                if self.config.debug:
                    self._debug_log(page_number, cursor, payload)

                ui_page = ((payload.get("data") or {}).get("uiPage"))
                if ui_page is None:
                    if page_number == 1 and allow_errors:
                        return
                    if modules_seen > 0 or cursor.module_after or cursor.content_after:
                        raise PaginationError(
                            f"{self.config.url_path}: uiPage returned null mid-scrape "
                            f"(page={page_number}, modules_seen={modules_seen})."
                        )
                    return

                new_edges = ui_modules_edges(payload)
                mid = _edge_module_id(new_edges[0]) if new_edges else ""
                edge_cursor = ""
                if new_edges and isinstance(new_edges[0], dict):
                    edge_cursor = first_text(new_edges[0].get("cursor"))
                want = first_text(refs[i].module_id) if i < len(refs) else ""

                if not cursor.content_after and want and mid != want:
                    # Index listing order can disagree with the layout connection
                    # (ODP abort at index=14: listed id A, layout returned B).
                    # Prefer the listed id when it is in this payload; otherwise
                    # keep a new layout rail. Do not fail the scrape for a mismatch.
                    advanced = bool(mid) and mid not in hydrated_ids
                    if not advanced and cursor.module_after and (cursor.module_first or 1) < (i + 1):
                        cursor = PageCursor(
                            module_first=i + 1,
                            slice_module_id=want,
                            slice_index=i,
                            slice_last=not want,
                        )
                        continue
                    if not advanced and mid and mid in hydrated_ids:
                        skipped_dupes += 1
                        if edge_cursor:
                            layout_after = edge_cursor
                        self.last_skipped_dupes = skipped_dupes
                        break
                    if not advanced and want not in hydrated_ids:
                        raise PaginationError(
                            f"{self.config.url_path}: hydrate for module {want} "
                            f"returned {mid or 'no edges'} (index={i})."
                        )

                if not cursor.content_after and mid and mid in hydrated_ids:
                    skipped_dupes += 1
                    if edge_cursor:
                        layout_after = edge_cursor
                    self.last_skipped_dupes = skipped_dupes
                    break

                yield page_number, cursor, payload

                if not cursor.content_after:
                    if mid:
                        hydrated_ids.add(mid)
                    modules_seen += len(new_edges)
                    self.last_modules_seen = modules_seen
                    if i < len(refs) and new_edges:
                        if mid or edge_cursor:
                            refs[i] = _ModuleRef(
                                module_id=mid or refs[i].module_id,
                                cursor=edge_cursor or refs[i].cursor,
                                index=i,
                            )
                    if edge_cursor:
                        layout_after = edge_cursor

                self._note_content_progress(payload, cursor)
                nxt = self._next_content_cursor(payload, cursor)
                if nxt is None:
                    break
                cursor = nxt

        unique_listed = self._unique_listed
        listing_complete = raw_n >= total
        self.last_skipped_dupes = skipped_dupes
        if modules_seen < unique_listed and skipped_dupes == 0:
            raise PaginationError(
                f"{self.config.url_path}: scrape incomplete "
                f"(seen={modules_seen}, listed={unique_listed}, totalCount={total}, "
                f"hasNextPage=False) but module endCursor is missing."
            )
        if not listing_complete and total is not None and modules_seen < total:
            raise PaginationError(
                f"{self.config.url_path}: scrape incomplete "
                f"(seen={modules_seen}, totalCount={total}, hasNextPage=False) "
                f"but module endCursor is missing."
            )

    def iter_pages_allow_missing(self) -> Iterator[tuple[int, PageCursor, dict[str, Any]]]:
        """Like iter_pages, but tolerates GraphQL errors such as missing uiPage."""
        yield from self.iter_pages(allow_errors=True)

    def _debug_log(self, page_number: int, cursor: PageCursor, payload: dict[str, Any]) -> None:
        page = (((payload.get("data") or {}).get("uiPage")) or {})
        modules_connection = page.get("uiModules") or {}
        module_has_next, module_cursor = page_info(modules_connection)
        content_cursors = pending_content_cursors(payload)
        print(
            f"debug page={page_number} module_after={cursor.module_after or '-'} "
            f"content_after={cursor.content_after or '-'} "
            f"modules_has_next={module_has_next} modules_cursor={module_cursor or '-'} "
            f"totalCount={_modules_total_count(modules_connection) if isinstance(modules_connection, dict) else None}",
            file=sys.stderr,
        )
        for row_title, content_cursor in content_cursors.items():
            print(f"debug content_row={row_title!r} cursor={content_cursor}", file=sys.stderr)
