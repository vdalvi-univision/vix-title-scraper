"""Probe seed paths and clientConfig for explorer discovery (no tokens printed)."""

from __future__ import annotations

import json
import os
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
        with urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        return exc.code, payload


DISCOVERY_QUERY = """
query ProbePage($urlPath: ID!) {
  uiPage(urlPath: $urlPath) {
    urlPath
    pageName
    provider
    pageMetadata {
      title
      canonicalUrl
      altUrls
      breadcrumbs { title urlPath }
    }
    uiModules(pagination: { first: 8 }) {
      totalCount
      pageInfo { hasNextPage endCursor }
      edges {
        node {
          __typename
          moduleType
          ... on UiPageCarousel {
            id
            title
            contents(pagination: { first: 30 }) {
              edges { node { name urlPath } }
            }
          }
          ... on UiInlinePage { ctaUrlPath ctaText }
          ... on UiInlinePromo { ctaUrlPath }
          ... on UiHeroCarousel {
            id
            title
            contents(pagination: { first: 8 }) {
              edges {
                node {
                  heroTarget {
                    __typename
                    ... on UiPage { urlPath pageName }
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
    status, payload = gql("query { clientConfig { defaultUrlPath } }")
    print("clientConfig status", status)
    if payload.get("errors"):
        print("clientConfig errors", payload["errors"][0].get("message"))
    print("clientConfig data", payload.get("data"))

    for path in ["/", "/home", "/inicio", "/browse", "/micro-dramas", "/novelas", "/cinema"]:
        status, payload = gql(DISCOVERY_QUERY, {"urlPath": path})
        errs = payload.get("errors") or []
        page = ((payload.get("data") or {}).get("uiPage"))
        err_msg = errs[0].get("message") if errs else None
        print("---", path, "HTTP", status, "page=", bool(page), "err=", err_msg)
        if not page:
            continue
        meta = page.get("pageMetadata") or {}
        print(
            "  pageName=",
            page.get("pageName"),
            "modules=",
            ((page.get("uiModules") or {}).get("totalCount")),
            "canonical=",
            meta.get("canonicalUrl"),
        )
        discovered: list[str] = []
        for edge in ((page.get("uiModules") or {}).get("edges") or []):
            node = (edge or {}).get("node") or {}
            if node.get("__typename") == "UiPageCarousel":
                for ce in ((node.get("contents") or {}).get("edges") or []):
                    cn = (ce or {}).get("node") or {}
                    if cn.get("urlPath"):
                        discovered.append(cn["urlPath"])
            if node.get("ctaUrlPath"):
                discovered.append(node["ctaUrlPath"])
            if node.get("__typename") == "UiHeroCarousel":
                for ce in ((node.get("contents") or {}).get("edges") or []):
                    target = (((ce or {}).get("node") or {}).get("heroTarget") or {})
                    if target.get("urlPath"):
                        discovered.append(target["urlPath"])
            for crumb in meta.get("breadcrumbs") or []:
                if isinstance(crumb, dict) and crumb.get("urlPath"):
                    discovered.append(crumb["urlPath"])
        print("  discovered", len(discovered), "sample", discovered[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
