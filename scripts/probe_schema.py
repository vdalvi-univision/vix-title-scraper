"""Probe ViX staging GraphQL schema (does not print tokens)."""

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


def gql(query: str, variables: dict | None = None) -> tuple[object, dict]:
    endpoint = os.environ["VIX_GRAPHQL_ENDPOINT"]
    token = os.environ["AUTH_TOKEN"]
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "insomnia/9.3.1",
        "x-vix-app-version": os.getenv("VIX_APP_VERSION", "5.0.0"),
        "x-vix-device-type": os.getenv("VIX_DEVICE_TYPE", "mobile"),
        "x-vix-platform": os.getenv("VIX_PLATFORM", "android"),
    }
    if os.getenv("X_VIX_USER_TOKEN"):
        headers["x-vix-user-token"] = os.environ["X_VIX_USER_TOKEN"]
    req = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def type_info(name: str) -> dict | None:
    status, payload = gql(
        """
        query TypeInfo($n: String!) {
          __type(name: $n) {
            name
            kind
            fields(includeDeprecated: true) {
              name
              args {
                name
                type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
              }
              type { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
            }
            inputFields {
              name
              type { kind name ofType { kind name ofType { kind name } } }
            }
            possibleTypes { name }
            enumValues { name }
          }
        }
        """,
        {"n": name},
    )
    if status != 200:
        print(f"==== {name} HTTP {status}")
        print(json.dumps(payload.get("errors") or payload, indent=2)[:2000])
        return None
    data = (payload.get("data") or {}).get("__type")
    print(f"==== {name} {'FOUND' if data else 'MISSING'}")
    return data


def print_type(name: str) -> None:
    data = type_info(name)
    if not data:
        return
    if data.get("inputFields"):
        print("INPUT", json.dumps(data["inputFields"], indent=2)[:3000])
    if data.get("possibleTypes"):
        print("POSSIBLE", [p["name"] for p in data["possibleTypes"]])
    if data.get("enumValues"):
        print("ENUM", [e["name"] for e in data["enumValues"]])
    for f in data.get("fields") or []:
        args = ", ".join(a["name"] for a in (f.get("args") or []))
        print(f"  {f['name']}({args}) -> {json.dumps(f['type'])[:160]}")


def main() -> int:
    load_env()
    names = sys.argv[1:] or [
        "PaginationParams",
        "PaginationInput",
        "UiPage",
        "UiModuleConnection",
        "UiModuleEdge",
        "UiModule",
        "UiVideoCarousel",
        "UiHeroCarousel",
        "UiVideoCarouselItem",
        "UiHeroCarouselItem",
        "Video",
        "PageInfo",
    ]
    for name in names:
        print_type(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
