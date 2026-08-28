"""Probe ondemand hubs and list Query fields related to pages."""

from __future__ import annotations

import json
import os
import re
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


def gql(query: str, variables: dict | None = None) -> tuple[int, dict]:
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
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:300]}


QUERY = """
query ProbePage($urlPath: ID!) {
  uiPage(urlPath: $urlPath) {
    urlPath
    pageName
    uiModules(pagination: { first: 10 }) {
      totalCount
      edges {
        node {
          __typename
          moduleType
          ... on UiPageCarousel {
            title
            contents(pagination: { first: 50 }) {
              edges { node { name urlPath } }
            }
          }
          ... on UiInlinePage { ctaUrlPath }
          ... on UiInlinePromo { ctaUrlPath }
          ... on UiVideoCarousel { title }
          ... on UiHeroCarousel {
            title
            contents(pagination: { first: 10 }) {
              edges {
                node {
                  heroTarget {
                    __typename
                    ... on UiPage { urlPath }
                    ... on UiHeroPage { urlPath }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def main() -> int:
    load_env()
    status, payload = gql('query { __type(name: "Query") { fields { name } } }')
    names = [f["name"] for f in (((payload.get("data") or {}).get("__type") or {}).get("fields") or [])]
    interesting = [n for n in names if re.search(r"page|nav|menu|tab|hub|layout|path|genre|collection", n, re.I)]
    print("interesting fields:", sorted(interesting))

    for path in [
        "/ondemand",
        "/ondemandplus",
        "/premium",
        "/sports",
        "/deportes",
        "/kids",
        "/ninos",
        "/live",
        "/tv",
        "/channels",
        "/search",
        "/originals",
        "/peliculas",
        "/series",
        "/free",
        "/gratis",
    ]:
        status, payload = gql(QUERY, {"urlPath": path})
        errs = payload.get("errors") or []
        page = ((payload.get("data") or {}).get("uiPage"))
        print("---", path, "ok=", bool(page), "err=", (errs[0].get("message") if errs else None))
        if not page:
            continue
        mods = (page.get("uiModules") or {})
        print("  name=", page.get("pageName"), "modules=", mods.get("totalCount"))
        paths: list[str] = []
        for edge in mods.get("edges") or []:
            node = (edge or {}).get("node") or {}
            tn = node.get("__typename")
            if tn == "UiPageCarousel":
                for ce in ((node.get("contents") or {}).get("edges") or []):
                    cn = (ce or {}).get("node") or {}
                    if cn.get("urlPath"):
                        paths.append(f"{cn.get('name')}:{cn['urlPath']}")
            if node.get("ctaUrlPath"):
                paths.append(node["ctaUrlPath"])
            if tn == "UiHeroCarousel":
                for ce in ((node.get("contents") or {}).get("edges") or []):
                    t = (((ce or {}).get("node") or {}).get("heroTarget") or {})
                    if t.get("urlPath"):
                        paths.append(t["urlPath"])
        print("  paths sample:", paths[:25], "count", len(paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
