"""Validate layout/request graphql still execute. Never prints tokens."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text


def main() -> None:
    load_dotenv(Path(".env"))
    local = Path(".env.local")
    if local.is_file():
        load_dotenv(local, override=True)
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=60,
        retries=1,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, "default")
    client = GraphQLClient(cfg)
    for name in ("queries/layout.graphql", "queries/request.graphql"):
        q = Path(name).read_text(encoding="utf-8")
        payload = client.execute(
            q,
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": {"first": 8},
                "contentPagination": {"first": 5},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)[:5]
        cw = None
        for e in (
            (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
            or []
        ):
            n = (e or {}).get("node") or {}
            if first_text(n.get("__typename")) == "UiContinueWatchingCarousel":
                cw = n
                break
        contents = (cw or {}).get("contents") or {}
        items = []
        for ed in contents.get("edges") or []:
            node = (ed or {}).get("node") or {}
            video = node.get("video") if isinstance(node.get("video"), dict) else {}
            items.append(
                {
                    "typename": node.get("__typename"),
                    "title": first_text(node.get("title"), video.get("title")),
                    "id": first_text(node.get("id"), video.get("id")),
                }
            )
        print(
            f"{name}: errs={errs} cw_total={contents.get('totalCount')} "
            f"cw_edges={len(contents.get('edges') or [])} items={items[:5]} "
            f"user_token_sent={bool(cfg.x_vix_user_token)}"
        )


if __name__ == "__main__":
    main()
