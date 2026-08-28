"""Compare CW / Mi lista item counts across auth header variants. Never prints tokens."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text

QUERY = Path("queries/request.graphql").read_text(encoding="utf-8")


def jwt_meta(token: str | None) -> dict:
    if not token or token.count(".") != 2:
        return {"present": bool(token), "fmt": "non-jwt"}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = int(claims.get("exp") or 0)
        return {
            "present": True,
            "fmt": "jwt",
            "sub_prefix": str(claims.get("sub") or "")[:12],
            "iss": str(claims.get("iss") or "")[:40],
            "aud": str(claims.get("aud") or claims.get("audience") or "")[:40],
            "exp_in_s": exp - int(time.time()) if exp else None,
            "expired": bool(exp and exp < int(time.time())),
            "claim_keys": sorted(claims.keys())[:20],
        }
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "fmt": "jwt-error", "err": type(exc).__name__}


def fetch_personal_rails(cfg: ScrapeConfig) -> dict:
    client = GraphQLClient(cfg)
    after = None
    result = {"cw": None, "watchlist": None, "reco": None, "errors": []}
    for _ in range(8):
        pag: dict = {"first": 12}
        if after:
            pag["after"] = after
        payload = client.execute(
            QUERY,
            {
                "urlPath": "/ondemandplus",
                "uiModulesPagination": pag,
                "contentPagination": {"first": 10},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)
        if errs:
            result["errors"] = (errs or [])[:4]
        conn = (((payload.get("data") or {}).get("uiPage")) or {}).get("uiModules") or {}
        for e in conn.get("edges") or []:
            n = (e or {}).get("node") or {}
            tn = first_text(n.get("__typename"))
            contents = n.get("contents") if isinstance(n.get("contents"), dict) else {}
            edges = contents.get("edges") if isinstance(contents, dict) else []
            if not isinstance(edges, list):
                edges = []
            titles = []
            for edge in edges:
                node = (edge or {}).get("node") or {}
                video = node.get("video") if isinstance(node.get("video"), dict) else {}
                titles.append(
                    {
                        "typename": node.get("__typename"),
                        "id": first_text(node.get("id"), video.get("id")),
                        "title": first_text(node.get("title"), video.get("title")),
                    }
                )
            info = {
                "title": n.get("title"),
                "totalCount": contents.get("totalCount"),
                "edges": len(edges),
                "items": titles,
            }
            if tn == "UiContinueWatchingCarousel":
                result["cw"] = info
            elif tn == "UiWatchlistCarousel":
                result["watchlist"] = info
            elif tn == "UiRecommendedForYouCarousel":
                result["reco"] = info
        if result["cw"] and result["watchlist"]:
            break
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
    return result


def base_cfg() -> ScrapeConfig:
    return ScrapeConfig(
        url_path="/ondemandplus",
        endpoint=os.getenv("VIX_GRAPHQL_ENDPOINT") or PRODUCTION_ENDPOINT,
        timeout=60,
        retries=1,
        module_page_size=12,
        app_version=os.getenv("VIX_APP_VERSION") or "5.0.0",
        device_type=os.getenv("VIX_DEVICE_TYPE") or "desktop",
        platform=os.getenv("VIX_PLATFORM") or "web",
        user_agent=os.getenv("VIX_USER_AGENT")
        or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        installation_id=os.getenv("VIX_INSTALLATION_ID") or "",
        extra_headers={"Accept-Language": os.getenv("VIX_ACCEPT_LANGUAGE") or "es-MX,es;q=0.9"},
    )


def main() -> None:
    load_dotenv()
    auth = os.getenv("AUTH_TOKEN") or ""
    auth0 = os.getenv("AUTH_TOKEN_AUTH0") or ""
    user = os.getenv("X_VIX_USER_TOKEN") or ""
    auth_wc = os.getenv("AUTH_TOKEN_WC") or ""
    user_wc = os.getenv("X_VIX_USER_TOKEN_WC") or ""

    print("token_meta:")
    print("  AUTH_TOKEN", jwt_meta(auth))
    print("  AUTH_TOKEN_AUTH0", jwt_meta(auth0))
    print("  X_VIX_USER_TOKEN", jwt_meta(user))
    print("  AUTH_TOKEN_WC", jwt_meta(auth_wc))
    print("  X_VIX_USER_TOKEN_WC", jwt_meta(user_wc))

    variants = [
        ("auth+user", auth, user),
        ("auth0+user", auth0, user),
        ("auth_only", auth, None),
        ("auth0_only", auth0, None),
        ("user_only", None, user),
        ("wc_auth+user", auth_wc, user_wc),
        ("auth0+wc_user", auth0, user_wc),
    ]
    out = {}
    for name, a, u in variants:
        if not a and not u:
            print(f"{name}: skipped (no creds)")
            continue
        cfg = base_cfg()
        cfg.auth_token = a
        cfg.x_vix_user_token = u
        try:
            rails = fetch_personal_rails(cfg)
        except Exception as exc:  # noqa: BLE001
            rails = {"error": type(exc).__name__}
        out[name] = rails
        cw = rails.get("cw") or {}
        wl = rails.get("watchlist") or {}
        print(
            f"{name}: cw_edges={cw.get('edges')} cw_total={cw.get('totalCount')} "
            f"wl_edges={wl.get('edges')} wl_total={wl.get('totalCount')} "
            f"errs={rails.get('errors')}"
        )
        if cw.get("items"):
            for it in cw["items"][:8]:
                print(f"  CW item: {it}")

    Path("output/layout_compare/cw_auth_variants.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("wrote output/layout_compare/cw_auth_variants.json")


if __name__ == "__main__":
    main()
