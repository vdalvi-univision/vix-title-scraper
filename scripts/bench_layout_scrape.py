"""Benchmark layout scrape settings (no tokens printed).

Usage:
  .venv\\Scripts\\python.exe scripts\\bench_layout_scrape.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.scraper import TitleScraper

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "layout_compare" / "perf_bench.json"


def _run(label: str, *, query_file: Path, page_size: int, module_page_size: int) -> dict:
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint=PRODUCTION_ENDPOINT,
        query_file=query_file,
        page_size=page_size,
        module_page_size=module_page_size,
        timeout=60,
        retries=2,
        platform="web",
        device_type="desktop",
        download_images=False,
        deduplicate=False,
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, "default")
    if not cfg.auth_token:
        apply_auth_profile(cfg, "wc")
    if not cfg.auth_token:
        return {"label": label, "error": "no auth token in env"}

    started = time.perf_counter()
    rows = TitleScraper(cfg).scrape()
    elapsed = round(time.perf_counter() - started, 3)
    by_y = {}
    for r in rows:
        by_y.setdefault(r.carousel_y, []).append(r)
    first5 = []
    for y in sorted(by_y)[:5]:
        items = sorted(by_y[y], key=lambda x: x.carousel_x)
        first5.append(
            {
                "y": y,
                "row": items[0].row_title,
                "first": items[0].title,
                "n": len(items),
            }
        )
    return {
        "label": label,
        "seconds": elapsed,
        "rows": len(rows),
        "rails": len(by_y),
        "query": query_file.name,
        "page_size": page_size,
        "module_page_size": module_page_size,
        "first_5_rows": first5,
    }


def main() -> None:
    load_dotenv(ROOT / ".env")
    env_local = ROOT / ".env.local"
    if env_local.is_file():
        load_dotenv(env_local)

    layout_q = ROOT / "queries" / "layout.graphql"
    full_q = ROOT / "queries" / "request.graphql"
    results = []
    # Correctness-first module_page_size=1; compare fat vs lean query + content page size.
    if layout_q.is_file():
        results.append(
            _run("layout_p100_m1", query_file=layout_q, page_size=100, module_page_size=1)
        )
    results.append(_run("full_p50_m1", query_file=full_q, page_size=50, module_page_size=1))
    if layout_q.is_file():
        results.append(
            _run("layout_p50_m1", query_file=layout_q, page_size=50, module_page_size=1)
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
