"""Iteratively discover fields for new carousel types via GraphQL errors."""

from __future__ import annotations

import os
import re
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

BASE = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      edges {
        node {
          __typename
          moduleType
          trackingMetadataJson
          %FRAG%
        }
      }
    }
  }
}
"""


def run(client: GraphQLClient, frag: str) -> list[str]:
    q = BASE.replace("%FRAG%", frag)
    payload = client.execute(
        q,
        {
            "urlPath": "/ondemandplus",
            "uiModulesPagination": {"first": 20},
            "contentPagination": {"first": 3},
        },
        allow_errors=True,
    )
    return graphql_error_messages(payload)


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
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, "wc")
    client = GraphQLClient(cfg)

    candidates = {
        "UiContinueWatchingCarousel": """
          ... on UiContinueWatchingCarousel {
            id trackingId title treatment
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor }
              edges {
                cursor
                node {
                  __typename
                  ... on UiVideoCard {
                    id title
                    video { id title mcpId videoType }
                  }
                }
              }
            }
          }
        """,
        "UiWatchlistCarousel": """
          ... on UiWatchlistCarousel {
            id trackingId title treatment
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor }
              edges {
                cursor
                node {
                  __typename
                  ... on UiVideoCard {
                    id title
                    video { id title mcpId videoType }
                  }
                }
              }
            }
          }
        """,
        "UiRecentChannelsCarousel": """
          ... on UiRecentChannelsCarousel {
            id trackingId title treatment
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor }
              edges {
                cursor
                node {
                  __typename
                  id
                  channelId
                  title
                  name
                  textTitle
                }
              }
            }
          }
        """,
        "UiLiveVideoCarousel_rich": """
          ... on UiLiveVideoCarousel {
            id trackingId title treatment
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor }
              edges {
                cursor
                node {
                  __typename
                  id
                  channelId
                  title
                  name
                  textTitle
                  channel { id title name }
                  liveChannel { id title name }
                  video { id title }
                }
              }
            }
          }
        """,
        "UiSportsEventCarousel_rich": """
          ... on UiSportsEventCarousel {
            id trackingId title treatment sportId leagueId isLive
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor }
              edges {
                cursor
                node {
                  __typename
                  id
                  sportsEventId
                  title
                  name
                  textTitle
                  localTeamName
                  awayTeamName
                  clickTrackingJson
                }
              }
            }
          }
        """,
        "UiInlinePage_rich": """
          ... on UiInlinePage {
            id trackingId ctaText ctaUrlPath title textTitle
            clickTrackingJson
          }
        """,
    }

    for name, frag in candidates.items():
        errs = run(client, frag)
        if not errs:
            print(f"{name}: OK")
            continue
        # Summarize field errors
        interesting = []
        for e in errs[:8]:
            interesting.append(e[:220])
        print(f"{name}: FAIL")
        for e in interesting:
            print(" ", e)


if __name__ == "__main__":
    main()
