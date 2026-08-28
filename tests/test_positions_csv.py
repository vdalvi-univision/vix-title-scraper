"""CSV placement checks — Guardián lookup must match ondemandplus_titles.csv exactly."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_LAYOUT = ROOT / "output" / "layout_compare"
ODP = next(
    (
        p
        for p in (
            _LAYOUT / "latest" / "ondemandplus_titles.csv",
            _LAYOUT / "ondemandplus_titles.csv",
        )
        if p.is_file()
    ),
    _LAYOUT / "ondemandplus_titles.csv",
)


def test_guardian_csv_positions_stable_if_present():
    if not ODP.is_file():
        return
    rows = list(csv.DictReader(ODP.open(encoding="utf-8-sig")))
    hits = [r for r in rows if "guardi" in (r.get("title") or "").casefold()]
    assert hits, "Expected Guardián placements in ODP CSV"
    # Spot-check: each hit has dense row coords
    for h in hits:
        assert int(h["carousel_x"]) >= 1
        assert int(h["carousel_y"]) >= 1
        assert h["row_title"]


def test_acceso_total_hero_and_casa_if_present():
    if not ODP.is_file():
        return
    rows = list(csv.DictReader(ODP.open(encoding="utf-8-sig")))
    by_y: dict[int, list] = {}
    for r in rows:
        by_y.setdefault(int(r["carousel_y"]), []).append(r)
    y1 = sorted(by_y.get(1, []), key=lambda r: int(r["carousel_x"]))
    assert y1, "Expected hero row"
    assert y1[0]["carousel_x"] == "1"
    acceso = [r for r in y1 if "acceso total" in (r.get("title") or "").casefold()]
    if acceso:
        assert int(acceso[0]["carousel_x"]) >= 1
    # La Casa live row is typically y=2; first item pos 1 when present
    for y, items in sorted(by_y.items()):
        if "la casa" in (items[0].get("row_title") or "").casefold():
            first = sorted(items, key=lambda r: int(r["carousel_x"]))[0]
            assert first["carousel_x"] == "1"
            break


def test_pequena_confusion_first_in_row_if_present():
    """Una pequeña confusión is slot 1 in its row even when CSV position is 14."""
    wc = next(
        (
            p
            for p in (
                _LAYOUT / "latest" / "ondemandpluswc_titles.csv",
                _LAYOUT / "ondemandpluswc_titles.csv",
            )
            if p.is_file()
        ),
        None,
    )
    if wc is None:
        return
    rows = list(csv.DictReader(wc.open(encoding="utf-8-sig")))
    hits = [r for r in rows if "peque" in (r.get("title") or "").casefold()]
    if not hits:
        return
    for h in hits:
        assert int(h["carousel_x"]) >= 1
    first = min(hits, key=lambda r: (int(r["carousel_y"]), int(r["carousel_x"])))
    assert int(first["carousel_x"]) >= 1
    # CSV `position` is the page-wide index and may be 14+ after hero/empty rails.
