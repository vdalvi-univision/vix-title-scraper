"""Probe Recommended For You item typenames/fields. Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

INTROSPECT = """
query($n: String!) {
  __type(name: $n) {
    name
    kind
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

TYPED = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      edges {
        node {
          __typename
          moduleType
          ... on UiRecommendedForYouCarousel {
            id
            title
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor itemCount }
              edges {
                cursor
                node {
                  __typename
                  ... on UiVideoCard {
                    id
                    title
                    video { id title videoType mcpId }
                  }
                  ... on UiVideoCarouselItem {
                    id
                    title
                    video { id title videoType mcpId }
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

BARE = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      edges {
        node {
          __typename
          ... on UiRecommendedForYouCarousel {
            id
            title
            contents(pagination: $contentPagination) {
              totalCount
              edges {
                node {
                  __typename
                  id
                  title
                  video { id title videoType mcpId }
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


def unwrap_named(type_obj: dict | None) -> str | None:
    cur = type_obj
    while isinstance(cur, dict):
        if cur.get("name"):
            return cur["name"]
        cur = cur.get("ofType")
    return None


def extract_reco(payload: dict) -> dict:
    reco = None
    for e in (
        (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
        or []
    ):
        n = (e or {}).get("node") or {}
        if n.get("__typename") == "UiRecommendedForYouCarousel":
            reco = n
            break
    contents = (reco or {}).get("contents") or {}
    edges = contents.get("edges") or []
    items = []
    for ed in edges[:15]:
        node = (ed or {}).get("node") or {}
        v = node.get("video") or {}
        items.append(
            {
                "typename": node.get("__typename"),
                "id": node.get("id"),
                "title": node.get("title"),
                "video_title": v.get("title"),
                "video_id": v.get("id"),
                "keys": sorted(node.keys()),
            }
        )
    return {
        "totalCount": contents.get("totalCount"),
        "edges": len(edges),
        "pageInfo": contents.get("pageInfo"),
        "items": items,
        "errors": graphql_error_messages(payload)[:6],
    }


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
    print(
        f"auth_profile={used} auth_present={bool(cfg.auth_token)} "
        f"user_token_present={bool(cfg.x_vix_user_token)}"
    )

    client = GraphQLClient(cfg)
    out: dict = {"auth_profile": used}

    for tname in (
        "UiRecommendedForYouCarousel",
        "UiRecommendedForYouCard",
        "UiRecommendedCard",
        "RecommendedForYouCard",
        "UiVideoCarouselItem",
        "UiVideoCard",
    ):
        p = client.execute(INTROSPECT, {"n": tname}, allow_errors=True)
        t = ((p.get("data") or {}).get("__type"))
        if not t:
            print(f"introspect {tname}: MISSING")
            out[tname] = None
            continue
        fields = [f["name"] for f in (t.get("fields") or [])]
        pts = [x["name"] for x in (t.get("possibleTypes") or [])]
        contents_field = next(
            (f for f in (t.get("fields") or []) if f["name"] == "contents"), None
        )
        contents_type = unwrap_named((contents_field or {}).get("type"))
        print(
            f"introspect {tname}: kind={t.get('kind')} "
            f"fields={fields[:20]} possibleTypes={pts} contents-> {contents_type}"
        )
        out[tname] = {
            "kind": t.get("kind"),
            "fields": fields,
            "possibleTypes": pts,
            "contents_type": contents_type,
        }

    # Follow contents connection → edges → node
    carousel = out.get("UiRecommendedForYouCarousel") or {}
    conn_name = carousel.get("contents_type")
    if conn_name:
        p = client.execute(INTROSPECT, {"n": conn_name}, allow_errors=True)
        t = ((p.get("data") or {}).get("__type")) or {}
        edge_field = next((f for f in (t.get("fields") or []) if f["name"] == "edges"), None)
        edge_type = unwrap_named((edge_field or {}).get("type"))
        print(f"connection {conn_name}: edges->{edge_type}")
        out["contents_connection"] = {
            "name": conn_name,
            "fields": [f["name"] for f in (t.get("fields") or [])],
            "edge_type": edge_type,
        }
        if edge_type:
            p2 = client.execute(INTROSPECT, {"n": edge_type}, allow_errors=True)
            t2 = ((p2.get("data") or {}).get("__type")) or {}
            node_field = next((f for f in (t2.get("fields") or []) if f["name"] == "node"), None)
            node_type = unwrap_named((node_field or {}).get("type"))
            print(f"edge {edge_type}: node->{node_type}")
            out["edge"] = {"name": edge_type, "node_type": node_type}
            if node_type:
                p3 = client.execute(INTROSPECT, {"n": node_type}, allow_errors=True)
                t3 = ((p3.get("data") or {}).get("__type")) or {}
                print(
                    f"node {node_type}: kind={t3.get('kind')} "
                    f"possibleTypes={[x['name'] for x in (t3.get('possibleTypes') or [])]} "
                    f"fields={[f['name'] for f in (t3.get('fields') or [])][:25]}"
                )
                out["node_type"] = {
                    "name": node_type,
                    "kind": t3.get("kind"),
                    "possibleTypes": [x["name"] for x in (t3.get("possibleTypes") or [])],
                    "fields": [f["name"] for f in (t3.get("fields") or [])],
                }

    vars_ = {
        "urlPath": "/ondemandplus",
        "uiModulesPagination": {"first": 10},
        "contentPagination": {"first": 30},
    }
    for label, query in (("typed", TYPED), ("bare", BARE)):
        payload = client.execute(query, vars_, allow_errors=True)
        info = extract_reco(payload)
        out[f"live_{label}"] = info
        print(
            f"{label}: totalCount={info['totalCount']} edges={info['edges']} "
            f"errs={info['errors'][:2]}"
        )
        for it in info["items"][:8]:
            print(
                f"  {it['typename']} | {it['title'] or it['video_title']} | {it['video_id']}"
            )

    dest = Path("output/layout_compare/reco_probe.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
