"""Probe how self-minted tokens authenticate. Never prints token values."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import ScrapeConfig

QUERY = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"
FULL = Path("queries/request.graphql").read_text(encoding="utf-8")


def jwt_meta(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        now = int(time.time())
        return {
            "iss": data.get("iss"),
            "aud": data.get("aud"),
            "userDomain": data.get("userDomain"),
            "country": data.get("country"),
            "exp_in_sec": int(data.get("exp", 0)) - now,
            "expired": int(data.get("exp", 0)) < now,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": type(exc).__name__}


def raw_post(endpoint: str, headers: dict, body: dict) -> str:
    data = json.dumps(body).encode("utf-8")
    req = Request(endpoint, data=data, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:300]
        return f"HTTP {exc.code}: {raw}"
    except URLError as exc:
        return f"URLError: {exc}"
    page = ((payload.get("data") or {}).get("uiPage"))
    if page:
        return f"OK {page.get('pageName')!r}"
    errs = graphql_error_messages(payload)
    return (errs[0] if errs else "uiPage null")[:160]


def main() -> None:
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig()
    apply_auth_profile(cfg, "wc")
    token = cfg.auth_token or ""
    print("token_present", bool(token), "len", len(token))
    print("jwt_meta", jwt_meta(token))

    endpoint = "https://client-api.vix.com/gql/v2"
    body = {"query": QUERY, "variables": {"urlPath": "/ondemandpluswc"}}
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "insomnia/9.3.1",
        "x-vix-app-version": "5.0.0",
        "x-vix-device-type": "mobile",
        "x-vix-platform": "android",
        "Accept-Language": "es-MX,es;q=0.9",
    }

    modes = {
        "Authorization Bearer": {**base, "Authorization": f"Bearer {token}"},
        "Authorization raw": {**base, "Authorization": token},
        "x-vix-user-token only": {**base, "x-vix-user-token": token},
        "x-vix-auth-token": {**base, "x-vix-auth-token": token},
        "x-access-token": {**base, "x-access-token": token},
        "Bearer + x-vix-user-token": {
            **base,
            "Authorization": f"Bearer {token}",
            "x-vix-user-token": token,
        },
        "Bearer + country MX header": {
            **base,
            "Authorization": f"Bearer {token}",
            "x-vix-country": "MX",
            "cloudfront-viewer-country": "MX",
        },
    }
    print("=== header modes prod /ondemandpluswc ===")
    for name, headers in modes.items():
        print(f"{name}: {raw_post(endpoint, headers, body)}")

    # Also try identity exchange endpoints if any respond
    print("=== identity host probes (no token body logged) ===")
    for url in (
        "https://identity-api.self.vix.com/health",
        "https://identity-api.self.vix.com/",
        "https://self.vix.com/",
    ):
        try:
            req = Request(url, headers={"User-Agent": "insomnia/9.3.1"}, method="GET")
            with urlopen(req, timeout=15) as resp:
                print(url, "HTTP", resp.status, (resp.read()[:80]))
        except Exception as exc:  # noqa: BLE001
            print(url, type(exc).__name__, str(exc)[:120])

    # Standard client path for both pages
    print("=== GraphQLClient both pages ===")
    for path in ("/ondemandplus", "/ondemandpluswc"):
        c = ScrapeConfig(
            url_path=path,
            endpoint=endpoint,
            timeout=25,
            retries=0,
            app_version="5.0.0",
            device_type="mobile",
            platform="android",
            extra_headers={"Accept-Language": "es-MX,es;q=0.9", "x-vix-country": "MX"},
        )
        apply_auth_profile(c, "wc")
        client = GraphQLClient(c)
        try:
            payload = client.execute(QUERY, {"urlPath": path}, allow_errors=True)
            page = ((payload.get("data") or {}).get("uiPage"))
            errs = graphql_error_messages(payload)
            print(path, "OK" if page else (errs[0] if errs else "null")[:160])
        except Exception as exc:  # noqa: BLE001
            print(path, "EXC", type(exc).__name__, str(exc)[:160])


if __name__ == "__main__":
    main()
