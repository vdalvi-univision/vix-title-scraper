"""Probe Authorization + x-vix-user-token + install-id combos. Never prints tokens."""

from __future__ import annotations

from pathlib import Path

from vix_scraper.auth import apply_auth_profile, resolve_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

INSTALL = "eae73e7c-bdd0-e756-6823-a44ade5149db"
QUERY = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"


def probe(
    *,
    profile: str,
    path: str,
    use_user_token: bool,
    use_install: bool,
    auth_override_profile: str | None = None,
) -> str:
    """auth_override_profile: use that profile's AUTH_TOKEN but keep user token from profile."""
    auth_src = auth_override_profile or profile
    auth_creds = resolve_auth_profile(auth_src)
    user_creds = resolve_auth_profile(profile)
    cfg = ScrapeConfig(
        url_path=path,
        endpoint=PRODUCTION_ENDPOINT,
        timeout=25,
        retries=0,
        app_version="5.0.0",
        device_type="mobile",
        platform="android",
        auth_token=auth_creds.auth_token,
        x_vix_user_token=user_creds.x_vix_user_token if use_user_token else None,
        installation_id=INSTALL if use_install else "",
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
    )
    client = GraphQLClient(cfg)
    if not use_install:
        for key in ("x-vix-installation-id", "x-installation-id", "installation-id"):
            client._headers.pop(key, None)
    if not use_user_token:
        client._headers.pop("x-vix-user-token", None)
    # Report which headers are present (names only)
    hdr_flags = []
    if "Authorization" in client._headers:
        hdr_flags.append("Authorization")
    if "x-vix-user-token" in client._headers:
        hdr_flags.append("x-vix-user-token")
    if "x-vix-installation-id" in client._headers:
        hdr_flags.append("x-vix-installation-id")
    try:
        payload = client.execute(QUERY, {"urlPath": path}, allow_errors=True)
    except Exception as exc:  # noqa: BLE001
        return f"headers={hdr_flags} EXC {type(exc).__name__}: {str(exc)[:120]}"
    page = ((payload.get("data") or {}).get("uiPage"))
    if page:
        return f"headers={hdr_flags} OK pageName={page.get('pageName')!r}"
    errs = graphql_error_messages(payload)
    return f"headers={hdr_flags} FAIL {(errs[0] if errs else 'uiPage null')[:140]}"


def main() -> None:
    load_dotenv(Path(".env"))
    d = resolve_auth_profile("default")
    w = resolve_auth_profile("wc")
    print("default has AUTH", bool(d.auth_token), "USER", bool(d.x_vix_user_token))
    print("wc has AUTH", bool(w.auth_token), "USER", bool(w.x_vix_user_token))

    cases = []
    # Auth0 (default AUTH) + user token + install permutations
    for path in ("/ondemandplus", "/ondemandpluswc"):
        for user in (True, False):
            for install in (True, False):
                cases.append(("default-auth", "default", path, user, install, "default"))
    # Self-minted AUTH (wc) + user token + install
    for path in ("/ondemandplus", "/ondemandpluswc"):
        for user in (True, False):
            for install in (True, False):
                cases.append(("self-auth", "wc", path, user, install, "wc"))
    # Auth0 AUTH but pull user token from wc profile (same value) — covered by default
    # Cross: self AUTH with default user token
    for path in ("/ondemandplus", "/ondemandpluswc"):
        cases.append(("self-auth+default-user", "default", path, True, True, "wc"))

    seen = set()
    for label, profile, path, user, install, auth_src in cases:
        key = (label, path, user, install, auth_src)
        if key in seen:
            continue
        seen.add(key)
        result = probe(
            profile=profile,
            path=path,
            use_user_token=user,
            use_install=install,
            auth_override_profile=auth_src,
        )
        print(
            f"{label} path={path} user={user} install={install} -> {result}"
        )


if __name__ == "__main__":
    main()
