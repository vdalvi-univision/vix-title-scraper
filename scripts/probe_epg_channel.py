"""Probe EpgChannel fields on heroTarget. Never prints tokens."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

FRAGS = [
    "... on EpgChannel { id title name callSign }",
    "... on EpgChannel { id title }",
    "... on EpgChannel { id name }",
]


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=40,
        retries=1,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
    )
    apply_auth_profile(cfg, "wc")
    client = GraphQLClient(cfg)
    for frag in FRAGS:
        q = f"""
        query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {{
          uiPage(urlPath: $urlPath) {{
            uiModules(pagination: $uiModulesPagination) {{
              edges {{
                node {{
                  ... on UiHeroCarousel {{
                    contents(pagination: $contentPagination) {{
                      edges {{
                        node {{
                          id
                          heroTarget {{
                            __typename
                            {frag}
                          }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        """
        payload = client.execute(
            q,
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": {"first": 1},
                "contentPagination": {"first": 2},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)
        if errs:
            print("FAIL", frag, "->", errs[0][:180])
            continue
        edges = (
            (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
            or []
        )
        node = ((edges[0] or {}).get("node") or {}) if edges else {}
        items = (((node.get("contents") or {}).get("edges")) or [])
        first = ((items[0] or {}).get("node") or {}).get("heroTarget") if items else None
        print("OK", frag, "->", first)


if __name__ == "__main__":
    main()
