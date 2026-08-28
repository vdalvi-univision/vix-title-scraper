"""Probe layout display length vs catalog. Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile, jwt_safe_claims
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text

ROOT = Path(__file__).resolve().parents[1]


def _client() -> GraphQLClient:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(ROOT / ".env")
    local = ROOT / ".env.local"
    if local.is_file():
        load_dotenv(local, override=True)
    from vix_scraper.auth import is_jwt_expired, jwt_country

    for env_name in ("AUTH_TOKEN", "AUTH_TOKEN_AUTH0", "AUTH_TOKEN_WC"):
        raw = (os.getenv(env_name) or "").strip()
        print(
            "env",
            env_name,
            "present",
            bool(raw),
            "expired",
            is_jwt_expired(raw) if raw else None,
            "country",
            jwt_country(raw) if raw else "",
        )
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=60,
        retries=1,
        device_type="mobile",
        platform="ios",
        app_version="5.0.0",
        country="MX",
        accept_language="es-MX,es;q=0.9",
        user_agent="ViX/5.0.0 (iPhone; iOS 18.0)",
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
    )
    apply_auth_profile(cfg, "default")
    meta = jwt_safe_claims(cfg.auth_token)
    print(
        "auth",
        {
            "present": meta.get("present"),
            "expired": meta.get("expired"),
            "country": meta.get("country"),
            "user_token": bool(cfg.x_vix_user_token),
        },
    )
    return GraphQLClient(cfg)


INTROSPECT = """
query($n: String!) {
  __type(name: $n) {
    name
    kind
    fields {
      name
      args { name defaultValue type { kind name ofType { kind name } } }
      type { kind name ofType { kind name ofType { kind name } } }
    }
    inputFields {
      name
      defaultValue
      type { kind name ofType { kind name } }
    }
    possibleTypes { name }
  }
}
"""

QUERY_FIELDS = """
query {
  __type(name: "Query") {
    fields {
      name
      args { name type { kind name ofType { kind name } } }
    }
  }
}
"""

CANDIDATE_FIELDS = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      edges {
        node {
          __typename
          ... on UiVideoCarousel {
            id
            title
            treatment
            isPlaylist
            collectionId
            displayLimit
            maxItems
            visibleCount
            preview
            itemLimit
            contentLimit
            maxVisible
            displayCount
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage hasPreviousPage startCursor endCursor itemCount currentPage pageCount }
            }
          }
        }
      }
    }
  }
}
"""

RAILS = """
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    uiModules(pagination: $uiModulesPagination) {
      totalCount
      pageInfo { hasNextPage endCursor itemCount }
      edges {
        cursor
        node {
          __typename
          moduleType
          trackingMetadataJson
          ... on UiVideoCarousel {
            id
            title
            treatment
            isPlaylist
            collectionId
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage endCursor itemCount currentPage pageCount }
              edges {
                cursor
                node {
                  __typename
                  ... on UiVideoCard { id title video { title mcpId } }
                  ... on UiVideoCarouselItem { id title video { title mcpId } }
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

COLLECTION_CANDIDATES = [
    """query($id: ID!) { collection(id: $id) { __typename id totalCount title name } }""",
    """query($id: ID!) { contentCollection(id: $id) { __typename id totalCount title } }""",
    """query($id: ID!) { videoCollection(id: $id) { __typename id totalCount title } }""",
    """query($id: ID!) { collectionById(id: $id) { __typename id totalCount } }""",
]


def field_names(data: dict) -> list[str]:
    return [f["name"] for f in (data.get("fields") or [])]


def main() -> int:
    client = _client()

    print("\n==== PaginationParams")
    payload = client.execute(INTROSPECT, {"n": "PaginationParams"}, allow_errors=True)
    print(" introspect keys", list((payload.get("data") or {}).keys()), "top", list(payload.keys()), "errs", graphql_error_messages(payload)[:4])
    err = (payload.get("data") or {}).get("error")
    if err:
        print(" data.error", str(err)[:300])
    t = ((payload.get("data") or {}).get("__type")) or {}
    print("kind", t.get("kind"), "name", t.get("name"))
    for f in t.get("inputFields") or []:
        print(" input", f["name"], "default", f.get("defaultValue"), "type", f.get("type"))

    print("\n==== PageInfo")
    payload = client.execute(INTROSPECT, {"n": "PageInfo"}, allow_errors=True)
    t = ((payload.get("data") or {}).get("__type")) or {}
    print("fields", field_names(t))

    print("\n==== UiVideoCarousel")
    payload = client.execute(INTROSPECT, {"n": "UiVideoCarousel"}, allow_errors=True)
    t = ((payload.get("data") or {}).get("__type")) or {}
    print("fields", field_names(t))
    for f in t.get("fields") or []:
        if f["name"] in (
            "contents",
            "collectionId",
            "treatment",
            "isPlaylist",
            "displayLimit",
            "maxItems",
            "visibleCount",
            "preview",
            "itemLimit",
        ):
            print(" field", f["name"], "args", f.get("args"), "type", f.get("type"))

    print("\n==== Query collection-ish fields")
    payload = client.execute(QUERY_FIELDS, allow_errors=True)
    qfields = ((payload.get("data") or {}).get("__type") or {}).get("fields") or []
    for f in qfields:
        name = f["name"].lower()
        if any(k in name for k in ("collection", "contentlist", "layout", "module", "carousel")):
            print(" Query.", f["name"], "args", [a["name"] for a in (f.get("args") or [])])

    print("\n==== extra carousel fields (expect GraphQL errors for unknown)")
    payload = client.execute(
        CANDIDATE_FIELDS,
        {
            "urlPath": "/ondemandplus",
            "uiModulesPagination": {"first": 3},
            "contentPagination": {},
        },
        allow_errors=True,
    )
    for msg in graphql_error_messages(payload)[:20]:
        print(" ", msg[:220])

    interesting = ("cine", "nuevo")
    after = None
    seen = []
    while True:
        pag = {"first": 2}
        if after:
            pag["after"] = after
        payload = client.execute(
            RAILS,
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": pag,
                "contentPagination": {},
            },
            allow_errors=True,
        )
        if after is None:
            print(
                " first page errs",
                graphql_error_messages(payload)[:4],
                "uiPage",
                ((payload.get("data") or {}).get("uiPage") is not None),
            )
        conn = (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules")) or {}
        edges = conn.get("edges") or []
        if after is None:
            print(
                " modules total",
                conn.get("totalCount"),
                "edges",
                len(edges),
                "titles",
                [first_text((e or {}).get("node", {}).get("title")) for e in edges[:5]],
            )
        if not edges:
            break
        for edge in edges:
            node = (edge or {}).get("node") or {}
            title = first_text(node.get("title")).lower()
            if any(k in title for k in interesting):
                seen.append(node)
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            break
        after = info.get("endCursor")
        if len(seen) >= 2:
            break

    print("\n==== rails with empty contentPagination")
    for node in seen:
        contents = node.get("contents") or {}
        info = contents.get("pageInfo") or {}
        tracking = node.get("trackingMetadataJson")
        edges = contents.get("edges") or []
        titles = []
        for ed in edges:
            n = (ed or {}).get("node") or {}
            v = n.get("video") if isinstance(n.get("video"), dict) else {}
            titles.append(first_text(v.get("title"), n.get("title")))
        print(
            json.dumps(
                {
                    "title": node.get("title"),
                    "id": node.get("id"),
                    "treatment": node.get("treatment"),
                    "isPlaylist": node.get("isPlaylist"),
                    "collectionId": node.get("collectionId"),
                    "contents_totalCount": contents.get("totalCount"),
                    "pageInfo": info,
                    "edges": len(edges),
                    "first_title": titles[0] if titles else None,
                    "last_title": titles[-1] if titles else None,
                    "has_radical": any("radical" in (t or "").lower() for t in titles),
                    "tracking_keys": sorted(tracking.keys()) if isinstance(tracking, dict) else type(tracking).__name__,
                    "tracking": tracking if isinstance(tracking, dict) else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

        coll_id = node.get("collectionId")
        if coll_id:
            for q in COLLECTION_CANDIDATES:
                p = client.execute(q, {"id": coll_id}, allow_errors=True)
                errs = graphql_error_messages(p)[:1]
                data = p.get("data") or {}
                print("  collection_query", q.split("{")[1][:40].strip(), "data", data, "err", (errs[:1] or [None])[0])

        # Follow after with empty first vs first omitted
        end = (info or {}).get("endCursor")
        if end:
            p2 = client.execute(
                RAILS,
                {
                    "urlPath": "/ondemandplus",
                    "uiModulesPagination": {"first": 1, "after": None},
                    "contentPagination": {"after": end},
                },
                allow_errors=True,
            )
            # Find same module by id
            for edge in (
                (((p2.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges") or []
            ):
                n2 = (edge or {}).get("node") or {}
                if n2.get("id") != node.get("id"):
                    continue
                c2 = n2.get("contents") or {}
                e2 = c2.get("edges") or []
                t2 = []
                for ed in e2:
                    nn = (ed or {}).get("node") or {}
                    vv = nn.get("video") if isinstance(nn.get("video"), dict) else {}
                    t2.append(first_text(vv.get("title"), nn.get("title")))
                print(
                    "  after_page",
                    {
                        "totalCount": c2.get("totalCount"),
                        "pageInfo": c2.get("pageInfo"),
                        "edges": len(e2),
                        "titles": t2[:5],
                        "overlap": [t for t in t2 if t in titles],
                    },
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
