"""Debug why validated query returns 0 modules."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from vix_scraper.auth import apply_auth_profile, resolve_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    creds = resolve_auth_profile("wc")
    payload_b64 = creds.auth_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
    print("iss", claims.get("iss"), "exp_in", int(claims.get("exp", 0)) - int(time.time()))

    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=45,
        retries=0,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, "wc")
    client = GraphQLClient(cfg)
    print(
        "hdr",
        client._headers.get("x-vix-device-type"),
        client._headers.get("x-vix-platform"),
        "auth",
        bool(client._headers.get("Authorization")),
        "user",
        bool(client._headers.get("x-vix-user-token")),
    )

    minimal = """
    query($urlPath: ID!) {
      uiPage(urlPath: $urlPath) {
        urlPath
        pageName
        uiModules(pagination: { first: 3 }) {
          totalCount
          edges { node { __typename moduleType } }
        }
      }
    }
    """
    payload = client.execute(minimal, {"urlPath": "/ondemandplus"}, allow_errors=True)
    print("minimal errors", graphql_error_messages(payload)[:3])
    page = ((payload.get("data") or {}).get("uiPage"))
    print("minimal page", None if page is None else {k: page.get(k) for k in ("urlPath", "pageName")})
    if page:
        mods = page.get("uiModules") or {}
        print("minimal total", mods.get("totalCount"), "edges", len(mods.get("edges") or []))

    full = Path("queries/request.graphql").read_text(encoding="utf-8")
    payload2 = client.execute(
        full,
        {
            "urlPath": "/ondemandplus",
            "uiModulesPagination": {"first": 8},
            "contentPagination": {"first": 3},
        },
        allow_errors=True,
    )
    print("full errors", graphql_error_messages(payload2)[:5])
    print("full data keys", list((payload2.get("data") or {}).keys()))
    page2 = ((payload2.get("data") or {}).get("uiPage"))
    print("full uiPage is None", page2 is None)
    if page2:
        mods = page2.get("uiModules") or {}
        print("full total", mods.get("totalCount"), "edges", len(mods.get("edges") or []))


if __name__ == "__main__":
    main()
