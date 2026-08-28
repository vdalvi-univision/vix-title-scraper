"""Try identity-api.vix.com hosts for self-minted token acceptance. Never prints tokens."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from vix_scraper.auth import apply_auth_profile
from vix_scraper.config import load_dotenv
from vix_scraper.models import ScrapeConfig

HOSTS = [
    "https://identity-api.vix.com",
    "https://www.identity-api.vix.com",
    "https://id.vix.com",
    "https://auth.vix.com",
]
PATHS = ["/", "/health", "/v1/health", "/.well-known/openid-configuration"]


def main() -> None:
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig()
    apply_auth_profile(cfg, "wc")
    token = cfg.auth_token or ""

    for host in HOSTS:
        for path in PATHS:
            url = host + path
            for headers in (
                {"User-Agent": "insomnia/9.3.1", "Accept": "application/json"},
                {
                    "User-Agent": "insomnia/9.3.1",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            ):
                auth = "auth" if "Authorization" in headers else "anon"
                try:
                    req = Request(url, headers=headers, method="GET")
                    with urlopen(req, timeout=15) as resp:
                        body = resp.read()[:120]
                        print(f"{auth} {url} -> HTTP {resp.status} {body!r}")
                except HTTPError as exc:
                    body = exc.read()[:120]
                    print(f"{auth} {url} -> HTTP {exc.code} {body!r}")
                except URLError as exc:
                    print(f"{auth} {url} -> URLError {exc.reason}")

    # POST token introspection-ish guesses (safe: no response dump of token)
    endpoint = "https://client-api.vix.com/gql/v2"
    query = "query { __typename }"
    for header_name in ("x-vix-session-token", "x-vix-anonymous-token", "x-anonymous-id-token"):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "insomnia/9.3.1",
            "x-vix-app-version": "5.0.0",
            "x-vix-device-type": "mobile",
            "x-vix-platform": "android",
            header_name: token,
        }
        data = json.dumps({"query": query}).encode("utf-8")
        req = Request(endpoint, data=data, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            print(header_name, "->", str(payload)[:160])
        except HTTPError as exc:
            print(header_name, "-> HTTP", exc.code, exc.read()[:120])
        except Exception as exc:  # noqa: BLE001
            print(header_name, "->", type(exc).__name__, str(exc)[:120])


if __name__ == "__main__":
    main()
