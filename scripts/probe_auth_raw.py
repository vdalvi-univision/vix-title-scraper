"""Raw auth diagnostics (no token values printed)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vix_scraper.config import load_dotenv

ENDPOINTS = [
    "https://client-api.vix.com/gql/v2",
    "https://client-api.stg.vix.tv/gql/v2",
]
Q = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"


def post(endpoint: str, headers: dict) -> str:
    body = json.dumps({"query": Q, "variables": {"urlPath": "/ondemandplus"}}).encode("utf-8")
    # Redact header values that look like tokens in status only
    req = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            status = resp.status
    except HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return f"HTTP {status} non-json {raw[:120]}"
    except URLError as exc:
        return f"URLError {exc.reason}"

    page = ((payload.get("data") or {}).get("uiPage"))
    errs = payload.get("errors") or []
    msg = ""
    if errs and isinstance(errs, list) and isinstance(errs[0], dict):
        msg = str(errs[0].get("message", ""))[:120]
        code = ((errs[0].get("extensions") or {}) or {}).get("code")
        if code:
            msg = f"{msg} code={code}"
    if page:
        return f"HTTP {status} OK pageName={page.get('pageName')!r}"
    return f"HTTP {status} FAIL {msg or 'uiPage null'}"


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    auth0 = os.getenv("AUTH_TOKEN_AUTH0") or ""
    user = os.getenv("X_VIX_USER_TOKEN") or ""
    install = os.getenv("VIX_INSTALLATION_ID") or ""

    base = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "x-vix-app-version": "5.0.0",
    }

    variants = {
        "prod web Bearer auth0": {
            **base,
            "x-vix-device-type": "web",
            "x-vix-platform": "web",
            "Authorization": f"Bearer {auth0}",
        },
        "prod web Bearer+user+install": {
            **base,
            "x-vix-device-type": "web",
            "x-vix-platform": "web",
            "Authorization": f"Bearer {auth0}",
            "x-vix-user-token": user,
            "x-vix-installation-id": install,
            "Accept-Language": "es-MX,es;q=0.9",
        },
        "prod mobile Bearer auth0": {
            **base,
            "x-vix-device-type": "mobile",
            "x-vix-platform": "android",
            "Authorization": f"Bearer {auth0}",
        },
        "prod mobile insomnia UA auth0": {
            **base,
            "User-Agent": "insomnia/9.3.1",
            "x-vix-device-type": "mobile",
            "x-vix-platform": "android",
            "Authorization": f"Bearer {auth0}",
        },
        "prod web raw Authorization (no Bearer)": {
            **base,
            "x-vix-device-type": "web",
            "x-vix-platform": "web",
            "Authorization": auth0,
        },
    }

    for endpoint in ENDPOINTS:
        print(f"=== {endpoint} ===")
        for name, headers in variants.items():
            print(f"{name}: {post(endpoint, headers)}")


if __name__ == "__main__":
    main()
