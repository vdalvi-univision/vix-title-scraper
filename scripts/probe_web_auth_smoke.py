"""Smoke-test web platform + auth combos. Never prints tokens."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

INSTALL = "eae73e7c-bdd0-e756-6823-a44ade5149db"
Q = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def jwt_iss_exp(token: str | None) -> str:
    if not token or token.count(".") != 2:
        return "missing"
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return f"iss={data.get('iss')} exp_in={int(data.get('exp', 0)) - int(time.time())}"
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__


def try_combo(label: str, *, auth: str | None, user: str | None, platform: str, device: str) -> None:
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint=PRODUCTION_ENDPOINT,
        timeout=30,
        retries=0,
        auth_token=auth,
        x_vix_user_token=user,
        installation_id=INSTALL,
        platform=platform,
        device_type=device,
        user_agent=UA,
        app_version="5.0.0",
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    client = GraphQLClient(cfg)
    if not user:
        client._headers.pop("x-vix-user-token", None)
    try:
        payload = client.execute(Q, {"urlPath": "/ondemandplus"}, allow_errors=True)
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: EXC {type(exc).__name__}: {str(exc)[:120]}")
        return
    page = ((payload.get("data") or {}).get("uiPage"))
    if page:
        print(f"{label}: OK pageName={page.get('pageName')!r}")
    else:
        errs = graphql_error_messages(payload)
        print(f"{label}: FAIL {(errs[0] if errs else 'null')[:160]}")


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))

    auth0 = os.getenv("AUTH_TOKEN_AUTH0")
    self_auth = os.getenv("AUTH_TOKEN_WC") or os.getenv("AUTH_TOKEN")
    user = os.getenv("X_VIX_USER_TOKEN_WC") or os.getenv("X_VIX_USER_TOKEN")
    print("auth0", jwt_iss_exp(auth0))
    print("self", jwt_iss_exp(self_auth))
    print("user", jwt_iss_exp(user))

    combos = [
        ("web auth0+user", auth0, user, "web", "web"),
        ("web auth0-only", auth0, None, "web", "web"),
        ("web self+user", self_auth, user, "web", "web"),
        ("desktop auth0+user", auth0, user, "web", "desktop"),
        ("WEB/WEB auth0+user", auth0, user, "WEB", "WEB"),
        ("mobile auth0+user", auth0, user, "android", "mobile"),
        ("web auth0+user no-install", auth0, user, "web", "web"),
    ]
    for label, auth, usr, plat, dev in combos:
        if label.endswith("no-install"):
            cfg = ScrapeConfig(
                endpoint=PRODUCTION_ENDPOINT,
                timeout=30,
                retries=0,
                auth_token=auth,
                x_vix_user_token=usr,
                installation_id=None,
                platform=plat,
                device_type=dev,
                user_agent=UA,
                extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
            )
            # Force-clear install from env for this client
            old = os.environ.pop("VIX_INSTALLATION_ID", None)
            client = GraphQLClient(cfg)
            if old:
                os.environ["VIX_INSTALLATION_ID"] = old
            payload = client.execute(Q, {"urlPath": "/ondemandplus"}, allow_errors=True)
            page = ((payload.get("data") or {}).get("uiPage"))
            if page:
                print(f"{label}: OK pageName={page.get('pageName')!r}")
            else:
                errs = graphql_error_messages(payload)
                print(f"{label}: FAIL {(errs[0] if errs else 'null')[:160]}")
        else:
            try_combo(label, auth=auth, user=usr, platform=plat, device=dev)


if __name__ == "__main__":
    main()
