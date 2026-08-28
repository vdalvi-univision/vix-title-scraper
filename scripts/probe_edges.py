"""Resolve nested ofType names for connection edges."""

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
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


def gql(query: str, variables=None):
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


def unwrap(t):
    while t and t.get("ofType"):
        t = t["ofType"]
    return t


load_env()
for type_name in [
    "UiVideoRecommendationConnection",
    "UiBecauseYouCardConnection",
    "UiMixedContentCardConnection",
]:
    payload = gql(
        """
        query($n: String!) {
          __type(name: $n) {
            fields {
              name
              type { kind name ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } } }
            }
          }
        }
        """,
        {"n": type_name},
    )
    fields = (((payload.get("data") or {}).get("__type") or {}).get("fields") or [])
    for f in fields:
        if f["name"] == "edges":
            print(type_name, "edges ->", json.dumps(f["type"], indent=2))
            leaf = unwrap(f["type"])
            print("  leaf:", leaf)
            if leaf and leaf.get("name"):
                edge = gql(
                    """
                    query($n: String!) {
                      __type(name: $n) {
                        fields {
                          name
                          type { kind name ofType { kind name ofType { kind name } } }
                        }
                      }
                    }
                    """,
                    {"n": leaf["name"]},
                )
                print("  edge fields:", json.dumps((((edge.get("data") or {}).get("__type") or {}).get("fields")), indent=2)[:2000])
