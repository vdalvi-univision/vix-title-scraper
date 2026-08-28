"""Re-scrape /ondemandplus (web) and refresh combined+diff with WC file."""

from __future__ import annotations

import csv
import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.config import load_dotenv
from vix_scraper.exporter import CsvExporter
from vix_scraper.layout_compare import write_layout_diff_summary
from vix_scraper.models import PRODUCTION_ENDPOINT, ExportedTitle, ScrapeConfig
from vix_scraper.scraper import TitleScraper


def load_csv(path: Path) -> list[ExportedTitle]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        out: list[ExportedTitle] = []
        for r in reader:
            out.append(
                ExportedTitle(
                    position=int(r.get("position") or 0),
                    title=r.get("title") or "",
                    row_title=r.get("row_title") or "",
                    carousel_x=int(r.get("carousel_x") or 0),
                    carousel_y=int(r.get("carousel_y") or 0),
                    description=r.get("description") or "",
                    date_released=r.get("dateReleased") or "",
                    genres=r.get("genres") or "",
                    content_id=r.get("id") or "",
                    video_type=r.get("videoType") or "",
                    page_url_path=r.get("page_url_path") or "",
                    mcp_id=r.get("mcp_id") or "",
                    headline=r.get("headline") or "",
                    year_released=r.get("year_released") or "",
                    language=r.get("language") or "",
                    is_kids=r.get("is_kids") or "",
                    requires_subscription=r.get("requires_subscription") or "",
                    content_vertical=r.get("content_vertical") or "",
                    keywords=r.get("keywords") or "",
                    badges=r.get("badges") or "",
                    ratings=r.get("ratings") or "",
                    cast=r.get("cast") or "",
                    seasons_count=r.get("seasons_count") or "",
                    episodes_count=r.get("episodes_count") or "",
                    episode_number=r.get("episode_number") or "",
                    duration_seconds=r.get("duration_seconds") or "",
                    module_type=r.get("module_type") or "",
                    module_typename=r.get("module_typename") or "",
                    module_id=r.get("module_id") or "",
                    is_hero=r.get("is_hero") or "",
                    row_size=int(r.get("row_size") or 0),
                    auth_profile=r.get("auth_profile") or "",
                    tracking_json=r.get("tracking_json") or "",
                )
            )
        return out


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    out_dir = Path("output/layout_compare")
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint=PRODUCTION_ENDPOINT,
        query_file=Path("queries/request.graphql"),
        timeout=60,
        retries=2,
        page_size=50,
        module_page_size=1,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
        download_images=False,
    )
    apply_auth_profile(cfg, "wc")
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
    rows = TitleScraper(cfg).scrape()
    CsvExporter(out_dir / "ondemandplus_titles.csv").write(rows)
    print("ondemandplus rows", len(rows))

    seen = {}
    for r in rows:
        if r.row_title and r.row_title not in seen:
            seen[r.row_title] = (r.carousel_y, r.module_type, r.title)
    print("rails", len(seen), "types", sorted({r.module_type for r in rows}))
    for i, (t, meta) in enumerate(sorted(seen.items(), key=lambda kv: int(kv[1][0]))[:12], 1):
        print(f"{i:02d} y={meta[0]} {meta[1]} {t!r} first={meta[2]!r}")

    wc_path = out_dir / "ondemandpluswc_titles.csv"
    variant = load_csv(wc_path) if wc_path.is_file() else []
    CsvExporter(out_dir / "combined_titles.csv").write(rows + variant)
    write_layout_diff_summary(
        rows,
        variant,
        baseline_path="/ondemandplus",
        variant_path="/ondemandpluswc",
        output=out_dir / "layout_diff_summary.md",
    )
    print("combined", len(rows) + len(variant), "wc_source", wc_path.name)


if __name__ == "__main__":
    main()
