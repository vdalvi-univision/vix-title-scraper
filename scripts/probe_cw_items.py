"""Probe Continue Watching item typenames/fields. Never prints tokens."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text

# Introspect CW card type + fetch live items with alternate spreads.
INTROSPECT = """
query($n: String!) {
  __type(name: $n) {
    name
    fields {
      name
      type {
        name
        kind
        ofType {
          name
          kind
          ofType { name kind ofType { name } }
        }
      }
    }
    possibleTypes { name }
  }
}
"""

LIVE = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          __typename
          moduleType
          ... on UiContinueWatchingCarousel {
            id
            title
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor }
              edges {
                node {
                  __typename
                  ... on UiVideoCard {
                    id
                    title
                    video { id title videoType mcpId }
                  }
                  ... on UiContinueWatchingCard {
                    id
                    title
                    textTitle
                    video { id title videoType mcpId }
                    progress
                    percentComplete
                    watchedSeconds
                    remainingSeconds
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def main() -> None:
    load_dotenv()
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint=PRODUCTION_ENDPOINT,
        timeout=60,
        retries=2,
        module_page_size=12,
        app_version="5.0.0",
        device_type="desktop",
        platform="web",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        installation_id=os.getenv("VIX_INSTALLATION_ID")
        or "eae73e7c-bdd0-e756-6823-a44ade5149db",
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    used = "none"
    for profile in ("default", "wc"):
        apply_auth_profile(cfg, profile)
        if cfg.auth_token:
            used = profile
            break
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
        used = "auth0_env"
    print(f"auth_profile={used} token_present={bool(cfg.auth_token)} user_token={bool(cfg.x_vix_user_token)}")

    client = GraphQLClient(cfg)

    for type_name in (
        "UiContinueWatchingCard",
        "UiContinueWatchingCarousel",
        "UiContinueWatchingItem",
        "ContinueWatchingCard",
    ):
        payload = client.execute(INTROSPECT, {"n": type_name}, allow_errors=True)
        t = ((payload.get("data") or {}).get("__type"))
        if not t:
            print(f"INTROSPECT {type_name}: NOT FOUND")
            continue
        fields = [f["name"] for f in (t.get("fields") or [])]
        print(f"INTROSPECT {type_name}: fields={fields}")
        possibles = t.get("possibleTypes") or []
        if possibles:
            print(f"  possibleTypes={[p.get('name') for p in possibles]}")

    # Also introspect contents edge possible types via carousel field
    carousel_type = client.execute(INTROSPECT, {"n": "UiContinueWatchingCarousel"}, allow_errors=True)
    t = ((carousel_type.get("data") or {}).get("__type")) or {}
    for f in t.get("fields") or []:
        if f.get("name") == "contents":
            print("contents field type:", json.dumps(f.get("type"), ensure_ascii=False)[:500])

    after = None
    found = None
    for page in range(20):
        pag: dict = {"first": 12}
        if after:
            pag["after"] = after
        payload = client.execute(
            LIVE,
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": pag,
                "contentPagination": {"first": 20},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)
        if errs and page == 0:
            print("LIVE errors:", errs[:5])
        conn = (((payload.get("data") or {}).get("uiPage")) or {}).get("uiModules") or {}
        for e in conn.get("edges") or []:
            n = (e or {}).get("node") or {}
            if first_text(n.get("__typename")) == "UiContinueWatchingCarousel":
                found = n
                break
        if found:
            break
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")

    if not found:
        print("NO CW MODULE FOUND")
        return

    contents = found.get("contents") or {}
    edges = contents.get("edges") or []
    print(
        f"CW title={found.get('title')!r} id={found.get('id')} "
        f"totalCount={contents.get('totalCount')} edges={len(edges)}"
    )
    summary = []
    for i, e in enumerate(edges, 1):
        node = (e or {}).get("node") or {}
        v = node.get("video") if isinstance(node.get("video"), dict) else {}
        item = {
            "i": i,
            "typename": node.get("__typename"),
            "id": node.get("id"),
            "title": node.get("title") or node.get("textTitle"),
            "keys": sorted(node.keys()),
            "video_id": v.get("id"),
            "video_title": v.get("title"),
            "video_type": v.get("videoType"),
        }
        summary.append(item)
        print(
            f"item{i}: typename={item['typename']} id={item['id']} "
            f"title={item['title']!r} video_title={item['video_title']!r} keys={item['keys']}"
        )

    out = Path("output/layout_compare/cw_probe.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "auth_profile": used,
                "module_title": found.get("title"),
                "module_id": found.get("id"),
                "totalCount": contents.get("totalCount"),
                "items": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
