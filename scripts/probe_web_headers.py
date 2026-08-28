"""Find working web platform headers. Never prints tokens."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vix_scraper.config import load_dotenv

ENDPOINT = "https://client-api.vix.com/gql/v2"
Q = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"


def post(headers: dict) -> str:
    body = json.dumps({"query": Q, "variables": {"urlPath": "/ondemandplus"}}).encode("utf-8")
    req = Request(ENDPOINT, data=body, headers=headers, method="POST")
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
            return f"HTTP {status} non-json"
    except URLError as exc:
        return f"URLError {exc.reason}"
    page = ((payload.get("data") or {}).get("uiPage"))
    errs = payload.get("errors") or []
    msg = ""
    if errs and isinstance(errs[0], dict):
        msg = str(errs[0].get("message", ""))[:80]
        code = (errs[0].get("extensions") or {}).get("code")
        if code:
            msg = f"{msg}/{code}"
    if page:
        return f"HTTP {status} OK {page.get('pageName')!r}"
    return f"HTTP {status} FAIL {msg or 'null'}"


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    auth0 = os.getenv("AUTH_TOKEN_AUTH0") or ""
    user = os.getenv("X_VIX_USER_TOKEN") or ""
    install = os.getenv("VIX_INSTALLATION_ID") or ""

    chrome = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    combos = [
        ("mobile/android insomnia", "mobile", "android", "insomnia/9.3.1"),
        ("web/web insomnia", "web", "web", "insomnia/9.3.1"),
        ("web/desktop insomnia", "web", "desktop", "insomnia/9.3.1"),
        ("desktop/web insomnia", "desktop", "web", "insomnia/9.3.1"),
        ("desktop/desktop insomnia", "desktop", "desktop", "insomnia/9.3.1"),
        ("browser/web insomnia", "browser", "web", "insomnia/9.3.1"),
        ("ctv/web insomnia", "ctv", "web", "insomnia/9.3.1"),
        ("web/web chrome", "web", "web", chrome),
        ("mobile/android chrome", "mobile", "android", chrome),
        ("web/web vix-web", "web", "web", "vix-web/5.0.0"),
        ("web/web empty-ua", "web", "web", "python-urllib"),
    ]
    print("=== Auth0 only ===")
    for label, device, platform, ua in combos:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ua,
            "x-vix-app-version": "5.0.0",
            "x-vix-device-type": device,
            "x-vix-platform": platform,
            "Authorization": f"Bearer {auth0}",
        }
        print(f"{label}: {post(headers)}")

    print("=== Auth0 + user + install (web candidates) ===")
    for label, device, platform, ua in combos[:6]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": ua,
            "x-vix-app-version": "5.0.0",
            "x-vix-device-type": device,
            "x-vix-platform": platform,
            "Authorization": f"Bearer {auth0}",
            "x-vix-user-token": user,
            "x-vix-installation-id": install,
            "Accept-Language": "es-MX,es;q=0.9",
        }
        print(f"{label}: {post(headers)}")


if __name__ == "__main__":
    main()
