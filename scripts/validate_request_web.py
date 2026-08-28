"""Validate queries/request.graphql on prod with web headers. Never prints tokens."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.extractor import TitleExtractor
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig


def main() -> int:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    query = Path("queries/request.graphql").read_text(encoding="utf-8")
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=90,
        retries=1,
        device_type=os.getenv("VIX_DEVICE_TYPE", "desktop"),
        platform=os.getenv("VIX_PLATFORM", "web"),
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, "wc")
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
    client = GraphQLClient(cfg)
    payload = client.execute(
        query,
        {
            "urlPath": "/ondemandplus",
            "uiModulesPagination": {"first": 30},
            "contentPagination": {"first": 3},
        },
        allow_errors=True,
    )
    errs = graphql_error_messages(payload)
    print(f"errors={len(errs)}")
    for err in errs[:25]:
        print(" ", err[:240])
    edges = (
        (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
        or []
    )
    print("modules", len(edges))
    null_nodes = sum(1 for e in edges if not (e or {}).get("node"))
    print("null_nodes", null_nodes)
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    seen: dict[str, int] = {}
    for row in rows:
        if row.row_title and row.row_title not in seen:
            seen[row.row_title] = row.carousel_y
    titles = [t for t, _ in sorted(seen.items(), key=lambda kv: kv[1])]
    print("extracted_rows", len(rows))
    print("first_10_row_titles:")
    for t in titles[:10]:
        print(" ", t)
    print("module_types", sorted({r.module_type for r in rows}))
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
