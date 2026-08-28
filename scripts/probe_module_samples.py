"""Fetch sample nodes for non-video modules (titles/ids only). Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

QUERY = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      edges {
        node {
          __typename
          moduleType
          trackingMetadataJson
          ... on UiHeroCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { id textTitle heroTarget { __typename ... on VideoContent { id title } } } }
            }
          }
          ... on UiLiveVideoCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges {
                node {
                  __typename id channelId title name textTitle
                  channel { id title name }
                  liveChannel { id title name }
                  video { id title }
                }
              }
            }
          }
          ... on UiRecommendedForYouCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { id title video { id title } } }
            }
          }
          ... on UiContinueWatchingCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { __typename id title video { id title videoType } } }
            }
          }
          ... on UiWatchlistCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { __typename id title video { id title videoType } } }
            }
          }
          ... on UiRecentChannelsCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { __typename id channelId title name textTitle } }
            }
          }
          ... on UiSportsEventCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges {
                node {
                  __typename id sportsEventId title name textTitle
                  localTeamName awayTeamName
                }
              }
            }
          }
          ... on UiPageCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { id name urlPath } }
            }
          }
          ... on UiInlinePage {
            id ctaText ctaUrlPath title textTitle
          }
          ... on UiMixedContentCarousel {
            id title
            contents(pagination: $contentPagination) {
              edges { node { __typename ... on UiVideoCard { id title video { id title } } } }
            }
          }
        }
      }
    }
  }
}
"""


def summarize(path: str, client: GraphQLClient) -> None:
    payload = client.execute(
        QUERY,
        {
            "urlPath": path,
            "uiModulesPagination": {"first": 12},
            "contentPagination": {"first": 3},
        },
        allow_errors=True,
    )
    errs = graphql_error_messages(payload)
    if errs:
        print(path, "errors:", errs[:5])
    edges = (
        (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
        or []
    )
    print("====", path, "modules", len(edges))
    for edge in edges:
        node = (edge or {}).get("node") or {}
        tracking = node.get("trackingMetadataJson") or {}
        title = (
            node.get("title")
            or node.get("textTitle")
            or tracking.get("ui_module_title")
            or node.get("ctaText")
            or ""
        )
        print(f"- {node.get('__typename')} | {node.get('moduleType')} | {title!r}")
        if node.get("ctaUrlPath"):
            print(f"    ctaUrlPath={node.get('ctaUrlPath')}")
        contents = ((node.get("contents") or {}).get("edges")) or []
        for item in contents[:3]:
            n = (item or {}).get("node") or {}
            video = n.get("video") or {}
            hero = n.get("heroTarget") or {}
            channel = n.get("channel") or n.get("liveChannel") or {}
            label = (
                (video.get("title") if isinstance(video, dict) else None)
                or (hero.get("title") if isinstance(hero, dict) else None)
                or n.get("title")
                or n.get("textTitle")
                or n.get("name")
                or channel.get("title")
                or channel.get("name")
                or n.get("channelId")
                or n.get("sportsEventId")
                or n.get("id")
            )
            print(
                f"    item typename={n.get('__typename')} id={n.get('id') or n.get('channelId')} "
                f"label={label!r} keys={sorted(k for k,v in n.items() if v)[:12]}"
            )


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
    summarize("/ondemandplus", client)
    # Persist raw first-page dump for offline extractor work (no tokens in body)
    payload = client.execute(
        QUERY,
        {
            "urlPath": "/ondemandplus",
            "uiModulesPagination": {"first": 12},
            "contentPagination": {"first": 5},
        },
        allow_errors=True,
    )
    out = Path("output/layout_compare/ondemandplus_web_modules_sample.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
