"""Probe candidate paths for ondemandpluswc (no token printing)."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.layout_compare import discover_path_variants
from vix_scraper.models import STAGING_ENDPOINT, ScrapeConfig


def main() -> None:
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig(endpoint=STAGING_ENDPOINT, timeout=30, retries=1)
    apply_auth_profile(cfg, "default")
    client = GraphQLClient(cfg)

    variants = discover_path_variants("/ondemandpluswc")
    variants += [
        "/ondemandplus/wc",
        "/ondemand-plus-wc",
        "/ondemandplus-worldcup",
        "/worldcup",
        "/mundial",
        "/copa",
        "/copamundial",
        "/deportes",
        "/ondemandplusdeportes",
    ]
    seen: set[str] = set()
    uniq: list[str] = []
    for path in variants:
        if path not in seen:
            seen.add(path)
            uniq.append(path)

    query = (
        "query($urlPath: ID!) { uiPage(urlPath: $urlPath) "
        "{ urlPath pageName uiModules { totalCount } } }"
    )
    print(f"Probing {len(uniq)} paths...")
    for path in uniq:
        payload = client.execute(query, {"urlPath": path}, allow_errors=True)
        page = ((payload.get("data") or {}).get("uiPage"))
        if page:
            modules = (page.get("uiModules") or {}).get("totalCount")
            print(f"FOUND {path} pageName={page.get('pageName')!r} modules={modules}")
        else:
            errs = graphql_error_messages(payload)
            msg = (errs[0] if errs else "null")[:100]
            if "not found" not in msg.lower():
                print(f"OTHER {path} {msg}")

    nav = client.execute(
        Path("queries/navigation.graphql").read_text(encoding="utf-8"),
        allow_errors=True,
    )
    raw = json.dumps(nav)
    hits = sorted(
        set(re.findall(r'"(/[^"]*(?:wc|mundial|world.?cup|copa)[^"]*)"', raw, flags=re.I))
    )
    print("NAV hits:", hits[:50])
    hits2 = sorted(set(re.findall(r'"(/ondemand[^"]*)"', raw)))
    print("ondemand nav paths:", hits2[:40])

    csv_path = Path("output/layout_compare/ondemandplus_titles.csv")
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    print("ondemandplus rows", len(rows), "cols", len(rows[0]) if rows else 0)
    if rows:
        print("cols", list(rows[0].keys()))
        print("hero count", sum(1 for r in rows if r.get("is_hero") == "true"))
        print("max y", max(int(r["carousel_y"]) for r in rows))
        print(
            "first 5:",
            [
                (r["carousel_y"], r["carousel_x"], r["row_title"], r["title"][:40])
                for r in rows[:5]
            ],
        )
        assert "poster_url" not in rows[0]
        print("OK no image cols")


if __name__ == "__main__":
    main()
