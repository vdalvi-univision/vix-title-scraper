"""Print argument types for specific fields."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def load_env(path: Path = Path(".env")) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


def gql(query: str, variables: dict | None = None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
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
        with urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        return json.loads(e.read().decode())


def field_args(type_name: str, field_name: str) -> None:
    payload = gql(
        """
        query($n: String!) {
          __type(name: $n) {
            fields {
              name
              args {
                name
                type { kind name ofType { kind name ofType { kind name } } }
              }
            }
          }
        }
        """,
        {"n": type_name},
    )
    fields = (((payload.get("data") or {}).get("__type") or {}).get("fields") or [])
    for f in fields:
        if f["name"] == field_name:
            print(type_name, ".", field_name, json.dumps(f["args"], indent=2))
            return
    print(type_name, ".", field_name, "NOT FOUND")


load_env()
field_args("UiPage", "uiModules")
field_args("UiVideoCarousel", "contents")
field_args("UiHeroCarousel", "contents")
field_args("Query", "videoCarouselContents")
field_args("Query", "heroCarouselContents")
