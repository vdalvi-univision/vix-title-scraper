"""Deeper auth/endpoint/header probes. Never prints tokens."""

from __future__ import annotations

from pathlib import Path

from vix_scraper.auth import apply_auth_profile, resolve_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import ScrapeConfig

ENDPOINTS = [
    "https://client-api.vix.com/gql/v2",
    "https://client-api.stg.vix.tv/gql/v2",
    "https://client-api.self.vix.com/gql/v2",
    "https://api.vix.com/gql/v2",
    "https://web-api.vix.com/gql/v2",
]

PATHS = ["/ondemandplus", "/ondemandpluswc", "/home", "/ondemand"]
QUERY = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"


def run(endpoint: str, profile: str, path: str, *, mode: str = "bearer") -> str:
    cfg = ScrapeConfig(
        url_path=path,
        endpoint=endpoint,
        timeout=20,
        retries=0,
        app_version="5.0.0",
        device_type="mobile",
        platform="android",
        user_agent="insomnia/9.3.1",
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, profile)
    creds = resolve_auth_profile(profile)
    if mode == "user-token-only":
        cfg.auth_token = None
        cfg.x_vix_user_token = creds.auth_token
    elif mode == "both":
        cfg.auth_token = creds.auth_token
        cfg.x_vix_user_token = creds.auth_token
    elif mode == "no-auth":
        cfg.auth_token = None
        cfg.x_vix_user_token = None
    client = GraphQLClient(cfg)
    try:
        payload = client.execute(QUERY, {"urlPath": path}, allow_errors=True)
    except Exception as exc:  # noqa: BLE001
        return f"EXC {type(exc).__name__}: {str(exc)[:120]}"
    page = ((payload.get("data") or {}).get("uiPage"))
    if page:
        return f"OK {page.get('pageName')!r}"
    errors = graphql_error_messages(payload)
    return (errors[0] if errors else "uiPage null")[:120]


def main() -> None:
    load_dotenv(Path(".env"))

    print("=== endpoint x profile x path (bearer) ===")
    for endpoint in ENDPOINTS:
        short = endpoint.replace("https://", "")
        for profile in ("default", "wc"):
            for path in PATHS:
                result = run(endpoint, profile, path, mode="bearer")
                if result.startswith("OK") or "Malformed" in result or "Unauth" in result or "Forbidden" in result or "JWT" in result or "token" in result.lower():
                    print(f"{short} | {profile} | {path} -> {result}")
                elif path in ("/ondemandplus", "/ondemandpluswc"):
                    print(f"{short} | {profile} | {path} -> {result}")

    print("=== auth modes on prod for /ondemandplus ===")
    for profile in ("default", "wc"):
        for mode in ("bearer", "user-token-only", "both", "no-auth"):
            result = run(ENDPOINTS[0], profile, "/ondemandplus", mode=mode)
            print(f"prod | {profile} | {mode} | /ondemandplus -> {result}")

    print("=== auth modes on prod for /ondemandpluswc ===")
    for profile in ("default", "wc"):
        for mode in ("bearer", "user-token-only", "both"):
            result = run(ENDPOINTS[0], profile, "/ondemandpluswc", mode=mode)
            print(f"prod | {profile} | {mode} | /ondemandpluswc -> {result}")

    # Full query with wc on a few paths
    full = Path("queries/request.graphql").read_text(encoding="utf-8")
    print("=== full query prod/wc paths ===")
    for path in ("/ondemandpluswc", "/ondemandplus", "/home", "/deportes"):
        cfg = ScrapeConfig(
            url_path=path,
            endpoint=ENDPOINTS[0],
            timeout=30,
            retries=0,
            page_size=3,
            module_page_size=1,
            app_version="5.0.0",
            device_type="mobile",
            platform="android",
            extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
        )
        apply_auth_profile(cfg, "wc")
        client = GraphQLClient(cfg)
        variables = {
            "urlPath": path,
            "uiModulesPagination": {"first": 1},
            "contentPagination": {"first": 3},
        }
        try:
            payload = client.execute(full, variables, allow_errors=True)
        except Exception as exc:  # noqa: BLE001
            print(f"full wc {path} -> EXC {exc}")
            continue
        page = ((payload.get("data") or {}).get("uiPage"))
        errors = graphql_error_messages(payload)
        if page:
            print(f"full wc {path} -> OK {page.get('pageName')!r}")
        else:
            print(f"full wc {path} -> {(errors[0] if errors else 'null')[:160]}")


if __name__ == "__main__":
    main()
