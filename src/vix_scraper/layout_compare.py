"""Batch scrape + layout comparison helpers (no images)."""

from __future__ import annotations

import csv
import json
import os
import secrets
import shutil
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vix_scraper.auth import (
    apply_auth_profile,
    is_jwt_expired,
    jwt_catalog_country_warning,
    jwt_country,
    missing_profile_help,
    pin_token_to_catalog_country,
    resolve_auth_profile,
)
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.errors import GraphQLError, ScraperError
from vix_scraper.exporter import CsvExporter
from vix_scraper.models import (
    PRODUCTION_ENDPOINT,
    STAGING_ENDPOINT,
    ExportedTitle,
    ScrapeConfig,
)
from vix_scraper.scraper import TitleScraper

ProgressCallback = Callable[[dict[str, Any]], None]
MAX_PAGES_PER_RUN = 4
HISTORY_FILENAME = "history.json"
RUNS_DIRNAME = "runs"
LATEST_DIRNAME = "latest"


def format_ran_at_local(dt: datetime | None = None) -> tuple[str, str]:
    """Return (ISO UTC, human-local) timestamps for scrape metadata."""
    now = dt or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    utc = now.astimezone(timezone.utc)
    iso = utc.isoformat().replace("+00:00", "Z")
    human = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    return iso, human


def _atomic_write_text(path: Path, text: str) -> None:
    from vix_scraper.exporter import atomic_replace

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    atomic_replace(tmp, path)


def write_scrape_meta(output_dir: Path, meta: dict[str, Any]) -> Path:
    """Persist scrape metadata JSON next to layout CSVs (never includes tokens)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "scrape_meta.json"
    # Per-page sidecar for UI “Data as of …” when times differ
    pages = meta.get("pages") or {}
    if isinstance(pages, dict):
        for page_key, page_meta in pages.items():
            if not isinstance(page_meta, dict):
                continue
            slug = str(page_key).strip("/").replace("/", "_") or "root"
            side = output_dir / f"{slug}_scrape_meta.json"
            _atomic_write_text(side, json.dumps(page_meta, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return path


def cleanup_legacy_csv_names(output_dir: Path) -> list[str]:
    """Remove leftover ``*.new.csv`` / tmp CSV variants inside a run directory."""
    removed: list[str] = []
    if not output_dir.is_dir():
        return removed
    for path in output_dir.glob("*.csv"):
        name = path.name
        if name.endswith(".new.csv") or ".tmp" in name:
            try:
                path.unlink()
                removed.append(name)
            except OSError:
                continue
    return removed


def load_scrape_meta(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / "scrape_meta.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def path_slug(url_path: str) -> str:
    """Safe filename stem from a urlPath (e.g. /ondemandplus → ondemandplus)."""
    return url_path.strip("/").replace("/", "_") or "root"


def page_csv_name(url_path: str) -> str:
    return f"{path_slug(url_path)}_titles.csv"


def suggest_vix_link(url_path: str) -> str:
    path = url_path if str(url_path).startswith("/") else f"/{url_path}"
    return f"https://vix.com/es-mx{path}"


def normalize_pages(pages: list[Any] | None, *, max_pages: int = MAX_PAGES_PER_RUN) -> list[str]:
    """Normalize urlPaths; raise ScraperError if empty or over max."""
    raw = pages or []
    out: list[str] = []
    seen: set[str] = set()
    for p in raw:
        if isinstance(p, dict):
            s = str(p.get("url_path") or p.get("path") or "").strip()
        else:
            s = str(p or "").strip()
        if not s:
            continue
        if not s.startswith("/"):
            s = f"/{s}"
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    if not out:
        out = ["/ondemandplus", "/ondemandpluswc"]
    if len(out) > max_pages:
        raise ScraperError(f"You can scrape at most {max_pages} pages at a time.")
    return out


def new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)


def runs_root(layout_dir: Path) -> Path:
    return layout_dir / RUNS_DIRNAME


def run_dir_for(layout_dir: Path, run_id: str) -> Path:
    return runs_root(layout_dir) / run_id


def history_path(layout_dir: Path) -> Path:
    return layout_dir / HISTORY_FILENAME


def load_history(layout_dir: Path) -> dict[str, Any]:
    path = history_path(layout_dir)
    if not path.is_file():
        return {"latest_run_id": None, "runs": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"latest_run_id": None, "runs": []}
    if not isinstance(data, dict):
        return {"latest_run_id": None, "runs": []}
    runs = data.get("runs")
    if not isinstance(runs, list):
        runs = []
    return {"latest_run_id": data.get("latest_run_id"), "runs": runs}


def save_history(layout_dir: Path, history: dict[str, Any]) -> Path:
    layout_dir.mkdir(parents=True, exist_ok=True)
    path = history_path(layout_dir)
    _atomic_write_text(path, json.dumps(history, ensure_ascii=False, indent=2) + "\n")
    return path


def create_run_dir(layout_dir: Path, run_id: str | None = None) -> tuple[str, Path]:
    rid = run_id or new_run_id()
    run_dir = run_dir_for(layout_dir, rid)
    run_dir.mkdir(parents=True, exist_ok=True)
    return rid, run_dir


def sync_latest(layout_dir: Path, run_id: str, run_dir: Path) -> Path:
    """Copy a completed run into ``latest/`` and write a pointer file."""
    latest = layout_dir / LATEST_DIRNAME
    if latest.exists():
        shutil.rmtree(latest, ignore_errors=True)
    latest.mkdir(parents=True, exist_ok=True)
    for src in run_dir.iterdir():
        if src.is_file():
            shutil.copy2(src, latest / src.name)
    pointer = {"run_id": run_id, "dir": f"{RUNS_DIRNAME}/{run_id}"}
    _atomic_write_text(
        layout_dir / "latest.json",
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
    )
    return latest


def upsert_history_entry(layout_dir: Path, entry: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace a run entry (newest first). Never drops other runs."""
    history = load_history(layout_dir)
    run_id = entry.get("run_id")
    runs = [r for r in history["runs"] if isinstance(r, dict) and r.get("run_id") != run_id]
    runs.insert(0, entry)
    history["runs"] = runs
    if entry.get("status") in ("ok", "partial"):
        history["latest_run_id"] = run_id
    elif not history.get("latest_run_id"):
        history["latest_run_id"] = run_id
    save_history(layout_dir, history)
    return history


def _clear_latest_artifacts(layout_dir: Path) -> None:
    latest = layout_dir / LATEST_DIRNAME
    if latest.exists():
        shutil.rmtree(latest, ignore_errors=True)
    pointer_path = layout_dir / "latest.json"
    if pointer_path.is_file():
        pointer_path.unlink(missing_ok=True)


def delete_history_run(layout_dir: Path, run_id: str) -> dict[str, Any]:
    """Remove a history run folder and update history/latest pointers.

    Returns a result dict with ``ok`` and human-readable ``message``.
    Does not touch an in-progress scrape; callers must refuse active run_ids.
    """
    rid = (run_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or ".." in rid:
        return {"ok": False, "error": "That scrape id looks invalid."}

    history = load_history(layout_dir)
    runs = [r for r in history.get("runs") or [] if isinstance(r, dict)]
    match = next((r for r in runs if r.get("run_id") == rid), None)
    run_dir = run_dir_for(layout_dir, rid)
    if match is None and not run_dir.is_dir():
        return {"ok": False, "error": "That scrape is not in history."}

    was_latest = history.get("latest_run_id") == rid
    if run_dir.is_dir():
        shutil.rmtree(run_dir, ignore_errors=True)

    remaining = [r for r in runs if r.get("run_id") != rid]
    history["runs"] = remaining

    if was_latest:
        next_latest = None
        for entry in remaining:
            if entry.get("status") in ("ok", "partial") and entry.get("run_id"):
                next_latest = str(entry["run_id"])
                break
        if next_latest is None and remaining and remaining[0].get("run_id"):
            next_latest = str(remaining[0]["run_id"])
        history["latest_run_id"] = next_latest
        if next_latest:
            next_dir = run_dir_for(layout_dir, next_latest)
            if next_dir.is_dir():
                sync_latest(layout_dir, next_latest, next_dir)
            else:
                _clear_latest_artifacts(layout_dir)
        else:
            _clear_latest_artifacts(layout_dir)

    save_history(layout_dir, history)
    return {
        "ok": True,
        "deleted_run_id": rid,
        "latest_run_id": history.get("latest_run_id"),
        "message": "Scrape removed from history.",
    }


def delete_all_history(layout_dir: Path) -> dict[str, Any]:
    """Remove every history run, clear history.json, and reset latest/.

    Does not check scrape-in-progress; callers must refuse while scraping.
    """
    layout_dir.mkdir(parents=True, exist_ok=True)
    history = load_history(layout_dir)
    runs = [r for r in history.get("runs") or [] if isinstance(r, dict)]
    deleted_ids: list[str] = []
    runs_root = layout_dir / RUNS_DIRNAME
    if runs_root.is_dir():
        for child in list(runs_root.iterdir()):
            if child.is_dir():
                deleted_ids.append(child.name)
                shutil.rmtree(child, ignore_errors=True)
            elif child.is_file():
                child.unlink(missing_ok=True)
    else:
        for entry in runs:
            rid = entry.get("run_id")
            if not rid:
                continue
            deleted_ids.append(str(rid))
            run_dir = run_dir_for(layout_dir, str(rid))
            if run_dir.is_dir():
                shutil.rmtree(run_dir, ignore_errors=True)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_deleted: list[str] = []
    for rid in deleted_ids:
        if rid not in seen:
            seen.add(rid)
            unique_deleted.append(rid)
    for entry in runs:
        rid = str(entry.get("run_id") or "")
        if rid and rid not in seen:
            seen.add(rid)
            unique_deleted.append(rid)

    _clear_latest_artifacts(layout_dir)
    save_history(layout_dir, {"latest_run_id": None, "runs": []})
    count = len(unique_deleted)
    return {
        "ok": True,
        "deleted_count": count,
        "deleted_run_ids": unique_deleted,
        "latest_run_id": None,
        "message": (
            "All scrape history deleted."
            if count
            else "History was already empty."
        ),
    }


def migrate_legacy_layout_csvs(layout_dir: Path) -> str | None:
    """One-time: move root-level title CSVs into runs/<id>/ if history is empty."""
    history = load_history(layout_dir)
    if history.get("runs"):
        return history.get("latest_run_id")
    root_csvs = sorted(
        p for p in layout_dir.glob("*_titles.csv") if p.is_file() and p.name != "combined_titles.csv"
    )
    combined = layout_dir / "combined_titles.csv"
    meta = load_scrape_meta(layout_dir)
    if not root_csvs and not combined.is_file():
        return None
    run_id, run_dir = create_run_dir(layout_dir)
    pages: list[str] = []
    row_counts: dict[str, int] = {}
    for csv_path in root_csvs:
        shutil.copy2(csv_path, run_dir / csv_path.name)
        slug = csv_path.name.replace("_titles.csv", "")
        page = f"/{slug.replace('_', '/')}" if "_" in slug else f"/{slug}"
        # Prefer common flat slugs
        if slug in ("ondemandplus", "ondemandpluswc"):
            page = f"/{slug}"
        pages.append(page)
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                row_counts[page] = max(0, sum(1 for _ in csv.reader(f)) - 1)
        except OSError:
            row_counts[page] = 0
    if combined.is_file():
        shutil.copy2(combined, run_dir / "combined_titles.csv")
    if meta:
        _atomic_write_text(
            run_dir / "scrape_meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        )
        pages_meta = meta.get("pages") if isinstance(meta.get("pages"), dict) else {}
        if pages_meta:
            pages = list(pages_meta.keys())
            row_counts = {
                k: int((v or {}).get("row_count") or row_counts.get(k) or 0)
                for k, v in pages_meta.items()
                if isinstance(v, dict)
            }
    else:
        write_scrape_meta(
            run_dir,
            {
                "ran_at": None,
                "ran_at_local": None,
                "pages": {
                    p: {"url_path": p, "status": "ok", "row_count": row_counts.get(p, 0)}
                    for p in pages
                },
                "row_counts": row_counts,
                "migrated_from_root": True,
            },
        )
    status = "ok"
    entry = {
        "run_id": run_id,
        "ran_at": (meta or {}).get("ran_at"),
        "ran_at_local": (meta or {}).get("ran_at_local") or "Imported prior scrape",
        "pages": pages,
        "status": status,
        "row_counts": row_counts,
        "duration_seconds": (meta or {}).get("duration_seconds"),
        "dir": f"{RUNS_DIRNAME}/{run_id}",
        "files": sorted(p.name for p in run_dir.glob("*.csv")) + ["scrape_meta.json"],
    }
    upsert_history_entry(layout_dir, entry)
    sync_latest(layout_dir, run_id, run_dir)
    return run_id


def list_run_files(run_dir: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    if not run_dir.is_dir():
        return files
    for path in sorted(run_dir.iterdir()):
        if path.is_file() and (
            path.suffix.lower() in {".csv", ".json", ".md"} or path.name.endswith(".csv")
        ):
            files.append({"name": path.name, "kind": path.suffix.lstrip(".") or "file"})
    return files


@dataclass(slots=True)
class PathScrapeOutcome:
    url_path: str
    auth_profile: str
    status: str
    titles: list[ExportedTitle] = field(default_factory=list)
    message: str = ""
    endpoint: str = ""


@dataclass(slots=True)
class BatchResult:
    output_dir: Path
    outcomes: list[PathScrapeOutcome]
    combined_path: Path | None = None
    summary_path: Path | None = None
    endpoint: str = ""
    notes: list[str] = field(default_factory=list)


def _path_slug(url_path: str) -> str:
    return path_slug(url_path)


def _note_jwt_catalog_country(config: ScrapeConfig, notes: list[str]) -> None:
    """Record JWT vs catalog country mismatch as a note (and stderr). Never raises."""
    msg = jwt_catalog_country_warning(config.auth_token, getattr(config, "country", None))
    if msg and msg not in notes:
        print(msg, file=sys.stderr)
        notes.append(msg)


def resolve_working_endpoint(config: ScrapeConfig, probe_path: str = "/ondemandplus") -> tuple[str, str]:
    """Try preferred endpoint then staging/production; return (endpoint, note).

    Tries the config's current auth first, then other known auth profiles that have tokens.
    """
    preferred = (config.endpoint or PRODUCTION_ENDPOINT).rstrip("/")
    candidates = [preferred]
    if preferred != STAGING_ENDPOINT.rstrip("/"):
        candidates.append(STAGING_ENDPOINT)
    if PRODUCTION_ENDPOINT.rstrip("/") not in {c.rstrip("/") for c in candidates}:
        candidates.append(PRODUCTION_ENDPOINT)

    profile_order = [config.auth_profile or "default"]
    for name in ("default", "wc"):
        if name not in profile_order and resolve_auth_profile(name).has_auth_token:
            profile_order.append(name)

    notes: list[str] = []
    for endpoint in candidates:
        for profile in profile_order:
            cfg = ScrapeConfig(
                url_path=probe_path,
                endpoint=endpoint,
                app_version=config.app_version,
                device_type=config.device_type,
                platform=config.platform,
                user_agent=config.user_agent,
                country=getattr(config, "country", None) or "MX",
                accept_language=getattr(config, "accept_language", None) or "es-MX,es;q=0.9",
                installation_id=getattr(config, "installation_id", None),
                auth_token=getattr(config, "auth_token", None),
                x_vix_user_token=getattr(config, "x_vix_user_token", None),
                timeout=min(config.timeout, 30),
                retries=1,
                extra_headers=dict(config.extra_headers),
            )
            apply_auth_profile(cfg, profile)
            if not cfg.auth_token:
                continue
            client = GraphQLClient(cfg)
            try:
                payload = client.execute(
                    "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }",
                    {"urlPath": probe_path},
                    allow_errors=True,
                )
            except GraphQLError as exc:
                notes.append(f"{endpoint}/{profile}: {exc}")
                continue
            page = ((payload.get("data") or {}).get("uiPage"))
            if page:
                # Keep resolved auth on the live config when probe profile differs.
                if profile != (config.auth_profile or "default"):
                    apply_auth_profile(config, profile)
                note = f"Using endpoint {endpoint} (probe auth_profile={profile})."
                if endpoint.rstrip("/") != preferred:
                    note = (
                        f"Preferred endpoint failed ({preferred}: {notes[-1] if notes else 'error'}). "
                        f"Fell back to {endpoint} (probe auth_profile={profile})."
                    )
                return endpoint, note
            errors = graphql_error_messages(payload)
            detail = "; ".join(errors) if errors else "uiPage null"
            if not errors and is_jwt_expired(cfg.auth_token):
                detail = "uiPage null (auth JWT expired)"
            notes.append(f"{endpoint}/{profile}: {detail}")
    raise GraphQLError("All GraphQL endpoints failed: " + " | ".join(notes))


def probe_page_exists(config: ScrapeConfig, url_path: str) -> tuple[bool, str]:
    client = GraphQLClient(config)
    payload = client.execute(
        "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName moduleCount: uiModules { totalCount } } }",
        {"urlPath": url_path},
        allow_errors=True,
    )
    page = ((payload.get("data") or {}).get("uiPage"))
    if page:
        return True, f"ok pageName={page.get('pageName')!r}"
    errors = graphql_error_messages(payload)
    if errors:
        return False, "; ".join(errors)
    if is_jwt_expired(config.auth_token):
        return False, "uiPage null (auth JWT expired)"
    return False, "uiPage null"


def discover_path_variants(base: str) -> list[str]:
    """Common path guesses when a variant 404s."""
    base = base if base.startswith("/") else f"/{base}"
    stem = base.rstrip("/")
    variants = [stem]
    for suffix in ("", "/"):
        candidate = stem + suffix
        if candidate not in variants:
            variants.append(candidate)
    # hyphen / underscore / casing variants for WC
    if stem.endswith("wc"):
        root = stem[: -len("wc")]
        for alt in (
            f"{root}-wc",
            f"{root}_wc",
            f"{root}WC",
            f"{root}-WC",
            "/ondemandplus-wc",
            "/ondemandplus_wc",
            "/ondemand/pluswc",
            "/wc/ondemandplus",
        ):
            if alt not in variants:
                variants.append(alt)
    return variants


def write_layout_diff_summary(
    baseline: list[ExportedTitle],
    variant: list[ExportedTitle],
    *,
    baseline_path: str,
    variant_path: str,
    output: Path,
) -> Path:
    """Compact high-signal layout diff for two page title lists."""
    base_ids = [r.content_id for r in baseline]
    var_ids = [r.content_id for r in variant]
    base_set, var_set = set(base_ids), set(var_ids)
    shared = base_set & var_set
    only_base = base_set - var_set
    only_var = var_set - base_set
    jaccard = (len(shared) / len(base_set | var_set)) if (base_set or var_set) else 0.0

    def first_pos(rows: list[ExportedTitle]) -> dict[str, ExportedTitle]:
        out: dict[str, ExportedTitle] = {}
        for row in rows:
            if row.content_id not in out:
                out[row.content_id] = row
        return out

    base_first = first_pos(baseline)
    var_first = first_pos(variant)

    def row_structure(rows: list[ExportedTitle]) -> list[tuple[int, str, str, int]]:
        by_y: dict[int, list[ExportedTitle]] = defaultdict(list)
        for row in rows:
            by_y[row.carousel_y].append(row)
        structure = []
        for y in sorted(by_y):
            items = by_y[y]
            n_titles = sum(1 for r in items if r.video_type != "EMPTY")
            structure.append((y, items[0].row_title, items[0].module_type, n_titles))
        return structure

    base_rows = row_structure(baseline)
    var_rows = row_structure(variant)
    base_hero = [r for r in baseline if r.is_hero == "true"]
    var_hero = [r for r in variant if r.is_hero == "true"]
    hero_base_ids = {r.content_id for r in base_hero}
    hero_var_ids = {r.content_id for r in var_hero}

    # Rank correlation on grid placement (row + slot), not the page-wide CSV index.
    rank_pairs = []
    for cid in shared:
        b, v = base_first[cid], var_first[cid]
        rank_pairs.append(
            (b.carousel_y * 10000 + b.carousel_x, v.carousel_y * 10000 + v.carousel_x)
        )
    spearman = _spearman(rank_pairs)

    n = 20
    base_first_n = base_ids[:n]
    var_first_n = var_ids[:n]
    first_n_overlap = len(set(base_first_n) & set(var_first_n))

    shifts = []
    for cid in shared:
        b, v = base_first[cid], var_first[cid]
        # Row identity is row_title + slot (carousel_x). A y-only drift (empty
        # Seguir viendo) or page-index-only change is not a placement shift.
        if b.carousel_x != v.carousel_x or b.row_title != v.row_title:
            shifts.append(
                {
                    "id": cid,
                    "title": b.title,
                    "base_page_index": b.position,
                    "var_page_index": v.position,
                    "delta_page_index": v.position - b.position,
                    "base_y": b.carousel_y,
                    "var_y": v.carousel_y,
                    "base_x": b.carousel_x,
                    "var_x": v.carousel_x,
                    "base_row": b.row_title,
                    "var_row": v.row_title,
                }
            )
    shifts.sort(
        key=lambda s: (
            abs(s["var_y"] - s["base_y"]),
            abs(s["var_x"] - s["base_x"]),
            abs(s["delta_page_index"]),
        ),
        reverse=True,
    )

    base_dupes = {k: c for k, c in Counter(base_ids).items() if c > 1}
    var_dupes = {k: c for k, c in Counter(var_ids).items() if c > 1}

    def genre_mix(rows: list[ExportedTitle]) -> Counter[str]:
        c: Counter[str] = Counter()
        for row in rows:
            parts = [g.strip() for g in (row.genres or "").split(",") if g.strip()]
            if not parts:
                c["(none)"] += 1
            else:
                for g in parts:
                    c[g] += 1
        return c

    lines = [
        f"# Layout diff: `{baseline_path}` vs `{variant_path}`",
        "",
        "## Counts",
        f"- Baseline placements: **{len(baseline)}** | unique IDs: **{len(base_set)}**",
        f"- Variant placements: **{len(variant)}** | unique IDs: **{len(var_set)}**",
        f"- Shared IDs: **{len(shared)}** | only baseline: **{len(only_base)}** | only variant: **{len(only_var)}**",
        f"- Jaccard (ID sets): **{jaccard:.3f}**",
        f"- Spearman (first-occurrence row/slot, shared IDs): **{spearman:.3f}**",
        f"- First-{n} title ID overlap: **{first_n_overlap}/{n}**",
        "",
        "## Hero",
        f"- Baseline hero placements: {len(base_hero)} (unique {len(hero_base_ids)})",
        f"- Variant hero placements: {len(var_hero)} (unique {len(hero_var_ids)})",
        f"- Hero ID overlap: {len(hero_base_ids & hero_var_ids)}",
        "",
        "### Baseline hero titles (order)",
    ]
    for r in base_hero[:15]:
        lines.append(f"- y={r.carousel_y} x={r.carousel_x}: {r.title} (`{r.content_id}`)")
    lines.append("")
    lines.append("### Variant hero titles (order)")
    for r in var_hero[:15]:
        lines.append(f"- y={r.carousel_y} x={r.carousel_x}: {r.title} (`{r.content_id}`)")

    lines.extend(["", "## Row structure (y | title | module_type | size)", "", "### Baseline"])
    for y, title, mtype, size in base_rows:
        lines.append(f"- {y}: {title!r} | {mtype} | n={size}")
    lines.append("")
    lines.append("### Variant")
    for y, title, mtype, size in var_rows:
        lines.append(f"- {y}: {title!r} | {mtype} | n={size}")

    base_row_titles = [t for _, t, _, _ in base_rows]
    var_row_titles = [t for _, t, _, _ in var_rows]
    lines.extend(
        [
            "",
            "## Row title set diff",
            f"- Only baseline rows: {sorted(set(base_row_titles) - set(var_row_titles))}",
            f"- Only variant rows: {sorted(set(var_row_titles) - set(base_row_titles))}",
            "",
            "## Duplicate exposure (ID appears >1 on page)",
            f"- Baseline dupes: {len(base_dupes)} IDs",
            f"- Variant dupes: {len(var_dupes)} IDs",
            "",
            "## Genre mix (top 10 by placement count)",
            "",
            "### Baseline",
        ]
    )
    for g, c in genre_mix(baseline).most_common(10):
        lines.append(f"- {g}: {c}")
    lines.append("")
    lines.append("### Variant")
    for g, c in genre_mix(variant).most_common(10):
        lines.append(f"- {g}: {c}")

    lines.extend(["", f"## Largest placement shifts (row slot / row name, top {min(25, len(shifts))})", ""])
    for s in shifts[:25]:
        lines.append(
            f"- {s['title']} (`{s['id']}`): slot {s['base_x']}→{s['var_x']}; "
            f"y {s['base_y']}→{s['var_y']}; "
            f"row {s['base_row']!r}→{s['var_row']!r}; "
            f"page_index {s['base_page_index']}→{s['var_page_index']} (Δ{s['delta_page_index']:+d})"
        )

    lines.extend(["", "## Sample IDs only on baseline (up to 20)", ""])
    for cid in sorted(only_base)[:20]:
        r = base_first[cid]
        lines.append(
            f"- {r.title} (`{cid}`) @ page_index={r.position} y={r.carousel_y} x={r.carousel_x} row={r.row_title!r}"
        )
    lines.extend(["", "## Sample IDs only on variant (up to 20)", ""])
    for cid in sorted(only_var)[:20]:
        r = var_first[cid]
        lines.append(
            f"- {r.title} (`{cid}`) @ page_index={r.position} y={r.carousel_y} x={r.carousel_x} row={r.row_title!r}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Machine-readable shift CSV alongside markdown
    shift_csv = output.with_name(output.stem + "_shifts.csv")
    if shifts:
        with shift_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(shifts[0].keys()))
            writer.writeheader()
            writer.writerows(shifts)
    return output


def _spearman(pairs: list[tuple[int, int]]) -> float:
    n = len(pairs)
    if n < 2:
        return 0.0
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]

    def ranks(vals: list[int]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        out = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den_x = sum((a - mean_x) ** 2 for a in rx) ** 0.5
    den_y = sum((b - mean_y) ** 2 for b in ry) ** 0.5
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def run_batch_scrape(
    config: ScrapeConfig,
    *,
    progress_callback: ProgressCallback | None = None,
) -> BatchResult:
    """Scrape many urlPaths into ``config.output_dir`` (typically a per-run folder).

    Overall ``ran_at`` is written only after the batch finishes (success or partial).
    Per-page timestamps are set when each page completes.
    """
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    legacy = cleanup_legacy_csv_names(output_dir)
    paths = normalize_pages(
        list(config.url_paths or ([config.url_path] if config.url_path else []))
    )
    config.url_paths = paths
    if legacy:
        notes_pre = [f"Removed legacy CSV name(s): {', '.join(legacy)}"]
    else:
        notes_pre = []

    def emit(event: dict[str, Any]) -> None:
        if progress_callback:
            progress_callback(event)

    profile_map = dict(config.auth_profile_map or {})
    default_profile = (config.auth_profile or "default").strip().lower()
    notes: list[str] = list(notes_pre)
    started = time.monotonic()
    # Layout compare never dedupes placements (same title in multiple rows is signal).
    config.deduplicate = False
    config.download_images = False
    # CMS LAYOUTS: ranked/playlist rails page contents until the connection ends.
    # Editorial rails use `items` (or the first layout page window), not catalog
    # totalCount. Never omit contents `first` (GraphQL default is 10) and never
    # send first:100 as a guessed catalog dump.
    config.paginate_contents = True
    config.contents_first_from_total = True
    layout_query = Path("queries/layout.graphql")
    # Fat VideoTitleFields at first ≈ rail length scored 109776 / 100000. Layout
    # compare always uses the lean identity query; never let VIX_GRAPHQL_QUERY
    # (or a leftover request.graphql) override it.
    if layout_query.is_file():
        config.query_file = layout_query
        config.query = None

    # If caller set tokens on config (e.g. UI session), mirror into env for profile helpers.
    # Values are never logged.
    if config.auth_token and not (os.getenv("AUTH_TOKEN") or "").strip():
        os.environ["AUTH_TOKEN"] = config.auth_token
    if config.x_vix_user_token and not (os.getenv("X_VIX_USER_TOKEN") or "").strip():
        os.environ["X_VIX_USER_TOKEN"] = config.x_vix_user_token
    if config.installation_id and not (os.getenv("VIX_INSTALLATION_ID") or "").strip():
        os.environ["VIX_INSTALLATION_ID"] = config.installation_id

    # Resolve endpoint using default profile credentials first.
    ui_token = config.auth_token
    default_creds = resolve_auth_profile(default_profile)
    if default_creds.has_auth_token:
        apply_auth_profile(config, default_profile)
    pin_token_to_catalog_country(config, ui_token)
    _note_jwt_catalog_country(config, notes)

    emit(
        {
            "event": "start",
            "pages_total": len(paths),
            "pages_done": 0,
            "message": "Scraping…",
        }
    )

    endpoint, endpoint_note = resolve_working_endpoint(config, probe_path=paths[0])
    config.endpoint = endpoint
    notes.append(endpoint_note)
    pin_token_to_catalog_country(config, ui_token)
    _note_jwt_catalog_country(config, notes)

    outcomes: list[PathScrapeOutcome] = []
    all_rows: list[ExportedTitle] = []
    page_meta: dict[str, Any] = {}
    install_present = bool((config.installation_id or os.getenv("VIX_INSTALLATION_ID") or "").strip())
    pages_done = 0

    for url_path in paths:
        profile = profile_map.get(url_path, default_profile)
        creds = resolve_auth_profile(profile)
        used_profile = profile
        page_started = time.monotonic()
        emit(
            {
                "event": "page_start",
                "page": url_path,
                "pages_done": pages_done,
                "pages_total": len(paths),
                "message": f"Scraping {url_path}…",
                "modules_done": 0,
                "items_done": 0,
            }
        )
        if not creds.has_auth_token:
            msg = missing_profile_help(profile)
            outcomes.append(
                PathScrapeOutcome(
                    url_path=url_path,
                    auth_profile=profile,
                    status="skipped_missing_auth",
                    message=msg,
                    endpoint=endpoint,
                )
            )
            page_meta[url_path] = {
                "ran_at": None,
                "ran_at_local": None,
                "url_path": url_path,
                "status": "skipped_missing_auth",
                "row_count": 0,
                "auth_profile": profile,
                "error": msg,
                "duration_seconds": round(time.monotonic() - page_started, 3),
            }
            pages_done += 1
            emit(
                {
                    "event": "page_done",
                    "page": url_path,
                    "status": "skipped_missing_auth",
                    "pages_done": pages_done,
                    "pages_total": len(paths),
                    "row_count": 0,
                }
            )
            continue

        apply_auth_profile(config, used_profile)
        pin_token_to_catalog_country(config, ui_token)
        _note_jwt_catalog_country(config, notes)
        config.url_path = url_path

        def _page_progress(info: dict[str, Any], _path: str = url_path) -> None:
            emit(
                {
                    "event": "page_progress",
                    "page": _path,
                    "pages_done": pages_done,
                    "pages_total": len(paths),
                    "modules_done": info.get("modules_done") or 0,
                    "modules_total": info.get("modules_total") or 0,
                    "items_done": info.get("items_done") or 0,
                    "message": f"Scraping {_path}…",
                }
            )

        scraper = TitleScraper(config, progress_callback=_page_progress)

        # Probe path; on auth/malformed failure try other profiles that have tokens.
        ok, detail = probe_page_exists(config, url_path)
        if not ok:
            for alt_profile in ("wc", "default"):
                if alt_profile == used_profile:
                    continue
                alt_creds = resolve_auth_profile(alt_profile)
                if not alt_creds.has_auth_token:
                    continue
                apply_auth_profile(config, alt_profile)
                pin_token_to_catalog_country(config, ui_token)
                alt_country = jwt_country(config.auth_token)
                requested = str(getattr(config, "country", None) or "").strip().upper()
                if requested and alt_country and alt_country != requested:
                    apply_auth_profile(config, used_profile)
                    pin_token_to_catalog_country(config, ui_token)
                    continue
                alt_ok, alt_detail = probe_page_exists(config, url_path)
                if alt_ok:
                    notes.append(
                        f"{url_path}: auth_profile={used_profile!r} failed ({detail[:120]}); "
                        f"fell back to auth_profile={alt_profile!r}."
                    )
                    used_profile = alt_profile
                    ok, detail = alt_ok, alt_detail
                    break
                # restore attempted profile for subsequent variant probes
                apply_auth_profile(config, used_profile)

        chosen = url_path
        if not ok:
            found = None
            for alt in discover_path_variants(url_path):
                if alt == url_path:
                    continue
                alt_ok, alt_detail = probe_page_exists(config, alt)
                if alt_ok:
                    found = alt
                    notes.append(f"{url_path} unavailable ({detail}); using variant {alt} ({alt_detail})")
                    break
            if not found:
                outcomes.append(
                    PathScrapeOutcome(
                        url_path=url_path,
                        auth_profile=used_profile,
                        status="missing",
                        message=detail,
                        endpoint=endpoint,
                    )
                )
                empty_path = output_dir / page_csv_name(url_path)
                CsvExporter(empty_path).write([])
                page_meta[url_path] = {
                    "ran_at": None,
                    "ran_at_local": None,
                    "url_path": url_path,
                    "status": "missing",
                    "row_count": 0,
                    "auth_profile": used_profile,
                    "error": detail,
                    "duration_seconds": round(time.monotonic() - page_started, 3),
                }
                pages_done += 1
                emit(
                    {
                        "event": "page_done",
                        "page": url_path,
                        "status": "missing",
                        "pages_done": pages_done,
                        "pages_total": len(paths),
                        "row_count": 0,
                    }
                )
                continue
            chosen = found

        apply_auth_profile(config, used_profile)

        config.url_path = chosen
        try:
            if config.debug:
                print(f"debug batch scrape path={chosen} auth_profile={used_profile}", file=sys.stderr)
            rows = scraper.scrape()
            out_path = output_dir / page_csv_name(url_path)
            CsvExporter(out_path).write(rows)
            all_rows.extend(rows)
            outcomes.append(
                PathScrapeOutcome(
                    url_path=chosen,
                    auth_profile=used_profile,
                    status="ok",
                    titles=rows,
                    message=f"wrote {len(rows)} rows to {out_path}",
                    endpoint=endpoint,
                )
            )
            # Timestamp only after this page finishes successfully.
            page_iso, page_local = format_ran_at_local()
            page_meta[url_path] = {
                "ran_at": page_iso,
                "ran_at_local": page_local,
                "url_path": chosen,
                "requested_path": url_path,
                "status": "ok",
                "row_count": len(rows),
                "csv": out_path.name,
                "auth_profile": used_profile,
                "duration_seconds": round(time.monotonic() - page_started, 3),
            }
            pages_done += 1
            emit(
                {
                    "event": "page_done",
                    "page": url_path,
                    "status": "ok",
                    "pages_done": pages_done,
                    "pages_total": len(paths),
                    "row_count": len(rows),
                    "ran_at_local": page_local,
                }
            )
        except Exception as exc:  # noqa: BLE001 — one page must not abort the batch
            outcomes.append(
                PathScrapeOutcome(
                    url_path=chosen,
                    auth_profile=used_profile,
                    status="error",
                    message=str(exc),
                    endpoint=endpoint,
                )
            )
            page_meta[url_path] = {
                "ran_at": None,
                "ran_at_local": None,
                "url_path": chosen,
                "status": "error",
                "row_count": 0,
                "auth_profile": used_profile,
                "error": str(exc),
                "duration_seconds": round(time.monotonic() - page_started, 3),
            }
            pages_done += 1
            emit(
                {
                    "event": "page_done",
                    "page": url_path,
                    "status": "error",
                    "pages_done": pages_done,
                    "pages_total": len(paths),
                    "row_count": 0,
                }
            )

    combined_path = output_dir / "combined_titles.csv"
    CsvExporter(combined_path).write(all_rows)

    summary_path = None
    ok_outcomes = [o for o in outcomes if o.status == "ok" and o.titles]
    if len(ok_outcomes) >= 2:
        summary_path = output_dir / "layout_diff_summary.md"
        write_layout_diff_summary(
            ok_outcomes[0].titles,
            ok_outcomes[1].titles,
            baseline_path=ok_outcomes[0].url_path,
            variant_path=ok_outcomes[1].url_path,
            output=summary_path,
        )

    duration = round(time.monotonic() - started, 3)
    errors = [
        {"url_path": o.url_path, "status": o.status, "message": o.message}
        for o in outcomes
        if o.status != "ok"
    ]
    ok_count = len([o for o in outcomes if o.status == "ok"])
    fail_count = len(outcomes) - ok_count
    if ok_count == 0:
        batch_status = "failed"
        ran_at_iso = ran_at_local = None
    else:
        batch_status = "ok" if fail_count == 0 else "partial"
        # Overall run timestamp only when at least one page completed successfully.
        ran_at_iso, ran_at_local = format_ran_at_local()

    row_counts = {p: int((page_meta.get(p) or {}).get("row_count") or 0) for p in paths}
    profiles = sorted({o.auth_profile for o in outcomes if o.auth_profile})
    requested_country = (getattr(config, "country", None) or "MX").strip().upper()
    token_country = jwt_country(config.auth_token)
    _note_jwt_catalog_country(config, notes)
    if requested_country and not token_country:
        notes.append(
            f"Auth JWT has no country claim; scrape catalog is {requested_country}. "
            "If collection order looks like another region, paste a session whose JWT "
            "country matches the selected catalog."
        )
    write_scrape_meta(
        output_dir,
        {
            "ran_at": ran_at_iso,
            "ran_at_local": ran_at_local,
            "status": batch_status,
            "endpoint": endpoint,
            "platform": config.platform,
            "device": config.device_type,
            "device_id": config.device_id or config.platform,
            "device_label": config.device_label or config.platform,
            "app_version": config.app_version,
            "country": requested_country,
            "accept_language": getattr(config, "accept_language", None) or "",
            "jwt_country": token_country or None,
            "pages": page_meta,
            "page_order": paths,
            "row_counts": row_counts,
            "auth_profiles_used": profiles,
            "install_id_present": install_present,
            "duration_seconds": duration,
            "errors": errors,
            "notes": notes,
            "combined_csv": combined_path.name if combined_path else None,
            "summary_md": summary_path.name if summary_path else None,
        },
    )

    emit(
        {
            "event": "finish",
            "status": batch_status,
            "pages_done": pages_done,
            "pages_total": len(paths),
            "ran_at": ran_at_iso,
            "ran_at_local": ran_at_local,
            "duration_seconds": duration,
            "message": "Scrape complete" if batch_status != "failed" else "Scrape failed",
        }
    )

    return BatchResult(
        output_dir=output_dir,
        outcomes=outcomes,
        combined_path=combined_path,
        summary_path=summary_path,
        endpoint=endpoint,
        notes=notes,
    )
