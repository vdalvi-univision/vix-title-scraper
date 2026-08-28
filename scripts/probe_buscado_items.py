"""Probe Lo más buscado first-item typenames. Never prints tokens."""

from __future__ import annotations

from pathlib import Path

from vix_scraper.auth import apply_auth_profile, jwt_safe_claims
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text

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
          ... on UiVideoCarousel {
            id
            title
            collectionId
            contents(pagination: $contentPagination) {
              totalCount
              pageInfo { hasNextPage itemCount }
              edges {
                cursor
                node {
                  __typename
                  ... on UiVideoCard {
                    id
                    title
                    clickTrackingJson
                    video { id title videoType mcpId }
                  }
                  ... on UiVideoCarouselItem {
                    id
                    title
                    clickTrackingJson
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


def _summarize_node(node: dict) -> dict:
    video = node.get("video") if isinstance(node.get("video"), dict) else {}
    tracking = node.get("clickTrackingJson") if isinstance(node.get("clickTrackingJson"), dict) else {}
    return {
        "typename": node.get("__typename"),
        "card_id": first_text(node.get("id")),
        "card_title": first_text(node.get("title")),
        "video_id": first_text(video.get("id")),
        "video_title": first_text(video.get("title")),
        "tracking_title": first_text(
            tracking.get("ui_content_title"),
            tracking.get("ui_object_title"),
        ),
        "keys": sorted(k for k in node.keys() if k != "clickTrackingJson"),
        "empty": not node,
    }


def main() -> int:
    load_dotenv(Path(".env"))
    local = Path(".env.local")
    if local.is_file():
        load_dotenv(local, override=True)

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
    )
    apply_auth_profile(cfg, "default")
    import os
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
    meta = jwt_safe_claims(cfg.auth_token)
    print(
        "auth",
        {
            "present": meta.get("present"),
            "is_jwt": meta.get("is_jwt"),
            "expired": meta.get("expired"),
            "exp_in_sec": meta.get("exp_in_sec"),
            "jwt_country": meta.get("country"),
            "user_token": bool(cfg.x_vix_user_token),
        },
    )
    # Morning scrapes still succeeded with this JWT; do not abort on exp.

    client = GraphQLClient(cfg)
    for tname in (
        "UiVideoCarousel",
        "UiVideoCard",
        "UiVideoCarouselItem",
        "UiCarouselContent",
        "UiVideoCarouselContent",
        "UiMixedContentCarousel",
    ):
        payload = client.execute(INTROSPECT, {"n": tname}, allow_errors=True)
        errs = graphql_error_messages(payload)[:2]
        data = ((payload.get("data") or {}).get("__type") or {})
        print(
            "TYPE",
            tname,
            "found",
            bool(data),
            "kind",
            data.get("kind"),
            "possible",
            [p.get("name") for p in (data.get("possibleTypes") or [])],
            "errs",
            errs,
        )
        if tname == "UiVideoCarousel":
            for field in data.get("fields") or []:
                if field.get("name") in ("contents", "collectionId"):
                    print("  field", field["name"], field["type"])

    for url_path in ("/ondemandplus", "/ondemandpluswc"):
        payload = client.execute(
            TYPED,
            {
                "urlPath": url_path,
                "uiModulesPagination": {"first": 12},
                "contentPagination": {"first": 8},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)[:4]
        print("PAGE", url_path, "errs", errs)
        for edge in (
            (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {}).get("edges")
            or []
        ):
            node = (edge or {}).get("node") or {}
            title = first_text(node.get("title"))
            if "buscado" not in title.lower():
                continue
            contents = node.get("contents") or {}
            items = [_summarize_node((ed or {}).get("node") or {}) for ed in (contents.get("edges") or [])]
            print(
                "RAIL",
                url_path,
                "title",
                title,
                "typename",
                node.get("__typename"),
                "collectionId",
                node.get("collectionId"),
                "total",
                contents.get("totalCount"),
                "n",
                len(items),
            )
            for i, item in enumerate(items, start=1):
                print("  ", i, item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
