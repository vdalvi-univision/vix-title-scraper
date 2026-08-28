"""Re-scrape ODP+WC with layout query, promote latest, print CW/gol lookup. Never prints tokens."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

from vix_scraper.auth import resolve_auth_profile
from vix_scraper.config import load_dotenv
from vix_scraper.layout_compare import (
    create_run_dir,
    run_batch_scrape,
    sync_latest,
    upsert_history_entry,
)
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k)
    load_dotenv(root / ".env")
    if (root / ".env.local").is_file():
        load_dotenv(root / ".env.local", override=True)

    creds = resolve_auth_profile("default")
    layout_dir = root / "output" / "layout_compare"
    run_id, run_dir = create_run_dir(layout_dir)
    pages = ["/ondemandplus", "/ondemandpluswc"]
    query = root / "queries" / "layout.graphql"
    cfg = ScrapeConfig(
        url_path=pages[0],
        url_paths=pages,
        endpoint=PRODUCTION_ENDPOINT,
        query_file=query if query.is_file() else root / "queries" / "request.graphql",
        auth_token=creds.auth_token,
        x_vix_user_token=creds.x_vix_user_token,
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        platform="web",
        device_type="desktop",
        user_agent=os.getenv("VIX_USER_AGENT"),
        output_dir=run_dir,
        module_page_size=1,
        timeout=60,
        retries=3,
        deduplicate=False,
        download_images=False,
        auth_profile="default",
        auth_profile_map={p: "default" for p in pages},
        extra_headers={"Accept-Language": os.getenv("VIX_ACCEPT_LANGUAGE") or "es-MX,es;q=0.9"},
    )
    print(f"run_id={run_id} auth={bool(creds.auth_token)} user={bool(creds.x_vix_user_token)}")
    result = run_batch_scrape(cfg)
    from vix_scraper.layout_compare import list_run_files, load_scrape_meta

    meta = load_scrape_meta(run_dir) or {}
    ok = [o for o in result.outcomes if o.status == "ok"]
    failed = [o for o in result.outcomes if o.status != "ok"]
    batch_status = meta.get("status") or ("ok" if not failed and ok else "failed")
    row_counts = meta.get("row_counts") or {o.url_path: len(o.titles) for o in result.outcomes}
    print(f"status={batch_status} row_counts={row_counts} notes={result.notes[:3]}")
    for o in failed:
        print("ERR", o.url_path, o.message[:180])

    page_csvs = list(run_dir.glob("*_titles.csv"))
    if batch_status in ("ok", "partial") and page_csvs:
        entry = {
            "run_id": run_id,
            "ran_at": meta.get("ran_at"),
            "ran_at_local": meta.get("ran_at_local") or "",
            "pages": pages,
            "status": batch_status,
            "row_counts": row_counts,
            "duration_seconds": meta.get("duration_seconds"),
            "dir": f"runs/{run_id}",
            "files": [f["name"] for f in list_run_files(run_dir)],
        }
        upsert_history_entry(layout_dir, entry)
        sync_latest(layout_dir, run_id, run_dir)
        print(f"promoted latest={run_id}")
    else:
        print("NOT promoting latest (scrape failed or empty)")

    # CW + gol/paraiso lookup from combined
    combined = layout_dir / "latest" / "combined_titles.csv"
    if not combined.is_file():
        combined = layout_dir / "combined_titles.csv"
    cw = 0
    hits = []
    import csv

    with combined.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rt = row.get("row_title") or ""
            title = row.get("title") or ""
            if rt == "Seguir viendo" or row.get("module_type") == "CONTINUE_WATCHING_CAROUSEL":
                cw += 1
                print(
                    "CW_ROW",
                    row.get("page_url_path"),
                    "y=",
                    row.get("carousel_y"),
                    "x=",
                    row.get("carousel_x"),
                    "title=",
                    title,
                )
            ntitle = norm(title)
            if re.search(r"gol|paraiso", ntitle):
                hits.append(
                    (
                        row.get("page_url_path"),
                        rt,
                        row.get("carousel_y"),
                        row.get("carousel_x"),
                        title,
                    )
                )
    print(f"cw_rows={cw} gol_paraiso_hits={len(hits)}")
    for h in hits[:20]:
        print("HIT", h)


if __name__ == "__main__":
    main()
