#!/usr/bin/env python3
"""
ViX-branded title layout compare UI + CLI.

CLI:
  .venv\\Scripts\\python.exe tools\\title_lookup.py "guardian"
  .venv\\Scripts\\python.exe tools\\title_lookup.py --json "casa"

Interactive form (stdlib HTTP server):
  .venv\\Scripts\\python.exe tools\\title_lookup.py
  tools\\run_title_lookup.ps1

Open http://127.0.0.1:8765/ — do not open as file://.
Tokens from the form are never logged.
"""

from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import sys
import threading
import time
import unicodedata
import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_DIR = PROJECT_ROOT / "output" / "layout_compare"
STATIC_DIR = Path(__file__).resolve().parent / "static"
ENV_LOCAL = PROJECT_ROOT / ".env.local"
STALE_HOURS = 12.0
MAX_PAGES = 4
MAX_COMPARE_RUNS = 3

DEFAULT_ENDPOINT = "https://client-api.vix.com/gql/v2"
DEFAULT_PAGES = [
    {"url_path": "/ondemandplus", "label": "On Demand Plus", "web_link": "https://vix.com/es-mx/ondemandplus"},
    {
        "url_path": "/ondemandpluswc",
        "label": "On Demand Plus WC",
        "web_link": "https://vix.com/es-mx/ondemandpluswc",
    },
]

# CLI / search table: "position" is the 1-based slot in the row (carousel_x), never the
# CSV page-wide index. That global index is `page_index` when exposed at all.
DISPLAY_FIELDS = [
    "page",
    "title",
    "row_title",
    "carousel_y",
    "carousel_x",
]
CLI_COLUMNS = [
    ("page", "page"),
    ("title", "title"),
    ("row_title", "row_title"),
    ("carousel_y", "row_#"),
    ("carousel_x", "position"),
]

# In-memory credentials for this server process only (never logged).
_SESSION: dict[str, str] = {}
# Per-device tokens/identity for this process (never logged).
_DEVICE_SESSION: dict[str, dict[str, str]] = {}

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+=*\.[A-Za-z0-9_\-]+=*\.[A-Za-z0-9_\-]+=*")
_BEARER_RE = re.compile(r"bearer\s+\S+", re.I)

# Background scrape progress (no tokens).
_SCRAPE_LOCK = threading.Lock()
_SCRAPE_STATE: dict[str, Any] = {
    "status": "idle",  # idle | running | done | error
    "message": "",
    "pages_done": 0,
    "pages_total": 0,
    "current_page": "",
    "current_device": "",
    "modules_done": 0,
    "modules_total": 0,
    "items_done": 0,
    "run_id": None,
    "ran_at_local": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "result": None,
}


def _ensure_src_path() -> None:
    src = str(PROJECT_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _layout_helpers():
    _ensure_src_path()
    from vix_scraper import layout_compare as lc

    return lc


def _devices_mod():
    _ensure_src_path()
    from vix_scraper import devices as devices_mod

    return devices_mod


def device_catalog_payload() -> list[dict[str, Any]]:
    return _devices_mod().catalog_payload()


def device_header_options() -> dict[str, list[str]]:
    return _devices_mod().header_option_lists()


def _count_data_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def friendly_page_label(url_path: str) -> str:
    mapping = {
        "/ondemandplus": "On Demand Plus",
        "/ondemandpluswc": "On Demand Plus WC",
    }
    if url_path in mapping:
        return mapping[url_path]
    slug = url_path.strip("/").replace("/", " · ") or "Home"
    return slug


def suggest_web_link(url_path: str) -> str:
    path = url_path if str(url_path).startswith("/") else f"/{url_path}"
    return f"https://vix.com/es-mx{path}"


def resolve_run_dir(run_id: str | None = None) -> tuple[str | None, Path]:
    """Return (run_id, directory with CSVs). Prefers runs/, then latest/, then root."""
    lc = _layout_helpers()
    lc.migrate_legacy_layout_csvs(LAYOUT_DIR)
    history = lc.load_history(LAYOUT_DIR)
    rid = run_id or history.get("latest_run_id")
    if rid:
        run_dir = lc.run_dir_for(LAYOUT_DIR, str(rid))
        if run_dir.is_dir() and any(run_dir.glob("*_titles.csv")):
            return str(rid), run_dir
    latest = LAYOUT_DIR / lc.LATEST_DIRNAME
    if latest.is_dir() and any(latest.glob("*_titles.csv")):
        pointer = LAYOUT_DIR / "latest.json"
        pointed = None
        if pointer.is_file():
            try:
                pointed = json.loads(pointer.read_text(encoding="utf-8")).get("run_id")
            except (OSError, json.JSONDecodeError):
                pointed = None
        return (str(pointed) if pointed else rid), latest
    # Fallback: root-level CSVs (pre-migration)
    if any(LAYOUT_DIR.glob("*_titles.csv")):
        return rid, LAYOUT_DIR
    raise FileNotFoundError(
        f"Missing title CSVs under {LAYOUT_DIR}. Use Scrape / Refresh in the UI first."
    )


def resolve_csv_paths(run_id: str | None = None) -> dict[str, Path]:
    """Map urlPath → CSV for a run (2–4 pages)."""
    lc = _layout_helpers()
    rid, run_dir = resolve_run_dir(run_id)
    meta = lc.load_scrape_meta(run_dir) or {}
    page_order = meta.get("page_order")
    pages_meta = meta.get("pages") if isinstance(meta.get("pages"), dict) else {}
    paths: dict[str, Path] = {}

    if isinstance(page_order, list) and page_order:
        ordered = [str(p) for p in page_order]
    elif pages_meta:
        ordered = list(pages_meta.keys())
    else:
        ordered = []

    for page in ordered:
        csv_name = None
        pm = pages_meta.get(page) if isinstance(pages_meta.get(page), dict) else {}
        if pm:
            csv_name = pm.get("csv")
        candidate = run_dir / (csv_name or lc.page_csv_name(page))
        if candidate.is_file():
            paths[page] = candidate

    if not paths:
        for csv_path in sorted(run_dir.glob("*_titles.csv")):
            if csv_path.name == "combined_titles.csv":
                continue
            slug = csv_path.name[: -len("_titles.csv")]
            page = f"/{slug}"
            paths[page] = csv_path

    if not paths:
        raise FileNotFoundError(
            f"Missing title CSVs for run {rid or '(latest)'} under {run_dir}."
        )
    return paths


def load_rows(paths: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page, path in paths.items():
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                title = (r.get("title") or "").strip()
                page_val = (r.get("page_url_path") or page or "").strip() or page
                rows.append(
                    {
                        "page": page_val if page_val.startswith("/") else page,
                        "title": title,
                        "row_title": (r.get("row_title") or "").strip(),
                        "carousel_y": (r.get("carousel_y") or "").strip(),
                        "carousel_x": (r.get("carousel_x") or "").strip(),
                        "position": (r.get("position") or "").strip(),
                        "module_type": (r.get("module_type") or "").strip(),
                        "module_id": (r.get("module_id") or "").strip(),
                        "row_size": (r.get("row_size") or "").strip(),
                        "is_hero": (r.get("is_hero") or "").strip().lower()
                        in ("true", "1", "yes"),
                        "video_type": (r.get("videoType") or r.get("video_type") or "").strip(),
                        "content_id": (r.get("id") or r.get("content_id") or "").strip(),
                        "_path": str(path),
                    }
                )
    return rows


def _fold(text: str) -> str:
    if not text:
        return ""
    norm = unicodedata.normalize("NFD", text.strip().casefold())
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def search_titles(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    q = _fold(query or "")
    if not q:
        return []
    # Title *or* row/module name — searching "cine" must list every title on
    # "Del cine a tu pantalla", not only movies whose title contains the query.
    hits = [
        r
        for r in rows
        if q in _fold(r.get("title") or "") or q in _fold(r.get("row_title") or "")
    ]
    hits.sort(
        key=lambda r: (
            r.get("page") or "",
            _int_or(r.get("carousel_y")),
            _int_or(r.get("carousel_x")),
            _int_or(r.get("position")),
            _fold(r.get("title") or ""),
        )
    )
    return hits


def _int_or(v: Any, default: int = 10**9) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def format_table(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "(no matches)"
    cols = list(CLI_COLUMNS)
    values: list[dict[str, str]] = []
    for h in hits:
        slot = slot_in_row(h)
        values.append(
            {
                "page": str(h.get("page") or ""),
                "title": str(h.get("title") or ""),
                "row_title": str(h.get("row_title") or ""),
                "carousel_y": str(h.get("carousel_y") or ""),
                "carousel_x": slot,
            }
        )
    widths = {key: len(header) for key, header in cols}
    for row in values:
        for key, _header in cols:
            widths[key] = max(widths[key], len(row.get(key, "")))
    header = "  ".join(h.ljust(widths[k]) for k, h in cols)
    sep = "  ".join("-" * widths[k] for k, _h in cols)
    lines = [header, sep]
    for row in values:
        lines.append("  ".join(row.get(k, "").ljust(widths[k]) for k, _h in cols))
    return "\n".join(lines)


def _redact_secrets(text: str) -> str:
    """Strip bearer / JWT-looking values from strings shown in the UI."""
    cleaned = _BEARER_RE.sub("Bearer [redacted]", text)
    cleaned = _JWT_RE.sub("[redacted]", cleaned)
    return cleaned


def map_error_message(exc: BaseException | str) -> str:
    """Map technical failures to actionable UI messages (never includes tokens)."""
    text = _redact_secrets(str(exc) if not isinstance(exc, str) else exc)
    low = text.lower()

    if "at most" in low and "pages" in low:
        return f"You can scrape at most {MAX_PAGES} pages at a time."
    if "at least one page" in low:
        return "Add at least one page path."
    if "at most" in low and "scrape" in low:
        return f"You can compare at most {MAX_COMPARE_RUNS} scrapes at a time."
    if "not in history" in low:
        return text if len(text) < 280 else (
            "That scrape is not in history. Pick a scrape from the History list."
        )
    if "not in the selected scrapes" in low:
        return text if len(text) < 320 else (
            "Those pages are not in the selected scrapes. Choose pages from the runs you picked."
        )
    if "permission denied" in low or "being used by another process" in low or "winerror 32" in low:
        return (
            "CSV file is locked — close Excel/Sheets (or any app) that has the titles CSV open, then retry."
        )
    if ("missing" in low and "csv" in low) or "filenotfound" in low:
        return (
            "CSV missing — run Scrape / Refresh data first, or pick a scrape from History."
        )
    if "enter authorization for" in low:
        return text if len(text) < 400 else text[:400] + "…"
    if "missing authorization" in low or ("auth_token" in low and "required" in low):
        return "Enter your Authorization Bearer token"
    if "no auth_token" in low or "has no auth_token" in low or "missing_auth" in low:
        return "Enter your Authorization Bearer token"
    if (
        "installation" in low
        or "statsig" in low
        or ("install" in low and "required" in low)
    ):
        return (
            "Installation ID is required for this page experience "
            "(Statsig / gated WC cluster). Paste x-vix-installation-id from the browser Network tab."
        )
    if "invalid_token" in low or "malformed" in low or "unauthorized" in low or "401" in low:
        return (
            "Token rejected by API — paste a fresh token from the browser Network tab; "
            "self-minted Authorization alone is not enough — also provide User token"
        )
    if "jwt country" in low or "catalog country" in low:
        return _redact_secrets(text if len(text) < 500 else text[:500] + "…")
    if "jwt expired" in low or "token expired" in low:
        return (
            "Auth token is expired — paste a fresh Authorization Bearer and x-vix-user-token "
            "from vix.com (Network → the gql/v2 request → Request Headers). VPN is not the issue."
        )
    if "complexity" in low and ("over the maximum" in low or "maximum of" in low):
        return (
            "GraphQL query was too heavy for one request (server complexity limit). "
            "The scraper now pages each row in smaller chunks — re-scrape. "
            "This is not a token or VPN problem."
        )
    if "all graphql endpoints failed" in low:
        cleaned = text
        if "bearer " in low:
            cleaned = "All GraphQL endpoints failed (auth-related detail redacted)."
        hint = ""
        if "uipage null" in low:
            hint = (
                " Empty uiPage with no GraphQL error usually means the Authorization token "
                "in use is expired or overridden (stale AUTH_TOKEN_AUTH0 beating a fresh paste), "
                "not a VPN problem."
            )
        combined = cleaned + hint
        if len(combined) > 500:
            combined = combined[:500] + "…"
        return combined
    if "uiPage null" in text or "empty page" in low or ("modulecount" in low and "0" in low):
        return (
            "Page returned empty / null — the API responded but uiPage was null. "
            "Usually an expired or overridden auth token (stale AUTH_TOKEN_AUTH0 can ignore "
            "a fresh paste). VPN / Accept-Language only matters if vix.com itself shows a US catalog."
        )
    if "geo" in low or "forbidden" in low or "403" in low:
        return (
            "Geo / access blocked — try a Mexico VPN and refresh tokens from vix.com in that region."
        )
    cleaned = text
    if "bearer " in low:
        cleaned = "Request failed (auth-related detail redacted). Check tokens and try again."
    if len(cleaned) > 400:
        cleaned = cleaned[:400] + "…"
    return _redact_secrets(cleaned)


def age_hours_from_iso(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        raw = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600.0
    except ValueError:
        return None


def build_compare_groups(
    hits: list[dict[str, Any]],
    pages: list[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Group hits by content_id for side-by-side compare across 2–4 pages."""
    page_list = list(pages or [])
    if not page_list:
        seen: list[str] = []
        for h in hits:
            p = h.get("page") or ""
            if p and p not in seen:
                seen.append(p)
        page_list = seen or ["/ondemandplus", "/ondemandpluswc"]

    by_key: dict[str, dict[str, Any]] = {}
    for h in hits:
        key = h.get("content_id") or _fold(h.get("title") or "") or h.get("title") or "?"
        g = by_key.setdefault(
            key,
            {
                "title": h.get("title") or "",
                "content_id": h.get("content_id") or "",
                "by_page": {p: [] for p in page_list},
                # Backward-compatible aliases for the two default pages / older tests
                "odp": [],
                "wc": [],
            },
        )
        if not g["title"] and h.get("title"):
            g["title"] = h["title"]
        if not g["content_id"] and h.get("content_id"):
            g["content_id"] = h["content_id"]
        slot = slot_in_row(h)
        slim = {
            "title": h.get("title") or "",
            "row_title": h.get("row_title") or "",
            "carousel_y": h.get("carousel_y") or "",
            # User-facing slot in this page's row — never the CSV page-wide index.
            "carousel_x": slot,
            "slot": slot,
            "position": h.get("position") or "",
            "page_index": h.get("position") or "",
            "page": h.get("page") or "",
        }
        page = h.get("page") or ""
        if page not in g["by_page"]:
            g["by_page"][page] = []
            if page not in page_list:
                page_list.append(page)
        g["by_page"][page].append(slim)
        if page == "/ondemandpluswc":
            g["wc"].append(slim)
        elif page == "/ondemandplus":
            g["odp"].append(slim)

    only_counts = {p: 0 for p in page_list}
    moved = same_row = 0
    groups: list[dict[str, Any]] = []
    for g in by_key.values():
        present = [p for p in page_list if g["by_page"].get(p)]
        missing = [p for p in page_list if not g["by_page"].get(p)]
        for p in missing:
            only_for = [x for x in present]
            if len(only_for) == 1:
                only_counts[only_for[0]] = only_counts.get(only_for[0], 0) + 1
            for plist in g["by_page"].values():
                for item in plist:
                    if "compare_status" not in item:
                        item["compare_status"] = "missing"
                        item["compare_label"] = "only on some pages"

        if len(present) >= 2:
            primary = present[0]
            for other in present[1:]:
                a_list = g["by_page"][primary]
                b_list = g["by_page"][other]
                for i, p in enumerate(a_list):
                    mate = b_list[i] if i < len(b_list) else None
                    if mate is None:
                        p["compare_status"] = "moved"
                        p["compare_label"] = "different place"
                        moved += 1
                        continue
                    same_row_name = (p.get("row_title") or "") == (mate.get("row_title") or "")
                    same_x = slot_in_row(p) == slot_in_row(mate)
                    if same_row_name and same_x:
                        p["compare_status"] = "same_row"
                        p["compare_label"] = "same place"
                        mate["compare_status"] = "same_row"
                        mate["compare_label"] = "same place"
                        same_row += 1
                    else:
                        p["compare_status"] = "moved"
                        p["compare_label"] = "different place"
                        mate["compare_status"] = "moved"
                        mate["compare_label"] = "different place"
                        moved += 1
        elif len(present) == 1:
            for item in g["by_page"][present[0]]:
                item["compare_status"] = "missing"
                item["compare_label"] = f"only on {friendly_page_label(present[0])}"

        g["pages"] = page_list
        g["sides"] = [
            {
                "page": p,
                "label": friendly_page_label(p),
                "placements": g["by_page"].get(p) or [],
            }
            for p in page_list
        ]
        groups.append(g)

    groups.sort(key=lambda g: _fold(g.get("title") or ""))
    summary: dict[str, int] = {
        "titles": len(groups),
        "placements": len(hits),
        "moved": moved,
        "same_row": same_row,
        "only_odp": only_counts.get("/ondemandplus", 0),
        "only_wc": only_counts.get("/ondemandpluswc", 0),
    }
    for p, n in only_counts.items():
        summary[f"only:{p}"] = n
    return groups, summary


def format_history_when(iso: str | None = None, fallback: str | None = None) -> str:
    """Format a scrape time as ``08 Aug 2026 15:03:25`` in local time."""
    months = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )

    def _fmt(dt: datetime) -> str:
        local = dt.astimezone() if dt.tzinfo else dt
        return (
            f"{local.day:02d} {months[local.month - 1]} {local.year} "
            f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}"
        )

    if iso:
        try:
            raw = str(iso).replace("Z", "+00:00")
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return _fmt(parsed)
        except ValueError:
            pass
    if fallback:
        text = str(fallback).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
                return _fmt(parsed)
            except ValueError:
                continue
        return text
    return ""


def page_csv_names(files: list[Any] | None, pages: list[Any] | None = None) -> list[str]:
    """Page title CSVs only (not combined or other artifacts), up to MAX_PAGES."""
    names: list[str] = []
    for item in files or []:
        name = item.get("name") if isinstance(item, dict) else str(item)
        if not name or not str(name).endswith("_titles.csv"):
            continue
        if str(name) == "combined_titles.csv":
            continue
        if name not in names:
            names.append(str(name))
    ordered: list[str] = []
    for page in pages or []:
        slug = str(page).strip("/").replace("/", "_") or "root"
        expect = f"{slug}_titles.csv"
        if expect in names and expect not in ordered:
            ordered.append(expect)
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered[:MAX_PAGES]


def _progress_percent(snap: dict[str, Any]) -> int:
    status = snap.get("status")
    if status == "done":
        return 100
    if status != "running":
        return 0
    pages_done = int(snap.get("pages_done") or 0)
    pages_total = max(1, int(snap.get("pages_total") or 1))
    modules_done = int(snap.get("modules_done") or 0)
    modules_total = int(snap.get("modules_total") or 0)
    if modules_total > 0:
        frac = min(1.0, modules_done / modules_total)
        raw = ((pages_done + frac) / pages_total) * 100
        return max(0, min(99, int(round(raw))))
    return max(0, min(99, int(round((pages_done / pages_total) * 100))))


def scrape_status_snapshot() -> dict[str, Any]:
    with _SCRAPE_LOCK:
        snap = dict(_SCRAPE_STATE)
    row_index = int(snap.get("modules_done") or 0)
    rows_total = int(snap.get("modules_total") or 0)
    snap["row_index"] = row_index
    snap["rows_total"] = rows_total
    snap["percent"] = _progress_percent(snap)
    return snap


def _set_scrape_state(**kwargs: Any) -> None:
    with _SCRAPE_LOCK:
        _SCRAPE_STATE.update(kwargs)


class LookupState:
    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id
        self.reload(run_id)

    def reload(self, run_id: str | None = None) -> None:
        if run_id is not None:
            self.run_id = run_id
        lc = _layout_helpers()
        lc.migrate_legacy_layout_csvs(LAYOUT_DIR)
        self.run_id, self.run_dir = resolve_run_dir(self.run_id)
        self.paths = resolve_csv_paths(self.run_id)
        self.page_order = list(self.paths.keys())
        self.rows = load_rows(self.paths)
        self.counts = {
            page: sum(1 for r in self.rows if r["page"] == page) for page in self.paths
        }
        self.scrape_meta = lc.load_scrape_meta(self.run_dir)

    def history_payload(self) -> dict[str, Any]:
        lc = _layout_helpers()
        lc.migrate_legacy_layout_csvs(LAYOUT_DIR)
        history = lc.load_history(LAYOUT_DIR)
        runs_out = []
        for entry in history.get("runs") or []:
            if not isinstance(entry, dict):
                continue
            rid = entry.get("run_id")
            status = str(entry.get("status") or "unknown").lower()
            if status not in {"ok", "partial"}:
                continue
            run_dir = lc.run_dir_for(LAYOUT_DIR, str(rid)) if rid else None
            files = lc.list_run_files(run_dir) if run_dir else []
            pages = entry.get("pages") or []
            scrape = lc.load_scrape_meta(run_dir) if run_dir else {}
            scrape = scrape if isinstance(scrape, dict) else {}
            device_id = entry.get("device_id") or scrape.get("device_id") or ""
            device_label = entry.get("device_label") or scrape.get("device_label") or ""
            platform = entry.get("platform") or scrape.get("platform") or ""
            device_type = entry.get("device_type") or scrape.get("device") or ""
            page_csvs = page_csv_names(files, pages)
            runs_out.append(
                {
                    "run_id": rid,
                    "ran_at": entry.get("ran_at"),
                    "ran_at_local": entry.get("ran_at_local") or "",
                    "when": format_history_when(
                        entry.get("ran_at") or scrape.get("ran_at"),
                        entry.get("ran_at_local") or scrape.get("ran_at_local") or "",
                    ),
                    "pages": pages,
                    "page_labels": [friendly_page_label(str(p)) for p in pages],
                    "status": entry.get("status") or "unknown",
                    "row_counts": entry.get("row_counts") or {},
                    "duration_seconds": entry.get("duration_seconds"),
                    "device_id": device_id,
                    "device_label": device_label,
                    "platform": platform,
                    "device_type": device_type,
                    "is_latest": rid == history.get("latest_run_id"),
                    "is_selected": rid == self.run_id,
                    "files": files,
                    "page_csvs": page_csvs,
                    "dir": entry.get("dir") or (f"runs/{rid}" if rid else ""),
                }
            )
        return {
            "latest_run_id": history.get("latest_run_id"),
            "selected_run_id": self.run_id,
            "runs": runs_out,
        }

    def meta(self) -> dict[str, Any]:
        scrape = self.scrape_meta or {}
        status = scrape_status_snapshot()
        scraping = status.get("status") == "running"

        ran_at = scrape.get("ran_at")
        ran_local = scrape.get("ran_at_local")
        age = age_hours_from_iso(str(ran_at) if ran_at else None)
        pages_out: dict[str, Any] = {}
        raw_pages = scrape.get("pages") if isinstance(scrape.get("pages"), dict) else {}
        for key in self.page_order:
            pm = raw_pages.get(key) if isinstance(raw_pages.get(key), dict) else {}
            pages_out[key] = {
                "label": friendly_page_label(key),
                "ran_at_local": pm.get("ran_at_local") or "",
                "row_count": self.counts.get(key, pm.get("row_count")),
                "status": "ok" if pm.get("status") == "ok" else (pm.get("status") or "ok"),
                "web_link": suggest_web_link(key),
            }

        web_links = {p: suggest_web_link(p) for p in self.page_order}
        return {
            "counts": self.counts,
            "pages": pages_out,
            "page_order": self.page_order,
            "page_labels": {p: friendly_page_label(p) for p in self.page_order},
            "web_links": web_links,
            "age_hours": age,
            "stale": bool(age is not None and age > STALE_HOURS),
            "ran_at_local": "" if scraping else (ran_local or ""),
            "data_as_of_label": "Scraping…" if scraping else (ran_local or "Not scraped yet"),
            "scraping": scraping,
            "duration_seconds": scrape.get("duration_seconds"),
            "run_id": self.run_id,
            "device_id": scrape.get("device_id") or "",
            "device_label": scrape.get("device_label") or "",
            "platform": scrape.get("platform") or "",
            "device_type": scrape.get("device") or "",
            "max_pages": MAX_PAGES,
            "default_pages": DEFAULT_PAGES,
            "devices": device_catalog_payload(),
            "saved_devices": saved_device_auth_ids(),
            **device_header_options(),
            "history": self.history_payload(),
            "scrape_status": status,
            "country": scrape.get("country") or "",
            "jwt_country": scrape.get("jwt_country") or None,
            "notes": list(scrape.get("notes") or []) if isinstance(scrape.get("notes"), list) else [],
        }

    def slim_hits(self, query: str) -> list[dict[str, Any]]:
        hits = search_titles(self.rows, query)
        out: list[dict[str, Any]] = []
        for h in hits:
            slot = slot_in_row(h)
            out.append(
                {
                    "page": h.get("page") or "",
                    "title": h.get("title") or "",
                    "row_title": h.get("row_title") or "",
                    "carousel_y": h.get("carousel_y") or "",
                    "carousel_x": slot,
                    "slot": slot,
                }
            )
        return out

    def compare(
        self,
        query: str,
        run_ids: list[str] | None = None,
        pages: list[str] | None = None,
    ) -> dict[str, Any]:
        ids = [str(r).strip() for r in (run_ids or []) if str(r).strip()]
        if not ids and self.run_id:
            ids = [str(self.run_id)]
        if not ids:
            # Fall back to currently loaded rows (legacy / empty history)
            page_list = _normalize_page_filters(pages) or list(self.page_order)
            hits = [
                h
                for h in search_titles(self.rows, query)
                if not page_list or (h.get("page") or "") in page_list
            ]
            groups, summary = build_compare_groups(hits, page_list or self.page_order)
            table_rows = build_table_rows(
                groups,
                page_list or self.page_order,
                run_id=self.run_id,
                ran_at_local=(self.scrape_meta or {}).get("ran_at_local") or "",
            )
            for g in groups:
                g["table_rows"] = build_table_rows(
                    [g],
                    page_list or self.page_order,
                    run_id=self.run_id,
                    ran_at_local=(self.scrape_meta or {}).get("ran_at_local") or "",
                )
            return {
                "query": query,
                "mode": "single",
                "run_ids": [self.run_id] if self.run_id else [],
                "runs": [],
                "groups": groups,
                "table_rows": table_rows,
                "matrix": {"columns": [], "rows": []},
                "summary": summary,
                "page_order": page_list or self.page_order,
                "page_labels": {
                    p: friendly_page_label(p) for p in (page_list or self.page_order)
                },
                "available_pages": list(self.page_order),
                "available_page_labels": {
                    p: friendly_page_label(p) for p in self.page_order
                },
                "max_compare_runs": MAX_COMPARE_RUNS,
                **self.meta(),
            }

        payload = compare_across_runs(query, ids, pages=pages)
        # Attach freshness/meta from the first selected run when possible
        try:
            if ids[0] != self.run_id:
                self.reload(ids[0])
            payload.update({k: v for k, v in self.meta().items() if k not in payload})
        except FileNotFoundError:
            payload["history"] = self.history_payload()
        return payload

    def layouts(
        self,
        run_ids: list[str] | None = None,
        pages: list[str] | None = None,
    ) -> dict[str, Any]:
        ids = [str(r).strip() for r in (run_ids or []) if str(r).strip()]
        if not ids and self.run_id:
            ids = [str(self.run_id)]
        if not ids:
            raise FileNotFoundError("Pick at least one scrape from History.")
        payload = page_layouts_across_runs(ids, pages=pages)
        try:
            if ids[0] != self.run_id:
                self.reload(ids[0])
            payload.update({k: v for k, v in self.meta().items() if k not in payload})
        except FileNotFoundError:
            payload["history"] = self.history_payload()
        return payload


def _strip_bearer(token: str) -> str:
    t = (token or "").strip()
    if t.lower().startswith("bearer "):
        return t[7:].strip()
    return t


def _device_env_suffix(device_id: str) -> str:
    return _devices_mod().env_suffix(device_id)


def _pick_nonempty(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _payload_device_count(payload: dict[str, Any] | None) -> int:
    """How many device ids the scrape POST selected (0 if omitted)."""
    raw = (payload or {}).get("devices")
    if raw is None or raw == "":
        return 0
    if isinstance(raw, str):
        return len([part.strip() for part in raw.split(",") if part.strip()])
    if isinstance(raw, list):
        return len(raw)
    return 0


def overlay_for_device(payload: dict[str, Any] | None, device: Any) -> dict[str, str]:
    """Resolve the shared session tokens plus this device's platform / device-type.

    One pasted token set is used for every selected device. ``device_creds`` may
    be only ``{platform, device_type}``; missing token keys fall back to the
    form / session / env. Catalog User-Agent and app version fill in when omitted.
    """
    dm = _devices_mod()
    resolved = dm.resolve_device(device)
    data = payload or {}
    block = dm.creds_block_from_payload(data, resolved.id)
    suffix = dm.env_suffix(resolved.id)
    session = _DEVICE_SESSION.get(resolved.id) or {}

    auth = _strip_bearer(
        _pick_nonempty(
            data.get("auth_token"),
            block.get("auth_token"),
            _SESSION.get("auth_token"),
            os.getenv("AUTH_TOKEN"),
            session.get("auth_token"),
            os.getenv(f"AUTH_TOKEN_{suffix}"),
        )
    )
    user = _pick_nonempty(
        data.get("x_vix_user_token"),
        block.get("x_vix_user_token"),
        _SESSION.get("x_vix_user_token"),
        os.getenv("X_VIX_USER_TOKEN"),
        session.get("x_vix_user_token"),
        os.getenv(f"X_VIX_USER_TOKEN_{suffix}"),
    )
    install = _pick_nonempty(
        data.get("installation_id"),
        block.get("installation_id"),
        _SESSION.get("installation_id"),
        os.getenv("VIX_INSTALLATION_ID"),
        session.get("installation_id"),
        os.getenv(f"VIX_INSTALLATION_ID_{suffix}"),
    )
    from vix_scraper.auth import is_jwt_expired, resolve_auth_profile

    creds = resolve_auth_profile("default")
    if not auth:
        auth = _strip_bearer(creds.auth_token or "")
    elif (
        is_jwt_expired(auth)
        and creds.auth_token
        and not is_jwt_expired(creds.auth_token)
    ):
        auth = _strip_bearer(creds.auth_token)
    if not user:
        user = (creds.x_vix_user_token or "").strip()

    # Console/dropdown overrides land in device_creds; a single-device POST also
    # sends top-level platform / device_type. Honor those so scrape headers match
    # the UI instead of a stale VIX_PLATFORM_* env from a previous web run.
    single_device = _payload_device_count(data) <= 1
    top_platform = data.get("platform") if single_device else ""
    top_device_type = data.get("device_type") if single_device else ""

    return {
        "auth_token": auth,
        "x_vix_user_token": user,
        "installation_id": install,
        "platform": _pick_nonempty(
            block.get("platform"),
            top_platform,
            os.getenv(f"VIX_PLATFORM_{suffix}"),
            session.get("platform"),
            resolved.platform,
        ),
        "device_type": _pick_nonempty(
            block.get("device_type"),
            top_device_type,
            os.getenv(f"VIX_DEVICE_TYPE_{suffix}"),
            session.get("device_type"),
            resolved.device_type,
        ),
        "app_version": _pick_nonempty(
            block.get("app_version"),
            os.getenv(f"VIX_APP_VERSION_{suffix}"),
            session.get("app_version"),
            resolved.app_version,
        ),
        "country": _pick_nonempty(
            data.get("country"),
            block.get("country"),
            os.getenv("VIX_COUNTRY"),
            "MX",
        ).upper(),
        "accept_language": _pick_nonempty(
            data.get("accept_language"),
            block.get("accept_language"),
            os.getenv("VIX_ACCEPT_LANGUAGE"),
            "es-MX,es;q=0.9",
        ),
        "user_agent": _pick_nonempty(
            block.get("user_agent"),
            os.getenv(f"VIX_USER_AGENT_{suffix}"),
            session.get("user_agent"),
            resolved.user_agent,
        ),
    }


def device_has_auth(payload: dict[str, Any] | None, device: Any) -> bool:
    return bool(overlay_for_device(payload, device).get("auth_token"))


def missing_required_creds_message(payload: dict[str, Any] | None) -> str | None:
    """UI scrape requires Authorization, user token, and installation ID on the POST."""
    data = payload or {}
    auth = _strip_bearer(str(data.get("auth_token") or ""))
    user = str(data.get("x_vix_user_token") or "").strip()
    install = str(data.get("installation_id") or "").strip()
    if not auth:
        return "Enter Authorization — paste from the Network tab → gql/v2 request headers"
    if not user:
        return "Enter User token"
    if not install:
        return "Installation ID is required"
    return None


def missing_device_auth_labels(payload: dict[str, Any] | None, devices: list[Any]) -> list[str]:
    return [d.label for d in devices if not device_has_auth(payload, d)]


def saved_device_auth_ids() -> list[str]:
    """Device ids that have a persisted/session token. Never returns token values."""
    out: list[str] = []
    for device in _devices_mod().list_devices():
        suffix = _devices_mod().env_suffix(device.id)
        if (os.getenv(f"AUTH_TOKEN_{suffix}") or "").strip():
            out.append(device.id)
        elif device.id == "web" and (
            (os.getenv("AUTH_TOKEN") or "").strip()
            or (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
            or bool(_SESSION.get("auth_token"))
        ):
            out.append(device.id)
        elif (_DEVICE_SESSION.get(device.id) or {}).get("auth_token"):
            out.append(device.id)
    return out


def build_device_scrape_config(
    payload: dict[str, Any],
    device: Any,
    pages: list[str],
    *,
    query_file: Path | None = None,
    output_dir: Path | None = None,
) -> Any:
    """Build a ScrapeConfig that uses *this* device's tokens and identity only."""
    from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

    dm = _devices_mod()
    resolved = dm.resolve_device(device)
    overlay = overlay_for_device(payload, resolved)
    endpoint = str(payload.get("endpoint") or DEFAULT_ENDPOINT).strip() or PRODUCTION_ENDPOINT
    cfg = ScrapeConfig(
        url_path=pages[0] if pages else "",
        url_paths=list(pages),
        endpoint=endpoint,
        query_file=query_file,
        auth_token=overlay.get("auth_token") or None,
        x_vix_user_token=overlay.get("x_vix_user_token") or None,
        installation_id=overlay.get("installation_id") or None,
        output_dir=output_dir or Path("output/layout_compare"),
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        timeout=60,
        retries=3,
        deduplicate=False,
        download_images=False,
        auth_profile="default",
        auth_profile_map={p: "default" for p in pages},
    )
    dm.apply_device(cfg, resolved, overlay)
    if overlay.get("country"):
        cfg.country = overlay["country"]
    if overlay.get("accept_language"):
        cfg.accept_language = overlay["accept_language"]
    return cfg


@contextmanager
def _device_env_scope(device: Any, overlay: dict[str, str]):
    """Pin process env to one device for the duration of its scrape. Never logs."""
    keys = (
        "AUTH_TOKEN",
        "X_VIX_USER_TOKEN",
        "VIX_INSTALLATION_ID",
        "AUTH_TOKEN_AUTH0",
        "VIX_PLATFORM",
        "VIX_DEVICE_TYPE",
        "VIX_USER_AGENT",
        "VIX_APP_VERSION",
        "VIX_COUNTRY",
        "VIX_ACCEPT_LANGUAGE",
    )
    saved = {key: os.environ.get(key) for key in keys}
    try:
        if overlay.get("auth_token"):
            os.environ["AUTH_TOKEN"] = overlay["auth_token"]
        else:
            os.environ.pop("AUTH_TOKEN", None)
        if overlay.get("x_vix_user_token"):
            os.environ["X_VIX_USER_TOKEN"] = overlay["x_vix_user_token"]
        else:
            os.environ.pop("X_VIX_USER_TOKEN", None)
        if overlay.get("installation_id"):
            os.environ["VIX_INSTALLATION_ID"] = overlay["installation_id"]
        else:
            os.environ.pop("VIX_INSTALLATION_ID", None)
        if getattr(device, "id", "") != "web":
            os.environ.pop("AUTH_TOKEN_AUTH0", None)
        os.environ["VIX_PLATFORM"] = overlay.get("platform") or "web"
        os.environ["VIX_DEVICE_TYPE"] = overlay.get("device_type") or "desktop"
        if overlay.get("user_agent"):
            os.environ["VIX_USER_AGENT"] = overlay["user_agent"]
        if overlay.get("app_version"):
            os.environ["VIX_APP_VERSION"] = overlay["app_version"]
        if overlay.get("country"):
            os.environ["VIX_COUNTRY"] = overlay["country"]
        if overlay.get("accept_language"):
            os.environ["VIX_ACCEPT_LANGUAGE"] = overlay["accept_language"]
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _store_device_session(device_id: str, overlay: dict[str, str]) -> None:
    _DEVICE_SESSION[device_id] = {
        key: value for key, value in overlay.items() if value
    }


def _apply_session_to_environ(payload: dict[str, Any]) -> None:
    """Set process env from form/session. Never log values."""
    devices = _devices_mod().devices_from_payload(payload)
    endpoint = str(payload.get("endpoint") or _SESSION.get("endpoint") or DEFAULT_ENDPOINT).strip()
    if endpoint:
        _SESSION["endpoint"] = endpoint
        os.environ["VIX_GRAPHQL_ENDPOINT"] = endpoint

    shared = overlay_for_device(payload, devices[0] if devices else "web")
    if shared.get("auth_token"):
        _SESSION["auth_token"] = shared["auth_token"]
        os.environ["AUTH_TOKEN"] = shared["auth_token"]
    if shared.get("x_vix_user_token"):
        _SESSION["x_vix_user_token"] = shared["x_vix_user_token"]
        os.environ["X_VIX_USER_TOKEN"] = shared["x_vix_user_token"]
    if shared.get("installation_id"):
        _SESSION["installation_id"] = shared["installation_id"]
        os.environ["VIX_INSTALLATION_ID"] = shared["installation_id"]

    first = devices[0] if devices else None
    if first is not None:
        first_overlay = overlay_for_device(payload, first)
        _SESSION["platform"] = first_overlay.get("platform") or first.platform
        _SESSION["device_type"] = first_overlay.get("device_type") or first.device_type
        _SESSION["device_id"] = first.id
        _SESSION["user_agent"] = first_overlay.get("user_agent") or first.user_agent
        _SESSION["app_version"] = first_overlay.get("app_version") or first.app_version
        os.environ["VIX_PLATFORM"] = _SESSION["platform"]
        os.environ["VIX_DEVICE_TYPE"] = _SESSION["device_type"]
        os.environ["VIX_USER_AGENT"] = _SESSION["user_agent"]
        os.environ["VIX_APP_VERSION"] = _SESSION["app_version"]

    for device in devices:
        overlay = overlay_for_device(payload, device)
        _store_device_session(device.id, overlay)
        suffix = _device_env_suffix(device.id)
        os.environ[f"VIX_PLATFORM_{suffix}"] = overlay.get("platform") or device.platform
        os.environ[f"VIX_DEVICE_TYPE_{suffix}"] = overlay.get("device_type") or device.device_type
        if overlay.get("user_agent"):
            os.environ[f"VIX_USER_AGENT_{suffix}"] = overlay["user_agent"]
        if overlay.get("app_version"):
            os.environ[f"VIX_APP_VERSION_{suffix}"] = overlay["app_version"]

    _SESSION["devices"] = ",".join(d.id for d in devices)
    os.environ["VIX_DEVICES"] = _SESSION["devices"]


def _read_env_local_map() -> dict[str, str]:
    if not ENV_LOCAL.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            out[key] = value
    return out


def _persist_env_local(payload: dict[str, Any]) -> None:
    """Write gitignored .env.local — never print contents."""
    existing = _read_env_local_map()
    devices = _devices_mod().devices_from_payload(payload)
    endpoint = str(payload.get("endpoint") or DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    existing["VIX_GRAPHQL_ENDPOINT"] = endpoint
    existing["VIX_DEVICES"] = ",".join(d.id for d in devices)

    shared = overlay_for_device(payload, devices[0] if devices else "web")
    if shared.get("auth_token"):
        existing["AUTH_TOKEN"] = shared["auth_token"]
    if shared.get("x_vix_user_token"):
        existing["X_VIX_USER_TOKEN"] = shared["x_vix_user_token"]
    if shared.get("installation_id"):
        existing["VIX_INSTALLATION_ID"] = shared["installation_id"]

    first = devices[0] if devices else None
    if first is not None:
        first_overlay = overlay_for_device(payload, first)
        existing["VIX_PLATFORM"] = first_overlay.get("platform") or first.platform
        existing["VIX_DEVICE_TYPE"] = first_overlay.get("device_type") or first.device_type
        if first_overlay.get("country"):
            existing["VIX_COUNTRY"] = first_overlay["country"]
        if first_overlay.get("accept_language"):
            existing["VIX_ACCEPT_LANGUAGE"] = first_overlay["accept_language"]

    keep_token_keys = {"AUTH_TOKEN", "X_VIX_USER_TOKEN", "VIX_INSTALLATION_ID", "AUTH_TOKEN_AUTH0"}
    drop_prefixes = ("AUTH_TOKEN_", "X_VIX_USER_TOKEN_", "VIX_INSTALLATION_ID_")
    for key in list(existing):
        if key in keep_token_keys:
            continue
        if key.startswith(drop_prefixes):
            existing.pop(key, None)

    for device in devices:
        overlay = overlay_for_device(payload, device)
        suffix = _device_env_suffix(device.id)
        existing[f"VIX_PLATFORM_{suffix}"] = overlay.get("platform") or device.platform
        existing[f"VIX_DEVICE_TYPE_{suffix}"] = overlay.get("device_type") or device.device_type

    preferred = [
        "VIX_GRAPHQL_ENDPOINT",
        "VIX_DEVICES",
        "VIX_PLATFORM",
        "VIX_DEVICE_TYPE",
        "VIX_COUNTRY",
        "VIX_ACCEPT_LANGUAGE",
        "AUTH_TOKEN",
        "X_VIX_USER_TOKEN",
        "VIX_INSTALLATION_ID",
    ]
    lines = ["# Generated by title_lookup UI — do not commit"]
    seen: set[str] = set()
    for key in preferred:
        if key in existing:
            lines.append(f"{key}={existing[key]}")
            seen.add(key)
    for key in sorted(existing):
        if key in seen:
            continue
        lines.append(f"{key}={existing[key]}")
    ENV_LOCAL.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pages_from_payload(payload: dict[str, Any]) -> list[str]:
    from vix_scraper.errors import ScraperError

    lc = _layout_helpers()
    pages_raw = payload.get("pages")
    if pages_raw is None:
        pages_raw = payload.get("page_details")
    if pages_raw is None:
        pages_raw = ["/ondemandplus", "/ondemandpluswc"]
    if not isinstance(pages_raw, list):
        pages_raw = [pages_raw]
    has_value = False
    for item in pages_raw:
        if isinstance(item, dict):
            text = str(item.get("url_path") or item.get("path") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            has_value = True
            break
    if not has_value:
        raise ScraperError("Add at least one page path.")
    return lc.normalize_pages(pages_raw)


def _reset_state_empty(state: LookupState) -> None:
    state.run_id = None
    state.run_dir = LAYOUT_DIR
    state.paths = {}
    state.page_order = []
    state.rows = []
    state.counts = {}
    state.scrape_meta = {}


def _empty_meta_payload(state: LookupState) -> dict[str, Any]:
    return {
        "counts": {},
        "pages": {},
        "page_order": [],
        "page_labels": {},
        "web_links": {},
        "ran_at_local": "",
        "data_as_of_label": "Not scraped yet",
        "scraping": False,
        "run_id": None,
        "history": state.history_payload(),
        "scrape_status": scrape_status_snapshot(),
        "max_pages": MAX_PAGES,
        "default_pages": DEFAULT_PAGES,
        "devices": device_catalog_payload(),
        "saved_devices": saved_device_auth_ids(),
        **device_header_options(),
    }


def delete_history_run_from_ui(run_id: str, state: LookupState) -> dict[str, Any]:
    """Delete a finished scrape from history. Refuses while a scrape is running."""
    _ensure_src_path()
    lc = _layout_helpers()
    rid = (run_id or "").strip()
    if not rid:
        return {"ok": False, "error": "Pick a scrape to delete."}

    with _SCRAPE_LOCK:
        if _SCRAPE_STATE.get("status") == "running":
            active = _SCRAPE_STATE.get("run_id")
            if active and str(active) == rid:
                return {
                    "ok": False,
                    "error": "That scrape is still running. Wait for it to finish, then delete.",
                }
            return {
                "ok": False,
                "error": "Wait for the current scrape to finish before deleting history.",
            }

    result = lc.delete_history_run(LAYOUT_DIR, rid)
    if not result.get("ok"):
        return result

    next_id = result.get("latest_run_id")
    preferred = next_id if state.run_id == rid else (state.run_id or next_id)
    try:
        state.reload(str(preferred) if preferred else None)
    except FileNotFoundError:
        try:
            state.reload(str(next_id) if next_id else None)
        except FileNotFoundError:
            _reset_state_empty(state)

    payload: dict[str, Any] = {
        "ok": True,
        "deleted_run_id": rid,
        "latest_run_id": next_id,
        "message": result.get("message") or "Scrape removed from history.",
    }
    if state.run_id:
        payload.update(state.meta())
    else:
        payload.update(_empty_meta_payload(state))
    return payload


def delete_all_history_from_ui(state: LookupState) -> dict[str, Any]:
    """Delete every scrape from history. Refuses while a scrape is running."""
    _ensure_src_path()
    lc = _layout_helpers()

    with _SCRAPE_LOCK:
        if _SCRAPE_STATE.get("status") == "running":
            return {
                "ok": False,
                "error": "Wait for the current scrape to finish before deleting history.",
            }

    result = lc.delete_all_history(LAYOUT_DIR)
    if not result.get("ok"):
        return result

    _reset_state_empty(state)
    payload: dict[str, Any] = {
        "ok": True,
        "deleted_count": result.get("deleted_count") or 0,
        "deleted_run_ids": result.get("deleted_run_ids") or [],
        "latest_run_id": None,
        "message": result.get("message") or "All scrape history deleted.",
    }
    payload.update(_empty_meta_payload(state))
    return payload


def _parse_csv_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts: list[str] = []
    for chunk in str(raw).split(","):
        item = chunk.strip()
        if item and item not in parts:
            parts.append(item)
    return parts


def _normalize_page_filters(pages: list[str] | None) -> list[str]:
    if not pages:
        return []
    out: list[str] = []
    for p in pages:
        path = str(p or "").strip()
        if not path:
            continue
        if not path.startswith("/"):
            path = f"/{path}"
        if path not in out:
            out.append(path)
    return out


def _run_meta_brief(run_id: str, run_dir: Path, history_entry: dict[str, Any] | None) -> dict[str, Any]:
    lc = _layout_helpers()
    scrape = lc.load_scrape_meta(run_dir) or {}
    pages = []
    if history_entry and isinstance(history_entry.get("pages"), list):
        pages = [str(p) for p in history_entry["pages"]]
    elif isinstance(scrape.get("page_order"), list):
        pages = [str(p) for p in scrape["page_order"]]
    return {
        "run_id": run_id,
        "ran_at": (history_entry or {}).get("ran_at") or scrape.get("ran_at"),
        "ran_at_local": (history_entry or {}).get("ran_at_local")
        or scrape.get("ran_at_local")
        or run_id,
        "pages": pages,
        "page_labels": [friendly_page_label(p) for p in pages],
        "status": (history_entry or {}).get("status") or scrape.get("status") or "unknown",
        "device_id": (history_entry or {}).get("device_id") or scrape.get("device_id") or "",
        "device_label": (history_entry or {}).get("device_label") or scrape.get("device_label") or "",
        "platform": (history_entry or {}).get("platform") or scrape.get("platform") or "",
    }


def slot_in_row(row: dict[str, Any] | None) -> str:
    """1-based tile index within the carousel/row — not the global page ``position``."""
    if not row:
        return ""
    raw = row.get("carousel_x")
    if raw is None or str(raw).strip() == "":
        raw = row.get("slot")
    if raw is None or str(raw).strip() == "":
        return ""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return str(raw).strip()
    return "1" if n <= 0 else str(n)


def _placement_cell(p: dict[str, Any] | None) -> dict[str, Any]:
    if not p:
        return {
            "row_title": "",
            "carousel_y": "",
            "carousel_x": "",
            "position": "",
            "page_index": "",
            "slot": "",
            "present": False,
            "label": "Not on this page",
        }
    slot = slot_in_row(p)
    page_index = p.get("position") or p.get("page_index") or ""
    return {
        "row_title": p.get("row_title") or "",
        "carousel_y": p.get("carousel_y") or "",
        "carousel_x": slot,
        "position": page_index,
        "page_index": page_index,
        "slot": slot,
        "present": True,
        "label": "",
        "compare_status": p.get("compare_status") or "",
        "compare_label": p.get("compare_label") or "",
    }


def build_table_rows(
    groups: list[dict[str, Any]],
    pages: list[str],
    *,
    run_id: str | None = None,
    ran_at_local: str | None = None,
) -> list[dict[str, Any]]:
    """Flatten compare groups into non-technical table rows (page × placement)."""
    rows_out: list[dict[str, Any]] = []
    for g in groups:
        by_page = g.get("by_page") or {}
        for page in pages:
            placements = by_page.get(page) or []
            if not placements:
                rows_out.append(
                    {
                        "title": g.get("title") or "",
                        "content_id": g.get("content_id") or "",
                        "page": page,
                        "page_label": friendly_page_label(page),
                        "run_id": run_id,
                        "ran_at_local": ran_at_local or "",
                        "row_title": "",
                        "carousel_y": "",
                        "carousel_x": "",
                        "position": "",
                        "page_index": "",
                        "slot": "",
                        "present": False,
                        "also_at": [],
                    }
                )
                continue
            primary = placements[0]
            also = [_placement_cell(x) for x in placements[1:]]
            primary_slot = slot_in_row(primary)
            primary_page_index = primary.get("position") or primary.get("page_index") or ""
            rows_out.append(
                {
                    "title": g.get("title") or "",
                    "content_id": g.get("content_id") or "",
                    "page": page,
                    "page_label": friendly_page_label(page),
                    "run_id": run_id,
                    "ran_at_local": ran_at_local or "",
                    "row_title": primary.get("row_title") or "",
                    "carousel_y": primary.get("carousel_y") or "",
                    "carousel_x": primary_slot,
                    "position": primary_page_index,
                    "page_index": primary_page_index,
                    "slot": primary_slot,
                    "present": True,
                    "compare_status": primary.get("compare_status") or "",
                    "compare_label": primary.get("compare_label") or "",
                    "also_at": also,
                }
            )
            for extra in placements[1:]:
                extra_slot = slot_in_row(extra)
                extra_page_index = extra.get("position") or extra.get("page_index") or ""
                rows_out.append(
                    {
                        "title": g.get("title") or "",
                        "content_id": g.get("content_id") or "",
                        "page": page,
                        "page_label": friendly_page_label(page),
                        "run_id": run_id,
                        "ran_at_local": ran_at_local or "",
                        "row_title": extra.get("row_title") or "",
                        "carousel_y": extra.get("carousel_y") or "",
                        "carousel_x": extra_slot,
                        "position": extra_page_index,
                        "page_index": extra_page_index,
                        "slot": extra_slot,
                        "present": True,
                        "is_also_at": True,
                        "compare_status": extra.get("compare_status") or "",
                        "compare_label": extra.get("compare_label") or "",
                        "also_at": [],
                    }
                )
    return rows_out


def _prepare_selected_runs(
    run_ids: list[str],
    pages: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve scrape folders, CSVs, and page filters for compare / layout views."""
    lc = _layout_helpers()
    lc.migrate_legacy_layout_csvs(LAYOUT_DIR)
    history = lc.load_history(LAYOUT_DIR)
    history_by_id = {
        str(r.get("run_id")): r
        for r in (history.get("runs") or [])
        if isinstance(r, dict) and r.get("run_id")
    }

    cleaned: list[str] = []
    for rid in run_ids:
        s = str(rid or "").strip()
        if s and s not in cleaned:
            cleaned.append(s)
    if not cleaned:
        raise FileNotFoundError("Pick at least one scrape from History.")
    if len(cleaned) > MAX_COMPARE_RUNS:
        raise ValueError(f"You can compare at most {MAX_COMPARE_RUNS} scrapes at a time.")

    missing = [rid for rid in cleaned if rid not in history_by_id]
    still_missing: list[str] = []
    for rid in missing:
        if not lc.run_dir_for(LAYOUT_DIR, rid).is_dir():
            still_missing.append(rid)
    if still_missing:
        bad = ", ".join(still_missing)
        raise FileNotFoundError(
            f"That scrape is not in history: {bad}. Pick a scrape from the History list."
        )

    page_filter = _normalize_page_filters(pages)
    run_briefs: list[dict[str, Any]] = []
    rows_by_run: dict[str, list[dict[str, Any]]] = {}
    union_pages: list[str] = []

    for rid in cleaned:
        _, run_dir = resolve_run_dir(rid)
        run_briefs.append(_run_meta_brief(rid, run_dir, history_by_id.get(rid)))
        paths = resolve_csv_paths(rid)
        for page in paths:
            if page not in union_pages:
                union_pages.append(page)
        rows_by_run[rid] = load_rows(paths)

    selected_pages = page_filter or list(union_pages)
    if page_filter:
        unknown = [p for p in page_filter if p not in union_pages]
        if unknown:
            raise ValueError(
                "Those pages are not in the selected scrapes: "
                + ", ".join(unknown)
                + ". Choose pages that appear in the History runs you picked."
            )
        selected_pages = [p for p in page_filter if p in union_pages]
    if not selected_pages:
        raise FileNotFoundError("No pages found in the selected scrapes.")
    if len(selected_pages) > MAX_PAGES:
        selected_pages = selected_pages[:MAX_PAGES]

    return {
        "cleaned": cleaned,
        "run_briefs": run_briefs,
        "rows_by_run": rows_by_run,
        "union_pages": union_pages,
        "selected_pages": selected_pages,
        "history_by_id": history_by_id,
    }


def _is_empty_tile(row: dict[str, Any]) -> bool:
    video_type = str(row.get("video_type") or "").strip().upper()
    title = str(row.get("title") or "").strip()
    return video_type == "EMPTY" or title == "(empty)"


def layout_rails_for_page(rows: list[dict[str, Any]], page: str) -> list[dict[str, Any]]:
    """Group CSV title rows into ordered rails with numbered tiles."""
    by_y: dict[int, dict[str, Any]] = {}
    for row in rows:
        if (row.get("page") or "") != page:
            continue
        y = _int_or(row.get("carousel_y"), 0)
        if y <= 0:
            continue
        rail = by_y.get(y)
        if rail is None:
            declared = _int_or(row.get("row_size"), -1)
            rail = {
                "carousel_y": y,
                "row_title": (row.get("row_title") or "").strip(),
                "module_type": (row.get("module_type") or "").strip(),
                "is_hero": bool(row.get("is_hero")),
                "row_size": declared if declared >= 0 else None,
                "titles": [],
            }
            by_y[y] = rail
        elif row.get("row_title") and not rail["row_title"]:
            rail["row_title"] = str(row.get("row_title") or "").strip()
        if row.get("is_hero"):
            rail["is_hero"] = True
        declared = _int_or(row.get("row_size"), -1)
        if declared >= 0 and rail.get("row_size") is None:
            rail["row_size"] = declared
        rail["titles"].append(
            {
                "carousel_x": _int_or(row.get("carousel_x"), 0),
                "title": (row.get("title") or "").strip() or "(empty)",
                "video_type": (row.get("video_type") or "").strip(),
                "empty": _is_empty_tile(row),
            }
        )

    rails: list[dict[str, Any]] = []
    for y in sorted(by_y):
        rail = by_y[y]
        rail["titles"].sort(key=lambda t: (int(t["carousel_x"] or 0), str(t["title"])))
        filled = [t for t in rail["titles"] if not t.get("empty")]
        if rail.get("row_size") is None:
            rail["row_size"] = len(filled)
        rail["empty"] = not filled
        rails.append(rail)
    return rails


def page_layouts_across_runs(
    run_ids: list[str],
    pages: list[str] | None = None,
) -> dict[str, Any]:
    """Side-by-side page layouts (rows in CMS/API order, titles by carousel_x)."""
    bundle = _prepare_selected_runs(run_ids, pages)
    cleaned = bundle["cleaned"]
    selected_pages = bundle["selected_pages"]
    columns: list[dict[str, Any]] = []
    for page in selected_pages:
        scrapes: list[dict[str, Any]] = []
        for brief in bundle["run_briefs"]:
            rid = str(brief.get("run_id") or "")
            rails = layout_rails_for_page(bundle["rows_by_run"].get(rid) or [], page)
            scrapes.append(
                {
                    "run_id": rid,
                    "when": format_history_when(
                        brief.get("ran_at"),
                        brief.get("ran_at_local") or brief.get("ran_at"),
                    ),
                    "device_label": brief.get("device_label") or "",
                    "row_count": len(rails),
                    "rows": rails,
                }
            )
        columns.append(
            {
                "page": page,
                "label": friendly_page_label(page),
                "scrapes": scrapes,
            }
        )
    return {
        "mode": "layouts",
        "run_ids": cleaned,
        "runs": bundle["run_briefs"],
        "pages": columns,
        "page_order": selected_pages,
        "page_labels": {p: friendly_page_label(p) for p in selected_pages},
        "available_pages": bundle["union_pages"],
        "available_page_labels": {
            p: friendly_page_label(p) for p in bundle["union_pages"]
        },
        "summary": {
            "runs": len(cleaned),
            "pages": len(selected_pages),
            "rows": sum(s["row_count"] for col in columns for s in col["scrapes"]),
        },
        "max_compare_runs": MAX_COMPARE_RUNS,
    }


def compare_across_runs(
    query: str,
    run_ids: list[str],
    pages: list[str] | None = None,
) -> dict[str, Any]:
    """Compare a title across pages and up to MAX_COMPARE_RUNS history runs."""
    bundle = _prepare_selected_runs(run_ids, pages)
    cleaned = bundle["cleaned"]
    run_briefs = bundle["run_briefs"]
    union_pages = bundle["union_pages"]
    selected_pages = bundle["selected_pages"]
    run_hits = {rid: search_titles(rows, query) for rid, rows in bundle["rows_by_run"].items()}

    multi = len(cleaned) > 1
    all_groups: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "titles": 0,
        "placements": 0,
        "runs": len(cleaned),
        "pages": len(selected_pages),
        "moved": 0,
        "same_row": 0,
    }

    # Index hits by title key across runs
    keys_order: list[str] = []
    key_title: dict[str, str] = {}
    key_content: dict[str, str] = {}
    keyed: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for rid in cleaned:
        for h in run_hits[rid]:
            page = h.get("page") or ""
            if page not in selected_pages:
                continue
            key = h.get("content_id") or _fold(h.get("title") or "") or h.get("title") or "?"
            if key not in keyed:
                keyed[key] = {r: [] for r in cleaned}
                keys_order.append(key)
                key_title[key] = h.get("title") or ""
                key_content[key] = h.get("content_id") or ""
            if not key_title[key] and h.get("title"):
                key_title[key] = h["title"]
            if not key_content[key] and h.get("content_id"):
                key_content[key] = h["content_id"]
            keyed[key][rid].append(h)
            summary["placements"] = int(summary["placements"]) + 1

    summary["titles"] = len(keys_order)

    for key in keys_order:
        # Per-run groups for status labels within a single run's pages
        by_run_groups: dict[str, dict[str, Any]] = {}
        for rid in cleaned:
            hits = [h for h in keyed[key][rid] if (h.get("page") or "") in selected_pages]
            groups, run_summary = build_compare_groups(hits, selected_pages)
            g0 = groups[0] if groups else {
                "title": key_title[key],
                "content_id": key_content[key],
                "by_page": {p: [] for p in selected_pages},
                "sides": [
                    {"page": p, "label": friendly_page_label(p), "placements": []}
                    for p in selected_pages
                ],
                "odp": [],
                "wc": [],
                "pages": selected_pages,
            }
            by_run_groups[rid] = g0
            summary["moved"] = int(summary["moved"]) + int(run_summary.get("moved") or 0)
            summary["same_row"] = int(summary["same_row"]) + int(run_summary.get("same_row") or 0)

        # Flat table: page × run
        title_table: list[dict[str, Any]] = []
        for page in selected_pages:
            for rid, brief in zip(cleaned, run_briefs):
                g = by_run_groups[rid]
                placements = (g.get("by_page") or {}).get(page) or []
                if not placements:
                    row = {
                        "title": key_title[key],
                        "content_id": key_content[key],
                        "page": page,
                        "page_label": friendly_page_label(page),
                        "run_id": rid,
                        "ran_at_local": brief.get("ran_at_local") or rid,
                    "device_label": brief.get("device_label") or "",
                        "row_title": "",
                        "carousel_y": "",
                        "carousel_x": "",
                        "position": "",
                        "page_index": "",
                        "slot": "",
                        "present": False,
                        "also_at": [],
                    }
                    title_table.append(row)
                    table_rows.append(row)
                    continue
                # Multiple placements → primary + also_at (and extra rows if preferred)
                primary = placements[0]
                also_cells = [_placement_cell(x) for x in placements[1:]]
                primary_slot = slot_in_row(primary)
                primary_page_index = primary.get("position") or primary.get("page_index") or ""
                row = {
                    "title": key_title[key],
                    "content_id": key_content[key],
                    "page": page,
                    "page_label": friendly_page_label(page),
                    "run_id": rid,
                    "ran_at_local": brief.get("ran_at_local") or rid,
                    "device_label": brief.get("device_label") or "",
                    "row_title": primary.get("row_title") or "",
                    "carousel_y": primary.get("carousel_y") or "",
                    "carousel_x": primary_slot,
                    "position": primary_page_index,
                    "page_index": primary_page_index,
                    "slot": primary_slot,
                    "present": True,
                    "compare_status": primary.get("compare_status") or "",
                    "compare_label": primary.get("compare_label") or "",
                    "also_at": also_cells,
                }
                title_table.append(row)
                table_rows.append(row)
                for extra in placements[1:]:
                    extra_slot = slot_in_row(extra)
                    extra_page_index = extra.get("position") or extra.get("page_index") or ""
                    extra_row = {
                        "title": key_title[key],
                        "content_id": key_content[key],
                        "page": page,
                        "page_label": friendly_page_label(page),
                        "run_id": rid,
                        "ran_at_local": brief.get("ran_at_local") or rid,
                        "device_label": brief.get("device_label") or "",
                        "row_title": extra.get("row_title") or "",
                        "carousel_y": extra.get("carousel_y") or "",
                        "carousel_x": extra_slot,
                        "position": extra_page_index,
                        "page_index": extra_page_index,
                        "slot": extra_slot,
                        "present": True,
                        "is_also_at": True,
                        "compare_status": extra.get("compare_status") or "",
                        "compare_label": extra.get("compare_label") or "",
                        "also_at": [],
                    }
                    title_table.append(extra_row)
                    table_rows.append(extra_row)

        # Matrix: one row per page, cells keyed by run_id
        for page in selected_pages:
            cells: dict[str, Any] = {}
            for rid, brief in zip(cleaned, run_briefs):
                g = by_run_groups[rid]
                placements = (g.get("by_page") or {}).get(page) or []
                cells[rid] = {
                    "run_id": rid,
                    "ran_at_local": brief.get("ran_at_local") or rid,
                    "device_label": brief.get("device_label") or "",
                    "placements": [_placement_cell(p) for p in placements] if placements else [],
                    "present": bool(placements),
                }
            matrix_rows.append(
                {
                    "title": key_title[key],
                    "content_id": key_content[key],
                    "page": page,
                    "page_label": friendly_page_label(page),
                    "cells": cells,
                }
            )

        primary_group = by_run_groups[cleaned[0]]
        primary_group["table_rows"] = title_table
        primary_group["by_run"] = {
            rid: {
                "run_id": rid,
                "ran_at_local": brief.get("ran_at_local") or rid,
                "by_page": by_run_groups[rid].get("by_page") or {},
                "sides": by_run_groups[rid].get("sides") or [],
            }
            for rid, brief in zip(cleaned, run_briefs)
        }
        all_groups.append(primary_group)

    # Single-run: prefer classic groups + table_rows without run column noise
    if not multi and all_groups:
        brief0 = run_briefs[0]
        hits0 = [h for h in run_hits[cleaned[0]] if (h.get("page") or "") in selected_pages]
        groups, single_summary = build_compare_groups(hits0, selected_pages)
        for g in groups:
            g["table_rows"] = build_table_rows(
                [g],
                selected_pages,
                run_id=cleaned[0],
                ran_at_local=str(brief0.get("ran_at_local") or ""),
            )
        all_groups = groups
        summary.update(single_summary)
        summary["runs"] = 1
        summary["pages"] = len(selected_pages)
        table_rows = []
        for g in all_groups:
            table_rows.extend(g.get("table_rows") or [])
        matrix_rows = []
        for g in all_groups:
            for page in selected_pages:
                placements = (g.get("by_page") or {}).get(page) or []
                matrix_rows.append(
                    {
                        "title": g.get("title") or "",
                        "content_id": g.get("content_id") or "",
                        "page": page,
                        "page_label": friendly_page_label(page),
                        "cells": {
                            cleaned[0]: {
                                "run_id": cleaned[0],
                                "ran_at_local": brief0.get("ran_at_local") or cleaned[0],
                                "placements": [_placement_cell(p) for p in placements]
                                if placements
                                else [],
                                "present": bool(placements),
                            }
                        },
                    }
                )

    return {
        "query": query,
        "mode": "multi" if multi else "single",
        "run_ids": cleaned,
        "runs": run_briefs,
        "groups": all_groups,
        "table_rows": table_rows,
        "matrix": {
            "columns": [
                {
                    "run_id": b["run_id"],
                    "ran_at_local": b.get("ran_at_local") or b["run_id"],
                    "device_label": b.get("device_label") or "",
                }
                for b in run_briefs
            ],
            "rows": matrix_rows,
        },
        "summary": summary,
        "page_order": selected_pages,
        "page_labels": {p: friendly_page_label(p) for p in selected_pages},
        "available_pages": union_pages,
        "available_page_labels": {p: friendly_page_label(p) for p in union_pages},
        "max_compare_runs": MAX_COMPARE_RUNS,
    }


def start_scrape_from_ui(payload: dict[str, Any], state: LookupState) -> dict[str, Any]:
    """Validate and start background scrape. Returns immediately (no tokens in response)."""
    _ensure_src_path()
    from vix_scraper.errors import ScraperError

    with _SCRAPE_LOCK:
        if _SCRAPE_STATE.get("status") == "running":
            return {
                "ok": True,
                "started": False,
                "message": "A scrape is already running.",
                "status": scrape_status_snapshot(),
            }

    try:
        pages = _pages_from_payload(payload)
        devices = _devices_mod().devices_from_payload(payload)
    except Exception as exc:  # noqa: BLE001
        raise ScraperError(map_error_message(exc)) from exc

    missing_msg = missing_required_creds_message(payload)
    if missing_msg:
        raise ScraperError(missing_msg)

    missing = missing_device_auth_labels(payload, devices)
    if missing:
        raise ScraperError(
            "Enter Authorization — paste from the Network tab → gql/v2 request headers"
        )

    _apply_session_to_environ(payload)
    if payload.get("persist_local"):
        _persist_env_local(payload)

    _set_scrape_state(
        status="running",
        message="Scraping…",
        pages_done=0,
        pages_total=len(pages) * len(devices),
        current_page="",
        current_device=devices[0].label if devices else "",
        modules_done=0,
        modules_total=0,
        items_done=0,
        run_id=None,
        ran_at_local=None,
        error=None,
        started_at=time.time(),
        finished_at=None,
        result=None,
    )

    thread = threading.Thread(
        target=_scrape_worker,
        args=(payload, pages, devices, state),
        daemon=True,
        name="vix-scrape",
    )
    thread.start()
    return {
        "ok": True,
        "started": True,
        "message": "Scraping…",
        "pages_total": len(pages) * len(devices),
        "status": scrape_status_snapshot(),
    }


def _scrape_worker(
    payload: dict[str, Any],
    pages: list[str],
    devices: list[Any],
    state: LookupState,
) -> None:
    _ensure_src_path()
    from vix_scraper.config import load_dotenv
    from vix_scraper.layout_compare import (
        create_run_dir,
        list_run_files,
        run_batch_scrape,
        sync_latest,
        upsert_history_entry,
    )
    load_dotenv(PROJECT_ROOT / ".env")
    if ENV_LOCAL.is_file():
        # UI-persisted credentials must override stale .env values.
        load_dotenv(ENV_LOCAL, override=True)

    lc_run_id = None
    try:
        _apply_session_to_environ(payload)
        if not devices:
            devices = _devices_mod().devices_from_payload(payload)

        layout_query = PROJECT_ROOT / "queries" / "layout.graphql"
        query_file = (
            layout_query if layout_query.is_file() else PROJECT_ROOT / "queries" / "request.graphql"
        )
        total_units = max(1, len(pages) * len(devices))
        overall_started = time.monotonic()
        summaries: list[str] = []
        last_ok_run: str | None = None
        any_ok = False

        for idx, device in enumerate(devices):
            lc_run_id, run_dir = create_run_dir(LAYOUT_DIR)
            overlay = overlay_for_device(payload, device)
            _set_scrape_state(
                run_id=lc_run_id,
                current_device=device.label,
                message=f"Scraping {device.label}…",
                pages_done=idx * len(pages),
                pages_total=total_units,
            )

            def on_progress(
                event: dict[str, Any],
                _idx: int = idx,
                _device: Any = device,
            ) -> None:
                page = str(event.get("page") or "")
                msg = event.get("message") or "Scraping…"
                _set_scrape_state(
                    message=f"{_device.label}: {msg}",
                    pages_done=_idx * len(pages) + int(event.get("pages_done") or 0),
                    pages_total=total_units,
                    current_page=f"{_device.label} {page}".strip(),
                    current_device=_device.label,
                    modules_done=int(event.get("modules_done") or 0),
                    modules_total=int(event.get("modules_total") or 0),
                    items_done=int(event.get("items_done") or 0),
                    ran_at_local=event.get("ran_at_local"),
                )

            started = time.monotonic()
            try:
                if not overlay.get("auth_token"):
                    raise RuntimeError(
                        "Enter Authorization — paste from the Network tab → gql/v2 request headers"
                    )
                cfg = build_device_scrape_config(
                    payload,
                    device,
                    pages,
                    query_file=query_file,
                    output_dir=run_dir,
                )
                with _device_env_scope(device, overlay):
                    result = run_batch_scrape(cfg, progress_callback=on_progress)
                duration = round(time.monotonic() - started, 1)
                ok = [o for o in result.outcomes if o.status == "ok"]
                failed = [o for o in result.outcomes if o.status != "ok"]
                meta = _layout_helpers().load_scrape_meta(run_dir) or {}
                batch_status = meta.get("status") or ("ok" if not failed and ok else "failed")
                row_counts = {
                    (
                        o.url_path if o.url_path in pages else pages[i] if i < len(pages) else o.url_path
                    ): len(o.titles)
                    for i, o in enumerate(result.outcomes)
                }
                if isinstance(meta.get("row_counts"), dict):
                    row_counts = {str(k): int(v or 0) for k, v in meta["row_counts"].items()}
            except Exception as device_exc:  # noqa: BLE001
                duration = round(time.monotonic() - started, 1)
                err = map_error_message(device_exc)
                batch_status = "failed"
                row_counts = {p: 0 for p in pages}
                meta = {
                    "status": "failed",
                    "device_id": device.id,
                    "device_label": device.label,
                    "platform": overlay.get("platform") or device.platform,
                    "device": overlay.get("device_type") or device.device_type,
                    "pages": {
                        p: {"status": "error", "error": err, "row_count": 0} for p in pages
                    },
                    "row_counts": row_counts,
                    "error": err,
                }
                try:
                    _layout_helpers().write_scrape_meta(run_dir, meta)
                except Exception:
                    pass

            entry = {
                "run_id": lc_run_id,
                "ran_at": meta.get("ran_at"),
                "ran_at_local": meta.get("ran_at_local") or "",
                "pages": pages,
                "status": batch_status,
                "row_counts": row_counts,
                "duration_seconds": meta.get("duration_seconds") or duration,
                "dir": f"runs/{lc_run_id}",
                "files": [f["name"] for f in list_run_files(run_dir)],
                "device_id": device.id,
                "device_label": device.label,
                "platform": overlay.get("platform") or device.platform,
                "device_type": overlay.get("device_type") or device.device_type,
            }
            if batch_status in ("ok", "partial"):
                upsert_history_entry(LAYOUT_DIR, entry)
                sync_latest(LAYOUT_DIR, lc_run_id, run_dir)
                any_ok = True
                last_ok_run = lc_run_id
            else:
                history = _layout_helpers().load_history(LAYOUT_DIR)
                runs = [r for r in history.get("runs") or [] if isinstance(r, dict)]
                runs.insert(0, entry)
                history["runs"] = runs
                _layout_helpers().save_history(LAYOUT_DIR, history)

            parts = [f"{device.label} in {duration}s"]
            for p in pages:
                label = friendly_page_label(p)
                count = row_counts.get(p)
                status_p = ((meta.get("pages") or {}).get(p) or {}).get("status")
                if status_p == "ok":
                    parts.append(f"{label}: {count} titles")
                else:
                    err = ((meta.get("pages") or {}).get(p) or {}).get("error") or status_p or "failed"
                    parts.append(f"{label}: {map_error_message(err)}")
            summaries.append(" · ".join(parts))

        duration = round(time.monotonic() - overall_started, 1)
        result_payload = {
            "ok": any_ok,
            "message": " | ".join(summaries) or f"Done in {duration}s",
            "duration_seconds": duration,
            "ran_at_local": "",
            "run_id": last_ok_run or lc_run_id,
            "status": "ok" if any_ok else "failed",
            "devices": [d.id for d in devices],
        }
        try:
            state.reload(last_ok_run if any_ok else state.run_id)
        except FileNotFoundError:
            pass

        _set_scrape_state(
            status="done" if result_payload["ok"] else "error",
            message=result_payload["message"],
            pages_done=total_units,
            pages_total=total_units,
            ran_at_local=result_payload.get("ran_at_local"),
            finished_at=time.time(),
            error=None if result_payload["ok"] else result_payload["message"],
            result=result_payload,
            run_id=result_payload["run_id"],
        )
    except Exception as exc:  # noqa: BLE001
        _set_scrape_state(
            status="error",
            message=map_error_message(exc),
            error=map_error_message(exc),
            finished_at=time.time(),
            ran_at_local=None,
            run_id=lc_run_id,
            result={"ok": False, "error": map_error_message(exc), "run_id": lc_run_id},
        )


def _query_from_qs(qs: dict[str, list[str]]) -> str:
    return (qs.get("q") or [""])[0].strip()


def make_handler(state: LookupState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            msg = fmt % args
            if "auth" in msg.lower() or "token" in msg.lower() or "bearer" in msg.lower():
                msg = msg.split("?")[0] + " [redacted]"
            sys.stderr.write("%s - %s\n" % (self.address_string(), msg))

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(code, body, "application/json; charset=utf-8")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _serve_static(self, rel: str) -> None:
            rel = rel.lstrip("/").replace("\\", "/")
            if rel.startswith("static/"):
                rel = rel[len("static/") :]
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self._send(200, data, ctype)

        def _serve_run_file(self, run_id: str, filename: str) -> None:
            lc = _layout_helpers()
            safe_name = Path(filename).name
            if safe_name != filename or ".." in filename:
                self._send(400, b"Bad filename", "text/plain; charset=utf-8")
                return
            run_dir = lc.run_dir_for(LAYOUT_DIR, run_id).resolve()
            target = (run_dir / safe_name).resolve()
            if not str(target).startswith(str(run_dir)) or not target.is_file():
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix.lower() == ".csv":
                ctype = "text/csv; charset=utf-8"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query, keep_blank_values=True)

            if path in ("/", "/index.html"):
                index = STATIC_DIR / "index.html"
                self._send(200, index.read_bytes(), "text/html; charset=utf-8")
                return

            if path.startswith("/static/"):
                self._serve_static(path)
                return

            if path == "/api/scrape/status":
                self._send_json(200, scrape_status_snapshot())
                return

            if path == "/api/history":
                try:
                    state.reload(state.run_id)
                except FileNotFoundError:
                    pass
                self._send_json(200, state.history_payload())
                return

            if path.startswith("/api/runs/") and "/files/" in path:
                # /api/runs/<run_id>/files/<filename>
                rest = path[len("/api/runs/") :]
                parts = rest.split("/files/", 1)
                if len(parts) != 2:
                    self._send(404, b"Not found", "text/plain; charset=utf-8")
                    return
                self._serve_run_file(unquote(parts[0]), unquote(parts[1]))
                return

            run_q = (qs.get("run_id") or [None])[0]

            if path == "/api/meta":
                try:
                    if run_q:
                        state.reload(run_q)
                    else:
                        state.reload(state.run_id)
                except FileNotFoundError as exc:
                    self._send_json(
                        200,
                        {
                            "counts": {},
                            "pages": {},
                            "page_order": [],
                            "error": map_error_message(exc),
                            "web_links": {},
                            "age_hours": None,
                            "stale": True,
                            "ran_at_local": "",
                            "data_as_of_label": "Not scraped yet",
                            "scraping": scrape_status_snapshot().get("status") == "running",
                            "max_pages": MAX_PAGES,
                            "default_pages": DEFAULT_PAGES,
                            "devices": device_catalog_payload(),
                            "saved_devices": saved_device_auth_ids(),
                            **device_header_options(),
                            "history": state.history_payload(),
                            "scrape_status": scrape_status_snapshot(),
                        },
                    )
                    return
                self._send_json(200, state.meta())
                return

            if path in ("/api/search", "/search"):
                q = _query_from_qs(qs)
                try:
                    if run_q:
                        state.reload(run_q)
                    else:
                        state.reload(state.run_id)
                    slim = state.slim_hits(q)
                except FileNotFoundError as exc:
                    self._send_json(404, {"error": map_error_message(exc), "query": q, "hits": []})
                    return
                self._send_json(200, {"query": q, "hits": slim, **state.meta()})
                return

            if path == "/api/compare":
                q = _query_from_qs(qs)
                run_ids = _parse_csv_list((qs.get("run_ids") or [""])[0])
                if not run_ids and run_q:
                    run_ids = [run_q]
                pages = _normalize_page_filters(_parse_csv_list((qs.get("pages") or [""])[0]))
                try:
                    if run_ids:
                        # Validate via compare; keep selected state on first run
                        if run_ids[0] != state.run_id:
                            try:
                                state.reload(run_ids[0])
                            except FileNotFoundError:
                                pass
                    elif state.run_id:
                        state.reload(state.run_id)
                    payload = state.compare(q, run_ids=run_ids or None, pages=pages or None)
                except ValueError as exc:
                    self._send_json(
                        400,
                        {
                            "error": map_error_message(exc),
                            "query": q,
                            "groups": [],
                            "table_rows": [],
                        },
                    )
                    return
                except FileNotFoundError as exc:
                    self._send_json(
                        404,
                        {
                            "error": map_error_message(exc),
                            "query": q,
                            "groups": [],
                            "table_rows": [],
                        },
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        400,
                        {
                            "error": map_error_message(exc),
                            "query": q,
                            "groups": [],
                            "table_rows": [],
                            "matrix": {"columns": [], "rows": []},
                        },
                    )
                    return
                self._send_json(200, payload)
                return

            if path == "/api/layouts":
                run_ids = _parse_csv_list((qs.get("run_ids") or [""])[0])
                if not run_ids and run_q:
                    run_ids = [run_q]
                pages = _normalize_page_filters(_parse_csv_list((qs.get("pages") or [""])[0]))
                try:
                    if run_ids:
                        if run_ids[0] != state.run_id:
                            try:
                                state.reload(run_ids[0])
                            except FileNotFoundError:
                                pass
                    elif state.run_id:
                        state.reload(state.run_id)
                    payload = state.layouts(run_ids=run_ids or None, pages=pages or None)
                except ValueError as exc:
                    self._send_json(
                        400,
                        {"error": map_error_message(exc), "mode": "layouts", "pages": []},
                    )
                    return
                except FileNotFoundError as exc:
                    self._send_json(
                        404,
                        {"error": map_error_message(exc), "mode": "layouts", "pages": []},
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        400,
                        {"error": map_error_message(exc), "mode": "layouts", "pages": []},
                    )
                    return
                self._send_json(200, payload)
                return

            self._send(404, b"Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/api/scrape":
                payload = self._read_json()
                try:
                    result = start_scrape_from_ui(payload, state)
                    self._send_json(200, result)
                except Exception as exc:  # noqa: BLE001
                    self._send_json(400, {"ok": False, "error": map_error_message(exc)})
                return

            if path == "/api/history/clear":
                result = delete_all_history_from_ui(state)
                code = 200 if result.get("ok") else 400
                self._send_json(code, result)
                return

            if path == "/api/select-run":
                data = self._read_json()
                rid = str(data.get("run_id") or "").strip()
                if not rid:
                    self._send_json(400, {"ok": False, "error": "Pick a scrape from History."})
                    return
                try:
                    state.reload(rid)
                    self._send_json(200, {"ok": True, **state.meta()})
                except FileNotFoundError as exc:
                    self._send_json(404, {"ok": False, "error": map_error_message(exc)})
                return

            if path in ("/api/search", "/api/compare"):
                data = self._read_json()
                q = str(data.get("q") or "").strip()
                rid = str(data.get("run_id") or "").strip() or None
                run_ids_raw = data.get("run_ids")
                if isinstance(run_ids_raw, list):
                    run_ids = [str(x).strip() for x in run_ids_raw if str(x).strip()]
                elif isinstance(run_ids_raw, str):
                    run_ids = _parse_csv_list(run_ids_raw)
                else:
                    run_ids = [rid] if rid else []
                pages_raw = data.get("pages")
                if isinstance(pages_raw, list):
                    pages = _normalize_page_filters([str(x) for x in pages_raw])
                elif isinstance(pages_raw, str):
                    pages = _normalize_page_filters(_parse_csv_list(pages_raw))
                else:
                    pages = []
                try:
                    if run_ids:
                        try:
                            state.reload(run_ids[0])
                        except FileNotFoundError:
                            pass
                    else:
                        state.reload(rid or state.run_id)
                    if path == "/api/compare":
                        self._send_json(
                            200,
                            state.compare(
                                q,
                                run_ids=run_ids or None,
                                pages=pages or None,
                            ),
                        )
                    else:
                        self._send_json(
                            200, {"query": q, "hits": state.slim_hits(q), **state.meta()}
                        )
                except ValueError as exc:
                    self._send_json(400, {"error": map_error_message(exc), "query": q})
                except FileNotFoundError as exc:
                    self._send_json(404, {"error": map_error_message(exc), "query": q})
                except Exception as exc:  # noqa: BLE001
                    self._send_json(
                        400,
                        {
                            "error": map_error_message(exc),
                            "query": q,
                            "groups": [],
                            "table_rows": [],
                        },
                    )
                return

            if path in ("/", "/index.html"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                qs = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
                q = _query_from_qs(qs)
                self.send_response(303)
                self.send_header("Location", "/?q=" + quote(q, safe=""))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            self._send(404, b"Not found", "text/plain; charset=utf-8")

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            if path == "/api/history":
                result = delete_all_history_from_ui(state)
                code = 200 if result.get("ok") else 400
                self._send_json(code, result)
                return
            prefix = "/api/history/"
            if path.startswith(prefix):
                rid = unquote(path[len(prefix) :].strip("/"))
                result = delete_history_run_from_ui(rid, state)
                code = 200 if result.get("ok") else 400
                if result.get("error") and "not in history" in str(result.get("error")).lower():
                    code = 404
                self._send_json(code, result)
                return
            self._send(404, b"Not found", "text/plain; charset=utf-8")

    return Handler


def run_server(port: int, open_browser: bool) -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
        sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    _ensure_src_path()
    try:
        from vix_scraper.config import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
        if ENV_LOCAL.is_file():
            load_dotenv(ENV_LOCAL, override=True)
        from vix_scraper.auth import resolve_auth_profile

        creds = resolve_auth_profile("default")
        if creds.auth_token:
            _SESSION["auth_token"] = creds.auth_token
        if creds.x_vix_user_token:
            _SESSION["x_vix_user_token"] = creds.x_vix_user_token
        if (os.getenv("VIX_INSTALLATION_ID") or "").strip():
            _SESSION["installation_id"] = (os.getenv("VIX_INSTALLATION_ID") or "").strip()
    except Exception:
        pass

    try:
        _layout_helpers().migrate_legacy_layout_csvs(LAYOUT_DIR)
        state = LookupState()
        meta = state.meta()
        print("ViX title layout compare server", flush=True)
        for page in meta.get("page_order") or []:
            print(
                f"  {friendly_page_label(page):20}",
                f"({meta['counts'].get(page, 0)} titles)",
                flush=True,
            )
        if meta.get("ran_at_local"):
            print(f"  Data as of       {meta['ran_at_local']}", flush=True)
        if meta.get("run_id"):
            print(f"  Run              {meta['run_id']}", flush=True)
    except FileNotFoundError as exc:
        print("ViX title layout compare server", flush=True)
        print(f"  Warning: {map_error_message(exc)}", flush=True)

        class _EmptyState(LookupState):
            def __init__(self) -> None:  # noqa: D107
                self.run_id = None
                self.run_dir = LAYOUT_DIR
                self.paths = {}
                self.page_order = []
                self.rows = []
                self.counts = {}
                self.scrape_meta = None

            def reload(self, run_id: str | None = None) -> None:
                if run_id is not None:
                    self.run_id = run_id
                try:
                    LookupState.reload(self, run_id)
                except FileNotFoundError:
                    self.paths = {}
                    self.page_order = []
                    self.rows = []
                    self.counts = {}
                    self.scrape_meta = None

        state = _EmptyState()

    host = "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    url = f"http://{host}:{port}/"
    print(f"Open form: {url}", flush=True)
    print("(Do not open the .py/.html as a file — use this URL.)", flush=True)
    print("Ctrl+C to stop.", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        httpd.server_close()


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def run_cli(query: str, as_json: bool) -> int:
    state = LookupState()
    meta = state.meta()
    if as_json:
        print(json.dumps(state.compare(query), ensure_ascii=False, indent=2))
    else:
        labels = [f"{friendly_page_label(p)}: {meta['counts'].get(p, 0)}" for p in meta.get("page_order") or []]
        print(" | ".join(labels) if labels else "No pages loaded")
        if meta.get("ran_at_local"):
            print(f"Data as of: {meta['ran_at_local']}")
        print()
        slim = state.slim_hits(query)
        print(f"Query: {query!r}  ->  {len(slim)} placement(s)")
        print()
        print(format_table(slim))
    return 0 if state.slim_hits(query) else 1


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="ViX title layout compare — lookup + scrape UI"
    )
    parser.add_argument(
        "query",
        nargs="?",
        help="Title substring to search (omit to launch interactive form)",
    )
    parser.add_argument("--json", action="store_true", help="CLI JSON output")
    parser.add_argument("--port", type=int, default=8765, help="Server port (default 8765)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the browser when starting the form server",
    )
    args = parser.parse_args(argv)

    if args.query:
        return run_cli(args.query, as_json=args.json)

    run_server(port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
