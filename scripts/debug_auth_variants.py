"""Try auth variants for desktop/web. Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path

from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

Q = """
query($urlPath: ID!) {
  uiPage(urlPath: $urlPath) {
    urlPath
    pageName
    uiModules(pagination: { first: 3 }) {
      totalCount
      edges { node { __typename } }
    }
  }
}
"""


def try_one(label: str, cfg: ScrapeConfig) -> None:
    client = GraphQLClient(cfg)
    if not cfg.x_vix_user_token:
        client._headers.pop("x-vix-user-token", None)
    payload = client.execute(Q, {"urlPath": "/ondemandplus"}, allow_errors=True)
    page = ((payload.get("data") or {}).get("uiPage"))
    err = graphql_error_messages(payload)
    data = payload.get("data")
    data_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
    if page:
        mods = page.get("uiModules") or {}
        print(
            f"{label}: OK page={page.get('pageName')!r} total={mods.get('totalCount')} "
            f"edges={len(mods.get('edges') or [])}"
        )
    else:
        extra = ""
        if isinstance(data, dict) and "error" in data:
            extra = f" data.error={str(data.get('error'))[:120]}"
        print(f"{label}: FAIL errs={err[:1]} data_keys={data_keys}{extra}")


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    auth0 = os.getenv("AUTH_TOKEN_AUTH0")
    user = os.getenv("X_VIX_USER_TOKEN")
    install = os.getenv("VIX_INSTALLATION_ID")
    ua = os.getenv("VIX_USER_AGENT")

    base = dict(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=30,
        retries=0,
        device_type="desktop",
        platform="web",
        user_agent=ua,
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    try_one(
        "auth0+user+install",
        ScrapeConfig(**base, auth_token=auth0, x_vix_user_token=user, installation_id=install),
    )
    try_one(
        "auth0-only+install",
        ScrapeConfig(**base, auth_token=auth0, x_vix_user_token=None, installation_id=install),
    )
    try_one(
        "auth0-only no-install",
        ScrapeConfig(**base, auth_token=auth0, x_vix_user_token=None, installation_id=None),
    )
    # Force clear install env for last case already handled inside client if installation_id None?
    # GraphQLClient falls back to env VIX_INSTALLATION_ID — temporarily unset.
    old = os.environ.pop("VIX_INSTALLATION_ID", None)
    try_one(
        "auth0-only no-install-env",
        ScrapeConfig(**base, auth_token=auth0, x_vix_user_token=None, installation_id=None),
    )
    if old:
        os.environ["VIX_INSTALLATION_ID"] = old
    try_one(
        "mobile auth0-only",
        ScrapeConfig(
            endpoint=PRODUCTION_ENDPOINT,
            timeout=30,
            retries=0,
            device_type="mobile",
            platform="android",
            user_agent="insomnia/9.3.1",
            auth_token=auth0,
            x_vix_user_token=None,
            installation_id=None,
        ),
    )


if __name__ == "__main__":
    main()
