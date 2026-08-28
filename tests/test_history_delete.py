"""History delete updates runs/ + history.json + latest pointer."""

from __future__ import annotations

import json
from pathlib import Path

from vix_scraper.layout_compare import (
    LATEST_DIRNAME,
    create_run_dir,
    delete_all_history,
    delete_history_run,
    load_history,
    sync_latest,
    upsert_history_entry,
)


def test_delete_history_run_promotes_next_latest(tmp_path: Path) -> None:
    layout = tmp_path / "layout_compare"
    rid_old, dir_old = create_run_dir(layout, "20260101_000000_aaaaaa")
    (dir_old / "ondemandplus_titles.csv").write_text("page_url_path,title\n/ondemandplus,A\n", encoding="utf-8")
    upsert_history_entry(
        layout,
        {
            "run_id": rid_old,
            "ran_at_local": "old",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1},
            "dir": f"runs/{rid_old}",
        },
    )
    sync_latest(layout, rid_old, dir_old)

    rid_new, dir_new = create_run_dir(layout, "20260102_000000_bbbbbb")
    (dir_new / "ondemandplus_titles.csv").write_text("page_url_path,title\n/ondemandplus,B\n", encoding="utf-8")
    upsert_history_entry(
        layout,
        {
            "run_id": rid_new,
            "ran_at_local": "new",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1},
            "dir": f"runs/{rid_new}",
        },
    )
    sync_latest(layout, rid_new, dir_new)

    result = delete_history_run(layout, rid_new)
    assert result["ok"] is True
    assert result["deleted_run_id"] == rid_new
    assert result["latest_run_id"] == rid_old
    assert not dir_new.exists()
    assert dir_old.exists()
    history = load_history(layout)
    assert history["latest_run_id"] == rid_old
    assert [r["run_id"] for r in history["runs"]] == [rid_old]
    pointer = json.loads((layout / "latest.json").read_text(encoding="utf-8"))
    assert pointer["run_id"] == rid_old
    assert (layout / "latest" / "ondemandplus_titles.csv").is_file()


def test_delete_all_history_clears_runs_history_and_latest(tmp_path: Path) -> None:
    layout = tmp_path / "layout_compare"
    rid_a, dir_a = create_run_dir(layout, "20260101_000000_aaaaaa")
    (dir_a / "ondemandplus_titles.csv").write_text(
        "page_url_path,title\n/ondemandplus,A\n", encoding="utf-8"
    )
    upsert_history_entry(
        layout,
        {
            "run_id": rid_a,
            "ran_at_local": "old",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1},
            "dir": f"runs/{rid_a}",
        },
    )
    sync_latest(layout, rid_a, dir_a)

    rid_b, dir_b = create_run_dir(layout, "20260102_000000_bbbbbb")
    (dir_b / "ondemandplus_titles.csv").write_text(
        "page_url_path,title\n/ondemandplus,B\n", encoding="utf-8"
    )
    upsert_history_entry(
        layout,
        {
            "run_id": rid_b,
            "ran_at_local": "new",
            "pages": ["/ondemandplus"],
            "status": "ok",
            "row_counts": {"/ondemandplus": 1},
            "dir": f"runs/{rid_b}",
        },
    )
    sync_latest(layout, rid_b, dir_b)

    result = delete_all_history(layout)
    assert result["ok"] is True
    assert result["deleted_count"] >= 2
    assert result["latest_run_id"] is None
    assert not dir_a.exists()
    assert not dir_b.exists()
    history = load_history(layout)
    assert history["latest_run_id"] is None
    assert history["runs"] == []
    assert not (layout / LATEST_DIRNAME).exists()
    assert not (layout / "latest.json").exists()


def test_delete_all_history_when_already_empty(tmp_path: Path) -> None:
    layout = tmp_path / "layout_compare"
    layout.mkdir(parents=True)
    result = delete_all_history(layout)
    assert result["ok"] is True
    assert result["deleted_count"] == 0
    history = load_history(layout)
    assert history["runs"] == []
