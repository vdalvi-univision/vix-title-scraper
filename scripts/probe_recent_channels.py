"""Find a working UiRecentChannelsCarousel selection set."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

CANDIDATES = [
    """
    ... on UiRecentChannelsCarousel {
      id trackingId title treatment
      contents(pagination: $contentPagination) {
        totalCount pageInfo { hasNextPage endCursor }
        edges { cursor node { __typename id channelId } }
      }
    }
    """,
    """
    ... on UiRecentChannelsCarousel {
      id trackingId title
      contents(pagination: $contentPagination) {
        edges {
          node {
            __typename id channelId
            channel { id title }
            image { link imageRole }
            logoImage { link imageRole }
          }
        }
      }
    }
    """,
    """
    ... on UiRecentChannelsCarousel {
      id trackingId title
      contents(pagination: $contentPagination) {
        edges {
          node {
            ... on UiLiveVideoCard {
              id channelId
              channel { id title }
            }
          }
        }
      }
    }
    """,
]


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=45,
        retries=1,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
    )
    apply_auth_profile(cfg, "wc")
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
    client = GraphQLClient(cfg)

    # Page far enough to include recent channels (~ module 28); use after cursors via large first.
    for i, frag in enumerate(CANDIDATES, 1):
        q = f"""
        query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {{
          uiPage(urlPath: $urlPath) {{
            uiModules(pagination: $uiModulesPagination) {{
              edges {{
                node {{
                  __typename
                  moduleType
                  trackingMetadataJson
                  {frag}
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
                "uiModulesPagination": {"first": 35},
                "contentPagination": {"first": 3},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)
        edges = (
            (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
            or []
        )
        recent = [
            (e.get("node") or {})
            for e in edges
            if ((e.get("node") or {}).get("__typename") == "UiRecentChannelsCarousel")
        ]
        print(f"candidate {i}: errors={len(errs)} modules={len(edges)} recent={len(recent)}")
        if errs:
            print(" ", errs[0][:200])
        if recent:
            node = recent[0]
            tracking = node.get("trackingMetadataJson") or {}
            print(
                "  title",
                node.get("title") or tracking.get("ui_module_title"),
                "contents",
                len((((node.get("contents") or {}).get("edges")) or [])),
            )
            for edge in (((node.get("contents") or {}).get("edges")) or [])[:2]:
                print("  item", (edge or {}).get("node"))


if __name__ == "__main__":
    main()
