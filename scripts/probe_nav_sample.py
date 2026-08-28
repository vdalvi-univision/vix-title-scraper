"""Fetch uiNavigation sample paths (no tokens)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def load_env(path: Path = Path(".env")) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def gql(query: str) -> dict:
    body = json.dumps({"query": query}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {os.environ['AUTH_TOKEN']}",
        "User-Agent": "insomnia/9.3.1",
        "x-vix-app-version": "5.0.0",
        "x-vix-device-type": "mobile",
        "x-vix-platform": "android",
    }
    req = Request(os.environ["VIX_GRAPHQL_ENDPOINT"], data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return json.loads(exc.read().decode("utf-8", errors="replace"))


def walk(items: list, out: list[tuple[str, str]], prefix: str = "") -> None:
    for item in items or []:
        text = item.get("text") or ""
        path = item.get("urlPath") or ""
        label = f"{prefix}{text}" if text else prefix
        if path:
            out.append((label, path))
        walk(item.get("subItems") or [], out, prefix=f"{label}/")


def main() -> int:
    load_env()
    payload = gql(
        """
        query {
          uiNavigation {
            __typename
            id
            menuType
            trackingId
            sections {
              items {
                id
                text
                urlPath
                action
                itemType
                iconName
                icon { link }
                subItems {
                  id
                  text
                  urlPath
                  action
                  itemType
                  subItems {
                    id
                    text
                    urlPath
                    action
                    itemType
                  }
                }
              }
            }
          }
        }
        """
    )
    if payload.get("errors"):
        print("errors:", payload["errors"][0].get("message"))
        print(json.dumps(payload.get("errors"), indent=2)[:1500])
        return 1

    menus = (payload.get("data") or {}).get("uiNavigation") or []
    print("menus", len(menus))
    pairs: list[tuple[str, str]] = []
    for menu in menus:
        print("menuType=", menu.get("menuType"), "sections=", len(menu.get("sections") or []))
        for section in menu.get("sections") or []:
            walk(section.get("items") or [], pairs)
    print("paths", len(pairs))
    for label, path in pairs:
        print(f"  {label!r} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
