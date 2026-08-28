"""Discover urlPaths from navigation and uiPage payloads."""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

from vix_scraper.util import dumps_json, first_text

_PATH_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_./\-]*$")


def normalize_path(value: Any, *, allow_external: bool = False) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://"):
        if not allow_external:
            parsed = urlparse(text)
            # Keep only same-site path segments from full ViX URLs.
            if "vix." not in (parsed.netloc or "") and not (parsed.netloc or "").endswith("vix.com"):
                return None
            text = parsed.path or ""
        else:
            return text
    if not text.startswith("/"):
        text = "/" + text
    # Drop fragments/query if present in relative paths.
    text = text.split("?", 1)[0].split("#", 1)[0]
    if text != "/" and text.endswith("/"):
        text = text.rstrip("/")
    if text == "/":
        return None  # root is not a valid uiPage for US staging/prod in practice
    if not _PATH_RE.match(text):
        return None
    # Skip pure legal/auth utility pages unless explicitly seeded — still allow them if found.
    return text


def _add_path(bucket: set[str], value: Any, *, allow_external: bool = False) -> None:
    path = normalize_path(value, allow_external=allow_external)
    if path:
        bucket.add(path)


def walk_nav_items(items: Iterable[Any], out: set[str]) -> None:
    for item in items or []:
        if not isinstance(item, dict):
            continue
        _add_path(out, item.get("urlPath"))
        walk_nav_items(item.get("subItems") or [], out)


def extract_paths_from_navigation(payload: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    data = payload.get("data") or {}
    config = data.get("clientConfig") or {}
    if isinstance(config, dict):
        _add_path(paths, config.get("defaultUrlPath"))
    for menu in data.get("uiNavigation") or []:
        if not isinstance(menu, dict):
            continue
        for section in menu.get("sections") or []:
            if isinstance(section, dict):
                walk_nav_items(section.get("items") or [], paths)
    return paths


def _walk_dict_for_paths(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"urlPath", "ctaUrlPath"}:
                _add_path(out, value)
            else:
                _walk_dict_for_paths(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_dict_for_paths(item, out)


def extract_paths_from_page_payload(payload: dict[str, Any]) -> set[str]:
    """Collect linked urlPaths from a uiPage response (modules, heroes, breadcrumbs)."""
    paths: set[str] = set()
    page = ((payload.get("data") or {}).get("uiPage")) or {}
    if not isinstance(page, dict):
        return paths

    meta = page.get("pageMetadata") or {}
    if isinstance(meta, dict):
        for crumb in meta.get("breadcrumbs") or []:
            if isinstance(crumb, dict):
                _add_path(paths, crumb.get("urlPath"))
        for alt in meta.get("altUrls") or []:
            _add_path(paths, alt)

    # Walk modules + nested CTA/page links.
    _walk_dict_for_paths(page.get("uiModules"), paths)
    return paths


def navigation_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten navigation into simple dict rows for optional export."""
    rows: list[dict[str, str]] = []
    for menu in ((payload.get("data") or {}).get("uiNavigation") or []):
        if not isinstance(menu, dict):
            continue
        menu_type = first_text(menu.get("menuType"))
        for section in menu.get("sections") or []:
            if not isinstance(section, dict):
                continue
            _flatten_items(section.get("items") or [], rows, menu_type=menu_type, parent="")
    return rows


def _flatten_items(
    items: Iterable[Any],
    rows: list[dict[str, str]],
    *,
    menu_type: str,
    parent: str,
) -> None:
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = first_text(item.get("text"))
        path = first_text(item.get("urlPath"))
        rows.append(
            {
                "menu_type": menu_type,
                "parent": parent,
                "text": text,
                "url_path": path,
                "action": first_text(item.get("action")),
                "item_type": first_text(item.get("itemType")),
                "icon_name": first_text(item.get("iconName")),
                "icon_link": first_text(((item.get("icon") or {}) if isinstance(item.get("icon"), dict) else {}).get("link")),
            }
        )
        label = text or path
        _flatten_items(
            item.get("subItems") or [],
            rows,
            menu_type=menu_type,
            parent=f"{parent}/{label}" if parent else label,
        )


# Re-export for callers that historically imported dumps_json from discovery.
__all__ = [
    "dumps_json",
    "extract_paths_from_navigation",
    "extract_paths_from_page_payload",
    "navigation_rows",
    "normalize_path",
]
