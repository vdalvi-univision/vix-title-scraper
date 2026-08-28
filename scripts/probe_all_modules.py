"""Dump ALL uiModules edges (typenames/titles) before filtering. Never prints tokens."""

from __future__ import annotations

import json
from pathlib import Path

from vix_scraper.auth import apply_auth_profile, resolve_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

INSTALL = "eae73e7c-bdd0-e756-6823-a44ade5149db"
OUT = Path("output/layout_compare")

# Minimal query: interface fields only so unknown typenames still appear.
MINIMAL = """
query UiModulesInventory($urlPath: ID!, $uiModulesPagination: PaginationParams) {
  uiPage(urlPath: $urlPath) {
    urlPath
    pageName
    uiModules(pagination: $uiModulesPagination) {
      totalCount
      pageInfo {
        hasNextPage
        endCursor
      }
      edges {
        cursor
        node {
          __typename
          moduleType
          trackingMetadataJson
        }
      }
    }
  }
}
"""


def title_of(node: dict) -> str:
    tracking = node.get("trackingMetadataJson") or {}
    if not isinstance(tracking, dict):
        tracking = {}
    return (
        node.get("title")
        or node.get("textTitle")
        or tracking.get("ui_module_title")
        or tracking.get("ui_carousel_slug")
        or node.get("ctaText")
        or ""
    )


def _prefer_auth0_bearer(cfg: ScrapeConfig) -> str:
    """Use AUTH_TOKEN_AUTH0 for Authorization when present (self-mint may be expired)."""
    import os

    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
        return "auth0_env"
    return cfg.auth_profile


def inventory(path: str, profile: str) -> dict:
    cfg = ScrapeConfig(
        url_path=path,
        endpoint=PRODUCTION_ENDPOINT,
        timeout=45,
        retries=2,
        module_page_size=10,
        app_version="5.0.0",
        device_type="desktop",
        platform="web",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        installation_id=INSTALL,
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, profile)
    if not cfg.auth_token:
        for alt in ("wc", "default"):
            if resolve_auth_profile(alt).has_auth_token:
                apply_auth_profile(cfg, alt)
                profile = alt
                break
    auth_label = _prefer_auth0_bearer(cfg)
    # Keep user token from profile (x-vix-user-token) + install id on headers.
    client = GraphQLClient(cfg)
    modules: list[dict] = []
    after = None
    page = 0
    total_count = None
    while True:
        pagination: dict = {"first": 10}
        if after:
            pagination["after"] = after
        payload = client.execute(
            MINIMAL,
            {"urlPath": path, "uiModulesPagination": pagination},
            allow_errors=True,
        )
        page += 1
        errs = graphql_error_messages(payload)
        ui = ((payload.get("data") or {}).get("uiPage")) or {}
        conn = ui.get("uiModules") or {}
        if total_count is None:
            total_count = conn.get("totalCount")
        edges = conn.get("edges") or []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            tracking = node.get("trackingMetadataJson") or {}
            if not isinstance(tracking, dict):
                tracking = {}
            modules.append(
                {
                    "page": page,
                    "cursor": (edge or {}).get("cursor"),
                    "__typename": node.get("__typename"),
                    "moduleType": node.get("moduleType") or tracking.get("ui_object_type"),
                    "id": tracking.get("ui_module_id") or node.get("id"),
                    "title": title_of(node),
                    "ctaUrlPath": node.get("ctaUrlPath"),
                    "tracking_keys": sorted(tracking.keys())[:12],
                }
            )
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            break
        after = info["endCursor"]
        if page > 40:
            break
    type_counts: dict[str, int] = {}
    for m in modules:
        key = f"{m['__typename']}|{m['moduleType']}"
        type_counts[key] = type_counts.get(key, 0) + 1
    return {
        "path": path,
        "auth_profile_used": profile,
        "auth_bearer_source": auth_label,
        "auth_token_present": bool(cfg.auth_token),
        "user_token_present": bool(cfg.x_vix_user_token),
        "install_id_set": True,
        "platform": cfg.platform,
        "device_type": cfg.device_type,
        "errors": errs[:5] if errs else [],
        "totalCount": total_count,
        "modules_seen": len(modules),
        "type_counts": type_counts,
        "modules": modules,
    }


def main() -> None:
    load_dotenv(Path(".env"))
    OUT.mkdir(parents=True, exist_ok=True)
    results = {}
    for path, profile in (("/ondemandplus", "wc"), ("/ondemandpluswc", "wc")):
        print(f"=== inventory {path} ===")
        try:
            data = inventory(path, profile)
        except Exception as exc:  # noqa: BLE001
            data = {"path": path, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        results[path] = data
        if "modules" in data:
            print(f"totalCount={data.get('totalCount')} seen={data.get('modules_seen')}")
            print("type_counts:", json.dumps(data["type_counts"], ensure_ascii=False, indent=2))
            print("first 15 modules:")
            for m in data["modules"][:15]:
                print(
                    f"  {m['__typename']:30} {m['moduleType'] or '-':28} {m['title']!r}"
                )
            out = OUT / f"{path.strip('/').replace('/', '_')}_modules_inventory.json"
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print("wrote", out)
        else:
            print(data)
    summary = OUT / "modules_inventory_summary.json"
    # Strip bulky module lists from summary file? keep full for debug.
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", summary)


if __name__ == "__main__":
    main()
