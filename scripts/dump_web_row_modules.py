"""Live-dump ordered uiModules with item counts + visibility flags. Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile, resolve_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.extractor import (
    is_content_module,
    is_inline_module,
    is_visible_web_row,
    module_has_content_edges,
)
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text

OUT = Path("output/layout_compare")
QUERY = Path("queries/request.graphql").read_text(encoding="utf-8")


def _prefer_auth0_bearer(cfg: ScrapeConfig) -> str:
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    if auth0:
        cfg.auth_token = auth0
        return "auth0_env"
    return cfg.auth_profile


def title_of(module: dict) -> str:
    tracking = module.get("trackingMetadataJson") or {}
    if not isinstance(tracking, dict):
        tracking = {}
    return first_text(
        module.get("title"),
        module.get("textTitle"),
        tracking.get("ui_module_title"),
        module.get("ctaText"),
        tracking.get("ui_carousel_slug"),
        module.get("ctaUrlPath"),
        "(untitled)",
    )


def dump_path(path: str, profile: str = "wc") -> dict:
    cfg = ScrapeConfig(
        url_path=path,
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
    apply_auth_profile(cfg, profile)
    if not cfg.auth_token:
        for alt in ("wc", "default"):
            if resolve_auth_profile(alt).has_auth_token:
                apply_auth_profile(cfg, alt)
                profile = alt
                break
    auth_label = _prefer_auth0_bearer(cfg)
    client = GraphQLClient(cfg)

    modules: list[dict] = []
    after = None
    page_n = 0
    total_count = None
    errs: list[str] = []
    while True:
        pagination: dict = {"first": 12}
        if after:
            pagination["after"] = after
        payload = client.execute(
            QUERY,
            {
                "urlPath": path,
                "uiModulesPagination": pagination,
                "contentPagination": {"first": 3},
            },
            allow_errors=True,
        )
        page_n += 1
        errs = graphql_error_messages(payload) or errs
        ui = ((payload.get("data") or {}).get("uiPage")) or {}
        conn = ui.get("uiModules") or {}
        if total_count is None:
            total_count = conn.get("totalCount")
        edges = conn.get("edges") or []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            contents = node.get("contents") if isinstance(node.get("contents"), dict) else {}
            edges_list = contents.get("edges") if isinstance(contents, dict) else []
            if not isinstance(edges_list, list):
                edges_list = []
            item_count = len(edges_list)
            empty = item_count == 0 and not is_inline_module(node)
            row = {
                "raw_index": len(modules) + 1,
                "title": title_of(node),
                "moduleType": first_text(node.get("moduleType")),
                "typename": first_text(node.get("__typename")),
                "item_count": item_count,
                "empty": empty,
                "is_inline": is_inline_module(node),
                "is_content": is_content_module(node),
                "has_edges": module_has_content_edges(node),
                "is_visible_web_row": is_visible_web_row(node),
                "ctaUrlPath": first_text(node.get("ctaUrlPath")),
                "ctaText": first_text(node.get("ctaText")),
            }
            modules.append(row)
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            break
        after = info["endCursor"]
        if page_n > 40:
            break

    visible = [m for m in modules if m["is_visible_web_row"]]
    for i, m in enumerate(visible, start=1):
        m["carousel_y"] = i

    return {
        "path": path,
        "auth_profile_used": profile,
        "auth_bearer_source": auth_label,
        "auth_token_present": bool(cfg.auth_token),
        "user_token_present": bool(cfg.x_vix_user_token),
        "platform": cfg.platform,
        "device_type": cfg.device_type,
        "errors": (errs or [])[:5],
        "totalCount": total_count,
        "modules_seen": len(modules),
        "modules": modules,
        "visible_titles": [m["title"] for m in visible],
        "micros_y": next(
            (m.get("carousel_y") for m in visible if m["title"].lower() == "micros"),
            None,
        ),
    }


def main() -> None:
    load_dotenv(Path(".env"))
    OUT.mkdir(parents=True, exist_ok=True)
    for path in ("/ondemandplus", "/ondemandpluswc"):
        print(f"=== {path} ===")
        data = dump_path(path)
        out = OUT / f"{path.strip('/').replace('/', '_')}_web_row_dump.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            f"seen={data['modules_seen']} totalCount={data['totalCount']} "
            f"visible={len(data['visible_titles'])} Micros_y={data['micros_y']}"
        )
        print("raw modules (index | type | empty | visible? | title):")
        for m in data["modules"]:
            print(
                f"  {m['raw_index']:02d} {m['moduleType'] or '-':32} "
                f"n={m['item_count']:<3} empty={str(m['empty']):5} "
                f"vis={str(m['is_visible_web_row']):5} {m['title']!r}"
            )
        print("current visible order:")
        for i, title in enumerate(data["visible_titles"], 1):
            mark = " <-- Micros" if title.lower() == "micros" else ""
            print(f"  {i:02d} {title}{mark}")
        print("wrote", out)
        print()


if __name__ == "__main__":
    main()
