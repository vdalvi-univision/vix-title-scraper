"""Validate CW selection sets against production. Never prints tokens."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

CANDIDATES = {
    "ui_video_card_only": """
      ... on UiContinueWatchingCarousel {
        id title
        contents(pagination: $contentPagination) {
          totalCount
          edges {
            node {
              __typename
              ... on UiVideoCard {
                id title
                video { id title videoType }
              }
            }
          }
        }
      }
    """,
    "bare_fields": """
      ... on UiContinueWatchingCarousel {
        id title
        contents(pagination: $contentPagination) {
          totalCount
          edges {
            node {
              __typename
              id
              title
              textTitle
              video { id title videoType mcpId }
            }
          }
        }
      }
    """,
    "card_plus_bare": """
      ... on UiContinueWatchingCarousel {
        id title
        contents(pagination: $contentPagination) {
          totalCount
          edges {
            node {
              __typename
              ... on UiVideoCard {
                id title
                video { id title videoType mcpId }
              }
              id
              title
              video { id title videoType mcpId }
            }
          }
        }
      }
    """,
    "video_carousel_item": """
      ... on UiContinueWatchingCarousel {
        id title
        contents(pagination: $contentPagination) {
          totalCount
          edges {
            node {
              __typename
              ... on UiVideoCard {
                id title
                video { id title videoType }
              }
              ... on UiVideoCarouselItem {
                id title
                video { id title videoType }
              }
            }
          }
        }
      }
    """,
}


def wrap(inner: str) -> str:
    return f"""
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {{
  uiPage(urlPath: $urlPath) {{
    uiModules(pagination: $uiModulesPagination) {{
      edges {{
        node {{
          __typename
          {inner}
        }}
      }}
    }}
  }}
}}
"""


def main() -> None:
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=40,
        retries=0,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, "default")
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
    # Prefer not sending an expired user token for this validation.
    cfg.x_vix_user_token = os.getenv("X_VIX_USER_TOKEN")
    client = GraphQLClient(cfg)
    for name, inner in CANDIDATES.items():
        payload = client.execute(
            wrap(inner),
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": {"first": 6},
                "contentPagination": {"first": 10},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)
        cw = None
        for e in (
            (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
            or []
        ):
            n = (e or {}).get("node") or {}
            if n.get("__typename") == "UiContinueWatchingCarousel" or "contents" in n:
                if "contents" in n:
                    cw = n
                    break
        contents = (cw or {}).get("contents") or {}
        print(
            f"{name}: ok_data={payload.get('data') is not None} "
            f"total={contents.get('totalCount')} edges={len(contents.get('edges') or [])} "
            f"errs={errs[:2]}"
        )


if __name__ == "__main__":
    main()
