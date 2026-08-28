"""Compare /ondemandplus under Auth0 vs self-minted auth. Never prints tokens."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

from vix_scraper.auth import resolve_auth_profile
from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.scraper import TitleScraper

INSTALL = "eae73e7c-bdd0-e756-6823-a44ade5149db"
QUERY_FILE = Path("queries/request.graphql")


def jwt_meta(token: str | None) -> dict:
    if not token or token.count(".") != 2:
        return {"present": bool(token)}
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        now = int(time.time())
        keep = {
            "present": True,
            "iss": data.get("iss"),
            "aud": data.get("aud"),
            "sub": data.get("sub"),
            "exp": data.get("exp"),
            "exp_in_sec": int(data.get("exp", 0)) - now,
            "expired": int(data.get("exp", 0)) < now,
        }
        for c in data:
            if any(x in c.lower() for x in ("country", "region", "geo", "userdomain", "install")):
                keep[c] = data[c]
        return keep
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "error": type(exc).__name__}


def summarize_rows(rows: list) -> dict:
    heroes = [r for r in rows if r.is_hero]
    seen: dict[str, int] = {}
    for r in rows:
        title = (r.row_title or "").strip()
        if title and title not in seen:
            seen[title] = int(r.carousel_y or 0)
    row_titles = [t for t, _ in sorted(seen.items(), key=lambda kv: kv[1])]
    ids = {r.content_id for r in rows if r.content_id}
    return {
        "rows": len(rows),
        "unique_ids": len(ids),
        "n_rows": len(row_titles),
        "hero_count": len(heroes),
        "hero_titles": [
            h.title
            for h in sorted(heroes, key=lambda x: int(x.carousel_x or 0))[:10]
        ],
        "first_3_row_titles": row_titles[:3],
        "first_8_row_titles": row_titles[:8],
    }


def scrape_combo(label: str, auth_profile: str, user_profile: str, path: str) -> dict:
    auth = resolve_auth_profile(auth_profile)
    user = resolve_auth_profile(user_profile)
    if not auth.auth_token:
        return {"label": label, "path": path, "error": f"missing AUTH for {auth_profile}"}
    cfg = ScrapeConfig(
        url_path=path,
        endpoint=PRODUCTION_ENDPOINT,
        query_file=QUERY_FILE,
        timeout=45,
        retries=2,
        page_size=50,
        module_page_size=1,
        app_version="5.0.0",
        device_type="mobile",
        platform="android",
        auth_token=auth.auth_token,
        x_vix_user_token=user.x_vix_user_token,
        installation_id=INSTALL,
        auth_profile=label,
        extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
        download_images=False,
        debug=False,
    )
    try:
        rows = TitleScraper(cfg).scrape()
    except Exception as exc:  # noqa: BLE001
        return {
            "label": label,
            "path": path,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "auth_jwt": jwt_meta(auth.auth_token),
            "user_jwt": jwt_meta(user.x_vix_user_token),
        }
    summary = summarize_rows(rows)
    summary.update(
        {
            "label": label,
            "path": path,
            "auth_jwt": jwt_meta(auth.auth_token),
            "user_jwt": jwt_meta(user.x_vix_user_token),
        }
    )
    return summary


def env_inventory() -> None:
    secretish = re.compile(r"(TOKEN|SECRET|PASSWORD|KEY|BEARER|AUTH|JWT|COOKIE)", re.I)
    print("=== .env inventory (values redacted) ===")
    for k in sorted(os.environ):
        if not (k.startswith("VIX_") or k.startswith("AUTH_") or k.startswith("X_VIX_")):
            continue
        v = os.environ.get(k) or ""
        if secretish.search(k) or (len(v) > 40 and v.count(".") == 2):
            print(f"{k}: <present len={len(v)}>")
        else:
            print(f"{k}: {v}")

    print("\n=== profile JWT claims ===")
    for name in ("default", "wc"):
        creds = resolve_auth_profile(name)
        print(f"profile={name} AUTH={jwt_meta(creds.auth_token)}")
        print(f"profile={name} USER={jwt_meta(creds.x_vix_user_token)}")


def control_auth_only() -> None:
    print("\n=== Auth-only control (no user token) ===")
    for label, auth_p in (("auth0-only", "default"), ("selfmint-only", "wc")):
        auth = resolve_auth_profile(auth_p)
        cfg = ScrapeConfig(
            url_path="/ondemandplus",
            endpoint=PRODUCTION_ENDPOINT,
            timeout=25,
            retries=0,
            auth_token=auth.auth_token,
            x_vix_user_token=None,
            installation_id=INSTALL,
            extra_headers={"Accept-Language": "es-MX,es;q=0.9"},
        )
        client = GraphQLClient(cfg)
        client._headers.pop("x-vix-user-token", None)
        q = "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }"
        try:
            payload = client.execute(q, {"urlPath": "/ondemandplus"}, allow_errors=True)
            page = ((payload.get("data") or {}).get("uiPage"))
            if page:
                print(f"{label}: OK pageName={page.get('pageName')!r}")
            else:
                errs = graphql_error_messages(payload)
                print(f"{label}: FAIL {(errs[0] if errs else 'null')[:140]}")
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: EXC {type(exc).__name__}: {str(exc)[:120]}")


def main() -> None:
    load_dotenv(Path(".env"))
    env_inventory()
    control_auth_only()

    print("\n=== live full scrapes of /ondemandplus ===")
    combos = [
        ("auth0+user+install", "default", "default"),
        ("selfmint+user+install", "wc", "wc"),
        ("selfmint+default-user+install", "wc", "default"),
        ("auth0+wc-user+install", "default", "wc"),
    ]
    results = []
    for label, auth_p, user_p in combos:
        print(f"scraping {label} ...", flush=True)
        res = scrape_combo(label, auth_p, user_p, "/ondemandplus")
        results.append(res)
        if "error" in res:
            print(f"  FAIL {res['error']}")
        else:
            print(
                f"  OK rows={res['rows']} unique={res['unique_ids']} "
                f"n_rows={res['n_rows']} heroes={res['hero_count']}"
            )
            print(f"  hero={res['hero_titles']}")
            print(f"  row_titles[:3]={res['first_3_row_titles']}")

    # Compare current CSV snapshot
    csv_path = Path("output/layout_compare/ondemandplus_titles.csv")
    if csv_path.is_file():
        import csv

        with csv_path.open(encoding="utf-8", newline="") as fh:
            existing = list(csv.DictReader(fh))
        heroes = [r for r in existing if r.get("is_hero", "").lower() == "true"]
        seen: dict[str, int] = {}
        for r in existing:
            t = (r.get("row_title") or "").strip()
            if t and t not in seen:
                seen[t] = int(r.get("carousel_y") or 0)
        row_titles = [t for t, _ in sorted(seen.items(), key=lambda kv: kv[1])]
        print("\n=== current CSV ondemandplus_titles.csv ===")
        print(
            f"rows={len(existing)} auth_profiles={sorted({r.get('auth_profile') for r in existing})} "
            f"n_rows={len(row_titles)}"
        )
        print(f"hero={[r.get('title') for r in heroes[:10]]}")
        print(f"row_titles[:3]={row_titles[:3]}")

    out = Path("output/layout_compare/ondemandplus_auth_probe.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
