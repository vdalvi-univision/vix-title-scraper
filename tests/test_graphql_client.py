"""GraphQL client: schema-safe field stripping and complexity handling."""

from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from vix_scraper.client import (
    GraphQLClient,
    is_complexity_error,
    items_is_connection_mismatch,
    optional_module_fields_to_strip,
    rewrite_list_items_as_connection,
    strip_selection_fields,
    unknown_graphql_fields,
)
from vix_scraper.errors import GraphQLComplexityError, GraphQLError
from vix_scraper.models import ScrapeConfig


class FakeResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _cfg() -> ScrapeConfig:
    return ScrapeConfig(endpoint="https://example.test/gql", retries=3, timeout=5)


def test_unknown_graphql_fields_parsed():
    msgs = [
        'Cannot query field "displayLimit" on type "UiVideoCarousel".',
        'Cannot query field "maxItems" on type "UiVideoCarousel".',
    ]
    assert unknown_graphql_fields(msgs) == ["displayLimit", "maxItems"]


def test_strip_selection_fields_drops_limit_fields():
    query = """
query Q {
  uiPage {
    uiModules {
      edges {
        node {
          ... on UiVideoCarousel {
            id
            displayLimit
            maxItems
            visibleCount
            contents { totalCount }
          }
        }
      }
    }
  }
}
"""
    stripped = strip_selection_fields(query, ["displayLimit", "maxItems", "visibleCount"])
    assert "displayLimit" not in stripped
    assert "maxItems" not in stripped
    assert "visibleCount" not in stripped
    assert "contents { totalCount }" in stripped
    assert "id" in stripped


def test_strip_selection_fields_handles_inline_fields():
    query = "query { uiPage { uiModules { edges { node { displayLimit maxItems id } } } } }"
    stripped = strip_selection_fields(query, ["displayLimit", "maxItems"])
    assert "displayLimit" not in stripped
    assert "maxItems" not in stripped
    assert "id" in stripped
    assert "uiModules" in stripped


def test_is_complexity_error():
    assert is_complexity_error("Query complexity of 109776 is over the maximum of 100000")
    assert not is_complexity_error('Cannot query field "displayLimit" on type "UiVideoCarousel".')


def test_unknown_fields_are_stripped_and_request_retried(monkeypatch):
    queries: list[str] = []

    def fake_urlopen(request, timeout=None):
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        queries.append(body["query"])
        if "displayLimit" in body["query"]:
            err = json.dumps(
                {
                    "errors": [
                        {
                            "message": 'Cannot query field "displayLimit" on type "UiVideoCarousel".'
                        }
                    ]
                }
            )
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(err.encode("utf-8")),
            )
        return FakeResponse(json.dumps({"data": {"uiPage": {"urlPath": "/ondemandplus"}}}))

    monkeypatch.setattr("vix_scraper.client.urlopen", fake_urlopen)
    client = GraphQLClient(_cfg())
    query = "query { uiPage { uiModules { edges { node { displayLimit id } } } } }"
    payload = client.execute(query, {"urlPath": "/ondemandplus"})
    assert payload["data"]["uiPage"]["urlPath"] == "/ondemandplus"
    assert len(queries) == 2
    assert "displayLimit" in queries[0]
    assert "displayLimit" not in queries[1]


def test_complexity_error_is_not_retried_with_same_query(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        del request, timeout
        calls["n"] += 1
        err = json.dumps(
            {
                "errors": [
                    {"message": "Query complexity of 109776 is over the maximum of 100000"}
                ]
            }
        )
        raise HTTPError(
            "https://example.test/gql",
            400,
            "Bad Request",
            hdrs=None,
            fp=BytesIO(err.encode("utf-8")),
        )

    monkeypatch.setattr("vix_scraper.client.urlopen", fake_urlopen)
    client = GraphQLClient(_cfg())
    with pytest.raises(GraphQLComplexityError, match="109776"):
        client.execute("query { uiPage { urlPath } }", {"contentPagination": {"first": 25}})
    assert calls["n"] == 1


def test_unknown_fields_do_not_crash_layout_scrape(monkeypatch):
    from vix_scraper.scraper import TitleScraper

    page = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "totalCount": 1,
                    "pageInfo": {"hasNextPage": False, "endCursor": "m1"},
                    "edges": [
                        {
                            "cursor": "m1",
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Del cine a tu pantalla",
                                "id": "cine",
                                "contents": {
                                    "totalCount": 2,
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "edges": [
                                        {
                                            "node": {
                                                "__typename": "UiVideoCard",
                                                "id": "n1",
                                                "video": {
                                                    "id": "v1",
                                                    "mcpId": "1",
                                                    "title": "Papá o mamá",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "__typename": "UiVideoCarouselItem",
                                                "id": "n2",
                                                "video": {
                                                    "id": "v2",
                                                    "mcpId": "2",
                                                    "title": "Radical",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        },
                                    ],
                                },
                            },
                        }
                    ],
                },
            }
        }
    }

    def fake_urlopen(request, timeout=None):
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        if "displayLimit" in body["query"] or "maxItems" in body["query"]:
            err = json.dumps(
                {
                    "errors": [
                        {
                            "message": 'Cannot query field "displayLimit" on type "UiVideoCarousel".'
                        },
                        {
                            "message": 'Cannot query field "maxItems" on type "UiVideoCarousel".'
                        },
                    ]
                }
            )
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(err.encode("utf-8")),
            )
        return FakeResponse(json.dumps(page))

    monkeypatch.setattr("vix_scraper.client.urlopen", fake_urlopen)
    cfg = ScrapeConfig(
        endpoint="https://example.test/gql",
        url_path="/ondemandplus",
        query=(
            "query Q($urlPath: ID!, $uiModulesPagination: PaginationParams, "
            "$contentPagination: PaginationParams) { uiPage(urlPath: $urlPath) { "
            "uiModules(pagination: $uiModulesPagination) { totalCount pageInfo { "
            "hasNextPage endCursor } edges { cursor node { __typename ... on "
            "UiVideoCarousel { id title displayLimit maxItems contents(pagination: "
            "$contentPagination) { totalCount pageInfo { hasNextPage endCursor } "
            "edges { node { __typename ... on UiVideoCard { id video { id mcpId "
            "title videoType } } ... on UiVideoCarouselItem { id video { id mcpId "
            "title videoType } } } } } } } } } } }"
        ),
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
        retries=1,
        timeout=5,
    )
    rows = TitleScraper(cfg, client=GraphQLClient(cfg)).scrape()
    titles = [r.title for r in rows if r.video_type != "EMPTY"]
    assert titles == ["Papá o mamá", "Radical"]


def test_non_complexity_400_does_not_sleep_retry(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout=None):
        del request, timeout
        calls["n"] += 1
        err = json.dumps({"errors": [{"message": "INVALID_TOKEN"}]})
        raise HTTPError(
            "https://example.test/gql",
            400,
            "Bad Request",
            hdrs=None,
            fp=BytesIO(err.encode("utf-8")),
        )

    monkeypatch.setattr("vix_scraper.client.urlopen", fake_urlopen)
    client = GraphQLClient(_cfg())
    with pytest.raises(GraphQLError, match="INVALID_TOKEN"):
        client.execute("query { uiPage { urlPath } }")
    assert calls["n"] == 1


def test_optional_items_type_mismatch_strips_items_not_edges():
    msgs = [
        'Fragment cannot be spread here as objects of type "UiVideoCarouselItemConnection" '
        "can never be of type \"UiVideoCard\"."
    ]
    assert optional_module_fields_to_strip(msgs) == ["items"]
    query = (
        "query { uiPage { uiModules { edges { node { ... on UiVideoCarousel { "
        "items { ... on UiVideoCard { id } } contents { totalCount edges { node { id } } } } } } } } }"
    )
    stripped = strip_selection_fields(query, ["items"])
    assert "items {" not in stripped
    assert "contents { totalCount edges { node { id } } }" in stripped


def test_items_connection_mismatch_rewrites_instead_of_dropping_pins():
    msgs = [
        'Fragment cannot be spread here as objects of type "UiVideoCarouselItemConnection" '
        'can never be of type "UiVideoCard".'
    ]
    assert items_is_connection_mismatch(msgs) is True
    query = (
        "query { uiPage { uiModules { edges { node { ... on UiVideoCarousel { "
        "items { ... on UiVideoCard { id } } contents { totalCount edges { node { id } } } } } } } } }"
    )
    rewritten = rewrite_list_items_as_connection(query)
    assert "items {" in rewritten
    assert "edges" in rewritten
    assert "... on UiVideoCard { id }" in rewritten
    assert "contents { totalCount edges { node { id } } }" in rewritten
    assert rewritten != query


def test_items_connection_mismatch_retries_with_connection_shape(monkeypatch):
    queries: list[str] = []

    def fake_urlopen(request, timeout=None):
        del timeout
        body = json.loads(request.data.decode("utf-8"))
        queries.append(body["query"])
        if "items {" in body["query"] and "edges" not in body["query"].split("items {", 1)[1].split("contents", 1)[0]:
            err = json.dumps(
                {
                    "errors": [
                        {
                            "message": (
                                'Fragment cannot be spread here as objects of type '
                                '"UiVideoCarouselItemConnection" can never be of type "UiVideoCard".'
                            )
                        }
                    ]
                }
            )
            raise HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(err.encode("utf-8")),
            )
        return FakeResponse(json.dumps({"data": {"uiPage": {"urlPath": "/ondemandplus"}}}))

    monkeypatch.setattr("vix_scraper.client.urlopen", fake_urlopen)
    client = GraphQLClient(_cfg())
    query = (
        "query { uiPage { uiModules { edges { node { ... on UiVideoCarousel { "
        "items { ... on UiVideoCard { id } } contents { totalCount edges { node { id } } } } } } } } }"
    )
    payload = client.execute(query, {"urlPath": "/ondemandplus"})
    assert payload["data"]["uiPage"]["urlPath"] == "/ondemandplus"
    assert len(queries) == 2
    assert "node {" in queries[1]
    items_block = queries[1].split("items", 1)[1].split("contents", 1)[0]
    assert "edges" in items_block
    assert "contents { totalCount edges { node { id } } }" in queries[1]
