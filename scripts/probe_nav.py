"""Inspect uiNavigation Query field and sample response."""

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


def gql(query: str, variables: dict | None = None) -> dict:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
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


def unwrap(t: dict | None) -> dict | None:
    while t and t.get("ofType"):
        t = t["ofType"]
    return t


def main() -> int:
    load_env()
    payload = gql(
        """
        query {
          __type(name: "Query") {
            fields {
              name
              args {
                name
                type { kind name ofType { kind name ofType { kind name } } }
              }
              type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
            }
          }
        }
        """
    )
    for field in (((payload.get("data") or {}).get("__type") or {}).get("fields") or []):
        if field["name"] == "uiNavigation":
            print("uiNavigation args:", json.dumps(field.get("args"), indent=2))
            print("uiNavigation type:", json.dumps(field.get("type"), indent=2))
            leaf = unwrap(field.get("type"))
            print("leaf:", leaf)

    # Try progressive selection sets
    candidates = [
        "query { uiNavigation { __typename } }",
        "query { uiNavigation { items { __typename } } }",
        """
        query {
          uiNavigation {
            __typename
            ... on UiNavigation {
              items {
                __typename
              }
            }
          }
        }
        """,
    ]
    for q in candidates:
        payload = gql(q)
        print("TRY", " ".join(q.split())[:80])
        print("  errors:", ((payload.get("errors") or [{}])[0].get("message")))
        print("  data keys:", list(((payload.get("data") or {}) or {}).keys()))

    # Introspect returned type name if known from earlier leaf
    for type_name in [
        "UiNavigation",
        "UiNavigationV2",
        "Navigation",
        "UiNav",
        "BottomNavigation",
        "TopNavigation",
        "UiBottomNavigation",
        "UiTopNavigation",
        "UiTabNavigation",
    ]:
        payload = gql(
            """
            query($n: String!) {
              __type(name: $n) {
                name
                kind
                fields { name }
                possibleTypes { name }
              }
            }
            """,
            {"n": type_name},
        )
        t = ((payload.get("data") or {}).get("__type"))
        if t:
            print("FOUND", type_name, "kind", t.get("kind"), "fields", [f["name"] for f in (t.get("fields") or [])][:40])
            if t.get("possibleTypes"):
                print("  possible", [p["name"] for p in t["possibleTypes"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
