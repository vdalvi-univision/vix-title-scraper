"""Fetch CW nodes with untyped fields to learn schema. Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

# Probe field sets incrementally; GraphQL may reject unknown fields.
FIELD_SETS = [
    """
    id title textTitle name
    video { __typename id title videoType mcpId }
    """,
    """
    id title
    video { id title videoType }
    progress
    """,
    """
    id title
    video { id title videoType }
    percentWatched
    """,
    """
    id title
    video { id title videoType }
    watchedPercentage
    """,
    """
    id title
    video { id title videoType }
    heroTarget { __typename ... on VideoContent { id title videoType } }
    """,
    """
    id title
    video { id title videoType }
    image { link }
    """,
]


def build_query(node_fields: str) -> str:
    return f"""
query($urlPath: ID!, $uiModulesPagination: PaginationParams, $contentPagination: PaginationParams) {{
  uiPage(urlPath: $urlPath) {{
    uiModules(pagination: $uiModulesPagination) {{
      pageInfo {{ hasNextPage endCursor }}
      edges {{
        node {{
          __typename
          moduleType
          ... on UiContinueWatchingCarousel {{
            id
            title
            contents(pagination: $contentPagination) {{
              totalCount
              edges {{
                node {{
                  __typename
                  {node_fields}
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


def main() -> None:
    load_dotenv()
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
    apply_auth_profile(cfg, "default")
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
    client = GraphQLClient(cfg)

    results = []
    for i, fields in enumerate(FIELD_SETS):
        payload = client.execute(
            build_query(fields),
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": {"first": 8},
                "contentPagination": {"first": 20},
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
            if n.get("__typename") == "UiContinueWatchingCarousel":
                cw = n
                break
        contents = (cw or {}).get("contents") or {}
        entry = {
            "field_set": i,
            "errors": errs[:6],
            "totalCount": contents.get("totalCount"),
            "edges": len(contents.get("edges") or []),
            "items": [
                (ed or {}).get("node") for ed in (contents.get("edges") or [])[:5]
            ],
        }
        results.append(entry)
        print(
            f"set{i}: total={entry['totalCount']} edges={entry['edges']} "
            f"errs={entry['errors'][:2]}"
        )

    out = Path("output/layout_compare/cw_raw_fields.json")
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
