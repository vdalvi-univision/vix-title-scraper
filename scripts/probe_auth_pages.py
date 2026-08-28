"""Probe endpoints/pages with configured auth profiles. Never prints tokens."""

from __future__ import annotations

import json
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, STAGING_ENDPOINT, ScrapeConfig

PROBE = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"
PROBE_STR = "query($urlPath: String!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"


def try_once(endpoint: str, profile: str, path: str, query: str, label: str) -> None:
    cfg = ScrapeConfig(
        url_path=path,
        endpoint=endpoint,
        timeout=30,
        retries=0,
        app_version="5.0.0",
        device_type="mobile",
        platform="android",
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    apply_auth_profile(cfg, profile)
    client = GraphQLClient(cfg)
    try:
        payload = client.execute(query, {"urlPath": path}, allow_errors=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] EXCEPTION {type(exc).__name__}: {exc}")
        return
    page = ((payload.get("data") or {}).get("uiPage"))
    errors = graphql_error_messages(payload)
    if page:
        print(f"[{label}] OK urlPath={page.get('urlPath')!r} pageName={page.get('pageName')!r}")
    else:
        msg = "; ".join(errors) if errors else "uiPage null"
        # Truncate; never include raw payload tokens
        print(f"[{label}] FAIL {msg[:200]}")


def main() -> None:
    load_dotenv(Path(".env"))
    cases = [
        (PRODUCTION_ENDPOINT, "default", "/ondemandplus", PROBE, "prod/default/ID"),
        (PRODUCTION_ENDPOINT, "default", "/ondemandplus", PROBE_STR, "prod/default/String"),
        (PRODUCTION_ENDPOINT, "wc", "/ondemandpluswc", PROBE, "prod/wc/ID"),
        (PRODUCTION_ENDPOINT, "wc", "/ondemandpluswc", PROBE_STR, "prod/wc/String"),
        (STAGING_ENDPOINT, "default", "/ondemandplus", PROBE, "stg/default/ID"),
        (STAGING_ENDPOINT, "wc", "/ondemandpluswc", PROBE, "stg/wc/ID"),
    ]
    for args in cases:
        try_once(*args)

    # Also try full query first page only via variables like scraper
    query = Path("queries/request.graphql").read_text(encoding="utf-8")
    for endpoint, profile, path, label in (
        (PRODUCTION_ENDPOINT, "default", "/ondemandplus", "prod/default/full"),
        (PRODUCTION_ENDPOINT, "wc", "/ondemandpluswc", "prod/wc/full"),
        (STAGING_ENDPOINT, "default", "/ondemandplus", "stg/default/full"),
        (STAGING_ENDPOINT, "wc", "/ondemandpluswc", "stg/wc/full"),
    ):
        cfg = ScrapeConfig(
            url_path=path,
            endpoint=endpoint,
            timeout=45,
            retries=0,
            page_size=5,
            module_page_size=1,
            app_version="5.0.0",
            device_type="mobile",
            platform="android",
            extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
        )
        apply_auth_profile(cfg, profile)
        client = GraphQLClient(cfg)
        variables = {
            "urlPath": path,
            "uiModulesPagination": {"first": 1},
            "contentPagination": {"first": 5},
        }
        try:
            payload = client.execute(query, variables, allow_errors=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[{label}] EXCEPTION {type(exc).__name__}: {exc}")
            continue
        page = ((payload.get("data") or {}).get("uiPage"))
        errors = graphql_error_messages(payload)
        if page:
            modules = ((page.get("uiModules") or {}).get("edges")) or []
            print(f"[{label}] OK modules_batch={len(modules)} name={page.get('pageName')!r}")
        else:
            msg = "; ".join(errors) if errors else "uiPage null"
            print(f"[{label}] FAIL {msg[:240]}")
            # Show error extensions keys only
            for err in (payload.get("errors") or [])[:2]:
                if isinstance(err, dict):
                    ext = err.get("extensions") or {}
                    print(
                        f"  ext_keys={list(ext.keys())[:8]} "
                        f"code={ext.get('code')!r} status={ext.get('status')!r}"
                    )


if __name__ == "__main__":
    main()
