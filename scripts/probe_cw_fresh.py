"""CW probe with clean env + .env.local override. Never prints tokens."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.util import first_text

Q = Path("queries/layout.graphql").read_text(encoding="utf-8")


def clear_auth_env() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k)


def load_creds() -> None:
    load_dotenv(Path(".env"))
    if Path(".env.local").is_file():
        load_dotenv(Path(".env.local"), override=True)


def fetch_cw(cfg: ScrapeConfig, path: str = "/ondemandplus") -> dict:
    client = GraphQLClient(cfg)
    after = None
    for page in range(20):
        pag: dict = {"first": 4}
        if after:
            pag["after"] = after
        payload = client.execute(
            Q,
            {
                "urlPath": path,
                "uiModulesPagination": pag,
                "contentPagination": {"first": 20},
            },
            allow_errors=True,
        )
        errs = graphql_error_messages(payload)
        conn = (((payload.get("data") or {}).get("uiPage") or {}).get("uiModules") or {})
        for e in conn.get("edges") or []:
            n = (e or {}).get("node") or {}
            if first_text(n.get("__typename")) == "UiContinueWatchingCarousel":
                c = n.get("contents") or {}
                items = []
                for ed in c.get("edges") or []:
                    node = (ed or {}).get("node") or {}
                    v = node.get("video") if isinstance(node.get("video"), dict) else {}
                    series = {}
                    vtd = v.get("videoTypeData")
                    if isinstance(vtd, dict) and isinstance(vtd.get("series"), dict):
                        series = vtd["series"]
                    items.append(
                        {
                            "typename": node.get("__typename"),
                            "title": first_text(
                                series.get("title"),
                                node.get("title"),
                                v.get("title"),
                            ),
                            "id": first_text(series.get("id"), v.get("id"), node.get("id")),
                        }
                    )
                return {
                    "path": path,
                    "errors": errs[:3],
                    "title": n.get("title"),
                    "totalCount": c.get("totalCount"),
                    "edges": len(c.get("edges") or []),
                    "items": items,
                }
        info = conn.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
        if page == 0 and errs:
            return {"path": path, "errors": errs[:3], "missing": True}
    return {"path": path, "missing": True, "errors": []}


def main() -> None:
    clear_auth_env()
    load_creds()
    base = dict(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=60,
        retries=1,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    auth = os.getenv("AUTH_TOKEN")
    auth0 = os.getenv("AUTH_TOKEN_AUTH0")
    user = os.getenv("X_VIX_USER_TOKEN")
    auth_wc = os.getenv("AUTH_TOKEN_WC")
    user_wc = os.getenv("X_VIX_USER_TOKEN_WC")

    variants = [
        ("local_auth+user", auth, user),
        ("auth0+user", auth0, user),
        ("auth0+wc_user", auth0, user_wc),
        ("wc_auth+wc_user", auth_wc, user_wc),
        ("auth0_only", auth0, None),
    ]
    for name, a, u in variants:
        if not a:
            print(f"{name}: skip")
            continue
        cfg = ScrapeConfig(**base, auth_token=a, x_vix_user_token=u)
        for path in ("/ondemandplus", "/ondemandpluswc"):
            result = fetch_cw(cfg, path)
            print(
                f"{name} {path}: total={result.get('totalCount')} "
                f"edges={result.get('edges')} items={result.get('items')} "
                f"errs={result.get('errors')}"
            )


if __name__ == "__main__":
    main()
