"""Pagination must not silently stop when totalCount says more modules remain."""

from __future__ import annotations

import pytest

from vix_scraper.errors import PaginationError
from vix_scraper.models import ScrapeConfig
from vix_scraper.pagination import PagePaginator, layout_request_kind


def _contents_vars(captured: list[dict]) -> list[dict]:
    return [c for c in captured if "contentPagination" in c]


def _meta_from_payload(payload: dict, *, kind: str) -> dict:
    page = (((payload.get("data") or {}).get("uiPage")) or {})
    conn = page.get("uiModules") if isinstance(page.get("uiModules"), dict) else {}
    total = conn.get("totalCount")
    url = page.get("urlPath") or "/ondemandplus"
    if kind == "probe":
        return {
            "data": {
                "uiPage": {
                    "urlPath": url,
                    "pageName": page.get("pageName") or "",
                    "moduleCount": {"totalCount": total},
                }
            }
        }
    return payload


def _stub_modules_meta(query: str, variables: dict | None, payload: dict) -> dict | None:
    """Return a probe/index stub so test clients only implement contents hydrate."""
    kind = layout_request_kind(query, variables)
    if kind == "contents":
        return None
    return _meta_from_payload(payload, kind=kind)


class FakeClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.requests: list[tuple[str, dict]] = []

    def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
        del allow_errors
        vars_ = dict(variables or {})
        self.requests.append((query, vars_))
        kind = layout_request_kind(query, vars_)
        if kind == "probe":
            src = next((p for p in self.payloads if ((p.get("data") or {}).get("uiPage")) is not None), None)
            if src is None:
                return {"data": {"uiPage": None}}
            return _meta_from_payload(src, kind="probe")
        if kind == "index":
            return self._index_payload()
        if self.calls < len(self.payloads):
            payload = self.payloads[self.calls]
            self.calls += 1
            return payload
        if self.payloads:
            return self.payloads[-1]
        raise AssertionError("unexpected extra GraphQL call")

    def _index_payload(self) -> dict:
        edges: list[dict] = []
        seen: set[str] = set()
        total = None
        url = "/ondemandplus"
        for payload in self.payloads:
            page = ((payload.get("data") or {}).get("uiPage"))
            if not isinstance(page, dict):
                continue
            url = page.get("urlPath") or url
            conn = page.get("uiModules") if isinstance(page.get("uiModules"), dict) else {}
            if total is None:
                total = conn.get("totalCount")
            for edge in conn.get("edges") or []:
                node = dict((edge or {}).get("node") or {})
                mid = str(node.get("id") or node.get("trackingId") or "")
                key = mid or f"anon-{len(edges)}"
                if key in seen:
                    continue
                seen.add(key)
                node.pop("contents", None)
                edges.append({"cursor": (edge or {}).get("cursor") or "", "node": node})
        if total is None:
            total = len(edges)
        return _page(
            edges,
            has_next=False,
            end_cursor=(edges[-1].get("cursor") or None) if edges else None,
            total=int(total),
        )


def _page(
    edges: list[dict],
    *,
    has_next: bool,
    end_cursor: str | None,
    total: int,
    url_path: str = "/ondemandpluswc",
) -> dict:
    return {
        "data": {
            "uiPage": {
                "urlPath": url_path,
                "uiModules": {
                    "totalCount": total,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                    "edges": edges,
                },
            }
        }
    }


def _edge(module_id: str, cursor: str, *, titles: int = 2) -> dict:
    content_edges = [
        {
            "node": {
                "id": f"{module_id}-i{i}",
                "video": {"id": f"v-{module_id}-{i}", "title": f"T{i}", "videoType": "MOVIE"},
            }
        }
        for i in range(titles)
    ]
    return {
        "cursor": cursor,
        "node": {
            "__typename": "UiVideoCarousel",
            "moduleType": "VIDEO_CAROUSEL",
            "title": module_id,
            "id": module_id,
            "contents": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": content_edges,
            },
        },
    }


def test_recovers_when_has_next_false_but_total_count_remaining():
    """Regression: WC collapse to hero-only when API falsifies hasNextPage after Hero."""
    client = FakeClient(
        [
            _page(
                [_edge("Hero", "c1", titles=11)],
                has_next=False,
                end_cursor=None,
                total=3,
            ),
            _page(
                [_edge("Lo mas buscado", "c2")],
                has_next=False,
                end_cursor=None,
                total=3,
            ),
            _page(
                [_edge("Peliculas", "c3")],
                has_next=False,
                end_cursor=None,
                total=3,
            ),
        ]
    )
    cfg = ScrapeConfig(url_path="/ondemandpluswc", module_page_size=1, page_size=100)
    paginator = PagePaginator(client, cfg, query="query Q { __typename }")
    pages = list(paginator.iter_pages())
    assert len(pages) == 3
    assert pages[1][1].module_after == "c1"
    assert pages[1][1].content_after is None
    assert pages[2][1].module_after == "c2"


def test_wc_like_missing_cursor_completes_thirty_modules():
    """WC: first:1 layout would see Hero, totalCount=30, hasNextPage=false, no cursor.

    Two-phase listing (first: 30, no after) plus per-rail hydrate must finish all
    30 modules and must not raise PaginationError.
    """
    from vix_scraper.scraper import TitleScraper

    ids = ["hero"] + [f"rail-{n}" for n in range(2, 31)]
    listing_cursors = [f"idx-{i}" for i in range(30)]

    class WcClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            kind = layout_request_kind(query, variables)
            pag = dict((variables or {}).get("uiModulesPagination") or {})
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandpluswc",
                            "moduleCount": {"totalCount": 30},
                        }
                    }
                }
            if kind == "index":
                assert pag.get("first") == 30
                assert "after" not in pag
                edges = [
                    {
                        "cursor": listing_cursors[i],
                        "node": {
                            "__typename": "UiHeroCarousel" if i == 0 else "UiVideoCarousel",
                            "moduleType": "HERO_CAROUSEL" if i == 0 else "VIDEO_CAROUSEL",
                            "id": ids[i],
                            "title": ids[i],
                        },
                    }
                    for i in range(30)
                ]
                return _page(edges, has_next=False, end_cursor=listing_cursors[-1], total=30)
            after = pag.get("after")
            if after:
                idx = listing_cursors.index(after) + 1
            else:
                idx = 0
            titles = 0 if idx == 4 else 1
            return _page(
                [_edge(ids[idx], listing_cursors[idx], titles=titles)],
                has_next=False,
                end_cursor=None,
                total=30,
            )

    cfg = ScrapeConfig(
        url_path="/ondemandpluswc",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=WcClient()).scrape()
    ys = sorted({r.carousel_y for r in rows})
    assert ys == list(range(1, 31))
    assert len({r.row_title for r in rows}) == 30


def test_odp_like_with_cursors_still_lists_from_total_count():
    """ODP has real module cursors; still use probe+index so WC PageInfo lies cannot recur."""
    ids = ["hero"] + [f"rail-{n}" for n in range(2, 31)]
    listing_cursors = [f"odp-{i}" for i in range(30)]
    captured: list[dict] = []

    class OdpClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            vars_ = dict(variables or {})
            captured.append(vars_)
            kind = layout_request_kind(query, vars_)
            pag = dict(vars_.get("uiModulesPagination") or {})
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandplus",
                            "moduleCount": {"totalCount": 30},
                        }
                    }
                }
            if kind == "index":
                assert pag.get("first") == 30
                assert "after" not in pag
                edges = [
                    {
                        "cursor": listing_cursors[i],
                        "node": {
                            "__typename": "UiVideoCarousel",
                            "moduleType": "VIDEO_CAROUSEL",
                            "id": ids[i],
                            "title": ids[i],
                        },
                    }
                    for i in range(30)
                ]
                return _page(edges, has_next=False, end_cursor=listing_cursors[-1], total=30)
            after = pag.get("after")
            idx = listing_cursors.index(after) + 1 if after else 0
            return _page(
                [_edge(ids[idx], listing_cursors[idx], titles=1)],
                has_next=True,
                end_cursor=listing_cursors[idx],
                total=30,
            )

    cfg = ScrapeConfig(url_path="/ondemandplus", module_page_size=1, page_size=100)
    pages = list(PagePaginator(OdpClient(), cfg, query="query Q { __typename }").iter_pages())
    assert len(pages) == 30
    index_calls = [c for c in captured if "contentPagination" not in c and "uiModulesPagination" in c]
    assert index_calls
    assert index_calls[0]["uiModulesPagination"]["first"] == 30
    assert "after" not in index_calls[0]["uiModulesPagination"]


def test_wc_like_index_without_edge_cursors_uses_first_n_no_after():
    """If the listing also omits edge cursors, walk first:seen+1 from offset 0 (no invented after)."""
    from vix_scraper.scraper import TitleScraper

    ids = ["hero"] + [f"rail-{n}" for n in range(2, 31)]

    class NoCursorClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            kind = layout_request_kind(query, variables)
            pag = dict((variables or {}).get("uiModulesPagination") or {})
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandpluswc",
                            "moduleCount": {"totalCount": 30},
                        }
                    }
                }
            if kind == "index":
                edges = [
                    {
                        "cursor": "",
                        "node": {
                            "__typename": "UiVideoCarousel",
                            "id": ids[i],
                            "title": ids[i],
                            "moduleType": "VIDEO_CAROUSEL",
                        },
                    }
                    for i in range(30)
                ]
                return _page(edges, has_next=False, end_cursor=None, total=30)
            assert "after" not in pag
            n = int(pag.get("first") or 1)
            edges = [_edge(ids[i], "", titles=1) for i in range(n)]
            return _page(edges, has_next=False, end_cursor=None, total=30)

    cfg = ScrapeConfig(
        url_path="/ondemandpluswc",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=NoCursorClient()).scrape()
    assert sorted({r.carousel_y for r in rows}) == list(range(1, 31))


def test_uses_edge_cursor_when_end_cursor_missing():
    client = FakeClient(
        [
            _page([_edge("Hero", "edge-1")], has_next=True, end_cursor=None, total=2),
            _page([_edge("Rail", "edge-2")], has_next=False, end_cursor=None, total=2),
        ]
    )
    cfg = ScrapeConfig(url_path="/ondemandpluswc", module_page_size=1, page_size=100)
    paginator = PagePaginator(client, cfg, query="query Q { __typename }")
    pages = list(paginator.iter_pages())
    assert len(pages) == 2
    assert pages[1][1].module_after == "edge-1"
    assert pages[1][1].content_after is None


def test_custom_path_probe_null_uses_config_url_path():
    captured: list[dict] = []

    class NullPageClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del query, allow_errors
            captured.append(dict(variables or {}))
            return {"data": {"uiPage": None}}

    cfg = ScrapeConfig(url_path="/deportes", module_page_size=1, page_size=100)
    paginator = PagePaginator(NullPageClient(), cfg, query="query Q { __typename }")
    with pytest.raises(PaginationError, match=r"/deportes"):
        list(paginator.iter_pages())
    assert captured
    assert captured[0]["urlPath"] == "/deportes"


def test_custom_path_missing_cursor_still_walks_modules():
    """WC-like hasNextPage=false + no endCursor works for any url_path, not only WC."""
    captured: list[dict] = []

    class MoviesClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            vars_ = dict(variables or {})
            captured.append(vars_)
            kind = layout_request_kind(query, vars_)
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/movies",
                            "moduleCount": {"totalCount": 3},
                        }
                    }
                }
            if kind == "index":
                edges = [
                    _edge("Hero", "c1", titles=1),
                    _edge("Lo mas buscado", "c2", titles=1),
                    _edge("Peliculas", "c3", titles=1),
                ]
                for edge in edges:
                    edge["cursor"] = ""
                return _page(edges, has_next=False, end_cursor=None, total=3, url_path="/movies")
            n = int((vars_.get("uiModulesPagination") or {}).get("first") or 1)
            ids = ["Hero", "Lo mas buscado", "Peliculas"]
            edges = [_edge(ids[i], "", titles=1) for i in range(n)]
            return _page(edges, has_next=False, end_cursor=None, total=3, url_path="/movies")

    cfg = ScrapeConfig(url_path="/movies", module_page_size=1, page_size=100)
    paginator = PagePaginator(MoviesClient(), cfg, query="query Q { __typename }")
    pages = list(paginator.iter_pages())
    assert len(pages) == 3
    assert all(c.get("urlPath") == "/movies" for c in captured if "urlPath" in c)


def test_null_uipage_mid_scrape_raises():
    client = FakeClient(
        [
            _page([_edge("Hero", "c1")], has_next=True, end_cursor="c1", total=2),
            {"data": {"uiPage": None}},
        ]
    )
    cfg = ScrapeConfig(url_path="/ondemandpluswc", module_page_size=1, page_size=100)
    paginator = PagePaginator(client, cfg, query="query Q { __typename }")
    with pytest.raises(PaginationError, match="uiPage returned null"):
        list(paginator.iter_pages())


def _content_page(
    module_id: str,
    edges: list[dict],
    *,
    has_next: bool,
    end_cursor: str | None,
    total: int,
    module_cursor: str = "m1",
    is_playlist: bool = True,
) -> dict:
    node = {
        "__typename": "UiVideoCarousel",
        "moduleType": "VIDEO_CAROUSEL",
        "title": module_id,
        "id": module_id,
        "isPlaylist": is_playlist,
        "contents": {
            "totalCount": total,
            "pageInfo": {
                "hasNextPage": has_next,
                "endCursor": end_cursor,
            },
            "edges": edges,
        },
    }
    return {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "totalCount": 1,
                    "pageInfo": {"hasNextPage": False, "endCursor": module_cursor},
                    "edges": [
                        {
                            "cursor": module_cursor,
                            "node": node,
                        }
                    ],
                },
            }
        }
    }


def _content_edge(i: int, title: str) -> dict:
    return {
        "node": {
            "id": f"n{i}",
            "video": {"id": f"v{i}", "mcpId": str(i), "title": title, "videoType": "MOVIE"},
        }
    }


def test_content_pagination_stops_at_total_count_despite_has_next():
    """Regression: extras past GraphQL totalCount (Micros 107 vs 106)."""
    client = FakeClient(
        [
            _content_page(
                "micros",
                [_content_edge(1, "A"), _content_edge(2, "B")],
                has_next=True,
                end_cursor="c1",
                total=3,
            ),
            _content_page(
                "micros",
                [_content_edge(3, "C"), _content_edge(4, "Extra")],
                has_next=True,
                end_cursor="c2",
                total=3,
            ),
        ]
    )
    cfg = ScrapeConfig(url_path="/ondemandplus", module_page_size=1, page_size=2)
    paginator = PagePaginator(client, cfg, query="query Q { __typename }")
    pages = list(paginator.iter_pages())
    assert len(pages) == 2
    assert pages[1][1].content_after == "c1"
    assert client.calls == 2


def test_content_pagination_stops_when_cursor_repeats_same_tiles():
    client = FakeClient(
        [
            _content_page(
                "rail",
                [_content_edge(1, "A"), _content_edge(2, "B")],
                has_next=True,
                end_cursor="c1",
                total=10,
            ),
            _content_page(
                "rail",
                [_content_edge(1, "A"), _content_edge(2, "B")],
                has_next=True,
                end_cursor="c2",
                total=10,
            ),
        ]
    )
    cfg = ScrapeConfig(url_path="/ondemandplus", module_page_size=1, page_size=2)
    paginator = PagePaginator(client, cfg, query="query Q { __typename }")
    pages = list(paginator.iter_pages())
    assert len(pages) == 2
    assert client.calls == 2


def test_scraper_row_size_matches_unique_tiles_not_glued_modules():
    from vix_scraper.scraper import TitleScraper

    sibling = {
        "cursor": "m1",
        "node": {
            "__typename": "UiVideoCarousel",
            "moduleType": "VIDEO_CAROUSEL",
            "title": "Other",
            "id": "other",
            "contents": {
                "totalCount": 1,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [_content_edge(9, "Sibling")],
            },
        },
    }

    def wrap(edges, *, has_next, cursor, total):
        return {
            "data": {
                "uiPage": {
                    "urlPath": "/ondemandplus",
                    "uiModules": {
                        "totalCount": 2,
                        "pageInfo": {"hasNextPage": False, "endCursor": "m1"},
                        "edges": [
                            {
                                "cursor": "m1",
                                "node": {
                                    "__typename": "UiVideoCarousel",
                                    "moduleType": "VIDEO_CAROUSEL",
                                    "title": "Nuevo en ViX",
                                    "id": "nuevo",
                                    "isPlaylist": True,
                                    "contents": {
                                        "totalCount": total,
                                        "pageInfo": {
                                            "hasNextPage": has_next,
                                            "endCursor": cursor,
                                        },
                                        "edges": edges,
                                    },
                                },
                            },
                            sibling,
                        ],
                    },
                }
            }
        }

    client = FakeClient(
        [
            wrap(
                [_content_edge(1, "One"), _content_edge(2, "Two")],
                has_next=True,
                cursor="c1",
                total=3,
            ),
            wrap(
                [_content_edge(2, "Two"), _content_edge(3, "Three"), _content_edge(4, "Extra")],
                has_next=True,
                cursor="c2",
                total=3,
            ),
        ]
    )
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=2,
        page_size=2,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    nuevo = [r for r in rows if r.row_title == "Nuevo en ViX"]
    other = [r for r in rows if r.row_title == "Other"]
    assert [r.title for r in nuevo] == ["One", "Two", "Three"]
    assert nuevo[0].row_size == 3
    assert len({r.mcp_id or r.content_id for r in nuevo}) == 3
    assert [r.title for r in other] == ["Sibling"]
    assert all(r.title != "Sibling" for r in nuevo)


def test_layout_content_pagination_never_omits_first():
    """Empty PaginationParams would use GraphQL default first=10 — not the rail length."""
    captured: list[dict] = []

    class RecordingClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = _content_page(
                "nuevo",
                [_content_edge(1, "T1")],
                has_next=False,
                end_cursor=None,
                total=1,
            )
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            captured.append(dict(variables or {}))
            return page

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
    )
    list(PagePaginator(RecordingClient(), cfg, query="query Q { __typename }").iter_pages())
    assert captured
    content = captured[0]["contentPagination"]
    assert "first" in content
    assert content["first"] != 10
    assert content["first"] != 100
    assert captured[0]["uiModulesPagination"]["first"] == 1


def test_catalog_content_pagination_sends_first():
    captured: list[dict] = []

    class RecordingClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = _content_page(
                "nuevo",
                [_content_edge(1, "One"), _content_edge(2, "Two")],
                has_next=False,
                end_cursor=None,
                total=2,
            )
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            captured.append(dict(variables or {}))
            return page

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        module_page_size=1,
        page_size=50,
        paginate_contents=True,
    )
    list(PagePaginator(RecordingClient(), cfg, query="query Q { __typename }").iter_pages())
    assert captured[0]["contentPagination"]["first"] == 50


def test_layout_first_is_remaining_total_not_schema_or_catalog_dump():
    """After totalCount is known, first is remaining rail length — not 10, 12, or 100."""
    captured: list[dict] = []

    class ScriptedClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            first_page = _content_page(
                "cine",
                [_content_edge(1, "Papá o mamá")],
                has_next=True,
                end_cursor="c1",
                total=26,
            )
            meta = _stub_modules_meta(query, variables, first_page)
            if meta is not None:
                return meta
            captured.append(dict(variables or {}))
            first = int((variables or {}).get("contentPagination", {}).get("first") or 0)
            after = (variables or {}).get("contentPagination", {}).get("after")
            if not after:
                return first_page
            return _content_page(
                "cine",
                [_content_edge(i, f"T{i}") for i in range(2, first + 2)],
                has_next=False,
                end_cursor=None,
                total=26,
            )

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
    )
    pages = list(PagePaginator(ScriptedClient(), cfg, query="query Q { __typename }").iter_pages())
    assert len(pages) == 2
    assert "first" in captured[0]["contentPagination"]
    assert captured[0]["contentPagination"]["first"] != 10
    assert captured[0]["contentPagination"]["first"] != 12
    assert captured[0]["contentPagination"]["first"] != 100
    assert captured[1]["contentPagination"]["after"] == "c1"
    assert captured[1]["contentPagination"]["first"] == 26
    assert all(c["contentPagination"].get("first") not in (10, 12, 100) for c in captured)


def test_layout_rows_are_not_truncated_to_schema_default():
    """Regression: empty PaginationParams → schema first=10 dropped Radical at x=21."""
    from vix_scraper.scraper import TitleScraper

    titles = [f"T{i}" for i in range(1, 27)]
    titles[20] = "Radical"

    class ScriptedClient:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            first_page = _content_page(
                "Del cine a tu pantalla",
                [_content_edge(1, titles[0])],
                has_next=True,
                end_cursor="c1",
                total=26,
            )
            meta = _stub_modules_meta(query, variables, first_page)
            if meta is not None:
                return meta
            self.calls += 1
            after = (variables or {}).get("contentPagination", {}).get("after")
            if not after:
                return first_page
            return _content_page(
                "Del cine a tu pantalla",
                [_content_edge(i, titles[i - 1]) for i in range(2, 27)],
                has_next=False,
                end_cursor=None,
                total=26,
            )

    client = ScriptedClient()
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    cine = [r for r in rows if r.row_title == "Del cine a tu pantalla" and r.video_type != "EMPTY"]
    assert len(cine) == 26
    assert cine[20].title == "Radical"
    assert cine[20].carousel_x == 21
    assert cine[0].row_size == 26
    assert client.calls == 2


def test_layout_stops_at_display_limit_not_catalog_total():
    from vix_scraper.scraper import TitleScraper

    def wrap(edges, *, has_next, cursor, total, display_limit):
        return {
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
                                    "title": "Nuevo en ViX",
                                    "id": "nuevo",
                                    "displayLimit": display_limit,
                                    "contents": {
                                        "totalCount": total,
                                        "pageInfo": {
                                            "hasNextPage": has_next,
                                            "endCursor": cursor,
                                        },
                                        "edges": edges,
                                    },
                                },
                            }
                        ],
                    },
                }
            }
        }

    class ScriptedClient:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            first_page = wrap(
                [_content_edge(1, "T1")],
                has_next=True,
                cursor="c1",
                total=62,
                display_limit=15,
            )
            meta = _stub_modules_meta(query, variables, first_page)
            if meta is not None:
                return meta
            self.calls += 1
            after = (variables or {}).get("contentPagination", {}).get("after")
            if not after:
                return first_page
            return wrap(
                [_content_edge(i, f"T{i}") for i in range(2, 16)],
                has_next=True,
                cursor="c2",
                total=62,
                display_limit=15,
            )

    client = ScriptedClient()
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    nuevo = [r for r in rows if r.row_title == "Nuevo en ViX" and r.video_type != "EMPTY"]
    assert [r.title for r in nuevo] == [f"T{i}" for i in range(1, 16)]
    assert nuevo[0].row_size == 15
    assert client.calls == 2


def test_layout_uses_contents_total_when_smaller_than_collection():
    from vix_scraper.scraper import TitleScraper

    client = FakeClient(
        [
            {
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
                                        "collection": {"totalCount": 200},
                                        "contents": {
                                            "totalCount": 3,
                                            "pageInfo": {
                                                "hasNextPage": False,
                                                "endCursor": None,
                                            },
                                            "edges": [
                                                _content_edge(1, "A"),
                                                _content_edge(2, "B"),
                                                _content_edge(3, "C"),
                                            ],
                                        },
                                    },
                                }
                            ],
                        },
                    }
                }
            }
        ]
    )
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    cine = [r for r in rows if r.row_title == "Del cine a tu pantalla" and r.video_type != "EMPTY"]
    assert [r.title for r in cine] == ["A", "B", "C"]
    assert cine[0].row_size == 3
    assert client.calls == 1


def test_layout_walks_module_connection_when_totals_match():
    """No display-limit: contents.totalCount == collection.totalCount → walk until it ends."""
    from vix_scraper.scraper import TitleScraper

    class ScriptedClient:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            after = (variables or {}).get("contentPagination", {}).get("after")
            node = {
                "__typename": "UiVideoCarousel",
                "moduleType": "VIDEO_CAROUSEL",
                "title": "Nuevo en ViX",
                "id": "nuevo",
                "isPlaylist": True,
                "collection": {"totalCount": 4},
                "contents": {
                    "totalCount": 4,
                    "pageInfo": {
                        "hasNextPage": not after,
                        "endCursor": None if after else "c1",
                    },
                    "edges": (
                        [_content_edge(i, f"T{i}") for i in range(2, 5)]
                        if after
                        else [_content_edge(1, "T1")]
                    ),
                },
            }
            payload = {
                "data": {
                    "uiPage": {
                        "urlPath": "/ondemandplus",
                        "uiModules": {
                            "totalCount": 1,
                            "pageInfo": {"hasNextPage": False, "endCursor": "m1"},
                            "edges": [{"cursor": "m1", "node": node}],
                        },
                    }
                }
            }
            meta = _stub_modules_meta(query, variables, payload)
            if meta is not None:
                return meta
            self.calls += 1
            return payload

    client = ScriptedClient()
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    nuevo = [r for r in rows if r.row_title == "Nuevo en ViX" and r.video_type != "EMPTY"]
    assert [r.title for r in nuevo] == ["T1", "T2", "T3", "T4"]
    assert nuevo[0].row_size == 4
    assert client.calls == 2


def test_layout_paginator_source_does_not_hardcode_ten():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "vix_scraper" / "pagination.py"
    text = src.read_text(encoding="utf-8")
    assert '["first"] = 10' not in text
    assert '["first"] = 12' not in text
    assert '["first"] = 15' not in text
    assert '["first"] = 30' not in text
    assert '["first"] = 100' not in text
    assert "schema default" in text.lower() or "defaults to 10" in text


def test_complexity_error_retries_same_cursor_with_halved_first():
    """Transport: halve first and retry the same `after`; do not skip or duplicate edges."""
    from vix_scraper.errors import GraphQLComplexityError

    captured: list[dict] = []

    class ComplexityClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = _content_page(
                "cine",
                [_content_edge(1, "A")],
                has_next=False,
                end_cursor=None,
                total=1,
            )
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            pagination = dict((variables or {}).get("contentPagination") or {})
            captured.append({"first": int(pagination.get("first") or 0), "after": pagination.get("after")})
            first = int(pagination.get("first") or 0)
            if first > 20:
                raise GraphQLComplexityError(
                    "HTTP 400: Query complexity of 109776 is over the maximum of 100000"
                )
            return page

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        module_page_size=1,
        page_size=100,
        paginate_contents=True,
        contents_first_from_total=True,
    )
    pages = list(PagePaginator(ComplexityClient(), cfg, query="query Q { __typename }").iter_pages())
    assert len(pages) == 1
    assert captured[0]["first"] == 80
    assert captured[0]["after"] is None
    assert captured[-1]["first"] <= 20
    assert all(c["after"] is None for c in captured)
    firsts = [c["first"] for c in captured]
    assert firsts == sorted(firsts, reverse=True)


def test_layout_full_rail_one_request_when_first_covers_total():
    """26-title Del cine: lean query sends remaining/ceiling, not a 12-title window."""
    from vix_scraper.pagination import LAYOUT_CONTENTS_COMPLEXITY_FIRST
    from vix_scraper.scraper import TitleScraper

    titles = [f"T{i}" for i in range(1, 27)]
    titles[20] = "Radical"

    class OneShotClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = _content_page(
                "Del cine a tu pantalla",
                [_content_edge(i + 1, titles[i]) for i in range(26)],
                has_next=False,
                end_cursor=None,
                total=26,
            )
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            pagination = dict((variables or {}).get("contentPagination") or {})
            self.calls.append(pagination)
            first = int(pagination.get("first") or 0)
            assert first >= 26
            assert first != 10
            assert first != 12
            return page

    client = OneShotClient()
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    cine = [r for r in rows if r.row_title == "Del cine a tu pantalla" and r.video_type != "EMPTY"]
    assert len(cine) == 26
    assert [r.title for r in cine] == titles
    assert cine[20].title == "Radical"
    assert cine[20].carousel_x == 21
    assert cine[0].row_size == 26
    assert len(client.calls) == 1
    assert client.calls[0]["first"] == LAYOUT_CONTENTS_COMPLEXITY_FIRST
    assert "after" not in client.calls[0]


def test_lo_mas_buscado_thirty_items_in_cms_order():
    from vix_scraper.scraper import TitleScraper

    titles = [f"B{i}" for i in range(1, 31)]

    class OneShotClient:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = _content_page(
                "Lo más buscado",
                [_content_edge(i + 1, titles[i]) for i in range(30)],
                has_next=False,
                end_cursor=None,
                total=30,
            )
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            self.calls += 1
            first = int((variables or {}).get("contentPagination", {}).get("first") or 0)
            assert first >= 30
            return page

    client = OneShotClient()
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    buscado = [r for r in rows if r.row_title == "Lo más buscado" and r.video_type != "EMPTY"]
    assert [r.title for r in buscado] == titles
    assert [r.carousel_x for r in buscado] == list(range(1, 31))
    assert buscado[0].row_size == 30
    assert client.calls == 1


class _ExclusiveRailClient:
    """Serves exclusive `after cN` windows of a rail (cursor points past item N)."""

    def __init__(self, module_id: str, titles: list[str], *, overlap: bool = False) -> None:
        self.module_id = module_id
        self.titles = titles
        self.overlap = overlap
        self.calls: list[dict] = []

    def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
        del allow_errors
        page = _content_page(
            self.module_id,
            [],
            has_next=False,
            end_cursor=None,
            total=len(self.titles),
        )
        meta = _stub_modules_meta(query, variables, page)
        if meta is not None:
            return meta
        pagination = dict((variables or {}).get("contentPagination") or {})
        self.calls.append(pagination)
        first = int(pagination.get("first") or 0)
        after = pagination.get("after")
        start = 0
        if after:
            start = int(str(after)[1:])
            if self.overlap:
                start = max(0, start - 1)
        end = min(start + first, len(self.titles))
        edges = [_content_edge(i + 1, self.titles[i]) for i in range(start, end)]
        has_next = end < len(self.titles)
        return _content_page(
            self.module_id,
            edges,
            has_next=has_next,
            end_cursor=f"c{end}" if has_next else None,
            total=len(self.titles),
        )


def test_complexity_halve_then_continue_after_reconstructs_rail():
    """Small transport ceiling + complexity retry must keep global edge indexes."""
    from vix_scraper.errors import GraphQLComplexityError
    from vix_scraper.scraper import TitleScraper

    titles = [f"T{i}" for i in range(1, 27)]
    titles[20] = "Radical"
    inner = _ExclusiveRailClient("Del cine a tu pantalla", titles)

    class HalvingClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            if layout_request_kind(query, variables) != "contents":
                return inner.execute(query, variables, allow_errors=allow_errors)
            first = int((variables or {}).get("contentPagination", {}).get("first") or 0)
            if first > 8:
                raise GraphQLComplexityError(
                    "HTTP 400: Query complexity of 109776 is over the maximum of 100000"
                )
            return inner.execute(query, variables, allow_errors=allow_errors)

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        contents_page_max=16,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=HalvingClient()).scrape()
    cine = [r for r in rows if r.row_title == "Del cine a tu pantalla" and r.video_type != "EMPTY"]
    assert [r.title for r in cine] == titles
    assert cine[20].title == "Radical"
    assert cine[20].carousel_x == 21
    assert cine[0].row_size == 26
    assert len({r.carousel_x for r in cine}) == 26
    assert inner.calls[0].get("after") is None
    assert all(int(c["first"]) <= 8 for c in inner.calls)
    assert all(int(c["first"]) != 12 for c in inner.calls)


def test_cursor_overlap_dropped_without_gluing_sibling():
    from vix_scraper.scraper import TitleScraper

    titles = [f"T{i}" for i in range(1, 27)]
    titles[20] = "Radical"
    sibling = {
        "cursor": "m2",
        "node": {
            "__typename": "UiVideoCarousel",
            "moduleType": "VIDEO_CAROUSEL",
            "title": "Other",
            "id": "other",
            "contents": {
                "totalCount": 1,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [_content_edge(99, "Sibling")],
            },
        },
    }
    rail = _ExclusiveRailClient("Del cine a tu pantalla", titles, overlap=True)

    class OverlapWithSibling:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            kind = layout_request_kind(query, variables)
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandplus",
                            "moduleCount": {"totalCount": 2},
                        }
                    }
                }
            if kind == "index":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandplus",
                            "uiModules": {
                                "totalCount": 2,
                                "pageInfo": {"hasNextPage": False, "endCursor": "m2"},
                                "edges": [
                                    {
                                        "cursor": "m1",
                                        "node": {
                                            "__typename": "UiVideoCarousel",
                                            "moduleType": "VIDEO_CAROUSEL",
                                            "id": "Del cine a tu pantalla",
                                            "title": "Del cine a tu pantalla",
                                        },
                                    },
                                    {
                                        "cursor": "m2",
                                        "node": {
                                            "__typename": "UiVideoCarousel",
                                            "moduleType": "VIDEO_CAROUSEL",
                                            "id": "other",
                                            "title": "Other",
                                        },
                                    },
                                ],
                            },
                        }
                    }
                }
            payload = rail.execute(query, variables, allow_errors=allow_errors)
            modules = payload["data"]["uiPage"]["uiModules"]
            modules["totalCount"] = 2
            modules["edges"] = [modules["edges"][0], sibling]
            return payload

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=2,
        paginate_contents=True,
        contents_first_from_total=True,
        contents_page_max=8,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=OverlapWithSibling()).scrape()
    cine = [r for r in rows if r.row_title == "Del cine a tu pantalla" and r.video_type != "EMPTY"]
    other = [r for r in rows if r.row_title == "Other"]
    assert [r.title for r in cine] == titles
    assert cine[20].title == "Radical"
    assert cine[20].carousel_x == 21
    assert cine[0].row_size == 26
    assert [r.title for r in other] == ["Sibling"]
    assert all(r.title != "Sibling" for r in cine)


def _empty_module(typename: str, module_type: str, title: str, module_id: str, cursor: str) -> dict:
    return {
        "cursor": cursor,
        "node": {
            "__typename": typename,
            "moduleType": module_type,
            "title": title,
            "textTitle": title,
            "id": module_id,
            "contents": {
                "totalCount": 0,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [],
            },
        },
    }


def test_layout_keeps_empty_personalized_row():
    from vix_scraper.scraper import TitleScraper

    client = FakeClient(
        [
            {
                "data": {
                    "uiPage": {
                        "urlPath": "/ondemandplus",
                        "uiModules": {
                            "totalCount": 4,
                            "pageInfo": {"hasNextPage": False, "endCursor": "m4"},
                            "edges": [
                                _empty_module(
                                    "UiContinueWatchingCarousel",
                                    "CONTINUE_WATCHING_CAROUSEL",
                                    "Seguir viendo",
                                    "cw",
                                    "m1",
                                ),
                                _empty_module(
                                    "UiBecauseYouCarousel",
                                    "BECAUSE_YOU_CAROUSEL",
                                    "Porque viste La Rosa",
                                    "porque",
                                    "m2",
                                ),
                                _empty_module(
                                    "UiRecommendedForYouCarousel",
                                    "RECOMMENDED_FOR_YOU_CAROUSEL",
                                    "Recomendado para ti",
                                    "reco",
                                    "m3",
                                ),
                                _edge("Lo mas buscado", "m4", titles=2),
                            ],
                        },
                    }
                }
            }
        ]
    )
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=4,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    cw = [r for r in rows if r.row_title == "Seguir viendo"]
    porque = [r for r in rows if r.row_title == "Porque viste La Rosa"]
    reco = [r for r in rows if r.row_title == "Recomendado para ti"]
    buscado = [r for r in rows if r.row_title == "Lo mas buscado"]
    assert cw and cw[0].video_type == "EMPTY"
    assert porque and porque[0].video_type == "EMPTY"
    assert reco and reco[0].video_type == "EMPTY"
    assert buscado
    assert cw[0].carousel_y == 1
    assert porque[0].carousel_y == 2
    assert reco[0].carousel_y == 3
    assert buscado[0].carousel_y == 4


def test_layout_paginator_does_not_treat_twelve_as_product():
    from pathlib import Path

    from vix_scraper.pagination import LAYOUT_CONTENTS_COMPLEXITY_FIRST

    src = Path(__file__).resolve().parents[1] / "src" / "vix_scraper" / "pagination.py"
    text = src.read_text(encoding="utf-8")
    assert "LAYOUT_CONTENTS_PAGE_MAX = 12" not in text
    assert LAYOUT_CONTENTS_COMPLEXITY_FIRST != 12
    assert LAYOUT_CONTENTS_COMPLEXITY_FIRST != 10
    assert "complexity" in text.lower()


def test_modules_index_query_has_no_contents_and_documents_wc():
    from pathlib import Path

    from vix_scraper.pagination import load_modules_index_query

    qfile = Path(__file__).resolve().parents[1] / "queries" / "layout_modules.graphql"
    text = qfile.read_text(encoding="utf-8")
    assert "contents(" not in text
    assert "$contentPagination" not in text
    assert "ondemandpluswc" in text.lower()
    loaded = load_modules_index_query("query Q { __typename }")
    assert "UiPageModulesIndex" in loaded
    assert "contents(" not in loaded


def test_duplicate_index_module_ids_emit_once():
    """Regression: GraphQL listed the same four rails twice; y must not jump +4.

    Live ODP: Modo vacaciones / Canales / Top 10 / Éxitos at y=14-17 then the
    same module_ids again at y=18-21, which pushed Podcasts from 25 to 29.
    """
    from vix_scraper.pagination import PageCursor, _slice_hydrate_payload
    from vix_scraper.scraper import TitleScraper
    from vix_scraper.extractor import ui_modules_edges

    unique_ids = ["hero", "vacaciones", "canales", "top10", "exitos", "podcasts"]
    listing_ids = unique_ids[:5] + unique_ids[1:5] + [unique_ids[5]]
    unique_cursors = {mid: f"lay-{mid}" for mid in unique_ids}
    hydrate_calls: list[dict] = []

    class DupIndexClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            kind = layout_request_kind(query, variables)
            pag = dict((variables or {}).get("uiModulesPagination") or {})
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandplus",
                            "moduleCount": {"totalCount": len(listing_ids)},
                        }
                    }
                }
            if kind == "index":
                edges = [
                    {
                        "cursor": f"idx-{i}",
                        "node": {
                            "__typename": "UiVideoCarousel",
                            "moduleType": "VIDEO_CAROUSEL",
                            "id": listing_ids[i],
                            "title": listing_ids[i],
                        },
                    }
                    for i in range(len(listing_ids))
                ]
                return _page(edges, has_next=False, end_cursor="idx-last", total=len(listing_ids))
            hydrate_calls.append(pag)
            after = pag.get("after")
            if after:
                prev = after[4:] if after.startswith("lay-") else after
                idx = unique_ids.index(prev) + 1
            else:
                idx = 0
            mid = unique_ids[idx]
            return _page(
                [_edge(mid, unique_cursors[mid], titles=1)],
                has_next=False,
                end_cursor=None,
                total=len(listing_ids),
            )

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    scraper = TitleScraper(cfg, client=DupIndexClient())
    rows = scraper.scrape()
    by_y = {}
    for row in rows:
        by_y.setdefault(row.carousel_y, row.row_title)
    assert list(by_y.values()) == unique_ids
    assert max(by_y) == 6
    assert len(hydrate_calls) == 6

    payload = _page(
        [_edge("vacaciones", "c1"), _edge("canales", "c2"), _edge("top10", "c3")],
        has_next=False,
        end_cursor=None,
        total=3,
    )
    sliced = _slice_hydrate_payload(
        payload, PageCursor(slice_module_id="missing", slice_index=1)
    )
    kept = ui_modules_edges(sliced)
    assert len(kept) == 1
    assert (kept[0].get("node") or {}).get("id") == "canales"


def test_inclusive_module_after_hydrates_last_unique_rail():
    """Layout after is inclusive: first=1 replays the previous rail.

    That skip left ODP at seen=N-1 listed=N and aborted the scrape.
    """
    from vix_scraper.scraper import TitleScraper

    ids = ["hero", "reco", "buscado"]

    class InclusiveAfterClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            kind = layout_request_kind(query, variables)
            pag = dict((variables or {}).get("uiModulesPagination") or {})
            first = max(1, int(pag.get("first") or 1))
            after = pag.get("after")
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandplus",
                            "moduleCount": {"totalCount": len(ids)},
                        }
                    }
                }
            if kind == "index":
                edges = [
                    {
                        "cursor": f"idx-{mid}",
                        "node": {
                            "__typename": "UiVideoCarousel",
                            "moduleType": "VIDEO_CAROUSEL",
                            "id": mid,
                            "title": mid,
                        },
                    }
                    for mid in ids
                ]
                return _page(edges, has_next=False, end_cursor="idx-last", total=len(ids))
            if after:
                prev = str(after)[2:] if str(after).startswith("c-") else str(after)
                start = ids.index(prev)
            else:
                start = 0
            chunk = ids[start : start + first]
            edges = [_edge(mid, f"c-{mid}", titles=1) for mid in chunk]
            return _page(edges, has_next=False, end_cursor=None, total=len(ids))

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=InclusiveAfterClient()).scrape()
    by_y = {}
    for row in rows:
        if row.video_type == "EMPTY":
            continue
        by_y.setdefault(row.carousel_y, row.row_title)
    assert list(by_y.values()) == ids
    assert max(by_y) == 3


def test_layout_order_wins_when_index_ids_disagree():
    """ODP died at index=14: listing id A, layout hydrate returned B.

    Walk the layout connection. Do not abort because the cheap index list
    named a different module at that slot.
    """
    from vix_scraper.scraper import TitleScraper

    index_ids = ["hero", "listed-b", "listed-c"]
    layout_ids = ["hero", "layout-x", "layout-y"]

    class DriftClient:
        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            kind = layout_request_kind(query, variables)
            pag = dict((variables or {}).get("uiModulesPagination") or {})
            first = max(1, int(pag.get("first") or 1))
            after = pag.get("after")
            if kind == "probe":
                return {
                    "data": {
                        "uiPage": {
                            "urlPath": "/ondemandplus",
                            "moduleCount": {"totalCount": 3},
                        }
                    }
                }
            if kind == "index":
                edges = [
                    {
                        "cursor": f"idx-{mid}",
                        "node": {
                            "__typename": "UiVideoCarousel",
                            "moduleType": "VIDEO_CAROUSEL",
                            "id": mid,
                            "title": mid,
                        },
                    }
                    for mid in index_ids
                ]
                return _page(edges, has_next=False, end_cursor="idx-last", total=3)
            if after:
                prev = str(after)[2:] if str(after).startswith("c-") else str(after)
                start = layout_ids.index(prev)
                chunk = layout_ids[start : start + first]
            else:
                chunk = layout_ids[:first]
            edges = [_edge(mid, f"c-{mid}", titles=1) for mid in chunk]
            return _page(edges, has_next=False, end_cursor=None, total=3)

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=DriftClient()).scrape()
    by_y = {}
    for row in rows:
        by_y.setdefault(row.carousel_y, row.row_title)
    assert list(by_y.values()) == layout_ids


def test_editorial_items_not_walked_into_contents_dump():
    """CMS items list is the rail; do not page contents into a ranked catalog."""
    from vix_scraper.scraper import TitleScraper

    cms = [
        "Los Héroes del Norte",
        "Spider-Man: Lejos de Casa",
        "Archivo Negro",
        "Quiero Tu Vida",
        "¿Quieres ser mi hijo?",
        "40 y 20",
        "Pequeña Confusión",
        "Vecinos",
        "Crónica de un Fin",
        "Nada Que Ver",
        "El Show",
        "El Hombre Araña 3",
        "La Lola",
        "El Señor de los Cielos",
    ]
    dump = [f"Dump {i}" for i in range(1, 31)]
    dump[18] = "El Señor ranked"

    def _card(i: int, title: str) -> dict:
        return {
            "__typename": "UiVideoCard",
            "id": f"pin-{i}",
            "title": title,
            "video": {"id": f"v-{i}", "title": title, "videoType": "SERIES", "mcpId": str(i)},
        }

    node = {
        "__typename": "UiVideoCarousel",
        "moduleType": "VIDEO_CAROUSEL",
        "title": "Lo más buscado",
        "id": "buscado",
        "isPlaylist": True,
        "items": [_card(i, t) for i, t in enumerate(cms, start=1)],
        "contents": {
            "totalCount": 30,
            "pageInfo": {"hasNextPage": True, "endCursor": "c-dump"},
            "edges": [
                {
                    "node": {
                        "__typename": "UiVideoCard",
                        "id": f"d-{i}",
                        "title": t,
                        "video": {"id": f"d-{i}", "title": t, "videoType": "SERIES"},
                    }
                }
                for i, t in enumerate(dump, start=1)
            ],
        },
    }

    class ItemsClient:
        def __init__(self) -> None:
            self.content_afters = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = {
                "data": {
                    "uiPage": {
                        "urlPath": "/ondemandpluswc",
                        "uiModules": {
                            "totalCount": 1,
                            "pageInfo": {"hasNextPage": False, "endCursor": "m1"},
                            "edges": [{"cursor": "m1", "node": node}],
                        },
                    }
                }
            }
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            if (variables or {}).get("contentPagination", {}).get("after"):
                self.content_afters += 1
            return page

    client = ItemsClient()
    cfg = ScrapeConfig(
        url_path="/ondemandpluswc",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    rail = [r for r in rows if r.row_title == "Lo más buscado" and r.video_type != "EMPTY"]
    assert [r.title for r in rail] == cms
    assert rail[-1].title == "El Señor de los Cielos"
    assert rail[-1].carousel_x == 14
    assert client.content_afters == 0
    assert rail[0].row_size == 14


def test_exclusivo_catalog_dump_does_not_page_to_x_119():
    """items of 20 + contents.totalCount=163 must not emit x=119 or page contents."""
    from vix_scraper.scraper import TitleScraper

    pins = [f"Pin {i}" for i in range(1, 21)]
    dump = [("El Señor de los Cielos" if i == 119 else f"Dump {i}") for i in range(1, 164)]

    def _card(i: int, title: str) -> dict:
        return {
            "__typename": "UiVideoCard",
            "id": f"pin-{i}",
            "title": title,
            "video": {"id": f"v-{i}", "title": title, "videoType": "SERIES", "mcpId": str(i)},
        }

    node = {
        "__typename": "UiVideoCarousel",
        "moduleType": "VIDEO_CAROUSEL",
        "title": "Exclusivo en ViX",
        "id": "exclusivo",
        "isPlaylist": True,
        "items": [_card(i, t) for i, t in enumerate(pins, start=1)],
        "contents": {
            "totalCount": 163,
            "pageInfo": {"hasNextPage": True, "endCursor": "c-dump"},
            "edges": [
                {
                    "node": {
                        "__typename": "UiVideoCard",
                        "id": f"d-{i}",
                        "title": t,
                        "video": {"id": f"d-{i}", "title": t, "videoType": "SERIES"},
                    }
                }
                for i, t in enumerate(dump, start=1)
            ],
        },
    }

    class ItemsClient:
        def __init__(self) -> None:
            self.content_afters = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = {
                "data": {
                    "uiPage": {
                        "urlPath": "/ondemandpluswc",
                        "uiModules": {
                            "totalCount": 1,
                            "pageInfo": {"hasNextPage": False, "endCursor": "m1"},
                            "edges": [{"cursor": "m1", "node": node}],
                        },
                    }
                }
            }
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            if (variables or {}).get("contentPagination", {}).get("after"):
                self.content_afters += 1
            return page

    client = ItemsClient()
    cfg = ScrapeConfig(
        url_path="/ondemandpluswc",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    rail = [r for r in rows if r.row_title == "Exclusivo en ViX" and r.video_type != "EMPTY"]
    assert [r.title for r in rail] == pins
    assert rail[-1].carousel_x == 20
    assert all(r.carousel_x != 119 for r in rail)
    assert client.content_afters == 0
    assert rail[0].row_size == 20


def test_exclusivo_without_items_stops_at_first_layout_page():
    """Editorial (not playlist): first contents.edges window, not catalog 163."""
    from vix_scraper.scraper import TitleScraper

    window = [f"Pin {i}" for i in range(1, 27)]

    class WindowClient:
        def __init__(self) -> None:
            self.content_afters = 0

        def execute(self, query: str, variables: dict | None = None, *, allow_errors: bool = False):
            del allow_errors
            page = _content_page(
                "Exclusivo en ViX",
                [_content_edge(i, window[i - 1]) for i in range(1, 27)],
                has_next=True,
                end_cursor="c26",
                total=163,
                is_playlist=False,
            )
            # Restore the human row title (helper uses module_id as title).
            page["data"]["uiPage"]["uiModules"]["edges"][0]["node"]["title"] = "Exclusivo en ViX"
            page["data"]["uiPage"]["uiModules"]["edges"][0]["node"]["id"] = "exclusivo"
            meta = _stub_modules_meta(query, variables, page)
            if meta is not None:
                return meta
            if (variables or {}).get("contentPagination", {}).get("after"):
                self.content_afters += 1
            return page

    client = WindowClient()
    cfg = ScrapeConfig(
        url_path="/ondemandpluswc",
        query="query Q { __typename }",
        module_page_size=1,
        paginate_contents=True,
        contents_first_from_total=True,
        download_images=False,
    )
    rows = TitleScraper(cfg, client=client).scrape()
    rail = [r for r in rows if r.row_title == "Exclusivo en ViX" and r.video_type != "EMPTY"]
    assert len(rail) == 26
    assert rail[-1].carousel_x == 26
    assert client.content_afters == 0
    assert all(r.carousel_x < 50 for r in rail)
