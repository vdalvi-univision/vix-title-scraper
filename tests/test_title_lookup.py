"""Offline tests for title lookup / compare helpers (no network, no tokens)."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
import threading
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOOKUP_PATH = ROOT / "tools" / "title_lookup.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_lookup():
    spec = importlib.util.spec_from_file_location("title_lookup", LOOKUP_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["title_lookup"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write_page_csv(
    path: Path,
    page: str,
    title: str,
    content_id: str,
    x: str = "1",
    *,
    position: str | None = None,
    y: str = "1",
    row_title: str = "Hero",
    extra_rows: list[dict] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["page_url_path", "title", "row_title", "carousel_y", "carousel_x", "position", "id"],
        )
        w.writeheader()
        w.writerow(
            {
                "page_url_path": page,
                "title": title,
                "row_title": row_title,
                "carousel_y": y,
                "carousel_x": x,
                "position": position if position is not None else x,
                "id": content_id,
            }
        )
        for extra in extra_rows or []:
            w.writerow(extra)


def test_accent_insensitive_guardian():
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "Guardián de mi vida",
            "row_title": "Hero",
            "carousel_y": "1",
            "carousel_x": "5",
            "position": "5",
            "module_type": "HERO_CAROUSEL",
            "content_id": "series:mcp:5724",
        },
        {
            "page": "/ondemandpluswc",
            "title": "Guardián de mi vida",
            "row_title": "Hero Móvil",
            "carousel_y": "1",
            "carousel_x": "10",
            "position": "9",
            "module_type": "HERO_CAROUSEL",
            "content_id": "series:mcp:5724",
        },
    ]
    hits = mod.search_titles(rows, "guardian")
    assert len(hits) == 2
    groups, summary = mod.build_compare_groups(hits)
    assert summary["titles"] == 1
    assert summary["placements"] == 2
    assert groups[0]["odp"] and groups[0]["wc"]
    assert groups[0]["odp"][0]["compare_status"] == "moved"
    assert groups[0]["odp"][0]["carousel_y"] == "1"
    assert groups[0]["wc"][0]["carousel_y"] == "1"
    assert "module_type" not in groups[0]["odp"][0]
    assert len(groups[0]["sides"]) == 2


def test_accent_insensitive_ninos_solos():
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "No dejes a los niños solos",
            "row_title": "Lo más buscado en México",
            "carousel_y": "4",
            "carousel_x": "1",
            "position": "1",
            "content_id": "video:mcp:ninos",
        }
    ]
    hits = mod.search_titles(rows, "no dejes a los ninos solos")
    assert len(hits) == 1
    assert hits[0]["carousel_x"] == "1"
    assert hits[0]["row_title"] == "Lo más buscado en México"


def test_compare_three_pages():
    mod = _load_lookup()
    rows = [
        {
            "page": "/a",
            "title": "Alpha",
            "row_title": "Row A",
            "carousel_y": "1",
            "carousel_x": "1",
            "position": "1",
            "content_id": "id:1",
        },
        {
            "page": "/b",
            "title": "Alpha",
            "row_title": "Row B",
            "carousel_y": "1",
            "carousel_x": "2",
            "position": "2",
            "content_id": "id:1",
        },
        {
            "page": "/c",
            "title": "Alpha",
            "row_title": "Row C",
            "carousel_y": "1",
            "carousel_x": "3",
            "position": "3",
            "content_id": "id:1",
        },
    ]
    groups, summary = mod.build_compare_groups(rows, ["/a", "/b", "/c"])
    assert summary["titles"] == 1
    assert len(groups[0]["sides"]) == 3
    assert groups[0]["by_page"]["/c"]
    assert groups[0]["by_page"]["/a"][0]["compare_status"] in {"moved", "same_row"}


def test_slot_in_row_is_carousel_x_not_global_position():
    """UI Position must be the tile in the row (1), not the page-wide index (14)."""
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandpluswc",
            "title": "Una pequeña confusión",
            "row_title": "Lo más buscado",
            "carousel_y": "4",
            "carousel_x": "1",
            "position": "14",
            "content_id": "video:mcp:4585674",
        }
    ]
    groups, _ = mod.build_compare_groups(rows, ["/ondemandpluswc"])
    table = mod.build_table_rows(groups, ["/ondemandpluswc"])
    assert table[0]["carousel_x"] == "1"
    assert table[0]["position"] == "14"
    assert table[0]["slot"] == "1"
    assert table[0]["page_index"] == "14"
    assert mod.slot_in_row(table[0]) == "1"
    assert mod.slot_in_row({"carousel_x": "0"}) == "1"
    assert mod.slot_in_row({"position": "14"}) == ""
    assert mod.slot_in_row({"carousel_x": None, "position": "14"}) == ""
    assert mod.slot_in_row({"carousel_x": "", "position": "99"}) == ""


def test_normalize_pages_max_four():
    from vix_scraper.errors import ScraperError
    from vix_scraper.layout_compare import normalize_pages

    assert normalize_pages(["/a", "b"]) == ["/a", "/b"]
    with pytest.raises(ScraperError):
        normalize_pages(["/1", "/2", "/3", "/4", "/5"])


def test_history_creates_new_folder_each_run(tmp_path, monkeypatch):
    from vix_scraper.layout_compare import (
        create_run_dir,
        load_history,
        sync_latest,
        upsert_history_entry,
        write_scrape_meta,
    )

    layout = tmp_path / "layout_compare"
    r1, d1 = create_run_dir(layout)
    r2, d2 = create_run_dir(layout)
    assert r1 != r2
    assert d1 != d2
    assert d1.is_dir() and d2.is_dir()

    _write_page_csv(d1 / "ondemandplus_titles.csv", "/ondemandplus", "A", "id:a")
    _write_page_csv(d1 / "ondemandpluswc_titles.csv", "/ondemandpluswc", "B", "id:b")
    write_scrape_meta(
        d1,
        {
            "ran_at": "2026-01-01T00:00:00Z",
            "ran_at_local": "2026-01-01 00:00:00",
            "status": "ok",
            "page_order": ["/ondemandplus", "/ondemandpluswc"],
            "pages": {
                "/ondemandplus": {"status": "ok", "row_count": 1, "csv": "ondemandplus_titles.csv"},
                "/ondemandpluswc": {"status": "ok", "row_count": 1, "csv": "ondemandpluswc_titles.csv"},
            },
            "row_counts": {"/ondemandplus": 1, "/ondemandpluswc": 1},
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": r1,
            "ran_at": "2026-01-01T00:00:00Z",
            "ran_at_local": "2026-01-01 00:00:00",
            "pages": ["/ondemandplus", "/ondemandpluswc"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1, "/ondemandpluswc": 1},
            "dir": f"runs/{r1}",
        },
    )
    sync_latest(layout, r1, d1)

    _write_page_csv(d2 / "ondemandplus_titles.csv", "/ondemandplus", "C", "id:c")
    write_scrape_meta(
        d2,
        {
            "ran_at": "2026-01-02T00:00:00Z",
            "ran_at_local": "2026-01-02 00:00:00",
            "status": "ok",
            "page_order": ["/ondemandplus"],
            "pages": {"/ondemandplus": {"status": "ok", "row_count": 1, "csv": "ondemandplus_titles.csv"}},
            "row_counts": {"/ondemandplus": 1},
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": r2,
            "ran_at": "2026-01-02T00:00:00Z",
            "ran_at_local": "2026-01-02 00:00:00",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1},
            "dir": f"runs/{r2}",
        },
    )
    sync_latest(layout, r2, d2)

    history = load_history(layout)
    assert len(history["runs"]) == 2
    assert history["latest_run_id"] == r2
    assert d1.is_dir() and (d1 / "ondemandplus_titles.csv").is_file()
    assert (layout / "latest" / "ondemandplus_titles.csv").is_file()
    assert (layout / "history.json").is_file()


def test_resolve_csv_from_run(tmp_path, monkeypatch):
    mod = _load_lookup()
    from vix_scraper.layout_compare import create_run_dir, sync_latest, upsert_history_entry, write_scrape_meta

    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    rid, run_dir = create_run_dir(layout)
    odp = run_dir / "ondemandplus_titles.csv"
    wc = run_dir / "ondemandpluswc_titles.csv"
    _write_page_csv(odp, "/ondemandplus", "A", "id:a")
    _write_page_csv(wc, "/ondemandpluswc", "B", "id:b")
    write_scrape_meta(
        run_dir,
        {
            "ran_at": "2026-01-01T00:00:00Z",
            "ran_at_local": "done time",
            "status": "ok",
            "page_order": ["/ondemandplus", "/ondemandpluswc"],
            "pages": {
                "/ondemandplus": {"status": "ok", "row_count": 1, "csv": odp.name},
                "/ondemandpluswc": {"status": "ok", "row_count": 1, "csv": wc.name},
            },
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": rid,
            "ran_at_local": "done time",
            "pages": ["/ondemandplus", "/ondemandpluswc"],
            "status": "ok",
            "dir": f"runs/{rid}",
        },
    )
    sync_latest(layout, rid, run_dir)
    paths = mod.resolve_csv_paths(rid)
    assert set(paths) == {"/ondemandplus", "/ondemandpluswc"}


def test_timestamp_not_set_while_scraping_meta():
    mod = _load_lookup()
    with mod._SCRAPE_LOCK:
        mod._SCRAPE_STATE.update(
            {
                "status": "running",
                "message": "Scraping…",
                "pages_done": 0,
                "pages_total": 2,
                "ran_at_local": None,
            }
        )
    # Build a minimal state-like meta via scrape_status_snapshot
    snap = mod.scrape_status_snapshot()
    assert snap["status"] == "running"
    assert snap.get("ran_at_local") in (None, "")
    with mod._SCRAPE_LOCK:
        mod._SCRAPE_STATE.update({"status": "idle", "message": "", "pages_done": 0, "pages_total": 0})


def test_progress_endpoint(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)

    class EmptyState(mod.LookupState):
        def __init__(self) -> None:
            self.run_id = None
            self.run_dir = layout
            self.paths = {}
            self.page_order = []
            self.rows = []
            self.counts = {}
            self.scrape_meta = None

        def reload(self, run_id=None):
            return None

    state = EmptyState()
    with mod._SCRAPE_LOCK:
        mod._SCRAPE_STATE.update(
            {
                "status": "running",
                "message": "Scraping…",
                "pages_done": 1,
                "pages_total": 3,
                "current_page": "/ondemandplus",
                "modules_done": 4,
                "modules_total": 45,
                "items_done": 40,
            }
        )

    handler = mod.make_handler(state)
    httpd = mod.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/scrape/status")
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["status"] == "running"
        assert body["pages_done"] == 1
        assert body["pages_total"] == 3
        assert body["current_page"] == "/ondemandplus"
        assert body["modules_done"] == 4
        assert body["row_index"] == 4
        assert "percent" in body
        assert 0 <= body["percent"] <= 99
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        with mod._SCRAPE_LOCK:
            mod._SCRAPE_STATE.update({"status": "idle", "message": "", "pages_done": 0, "pages_total": 0})


def test_max_four_validation_on_start(monkeypatch):
    mod = _load_lookup()
    from vix_scraper.errors import ScraperError

    class DummyState:
        run_id = None

        def reload(self, *a, **k):
            return None

    with pytest.raises(ScraperError):
        mod.start_scrape_from_ui(
            {
                "auth_token": "x",
                "pages": ["/1", "/2", "/3", "/4", "/5"],
            },
            DummyState(),
        )


def test_error_mapping_actionable():
    mod = _load_lookup()
    assert "Authorization Bearer" in mod.map_error_message("missing AUTH_TOKEN required")
    assert "Installation ID" in mod.map_error_message("Statsig installation id required")
    assert "Token rejected" in mod.map_error_message("GraphQL errors: INVALID_TOKEN")
    null_msg = mod.map_error_message("uiPage null")
    assert "empty" in null_msg.lower() or "null" in null_msg.lower()
    assert "AUTH_TOKEN_AUTH0" in null_msg or "token" in null_msg.lower()
    expired_msg = mod.map_error_message("uiPage null (auth JWT expired)")
    assert "expired" in expired_msg.lower()
    assert "VPN is not the issue" in expired_msg
    probe_fail = mod.map_error_message(
        "All GraphQL endpoints failed: https://client-api.vix.com/gql/v2/default: uiPage null"
    )
    assert "All GraphQL endpoints failed" in probe_fail
    assert "Mexico VPN" not in probe_fail
    assert "locked" in mod.map_error_message("WinError 32 being used by another process").lower()
    assert "at most 4" in mod.map_error_message("You can scrape at most 4 pages at a time.").lower()
    geo = mod.map_error_message(
        "Warning: session JWT country=US but scrape catalog country=MX. "
        "Continuing scrape with catalog geo headers (x-vix-country=MX)."
    )
    assert "JWT country=US" in geo
    assert "catalog country=MX" in geo
    assert "does not block" in geo or "Continuing scrape" in geo
    complexity = mod.map_error_message(
        "GraphQL request failed for https://client-api.vix.com/gql/v2: "
        "HTTP 400: Query complexity of 109776 is over the maximum of 100000"
    )
    assert "complexity" in complexity.lower() or "too heavy" in complexity.lower()
    assert "VPN" not in complexity or "not a token" in complexity.lower()
    assert "at least one page" in mod.map_error_message("Add at least one page path.").lower()


def test_batch_meta_timestamp_only_after_finish(tmp_path, monkeypatch):
    """run_batch_scrape writes overall ran_at only when at least one page succeeds."""
    from vix_scraper.layout_compare import format_ran_at_local, load_scrape_meta, write_scrape_meta

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Simulate pre-finish: no overall timestamp yet
    write_scrape_meta(
        run_dir,
        {
            "ran_at": None,
            "ran_at_local": None,
            "status": "running",
            "pages": {},
        },
    )
    meta = load_scrape_meta(run_dir)
    assert meta["ran_at"] is None
    iso, local = format_ran_at_local()
    write_scrape_meta(
        run_dir,
        {
            "ran_at": iso,
            "ran_at_local": local,
            "status": "ok",
            "pages": {"/ondemandplus": {"status": "ok", "ran_at_local": local, "row_count": 1}},
        },
    )
    meta2 = load_scrape_meta(run_dir)
    assert meta2["ran_at"]
    assert meta2["ran_at_local"]


def test_csv_guardian_live_data_if_present():
    """Integration against layout CSVs when available (latest or root)."""
    candidates = [
        ROOT / "output" / "layout_compare" / "latest" / "ondemandplus_titles.csv",
        ROOT / "output" / "layout_compare" / "ondemandplus_titles.csv",
    ]
    if not any(p.is_file() for p in candidates):
        return
    mod = _load_lookup()
    state = mod.LookupState()
    hits = mod.search_titles(state.rows, "guardian")
    assert any("Guardián" in (h.get("title") or "") or "Guardian" in (h.get("title") or "") for h in hits)


def _seed_two_runs(layout: Path):
    from vix_scraper.layout_compare import (
        create_run_dir,
        sync_latest,
        upsert_history_entry,
        write_scrape_meta,
    )

    r1, d1 = create_run_dir(layout, "20260101_120000_aaaaaa")
    _write_page_csv(d1 / "ondemandplus_titles.csv", "/ondemandplus", "Guardián", "id:g", x="3")
    _write_page_csv(d1 / "ondemandpluswc_titles.csv", "/ondemandpluswc", "Guardián", "id:g", x="8")
    write_scrape_meta(
        d1,
        {
            "ran_at": "2026-01-01T12:00:00Z",
            "ran_at_local": "2026-01-01 07:00:00 EST",
            "status": "ok",
            "page_order": ["/ondemandplus", "/ondemandpluswc"],
            "pages": {
                "/ondemandplus": {"status": "ok", "row_count": 1, "csv": "ondemandplus_titles.csv"},
                "/ondemandpluswc": {
                    "status": "ok",
                    "row_count": 1,
                    "csv": "ondemandpluswc_titles.csv",
                },
            },
            "row_counts": {"/ondemandplus": 1, "/ondemandpluswc": 1},
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": r1,
            "ran_at": "2026-01-01T12:00:00Z",
            "ran_at_local": "2026-01-01 07:00:00 EST",
            "pages": ["/ondemandplus", "/ondemandpluswc"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1, "/ondemandpluswc": 1},
            "dir": f"runs/{r1}",
        },
    )
    sync_latest(layout, r1, d1)

    r2, d2 = create_run_dir(layout, "20260102_180000_bbbbbb")
    _write_page_csv(d2 / "ondemandplus_titles.csv", "/ondemandplus", "Guardián", "id:g", x="1")
    _write_page_csv(d2 / "ondemandpluswc_titles.csv", "/ondemandpluswc", "Guardián", "id:g", x="2")
    write_scrape_meta(
        d2,
        {
            "ran_at": "2026-01-02T18:00:00Z",
            "ran_at_local": "2026-01-02 13:00:00 EST",
            "status": "ok",
            "page_order": ["/ondemandplus", "/ondemandpluswc"],
            "pages": {
                "/ondemandplus": {"status": "ok", "row_count": 1, "csv": "ondemandplus_titles.csv"},
                "/ondemandpluswc": {
                    "status": "ok",
                    "row_count": 1,
                    "csv": "ondemandpluswc_titles.csv",
                },
            },
            "row_counts": {"/ondemandplus": 1, "/ondemandpluswc": 1},
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": r2,
            "ran_at": "2026-01-02T18:00:00Z",
            "ran_at_local": "2026-01-02 13:00:00 EST",
            "pages": ["/ondemandplus", "/ondemandpluswc"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1, "/ondemandpluswc": 1},
            "dir": f"runs/{r2}",
        },
    )
    sync_latest(layout, r2, d2)
    return r1, r2


def test_page_layouts_groups_rows_and_titles(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, r2 = _seed_two_runs(layout)
    extra = [
        {
            "page_url_path": "/ondemandplus",
            "title": "Second tile",
            "row_title": "Hero",
            "carousel_y": "1",
            "carousel_x": "2",
            "position": "2",
            "id": "id:g2",
        },
        {
            "page_url_path": "/ondemandplus",
            "title": "(empty)",
            "row_title": "Recomendado para ti",
            "carousel_y": "2",
            "carousel_x": "1",
            "position": "3",
            "id": "empty:reco",
        },
    ]
    from vix_scraper.layout_compare import run_dir_for

    csv_path = run_dir_for(layout, r1) / "ondemandplus_titles.csv"
    _write_page_csv(
        csv_path,
        "/ondemandplus",
        "Guardián",
        "id:g",
        x="1",
        extra_rows=extra,
    )

    payload = mod.page_layouts_across_runs([r1, r2], pages=["/ondemandplus", "/ondemandpluswc"])
    assert payload["mode"] == "layouts"
    assert payload["run_ids"] == [r1, r2]
    pages = {col["page"]: col for col in payload["pages"]}
    assert "/ondemandplus" in pages
    assert "/ondemandpluswc" in pages
    odp = pages["/ondemandplus"]["scrapes"][0]["rows"]
    assert odp[0]["carousel_y"] == 1
    assert odp[0]["row_title"] == "Hero"
    assert [t["title"] for t in odp[0]["titles"]] == ["Guardián", "Second tile"]
    assert odp[0]["titles"][0]["carousel_x"] == 1
    assert odp[0]["titles"][1]["carousel_x"] == 2
    assert odp[1]["row_title"] == "Recomendado para ti"
    assert odp[1]["empty"] is True
    wc = pages["/ondemandpluswc"]["scrapes"][0]["rows"]
    assert wc[0]["titles"][0]["title"] == "Guardián"
    assert pages["/ondemandplus"]["scrapes"][1]["run_id"] == r2


def test_page_layouts_rejects_unknown_run(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, _r2 = _seed_two_runs(layout)
    with pytest.raises(FileNotFoundError) as exc:
        mod.page_layouts_across_runs([r1, "missing_run"])
    assert "not in history" in str(exc.value).lower()


def test_compare_across_runs_table_and_matrix(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, r2 = _seed_two_runs(layout)

    payload = mod.compare_across_runs("guardian", [r1, r2], pages=["/ondemandplus", "/ondemandpluswc"])
    assert payload["mode"] == "multi"
    assert payload["run_ids"] == [r1, r2]
    assert payload["summary"]["titles"] == 1
    assert payload["summary"]["placements"] == 4
    assert len(payload["matrix"]["columns"]) == 2
    assert payload["table_rows"]
    # Each page × each run should appear
    keys = {(row["page"], row["run_id"]) for row in payload["table_rows"] if row.get("present")}
    assert ("/ondemandplus", r1) in keys
    assert ("/ondemandplus", r2) in keys
    assert ("/ondemandpluswc", r1) in keys
    # Positions drifted between scrapes
    by_run = {
        (row["page"], row["run_id"]): row["carousel_x"]
        for row in payload["table_rows"]
        if row.get("present") and not row.get("is_also_at")
    }
    assert by_run[("/ondemandplus", r1)] == "3"
    assert by_run[("/ondemandplus", r2)] == "1"


def test_compare_single_run_tabular(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, _r2 = _seed_two_runs(layout)
    payload = mod.compare_across_runs("guardian", [r1])
    assert payload["mode"] == "single"
    assert payload["groups"]
    assert payload["groups"][0]["table_rows"]
    pages = {row["page"] for row in payload["groups"][0]["table_rows"]}
    assert "/ondemandplus" in pages
    assert "/ondemandpluswc" in pages


def test_compare_rejects_unknown_run_id(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, _r2 = _seed_two_runs(layout)
    with pytest.raises(FileNotFoundError) as exc:
        mod.compare_across_runs("guardian", [r1, "does_not_exist_zzzz"])
    assert "not in history" in str(exc.value).lower()


def test_compare_rejects_too_many_runs(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, r2 = _seed_two_runs(layout)
    with pytest.raises(ValueError) as exc:
        mod.compare_across_runs("guardian", [r1, r2, r1 + "x", r2 + "y"])
    assert "at most" in str(exc.value).lower()


def test_delete_all_history_api_blocks_while_scraping(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    _seed_two_runs(layout)

    class EmptyState(mod.LookupState):
        def __init__(self) -> None:
            self.run_id = None
            self.run_dir = layout
            self.paths = {}
            self.page_order = []
            self.rows = []
            self.counts = {}
            self.scrape_meta = None

        def reload(self, run_id=None):
            return None

        def history_payload(self):
            return {"latest_run_id": None, "selected_run_id": None, "runs": []}

    state = EmptyState()
    with mod._SCRAPE_LOCK:
        mod._SCRAPE_STATE.update({"status": "running", "message": "Scraping…", "run_id": "active"})
    try:
        body = mod.delete_all_history_from_ui(state)
        assert body["ok"] is False
        assert "finish" in (body.get("error") or "").lower()
        from vix_scraper.layout_compare import load_history

        assert len(load_history(layout)["runs"]) == 2
    finally:
        with mod._SCRAPE_LOCK:
            mod._SCRAPE_STATE.update({"status": "idle", "message": "", "run_id": None})


def test_delete_all_history_api_ok(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    _seed_two_runs(layout)

    class State(mod.LookupState):
        def __init__(self) -> None:
            self.run_id = None
            self.run_dir = layout
            self.paths = {}
            self.page_order = []
            self.rows = []
            self.counts = {}
            self.scrape_meta = None

        def reload(self, run_id=None):
            return None

        def history_payload(self):
            from vix_scraper.layout_compare import load_history

            h = load_history(layout)
            return {
                "latest_run_id": h.get("latest_run_id"),
                "selected_run_id": self.run_id,
                "runs": h.get("runs") or [],
            }

    state = State()
    with mod._SCRAPE_LOCK:
        mod._SCRAPE_STATE.update({"status": "idle", "message": "", "run_id": None})

    body = mod.delete_all_history_from_ui(state)
    assert body["ok"] is True
    assert body["deleted_count"] >= 2
    from vix_scraper.layout_compare import load_history

    hist = load_history(layout)
    assert hist["runs"] == []
    assert state.run_id is None


def test_compare_api_multi_run(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, r2 = _seed_two_runs(layout)

    state = mod.LookupState(r2)
    body = state.compare(
        "guardian",
        run_ids=[r1, r2],
        pages=["/ondemandplus", "/ondemandpluswc"],
    )
    assert body["mode"] == "multi"
    assert body["run_ids"] == [r1, r2]
    assert body["summary"]["placements"] == 4
    assert "table_rows" in body
    assert len(body["matrix"]["columns"]) == 2


def test_device_catalog_in_meta(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    labels = [d["label"] for d in mod.device_catalog_payload()]
    assert "Web" in labels
    assert "Roku" in labels
    assert len(labels) == 17
    r1, _r2 = _seed_two_runs(layout)
    state = mod.LookupState(r1)
    meta = state.meta()
    assert any(d["id"] == "web" and d["platform"] == "web" for d in meta["devices"])
    empty = mod._empty_meta_payload(state)
    assert empty["devices"][0]["label"] == "Fire Tablet"


def test_history_includes_device_label(tmp_path, monkeypatch):
    from vix_scraper.layout_compare import create_run_dir, upsert_history_entry, write_scrape_meta

    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    rid, run_dir = create_run_dir(layout, "20260817_120000_devweb")
    _write_page_csv(run_dir / "ondemandplus_titles.csv", "/ondemandplus", "Guardián", "id:g")
    write_scrape_meta(
        run_dir,
        {
            "ran_at": "2026-08-17T16:00:00Z",
            "ran_at_local": "2026-08-17 12:00:00 EDT",
            "status": "ok",
            "platform": "roku",
            "device": "tv",
            "device_id": "roku",
            "device_label": "Roku",
            "page_order": ["/ondemandplus"],
            "pages": {"/ondemandplus": {"status": "ok", "row_count": 1}},
            "row_counts": {"/ondemandplus": 1},
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": rid,
            "ran_at": "2026-08-17T16:00:00Z",
            "ran_at_local": "2026-08-17 12:00:00 EDT",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "device_id": "roku",
            "device_label": "Roku",
            "platform": "roku",
            "device_type": "tv",
            "dir": f"runs/{rid}",
        },
    )
    state = mod.LookupState(rid)
    hist = state.history_payload()
    assert hist["runs"][0]["device_label"] == "Roku"
    assert hist["runs"][0]["platform"] == "roku"
    assert state.meta()["device_label"] == "Roku"


def test_unknown_device_rejected_before_scrape():
    from vix_scraper.errors import ScraperError

    mod = _load_lookup()
    state = mod.LookupState()
    with pytest.raises(ScraperError, match="Unknown device"):
        mod.start_scrape_from_ui(
            {
                "auth_token": "dummy-token",
                "devices": ["not-a-real-device"],
                "pages": ["/ondemandplus"],
            },
            state,
        )


def test_missing_auth_rejected(monkeypatch):
    from vix_scraper.errors import ScraperError

    mod = _load_lookup()
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_ROKU", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_AUTH0", raising=False)
    monkeypatch.delenv("X_VIX_USER_TOKEN", raising=False)
    mod._SESSION.clear()
    mod._DEVICE_SESSION.clear()

    class EmptyCreds:
        auth_token = ""
        x_vix_user_token = ""

    monkeypatch.setattr(
        "vix_scraper.auth.resolve_auth_profile", lambda *a, **k: EmptyCreds()
    )
    state = type("DummyState", (), {"run_id": None, "reload": lambda *a, **k: None})()
    with pytest.raises(ScraperError, match="Enter Authorization"):
        mod.start_scrape_from_ui(
            {
                "devices": ["web", "roku"],
                "pages": ["/ondemandplus"],
            },
            state,
        )


def test_missing_user_token_rejected(monkeypatch):
    from vix_scraper.errors import ScraperError

    mod = _load_lookup()
    monkeypatch.delenv("X_VIX_USER_TOKEN", raising=False)
    mod._SESSION.clear()
    state = type("DummyState", (), {"run_id": None, "reload": lambda *a, **k: None})()
    with pytest.raises(ScraperError, match="User token"):
        mod.start_scrape_from_ui(
            {
                "auth_token": "dummy-token",
                "installation_id": "install-1",
                "devices": ["web"],
                "pages": ["/ondemandplus"],
            },
            state,
        )


def test_missing_install_id_rejected(monkeypatch):
    from vix_scraper.errors import ScraperError

    mod = _load_lookup()
    monkeypatch.delenv("VIX_INSTALLATION_ID", raising=False)
    mod._SESSION.clear()
    state = type("DummyState", (), {"run_id": None, "reload": lambda *a, **k: None})()
    with pytest.raises(ScraperError, match="Installation ID"):
        mod.start_scrape_from_ui(
            {
                "auth_token": "dummy-token",
                "x_vix_user_token": "user-token",
                "devices": ["web"],
                "pages": ["/ondemandplus"],
            },
            state,
        )


def test_shared_tokens_and_per_device_headers(monkeypatch):
    from vix_scraper.client import GraphQLClient
    from vix_scraper.devices import resolve_device

    mod = _load_lookup()
    monkeypatch.delenv("AUTH_TOKEN_ROKU", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_AUTH0", raising=False)
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    payload = {
        "auth_token": "shared-token",
        "x_vix_user_token": "shared-user",
        "installation_id": "shared-install",
        "devices": ["web", "roku"],
        "device_creds": {
            "web": {"platform": "web", "device_type": "desktop"},
            "roku": {"platform": "roku", "device_type": "smarttv"},
        },
        "pages": ["/ondemandplus"],
    }
    web_cfg = mod.build_device_scrape_config(payload, "web", ["/ondemandplus"])
    roku_cfg = mod.build_device_scrape_config(payload, "roku", ["/ondemandplus"])
    assert web_cfg.auth_token == "shared-token"
    assert roku_cfg.auth_token == "shared-token"
    assert roku_cfg.x_vix_user_token == "shared-user"
    assert roku_cfg.installation_id == "shared-install"
    assert roku_cfg.platform == "roku"
    assert roku_cfg.device_type == "smarttv"
    assert web_cfg.platform == "web"
    assert roku_cfg.user_agent == resolve_device("roku").user_agent
    assert web_cfg.user_agent == resolve_device("web").user_agent
    roku_headers = GraphQLClient._build_headers(roku_cfg)
    assert roku_headers["x-vix-platform"] == "roku"
    assert roku_headers["x-vix-device-type"] == "smarttv"
    assert roku_headers["Authorization"] == "Bearer shared-token"
    assert roku_headers["x-vix-user-token"] == "shared-user"
    assert roku_headers["x-vix-installation-id"] == "shared-install"
    assert roku_headers["User-Agent"] == resolve_device("roku").user_agent
    assert roku_cfg.country == "MX"
    assert roku_headers["x-vix-country"] == "MX"
    assert roku_headers["x-vix-geo-country"] == "MX"
    assert roku_headers["Accept-Language"].startswith("es-MX")


def test_old_device_creds_token_keys_are_optional(monkeypatch):
    from vix_scraper.client import GraphQLClient

    mod = _load_lookup()
    monkeypatch.delenv("AUTH_TOKEN_ROKU", raising=False)
    payload = {
        "auth_token": "shared-token",
        "devices": ["roku"],
        "device_creds": {"roku": {"platform": "androidtv", "device_type": "tablet"}},
        "pages": ["/ondemandplus"],
    }
    roku_cfg = mod.build_device_scrape_config(payload, "roku", ["/ondemandplus"])
    headers = GraphQLClient._build_headers(roku_cfg)
    assert roku_cfg.auth_token == "shared-token"
    assert headers["x-vix-platform"] == "androidtv"
    assert headers["x-vix-device-type"] == "tablet"
    assert "User-Agent" in headers


def test_legacy_per_device_token_still_used_if_global_missing(monkeypatch):
    mod = _load_lookup()
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_ROKU", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_AUTH0", raising=False)
    mod._SESSION.clear()
    payload = {
        "devices": ["roku"],
        "device_creds": {
            "roku": {
                "auth_token": "legacy-roku",
                "platform": "roku",
                "device_type": "smarttv",
            }
        },
    }
    overlay = mod.overlay_for_device(payload, "roku")
    assert overlay["auth_token"] == "legacy-roku"
    assert overlay["platform"] == "roku"


def test_top_level_ios_mobile_override_wins_when_creds_omit_headers(monkeypatch):
    """A console override of the scrape POST (platform=ios, device_type=mobile)
    must reach GraphQL headers even if device_creds only has tokens / is empty.
    """
    from vix_scraper.client import GraphQLClient

    mod = _load_lookup()
    monkeypatch.delenv("VIX_PLATFORM_WEB", raising=False)
    monkeypatch.delenv("VIX_DEVICE_TYPE_WEB", raising=False)
    monkeypatch.setenv("VIX_PLATFORM_WEB", "web")
    monkeypatch.setenv("VIX_DEVICE_TYPE_WEB", "desktop")
    payload = {
        "auth_token": "shared-token",
        "devices": ["web"],
        "platform": "ios",
        "device_type": "mobile",
        "pages": ["/ondemandplus"],
    }
    cfg = mod.build_device_scrape_config(payload, "web", ["/ondemandplus"])
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "ios"
    assert headers["x-vix-device-type"] == "mobile"


def test_dropdown_values_win_over_catalog_on_scrape_config():
    from vix_scraper.client import GraphQLClient

    mod = _load_lookup()
    payload = {
        "auth_token": "shared-token",
        "devices": ["web", "roku"],
        "device_creds": {
            "web": {"platform": "web", "device_type": "desktop"},
            "roku": {"platform": "androidtv", "device_type": "tablet"},
        },
        "pages": ["/ondemandplus"],
    }
    web_cfg = mod.build_device_scrape_config(payload, "web", ["/ondemandplus"])
    roku_cfg = mod.build_device_scrape_config(payload, "roku", ["/ondemandplus"])
    web_headers = GraphQLClient._build_headers(web_cfg)
    roku_headers = GraphQLClient._build_headers(roku_cfg)
    assert web_headers["x-vix-platform"] == "web"
    assert web_headers["x-vix-device-type"] == "desktop"
    assert roku_headers["x-vix-platform"] == "androidtv"
    assert roku_headers["x-vix-device-type"] == "tablet"
    assert roku_headers["Authorization"] == "Bearer shared-token"


def test_meta_includes_header_dropdown_options():
    mod = _load_lookup()
    opts = mod.device_header_options()
    assert opts["platform_options"] == [
        "android",
        "androidtv",
        "firetv",
        "firetablet",
        "roku",
        "ios",
        "tvos",
        "web",
        "samsungtv",
        "lgtv",
        "viziotv",
        "vidaatv",
        "samsung_galaxy",
        "comcasttv",
    ]
    assert opts["device_type_options"] == ["mobile", "tablet", "smarttv", "desktop"]
    devices = mod.device_catalog_payload()
    roku = next(d for d in devices if d["id"] == "roku")
    web = next(d for d in devices if d["id"] == "web")
    assert web["platform"] == "web" and web["device_type"] == "desktop"
    assert roku["platform"] == "roku" and roku["device_type"] == "smarttv"
    assert roku["device_type"] != "tv"


def test_map_error_never_includes_tokens():
    mod = _load_lookup()
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
    mapped = mod.map_error_message(
        "weird failure with " + token + " and Bearer " + token
    )
    assert token not in mapped
    assert "eyJ" not in mapped
    rejected = mod.map_error_message("GraphQL errors: INVALID_TOKEN " + token)
    assert token not in rejected
    assert "Token rejected" in rejected
    missing = mod.map_error_message(
        "Enter Authorization — paste from the Network tab → gql/v2 request headers"
    )
    assert "Authorization" in missing
    assert token not in missing


def test_roku_inherits_shared_session_token(monkeypatch):
    mod = _load_lookup()
    monkeypatch.delenv("AUTH_TOKEN_ROKU", raising=False)
    monkeypatch.setenv("AUTH_TOKEN", "shared-env-token")
    roku = mod._devices_mod().resolve_device("roku")
    payload = {"auth_token": "shared-form-token", "devices": ["roku"]}
    assert mod.device_has_auth(payload, roku)
    overlay = mod.overlay_for_device(payload, roku)
    assert overlay["auth_token"] == "shared-form-token"
    assert overlay["platform"] == "roku"
    env_only = mod.overlay_for_device({"devices": ["roku"]}, roku)
    assert env_only["auth_token"] == "shared-env-token"
    assert env_only["platform"] == "roku"


def test_persist_writes_shared_tokens_and_device_overrides(tmp_path, monkeypatch):
    mod = _load_lookup()
    env_local = tmp_path / ".env.local"
    monkeypatch.setattr(mod, "ENV_LOCAL", env_local)
    monkeypatch.delenv("AUTH_TOKEN_AUTH0", raising=False)
    mod._persist_env_local(
        {
            "devices": ["web", "roku"],
            "auth_token": "shared-secret",
            "x_vix_user_token": "shared-user",
            "installation_id": "shared-install",
            "device_creds": {
                "web": {"platform": "web", "device_type": "desktop"},
                "roku": {"platform": "roku", "device_type": "smarttv"},
            },
        }
    )
    text = env_local.read_text(encoding="utf-8")
    assert "AUTH_TOKEN=shared-secret" in text
    assert "X_VIX_USER_TOKEN=shared-user" in text
    assert "VIX_INSTALLATION_ID=shared-install" in text
    assert "AUTH_TOKEN_ROKU=" not in text
    assert "VIX_PLATFORM_ROKU=roku" in text
    assert "VIX_DEVICE_TYPE_ROKU=smarttv" in text
    assert "VIX_DEVICES=web,roku" in text
    assert "VIX_COUNTRY=MX" in text
    assert "VIX_ACCEPT_LANGUAGE=es-MX,es;q=0.9" in text


def test_format_table_prints_row_slot_not_page_index():
    mod = _load_lookup()
    text = mod.format_table(
        [
            {
                "page": "/ondemandpluswc",
                "title": "Una pequeña confusión",
                "row_title": "Lo más buscado",
                "carousel_y": "4",
                "carousel_x": "1",
                "position": "14",
            }
        ]
    )
    header = text.splitlines()[0]
    assert "position" in header
    assert "14" not in text
    assert " 1" in text or text.endswith("1") or "1" in text.splitlines()[-1]


def test_odp_wc_slots_are_independent_of_page_index():
    """Each page's Position is its own carousel_x; do not mix global indexes."""
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "Una pequeña confusión",
            "row_title": "Lo más buscado",
            "carousel_y": "4",
            "carousel_x": "1",
            "position": "14",
            "content_id": "video:mcp:4585674",
        },
        {
            "page": "/ondemandpluswc",
            "title": "Una pequeña confusión",
            "row_title": "Lo más buscado",
            "carousel_y": "3",
            "carousel_x": "1",
            "position": "2",
            "content_id": "video:mcp:4585674",
        },
    ]
    groups, _ = mod.build_compare_groups(rows, ["/ondemandplus", "/ondemandpluswc"])
    table = mod.build_table_rows(groups, ["/ondemandplus", "/ondemandpluswc"])
    present = [r for r in table if r.get("present") and not r.get("is_also_at")]
    assert {r["page"]: r["slot"] for r in present} == {
        "/ondemandplus": "1",
        "/ondemandpluswc": "1",
    }
    assert all(r["carousel_x"] == "1" for r in present)
    assert groups[0]["odp"][0]["compare_status"] == "same_row"


def test_seguir_viendo_y_shift_compares_row_title_not_page_index():
    """Empty Seguir viendo can shift carousel_y; identity stays on row_title + slot."""
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "Casa",
            "row_title": "Lo más buscado",
            "carousel_y": "4",
            "carousel_x": "1",
            "position": "20",
            "content_id": "id:casa",
        },
        {
            "page": "/ondemandpluswc",
            "title": "Casa",
            "row_title": "Lo más buscado",
            "carousel_y": "3",
            "carousel_x": "1",
            "position": "8",
            "content_id": "id:casa",
        },
    ]
    groups, _ = mod.build_compare_groups(rows, ["/ondemandplus", "/ondemandpluswc"])
    assert groups[0]["odp"][0]["compare_status"] == "same_row"
    assert groups[0]["odp"][0]["slot"] == "1"
    assert groups[0]["wc"][0]["slot"] == "1"


def test_duplicate_title_two_rows_each_has_own_slot():
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "Dup",
            "row_title": "Hero",
            "carousel_y": "1",
            "carousel_x": "5",
            "position": "5",
            "content_id": "id:dup",
        },
        {
            "page": "/ondemandplus",
            "title": "Dup",
            "row_title": "Lo más buscado",
            "carousel_y": "4",
            "carousel_x": "1",
            "position": "14",
            "content_id": "id:dup",
        },
    ]
    groups, _ = mod.build_compare_groups(rows, ["/ondemandplus"])
    table = mod.build_table_rows(groups, ["/ondemandplus"])
    present = [r for r in table if r.get("present")]
    assert len(present) == 2
    assert present[0]["slot"] == "5"
    assert present[1]["slot"] == "1"
    assert present[1]["is_also_at"] is True
    assert present[0]["row_title"] == "Hero"
    assert present[1]["row_title"] == "Lo más buscado"


def test_search_matches_row_title_and_keeps_later_slot():
    """Radical on Del cine a tu pantalla must show when searching the title or the row."""
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "Papá o mamá",
            "row_title": "Del cine a tu pantalla",
            "carousel_y": "24",
            "carousel_x": "1",
            "position": "201",
            "content_id": "video:mcp:4493919",
        },
        {
            "page": "/ondemandplus",
            "title": "Radical",
            "row_title": "Lo más buscado en México",
            "carousel_y": "5",
            "carousel_x": "5",
            "position": "26",
            "content_id": "video:mcp:4496014",
        },
        {
            "page": "/ondemandplus",
            "title": "Radical",
            "row_title": "Del cine a tu pantalla",
            "carousel_y": "24",
            "carousel_x": "21",
            "position": "540",
            "content_id": "video:mcp:4496014",
        },
        {
            "page": "/ondemandpluswc",
            "title": "Radical",
            "row_title": "Del cine a tu pantalla",
            "carousel_y": "10",
            "carousel_x": "21",
            "position": "157",
            "content_id": "video:mcp:4496014",
        },
    ]
    by_title = mod.search_titles(rows, "radical")
    cine_hits = [h for h in by_title if h["row_title"] == "Del cine a tu pantalla"]
    assert len(cine_hits) == 2
    assert {h["page"]: h["carousel_x"] for h in cine_hits} == {
        "/ondemandplus": "21",
        "/ondemandpluswc": "21",
    }

    groups, summary = mod.build_compare_groups(
        by_title, ["/ondemandplus", "/ondemandpluswc"]
    )
    assert summary["placements"] == 3
    table = mod.build_table_rows(groups, ["/ondemandplus", "/ondemandpluswc"])
    cine_table = [
        r
        for r in table
        if r.get("present") and r.get("row_title") == "Del cine a tu pantalla"
    ]
    assert len(cine_table) == 2
    assert all(r["slot"] == "21" for r in cine_table)

    by_row = mod.search_titles(rows, "cine")
    titles = {h["title"] for h in by_row}
    assert "Radical" in titles
    assert "Papá o mamá" in titles
    pantalla = mod.search_titles(rows, "pantalla")
    assert any(h["title"] == "Radical" and h["carousel_x"] == "21" for h in pantalla)


def test_incomplete_rows_do_not_crash_compare():
    mod = _load_lookup()
    rows = [
        {"page": "/ondemandplus", "title": "Partial"},
        {"title": "No page", "content_id": "x"},
        {
            "page": "/ondemandpluswc",
            "title": "Partial",
            "row_title": "Rail",
            "carousel_x": None,
            "position": "14",
            "content_id": "id:p",
        },
    ]
    hits = mod.search_titles(rows, "partial")
    groups, summary = mod.build_compare_groups(hits, ["/ondemandplus", "/ondemandpluswc"])
    table = mod.build_table_rows(groups, ["/ondemandplus", "/ondemandpluswc"])
    assert summary["titles"] >= 1
    wc = next(r for r in table if r["page"] == "/ondemandpluswc" and r.get("present"))
    assert wc["slot"] == ""
    assert wc["carousel_x"] == ""
    assert wc["position"] == "14"
    slim = mod.format_table(hits)
    assert "14" not in slim


def test_legacy_zero_carousel_x_displays_as_one():
    mod = _load_lookup()
    rows = [
        {
            "page": "/ondemandplus",
            "title": "Legacy",
            "row_title": "Hero",
            "carousel_y": "1",
            "carousel_x": "0",
            "position": "9",
            "content_id": "id:leg",
        }
    ]
    groups, _ = mod.build_compare_groups(rows, ["/ondemandplus"])
    table = mod.build_table_rows(groups, ["/ondemandplus"])
    assert table[0]["slot"] == "1"
    assert table[0]["carousel_x"] == "1"
    cell = mod._placement_cell(groups[0]["by_page"]["/ondemandplus"][0])
    assert cell["slot"] == "1"
    assert cell["carousel_x"] == "1"
    assert cell["page_index"] == "9"


def test_multi_run_compare_uses_slot_not_page_index(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    r1, r2 = _seed_two_runs(layout)
    # Same title, same row slot, different page-wide indexes
    from vix_scraper.layout_compare import run_dir_for

    d1 = run_dir_for(layout, r1)
    _write_page_csv(
        d1 / "ondemandplus_titles.csv",
        "/ondemandplus",
        "SlotTitle",
        "id:slot",
        x="1",
        position="14",
        y="4",
        row_title="Lo más buscado",
    )
    _write_page_csv(
        d1 / "ondemandpluswc_titles.csv",
        "/ondemandpluswc",
        "SlotTitle",
        "id:slot",
        x="1",
        position="3",
        y="3",
        row_title="Lo más buscado",
    )
    d2 = run_dir_for(layout, r2)
    _write_page_csv(
        d2 / "ondemandplus_titles.csv",
        "/ondemandplus",
        "SlotTitle",
        "id:slot",
        x="2",
        position="30",
        y="4",
        row_title="Lo más buscado",
    )
    _write_page_csv(
        d2 / "ondemandpluswc_titles.csv",
        "/ondemandpluswc",
        "SlotTitle",
        "id:slot",
        x="1",
        position="99",
        y="3",
        row_title="Lo más buscado",
    )
    payload = mod.compare_across_runs("slottitle", [r1, r2], pages=["/ondemandplus", "/ondemandpluswc"])
    by_run = {
        (row["page"], row["run_id"]): row
        for row in payload["table_rows"]
        if row.get("present") and not row.get("is_also_at")
    }
    assert by_run[("/ondemandplus", r1)]["slot"] == "1"
    assert by_run[("/ondemandplus", r2)]["slot"] == "2"
    assert by_run[("/ondemandplus", r1)]["carousel_x"] == "1"
    matrix_odp = next(r for r in payload["matrix"]["rows"] if r["page"] == "/ondemandplus")
    assert matrix_odp["cells"][r1]["placements"][0]["slot"] == "1"
    assert matrix_odp["cells"][r2]["placements"][0]["carousel_x"] == "2"


def test_device_run_slot_semantics(tmp_path, monkeypatch):
    from vix_scraper.layout_compare import (
        create_run_dir,
        upsert_history_entry,
        write_scrape_meta,
    )

    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    rid, run_dir = create_run_dir(layout, "20260103_090000_device")
    _write_page_csv(
        run_dir / "ondemandplus_titles.csv",
        "/ondemandplus",
        "Roku Hit",
        "id:roku",
        x="1",
        position="14",
        y="2",
        row_title="Populares",
    )
    write_scrape_meta(
        run_dir,
        {
            "ran_at": "2026-01-03T09:00:00Z",
            "ran_at_local": "2026-01-03 04:00:00 EST",
            "status": "ok",
            "device_id": "roku",
            "device_label": "Roku",
            "page_order": ["/ondemandplus"],
            "pages": {"/ondemandplus": {"status": "ok", "row_count": 1, "csv": "ondemandplus_titles.csv"}},
            "row_counts": {"/ondemandplus": 1},
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": rid,
            "ran_at": "2026-01-03T09:00:00Z",
            "ran_at_local": "2026-01-03 04:00:00 EST",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "device_id": "roku",
            "device_label": "Roku",
            "row_counts": {"/ondemandplus": 1},
            "dir": f"runs/{rid}",
        },
    )
    payload = mod.compare_across_runs("roku", [rid], pages=["/ondemandplus"])
    row = payload["groups"][0]["table_rows"][0]
    assert row["slot"] == "1"
    assert row["carousel_x"] == "1"
    assert row["position"] == "14"


def test_slim_hits_omits_page_index_as_position():
    mod = _load_lookup()

    class State(mod.LookupState):
        def __init__(self) -> None:
            self.rows = [
                {
                    "page": "/ondemandpluswc",
                    "title": "Una pequeña confusión",
                    "row_title": "Lo más buscado",
                    "carousel_y": "4",
                    "carousel_x": "1",
                    "position": "14",
                    "content_id": "video:mcp:4585674",
                }
            ]
            self.run_id = None
            self.page_order = ["/ondemandpluswc"]
            self.scrape_meta = None

    slim = State().slim_hits("confusion")
    assert slim[0]["carousel_x"] == "1"
    assert slim[0]["slot"] == "1"
    assert "position" not in slim[0]


def test_format_history_when_local_display():
    mod = _load_lookup()
    iso = "2026-08-08T19:03:25Z"
    text = mod.format_history_when(iso)
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
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
    expected = (
        f"{dt.day:02d} {months[dt.month - 1]} {dt.year} "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    )
    assert text == expected
    parts = text.split()
    assert len(parts) == 4
    assert parts[1] in months
    assert ":" in parts[3]
    assert mod.format_history_when(None, "already formatted") == "already formatted"


def test_page_csv_names_keeps_all_page_files():
    mod = _load_lookup()
    files = [
        {"name": "combined_titles.csv"},
        {"name": "scrape_meta.json"},
        {"name": "ondemandplus_titles.csv"},
        {"name": "ondemandpluswc_titles.csv"},
        {"name": "movies_titles.csv"},
        {"name": "layout_diff_summary.md"},
    ]
    names = mod.page_csv_names(
        files, ["/ondemandplus", "/ondemandpluswc", "/movies"]
    )
    assert names == [
        "ondemandplus_titles.csv",
        "ondemandpluswc_titles.csv",
        "movies_titles.csv",
    ]
    two_files = [
        {"name": "combined_titles.csv"},
        {"name": "ondemandplus_titles.csv"},
        {"name": "ondemandpluswc_titles.csv"},
    ]
    two = mod.page_csv_names(two_files, ["/ondemandplus", "/ondemandpluswc"])
    assert two == ["ondemandplus_titles.csv", "ondemandpluswc_titles.csv"]


def test_pages_from_payload_keeps_custom_paths():
    from vix_scraper.errors import ScraperError
    from vix_scraper.layout_compare import page_csv_name

    mod = _load_lookup()
    assert page_csv_name("/movies") == "movies_titles.csv"
    assert page_csv_name("/deportes") == "deportes_titles.csv"
    assert mod.friendly_page_label("/movies") == "movies"
    assert mod.friendly_page_label("/deportes") == "deportes"
    assert mod._pages_from_payload(
        {"pages": ["/ondemandplus", "/ondemandpluswc", "/movies"]}
    ) == ["/ondemandplus", "/ondemandpluswc", "/movies"]
    assert mod._pages_from_payload(
        {"page_details": [{"url_path": "/deportes", "web_link": "https://vix.com/es-mx/deportes"}]}
    ) == ["/deportes"]
    with pytest.raises(ScraperError, match="at least one page"):
        mod._pages_from_payload({"pages": ["", "  "]})


def _seed_custom_page_run(layout: Path, run_id: str = "20260825_120000_custom1"):
    from vix_scraper.layout_compare import (
        create_run_dir,
        sync_latest,
        upsert_history_entry,
        write_scrape_meta,
    )

    rid, run_dir = create_run_dir(layout, run_id)
    pages = ["/ondemandplus", "/ondemandpluswc", "/movies"]
    titles = {
        "/ondemandplus": ("Guardián", "id:g", "3"),
        "/ondemandpluswc": ("Guardián", "id:g", "8"),
        "/movies": ("Película extra", "id:m", "1"),
    }
    page_meta = {}
    row_counts = {}
    for page, (title, cid, x) in titles.items():
        csv_name = f"{page.strip('/')}_titles.csv"
        _write_page_csv(run_dir / csv_name, page, title, cid, x=x)
        page_meta[page] = {"status": "ok", "row_count": 1, "csv": csv_name}
        row_counts[page] = 1
    write_scrape_meta(
        run_dir,
        {
            "ran_at": "2026-08-25T16:00:00Z",
            "ran_at_local": "2026-08-25 12:00:00 EDT",
            "status": "ok",
            "page_order": pages,
            "pages": page_meta,
            "row_counts": row_counts,
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": rid,
            "ran_at": "2026-08-25T16:00:00Z",
            "ran_at_local": "2026-08-25 12:00:00 EDT",
            "pages": pages,
            "status": "ok",
            "row_counts": row_counts,
            "dir": f"runs/{rid}",
        },
    )
    sync_latest(layout, rid, run_dir)
    return rid


def test_custom_page_resolve_compare_layouts_and_api(tmp_path, monkeypatch):
    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    rid = _seed_custom_page_run(layout)

    paths = mod.resolve_csv_paths(rid)
    assert set(paths) == {"/ondemandplus", "/ondemandpluswc", "/movies"}
    assert paths["/movies"].name == "movies_titles.csv"

    state = mod.LookupState(rid)
    hist = state.history_payload()
    assert hist["runs"][0]["pages"] == ["/ondemandplus", "/ondemandpluswc", "/movies"]
    assert hist["runs"][0]["page_labels"][-1] == "movies"
    assert hist["runs"][0]["page_csvs"] == [
        "ondemandplus_titles.csv",
        "ondemandpluswc_titles.csv",
        "movies_titles.csv",
    ]
    assert state.meta()["page_order"] == ["/ondemandplus", "/ondemandpluswc", "/movies"]
    assert state.meta()["page_labels"]["/movies"] == "movies"

    hits = mod.search_titles(state.rows, "pelicula")
    assert any(h["page"] == "/movies" for h in hits)

    compare = mod.compare_across_runs(
        "guardian", [rid], pages=["/ondemandplus", "/ondemandpluswc", "/movies"]
    )
    assert "/movies" in compare["available_pages"]
    assert "/movies" in compare["page_order"]
    movie_rows = [r for r in compare["table_rows"] if r["page"] == "/movies"]
    assert movie_rows
    assert all(not r.get("present") for r in movie_rows)

    layouts = mod.page_layouts_across_runs(
        [rid], pages=["/ondemandplus", "/ondemandpluswc", "/movies"]
    )
    layout_pages = {col["page"]: col for col in layouts["pages"]}
    assert set(layout_pages) == {"/ondemandplus", "/ondemandpluswc", "/movies"}
    assert layout_pages["/movies"]["label"] == "movies"
    assert layout_pages["/movies"]["scrapes"][0]["rows"][0]["titles"][0]["title"] == "Película extra"
    assert "/movies" in layouts["available_pages"]

    handler = mod.make_handler(state)
    httpd = mod.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            f"/api/layouts?run_ids={rid}&pages=/ondemandplus,/ondemandpluswc,/movies",
        )
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert [col["page"] for col in body["pages"]] == [
            "/ondemandplus",
            "/ondemandpluswc",
            "/movies",
        ]
        conn.close()

        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(
            "GET",
            f"/api/compare?q=pelicula&run_ids={rid}&pages=/movies",
        )
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        assert resp.status == 200
        assert body["page_order"] == ["/movies"]
        assert any(r.get("present") and r["page"] == "/movies" for r in body["table_rows"])
        conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_history_payload_excludes_failures(tmp_path, monkeypatch):
    from vix_scraper.layout_compare import (
        create_run_dir,
        load_history,
        save_history,
        upsert_history_entry,
        write_scrape_meta,
    )

    mod = _load_lookup()
    layout = tmp_path / "layout_compare"
    monkeypatch.setattr(mod, "LAYOUT_DIR", layout)
    ok_id, ok_dir = create_run_dir(layout, "20260808_150325_okrun1")
    _write_page_csv(ok_dir / "ondemandplus_titles.csv", "/ondemandplus", "A", "id:a")
    _write_page_csv(ok_dir / "ondemandpluswc_titles.csv", "/ondemandpluswc", "B", "id:b")
    write_scrape_meta(
        ok_dir,
        {
            "ran_at": "2026-08-08T19:03:25Z",
            "ran_at_local": "2026-08-08 15:03:25 EDT",
            "status": "ok",
            "page_order": ["/ondemandplus", "/ondemandpluswc"],
            "pages": {
                "/ondemandplus": {"status": "ok", "csv": "ondemandplus_titles.csv"},
                "/ondemandpluswc": {"status": "ok", "csv": "ondemandpluswc_titles.csv"},
            },
        },
    )
    upsert_history_entry(
        layout,
        {
            "run_id": ok_id,
            "ran_at": "2026-08-08T19:03:25Z",
            "ran_at_local": "2026-08-08 15:03:25 EDT",
            "pages": ["/ondemandplus", "/ondemandpluswc"],
            "status": "ok",
            "dir": f"runs/{ok_id}",
        },
    )
    fail_id, fail_dir = create_run_dir(layout, "20260808_160000_fail99")
    write_scrape_meta(fail_dir, {"status": "failed", "ran_at": "2026-08-08T20:00:00Z"})
    history = load_history(layout)
    history["runs"] = list(history.get("runs") or [])
    history["runs"].insert(
        0,
        {
            "run_id": fail_id,
            "ran_at": "2026-08-08T20:00:00Z",
            "status": "failed",
            "pages": ["/ondemandplus"],
            "dir": f"runs/{fail_id}",
        },
    )
    save_history(layout, history)

    state = mod.LookupState(ok_id)
    payload = state.history_payload()
    ids = [r["run_id"] for r in payload["runs"]]
    assert ok_id in ids
    assert fail_id not in ids
    assert all(r["status"] in {"ok", "partial"} for r in payload["runs"])
    ok_run = next(r for r in payload["runs"] if r["run_id"] == ok_id)
    assert ok_run["when"].startswith("08 Aug 2026 ")
    assert ok_run["page_csvs"] == ["ondemandplus_titles.csv", "ondemandpluswc_titles.csv"]


def test_ui_static_keeps_required_controls():
    html = (ROOT / "tools" / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "tools" / "static" / "app.js").read_text(encoding="utf-8")
    css = (ROOT / "tools" / "static" / "app.css").read_text(encoding="utf-8")
    for needle in (
        'id="authToken"',
        'id="userToken"',
        'id="installId"',
        'id="persistLocal"',
        'id="btnScrape"',
        'id="q"',
        'id="btnCompare"',
        'id="runA"',
        'id="runB"',
        'id="historyList"',
        'id="platformSelect"',
        'id="deviceTypeSelect"',
        'id="pageEditor"',
        'id="compareOut"',
        'id="progressWrap"',
        'id="progressFill"',
        'id="pages"',
        'id="credentials"',
        'id="devices"',
        'id="comparison"',
        'id="viewLayouts"',
        'id="searchRow"',
        "app.js?v=ui16",
        "app.css?v=ui16",
        "vix-logo.png",
    ):
        assert needle in html, needle
    assert 'id="btnLayouts"' not in html
    assert 'id="layoutActions"' not in html
    assert "layoutActions" not in js
    assert "btnLayouts" not in js
    assert '$("searchRow").hidden = viewMode !== "search"' in js
    assert 'nPages + " page"' not in js
    assert "required" in html
    assert "Optional" not in html
    assert 'id="deviceSelect"' not in html
    assert '$("deviceSelect")' not in js
    assert "function deviceIdFromPlatform" in js
    assert 'id="deviceChecks"' not in html
    assert 'id="runC"' not in html
    assert "jwt country" not in js.lower()
    assert "Also at" not in js
    assert "/api/scrape/status" in js
    assert "/api/history/clear" in js
    assert "/api/layouts" in js
    assert "function renderLayouts" in js
    assert "layout-board" in css
    assert "/api/compare" in js
    assert "Row " in js
    assert "IBM Plex Sans" in css
    assert "#FF5A00" in css
    assert "#e84b66" not in css
    assert (ROOT / "tools" / "static" / "vix-logo.png").is_file()
    assert "function canRemovePage" in js
    assert "function ensureAtLeastOnePage" in js
    assert "idx > 0 && pageRows.length > 1" in js
    assert "Add at least one page path" in js
    assert "Each page needs a path" in js
    assert "function firstEmptyPageIndex" in js
    assert "icon-search" in html
    assert "btnDeleteAllHistory" in html
    assert "M4 7h16" in js
    # First page has no remove control; extra pages get a trash icon
    assert "page-remove-slot" in js
    assert "btn-remove" in js
    # Progress is hidden unless status is running
    assert 'status.status !== "running"' in js
    # Section order: intro, pages, auth, device, scrape, progress, comparison
    order = [
        html.find('class="intro"'),
        html.find('id="pages"'),
        html.find('id="credentials"'),
        html.find('id="devices"'),
        html.find('id="btnScrape"'),
        html.find('id="progressWrap"'),
        html.find('id="comparison"'),
    ]
    assert all(i >= 0 for i in order)
    assert order == sorted(order)


def test_first_page_not_removable_and_at_least_one():
    js = (ROOT / "tools" / "static" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "tools" / "static" / "index.html").read_text(encoding="utf-8")
    assert "function canRemovePage" in js
    assert "idx > 0 && pageRows.length > 1" in js
    assert "function ensureAtLeastOnePage" in js
    assert "if (!pageRows.length)" in js
    assert "Add at least one page path" in js
    assert "Each page needs a path" in js
    assert "function firstEmptyPageIndex" in js
    assert "page_details: pages" in js
    assert "At least one page is required" in html
    # First row never renders a remove button
    assert "canRemovePage(idx)" in js
    assert "page-remove-slot" in js


def test_empty_pages_rejected_before_scrape():
    from vix_scraper.errors import ScraperError

    mod = _load_lookup()
    state = type("DummyState", (), {"run_id": None, "reload": lambda *a, **k: None})()
    with pytest.raises(ScraperError, match="at least one page"):
        mod.start_scrape_from_ui(
            {
                "auth_token": "dummy-token",
                "x_vix_user_token": "user-token",
                "installation_id": "install-1",
                "devices": ["web"],
                "pages": [],
            },
            state,
        )
    with pytest.raises(ScraperError, match="at least one page"):
        mod.start_scrape_from_ui(
            {
                "auth_token": "dummy-token",
                "x_vix_user_token": "user-token",
                "installation_id": "install-1",
                "devices": ["web"],
                "pages": ["", "  "],
            },
            state,
        )
