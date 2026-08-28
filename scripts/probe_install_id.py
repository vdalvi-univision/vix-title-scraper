"""Probe install-id + auth combos. Never prints tokens."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, STAGING_ENDPOINT, ScrapeConfig

INSTALL = "eae73e7c-bdd0-e756-6823-a44ade5149db"
QUERY = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"


def token_kind(token: str | None) -> str:
    if not token:
        return "missing"
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        domain = data.get("userDomain") or data.get("iss") or "?"
        exp = int(data.get("exp") or 0)
        left = exp - int(time.time())
        return f"domain={domain} expired={left < 0} exp_in={left}s"
    except Exception as exc:  # noqa: BLE001
        return f"parse_error={type(exc).__name__}"


def probe(endpoint: str, profile: str, path: str, *, with_install: bool) -> str:
    cfg = ScrapeConfig(
        url_path=path,
        endpoint=endpoint,
        timeout=25,
        retries=0,
        app_version="5.0.0",
        device_type="mobile",
        platform="android",
        installation_id=INSTALL if with_install else None,
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, profile)
    # When with_install=False, clear env-driven install by overriding after build:
    # GraphQLClient reads env VIX_INSTALLATION_ID — temporarily strip via extra only when False
    client = GraphQLClient(cfg)
    if not with_install:
        for key in ("x-vix-installation-id", "x-installation-id", "installation-id"):
            client._headers.pop(key, None)
    else:
        # Ensure aliases present even if env missing
        client._headers["x-vix-installation-id"] = INSTALL
        client._headers["x-installation-id"] = INSTALL
        client._headers["installation-id"] = INSTALL
    try:
        payload = client.execute(QUERY, {"urlPath": path}, allow_errors=True)
    except Exception as exc:  # noqa: BLE001
        return f"EXC {type(exc).__name__}: {str(exc)[:140]}"
    page = ((payload.get("data") or {}).get("uiPage"))
    if page:
        return f"OK pageName={page.get('pageName')!r}"
    errs = graphql_error_messages(payload)
    return (errs[0] if errs else "uiPage null")[:160]


def main() -> None:
    load_dotenv(Path(".env"))
    from vix_scraper.auth import resolve_auth_profile

    print("default token:", token_kind(resolve_auth_profile("default").auth_token))
    print("wc token:", token_kind(resolve_auth_profile("wc").auth_token))
    print("install_id:", INSTALL)

    for endpoint, elabel in (
        (PRODUCTION_ENDPOINT, "prod"),
        (STAGING_ENDPOINT, "stg"),
    ):
        for profile in ("default", "wc"):
            for path in ("/ondemandplus", "/ondemandpluswc"):
                for with_install in (True, False):
                    label = f"{elabel}/{profile}/{path}/install={'on' if with_install else 'off'}"
                    print(label, "->", probe(endpoint, profile, path, with_install=with_install))


if __name__ == "__main__":
    main()
