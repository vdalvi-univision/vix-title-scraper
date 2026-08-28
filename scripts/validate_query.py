"""Validate GraphQL query files against the live endpoint (no tokens printed)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def load_env(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def gql(query: str, variables: dict | None = None) -> tuple[int, dict]:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {os.environ['AUTH_TOKEN']}",
        "User-Agent": "insomnia/9.3.1",
        "x-vix-app-version": os.getenv("VIX_APP_VERSION", "5.0.0"),
        "x-vix-device-type": os.getenv("VIX_DEVICE_TYPE", "mobile"),
        "x-vix-platform": os.getenv("VIX_PLATFORM", "android"),
    }
    req = Request(os.environ["VIX_GRAPHQL_ENDPOINT"], data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:800]}


def main() -> int:
    load_env()
    paths = [Path(p) for p in (sys.argv[1:] or ["queries/request.graphql", "queries/navigation.graphql"])]
    ok = True
    for path in paths:
        query = path.read_text(encoding="utf-8")
        if "UiNavigationBootstrap" in query or "uiNavigation" in query and "uiPage" not in query:
            status, payload = gql(query)
        else:
            status, payload = gql(
                query,
                {
                    "urlPath": "/micro-dramas",
                    "uiModulesPagination": {"first": 1},
                    "contentPagination": {"first": 2},
                },
            )
        errors = payload.get("errors") or []
        print(f"==== {path} HTTP {status} errors={len(errors)}")
        for err in errors[:12]:
            print(" ", err.get("message"))
            ok = False
        if not errors:
            data = payload.get("data") or {}
            if "uiPage" in data:
                page = data["uiPage"] or {}
                mods = ((page.get("uiModules") or {}).get("edges") or [])
                print(f"  page={page.get('pageName')} modules_edges={len(mods)}")
            if "uiNavigation" in data:
                print(f"  nav_menus={len(data.get('uiNavigation') or [])}")
            if "clientConfig" in data:
                print(f"  defaultUrlPath={(data.get('clientConfig') or {}).get('defaultUrlPath')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
