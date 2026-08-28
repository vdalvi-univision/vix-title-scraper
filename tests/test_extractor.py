"""Smoke tests for offline extraction (no network)."""

from __future__ import annotations

from vix_scraper.discovery import extract_paths_from_page_payload, normalize_path
from vix_scraper.extractor import (
    TitleExtractor,
    enrich_layout_fields,
    is_visible_web_row,
    pending_content_cursors,
)
from vix_scraper.exporter import deduplicate


SAMPLE = {
    "data": {
        "uiPage": {
            "urlPath": "/micro-dramas",
            "pageName": "Micros",
            "uiModules": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {
                        "node": {
                            "__typename": "UiHeroCarousel",
                            "moduleType": "HERO_CAROUSEL",
                            "title": "Destacados",
                            "id": "hero-1",
                            "contents": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "edges": [
                                    {
                                        "node": {
                                            "id": "nh",
                                            "video": {
                                                "id": "vh",
                                                "title": "Hero Show",
                                                "description": "Hero desc",
                                                "dateReleased": "2024-02-01",
                                                "videoType": "SERIES",
                                                "genresV2": [{"name": "Drama"}],
                                            },
                                        }
                                    }
                                ],
                            },
                        }
                    },
                    {
                        "node": {
                            "__typename": "UiVideoCarousel",
                            "moduleType": "VIDEO_CAROUSEL",
                            "title": "Populares",
                            "id": "row-1",
                            "isPlaylist": True,
                            "contents": {
                                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-a"},
                                "edges": [
                                    {
                                        "node": {
                                            "id": "n1",
                                            "image": {
                                                "link": "https://img.example/poster.jpg",
                                                "imageRole": "VERTICAL_POSTER",
                                                "filePath": "/poster.jpg",
                                                "mediaType": "image/jpeg",
                                            },
                                            "video": {
                                                "id": "v1",
                                                "title": "Drama Uno",
                                                "description": "Desc",
                                                "dateReleased": "2024-01-01",
                                                "videoType": "SERIES",
                                                "genresV2": [{"name": "Drama"}],
                                                "imageAssets": [
                                                    {
                                                        "link": "https://img.example/poster.jpg",
                                                        "imageRole": "VERTICAL_POSTER",
                                                        "filePath": "/poster.jpg",
                                                        "mediaType": "image/jpeg",
                                                    }
                                                ],
                                                "videoTypeData": {
                                                    "__typename": "VideoTypeSeriesData",
                                                    "seasonsCount": 1,
                                                    "episodesCount": 10,
                                                },
                                            },
                                        }
                                    },
                                    {
                                        "node": {
                                            "id": "n2",
                                            "video": {
                                                "id": "v1",
                                                "title": "Drama Uno",
                                                "description": "Dup",
                                                "dateReleased": "2024-01-01",
                                                "videoType": "SERIES",
                                                "genresV2": [{"name": "Drama"}],
                                            },
                                        }
                                    },
                                ],
                            },
                        }
                    },
                    {
                        "node": {
                            "__typename": "UiPageCarousel",
                            "moduleType": "PAGE_CAROUSEL",
                            "title": "Hubs",
                            "id": "pages-1",
                            "contents": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "edges": [
                                    {"node": {"name": "Series", "urlPath": "/ondemandplus/series"}},
                                    {"node": {"name": "Novelas", "urlPath": "/ondemandplus/novelas"}},
                                ],
                            },
                        }
                    },
                ],
            }
        }
    }
}


def test_extract_titles():
    rows = TitleExtractor().extract(SAMPLE, page_url_path="/micro-dramas")
    enrich_layout_fields(rows)
    # Hero + video carousel (2) + page carousel hubs (2)
    assert len(rows) == 5
    assert rows[0].title == "Hero Show"
    assert rows[0].is_hero == "true"
    assert rows[0].carousel_y == 1
    assert rows[0].module_typename == "UiHeroCarousel"
    assert rows[1].title == "Drama Uno"
    assert rows[1].row_title == "Populares"
    assert rows[1].carousel_x == 1
    assert rows[1].carousel_y == 2
    assert rows[1].position == 2  # global page index continues; first of row 2 is still x=1
    assert rows[1].is_hero == "false"
    assert rows[1].genres == "Drama"
    assert rows[1].content_id == "v1"
    assert rows[1].seasons_count == "1"
    assert rows[1].page_url_path == "/micro-dramas"
    assert rows[1].row_size == 2
    assert rows[3].title == "Series"
    assert rows[3].row_title == "Hubs"
    assert rows[3].carousel_y == 3
    assert rows[3].video_type == "PAGE"
    assert "poster_url" not in rows[1].as_csv_row()


def test_carousel_x_resets_per_row_while_position_is_global():
    """First tile in a later row is carousel_x=1 even when global position is 14."""

    def video_edge(i: int) -> dict:
        return {
            "node": {
                "id": f"n{i}",
                "video": {"id": f"v{i}", "title": f"Hero {i}", "videoType": "SERIES"},
            }
        }

    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {"edges": [video_edge(i) for i in range(1, 14)]},
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado",
                                "id": "row-2",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "c1",
                                                "video": {
                                                    "id": "video:mcp:4585674",
                                                    "title": "Una pequeña confusión",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    assert [r.carousel_x for r in rows if r.carousel_y == 1] == list(range(1, 14))
    first_row2 = next(r for r in rows if r.carousel_y == 2)
    assert first_row2.title == "Una pequeña confusión"
    assert first_row2.carousel_x == 1
    assert first_row2.position == 14


def test_content_continuation_coords():
    rows = TitleExtractor().extract(
        SAMPLE,
        page_url_path="/micro-dramas",
        module_y_base=2,
        content_x_offset=10,
        continuing_content=True,
    )
    # Only the rail with a content cursor (Populares) is extracted; siblings ignored.
    assert all(r.carousel_y == 2 for r in rows)
    assert rows[0].title == "Drama Uno"
    assert rows[0].carousel_x == 11
    assert rows[0].row_title == "Populares"


def test_skipped_non_row_does_not_consume_y():
    """Non-content chrome does not consume carousel_y; empty CW still occupies a slot."""
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "h1",
                                                "heroTarget": {
                                                    "__typename": "EpgChannel",
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiUnknownDecoration",
                                "moduleType": "DECORATION",
                                "id": "skip-me",
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiContinueWatchingCarousel",
                                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                                "title": "Seguir viendo",
                                "id": "cw-empty",
                                "contents": {"edges": []},
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiLiveVideoCarousel",
                                "moduleType": "LIVE_VIDEO_CAROUSEL",
                                "title": "La Casa en vivo 24/7",
                                "id": "live-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "live-card-1",
                                                "channelId": "channel:mcp:callsign:LCDLF01_SVOD",
                                                "channel": {
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    assert rows[0].title == "Acceso Total 24/7"
    assert rows[0].carousel_y == 1
    assert rows[0].carousel_x == 1
    assert rows[0].is_hero == "true"
    by_y = {r.carousel_y: r for r in rows}
    assert by_y[2].row_title == "Seguir viendo"
    assert by_y[2].video_type == "EMPTY"
    assert by_y[2].title == "(empty)"
    assert rows[-1].row_title == "La Casa en vivo 24/7"
    assert rows[-1].carousel_y == 3  # decoration skipped; empty CW kept
    assert rows[-1].carousel_x == 1


def _video_edge(title: str, mid: str, *, empty: bool = False) -> dict:
    edges = []
    if not empty:
        edges = [
            {
                "node": {
                    "id": f"{mid}-item",
                    "video": {
                        "id": f"series:mcp:{mid}",
                        "title": f"{title} item",
                        "videoType": "SERIES",
                    },
                }
            }
        ]
    return {
        "node": {
            "__typename": "UiVideoCarousel",
            "moduleType": "VIDEO_CAROUSEL",
            "title": title,
            "id": mid,
            "contents": {"edges": edges},
        }
    }


def _live_edge(title: str, mid: str) -> dict:
    return {
        "node": {
            "__typename": "UiLiveVideoCarousel",
            "moduleType": "LIVE_VIDEO_CAROUSEL",
            "title": title,
            "id": mid,
            "contents": {
                "edges": [
                    {
                        "node": {
                            "id": f"{mid}-ch",
                            "channel": {
                                "id": "channel:mcp:callsign:TUDN",
                                "title": "TUDN",
                            },
                        }
                    }
                ]
            },
        }
    }


def _inline_edge(mid: str, cta: str = "Ver más", path: str = "/ondemandplus/x") -> dict:
    return {
        "node": {
            "__typename": "UiInlinePage",
            "moduleType": "INLINE_PAGE",
            "id": mid,
            "ctaText": cta,
            "ctaUrlPath": path,
            "trackingMetadataJson": {"ui_module_title": "", "ui_carousel_slug": mid},
        }
    }


def test_inline_page_is_not_a_web_row():
    assert not is_visible_web_row(_inline_edge("inline-1")["node"])


def test_odp_micros_is_web_row_17():
    """ODP sequence: empty Seguir viendo keeps a slot; INLINE chrome does not. MicrOs = Row 17."""
    edges = [
        {
            "node": {
                "__typename": "UiHeroCarousel",
                "moduleType": "HERO_CAROUSEL",
                "title": "Hero",
                "id": "hero-1",
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": "h1",
                                "heroTarget": {
                                    "__typename": "EpgChannel",
                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                    "title": "Acceso Total 24/7",
                                },
                            }
                        }
                    ]
                },
            }
        },
        _live_edge("La Casa en vivo 24/7", "live-casa"),
        {
            "node": {
                "__typename": "UiRecommendedForYouCarousel",
                "moduleType": "RECOMMENDED_FOR_YOU_CAROUSEL",
                "title": "Recomendado para ti",
                "id": "reco-1",
                "contents": {"edges": []},
            }
        },
        {
            "node": {
                "__typename": "UiContinueWatchingCarousel",
                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                "title": "Seguir viendo",
                "id": "cw-empty",
                "contents": {"edges": []},
            }
        },
        _video_edge("Lo más buscado en México", "v-lo"),
        _video_edge("Que siga la pasión con estos éxitos", "v-pasion"),
        _video_edge("Próximos estrenos", "v-prox"),
        {
            "node": {
                "__typename": "UiWatchlistCarousel",
                "moduleType": "WATCH_LIST_CAROUSEL",
                "title": "Mi lista",
                "id": "wl-empty",
                "contents": {"edges": []},
            }
        },
        _inline_edge("inline-ver-mas-1"),
        _video_edge("Solo para chavos", "v-chavos"),
        _video_edge("Disfruta en familia", "v-fam"),
        _video_edge("Selección especial", "v-sel"),
        _inline_edge("inline-ver-mas-2", path="/acceso/mv/lacasadelosfamosos"),
        _video_edge("Nuevos episodios esta semana", "v-nuevos"),
        {
            "node": {
                "__typename": "UiSportsEventCarousel",
                "moduleType": "SPORTS_EVENT_CAROUSEL",
                "title": "En vivo y Próximamente",
                "id": "sports-1",
                "contents": {"edges": [{"node": {"id": "ev1", "title": "Partido"}}]},
            }
        },
        _video_edge("Nueva temporada disponible", "v-temp"),
        _live_edge("Canales en vivo", "live-ch"),
        _video_edge("Top 10 en México hoy", "v-top"),
        _video_edge("MicrOs", "v-micro"),
        _video_edge("Éxitos indiscutibles", "v-exitos"),
    ]
    payload = {"data": {"uiPage": {"urlPath": "/ondemandplus", "uiModules": {"edges": edges}}}}
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    by_y = {}
    for r in rows:
        by_y.setdefault(r.carousel_y, r.row_title)
    expected_first_17 = [
        "Hero",
        "La Casa en vivo 24/7",
        "Recomendado para ti",
        "Seguir viendo",
        "Lo más buscado en México",
        "Que siga la pasión con estos éxitos",
        "Próximos estrenos",
        "Mi lista",
        "Solo para chavos",
        "Disfruta en familia",
        "Selección especial",
        "Nuevos episodios esta semana",
        "En vivo y Próximamente",
        "Nueva temporada disponible",
        "Canales en vivo",
        "Top 10 en México hoy",
        "MicrOs",
    ]
    assert [by_y[i] for i in range(1, 18)] == expected_first_17
    assert by_y[1] == "Hero"
    assert rows[0].title == "Acceso Total 24/7"
    assert by_y[2] == "La Casa en vivo 24/7"
    assert by_y[3] == "Recomendado para ti"
    assert by_y[4] == "Seguir viendo"
    cw = [r for r in rows if r.row_title == "Seguir viendo"]
    assert cw and cw[0].video_type == "EMPTY"
    reco = [r for r in rows if r.row_title == "Recomendado para ti"]
    assert reco and reco[0].video_type == "EMPTY"
    assert not any(r.module_type == "INLINE_PAGE" for r in rows)
    assert not any(r.row_title == "Ver más" for r in rows)
    micros = [r for r in rows if r.row_title == "MicrOs"]
    assert micros and micros[0].carousel_y == 17


def test_imperdibles_keeps_empty_continue_watching_slot():
    """WC-like sequence: empty Seguir viendo occupies a slot; Imperdibles is y=14 not 13."""
    def _video_row(title: str, mid: str) -> dict:
        return {
            "node": {
                "__typename": "UiVideoCarousel",
                "moduleType": "VIDEO_CAROUSEL",
                "title": title,
                "id": mid,
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": f"{mid}-item",
                                "video": {
                                    "id": f"series:mcp:{mid}",
                                    "title": f"{title} item",
                                    "videoType": "SERIES",
                                },
                            }
                        }
                    ]
                },
            }
        }

    def _live_row(title: str, mid: str) -> dict:
        return {
            "node": {
                "__typename": "UiLiveVideoCarousel",
                "moduleType": "LIVE_VIDEO_CAROUSEL",
                "title": title,
                "id": mid,
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": f"{mid}-ch",
                                "channel": {
                                    "id": "channel:mcp:callsign:TUDN",
                                    "title": "TUDN",
                                },
                            }
                        }
                    ]
                },
            }
        }

    edges = [
        {
            "node": {
                "__typename": "UiHeroCarousel",
                "moduleType": "HERO_CAROUSEL",
                "title": "Hero",
                "id": "hero-1",
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": "h1",
                                "heroTarget": {
                                    "__typename": "EpgChannel",
                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                    "title": "Acceso Total 24/7",
                                },
                            }
                        }
                    ]
                },
            }
        },
        {
            "node": {
                "__typename": "UiRecommendedForYouCarousel",
                "moduleType": "RECOMMENDED_FOR_YOU_CAROUSEL",
                "title": "Recomendado para ti",
                "id": "reco-1",
                "contents": {"edges": []},
            }
        },
        {
            "node": {
                "__typename": "UiSportsEventCarousel",
                "moduleType": "SPORTS_EVENT_CAROUSEL",
                "title": "En vivo y Próximamente",
                "id": "sports-1",
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": "ev1",
                                "title": "Partido",
                            }
                        }
                    ]
                },
            }
        },
        _video_row("Lo más buscado", "v-lo"),
        _video_row("Películas destacadas", "v-pel"),
        {
            "node": {
                "__typename": "UiContinueWatchingCarousel",
                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                "title": "Seguir viendo",
                "id": "cw-empty",
                "contents": {"edges": []},
            }
        },
        _video_row("Aclamadas por los fans", "v-aclam"),
        _video_row("Nueva temporada disponible", "v-nueva"),
        _live_row("La Casa en vivo 24/7", "live-casa"),
        {
            "node": {
                "__typename": "UiWatchlistCarousel",
                "moduleType": "WATCH_LIST_CAROUSEL",
                "title": "Mi lista",
                "id": "wl-empty",
                "contents": {"edges": []},
            }
        },
        _video_row("MicrOs", "v-micro"),
        _video_row("Del cine a tu pantalla", "v-cine"),
        _live_row("Canales en vivo", "live-ch"),
        {
            "node": {
                "__typename": "UiMixedContentCarousel",
                "moduleType": "MIXED_LIST_CAROUSEL",
                "title": "Imperdibles de esta semana",
                "id": "mixed-imp",
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": "imp-1",
                                "video": {
                                    "id": "series:mcp:5724",
                                    "title": "Guardián de mi vida",
                                    "videoType": "SERIES",
                                },
                            }
                        }
                    ]
                },
            }
        },
    ]
    # Raw GraphQL index of Imperdibles is 14; visible y is also 14 (empty CW kept).
    assert len(edges) == 14
    payload = {"data": {"uiPage": {"urlPath": "/ondemandpluswc", "uiModules": {"edges": edges}}}}
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    by_y = {}
    for r in rows:
        by_y.setdefault(r.carousel_y, r.row_title)
    assert by_y[1] == "Hero"
    assert by_y[2] == "Recomendado para ti"
    assert by_y[6] == "Seguir viendo"
    assert by_y[7] == "Aclamadas por los fans"
    assert by_y[9] == "La Casa en vivo 24/7"
    assert by_y[10] == "Mi lista"
    cw = [r for r in rows if r.row_title == "Seguir viendo"]
    assert cw and cw[0].video_type == "EMPTY"
    imper = [r for r in rows if r.row_title == "Imperdibles de esta semana"]
    assert imper, "Imperdibles missing"
    assert imper[0].carousel_y == 14
    assert by_y[14] == "Imperdibles de esta semana"


def test_acceso_total_and_guardian_fixture_placements():
    """Lock Acceso Total hero #1 and Guardián hero slot against a web-like fixture."""
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "hero-channel",
                                                "heroTarget": {
                                                    "__typename": "EpgChannel",
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "id": "n2",
                                                "heroTarget": {
                                                    "__typename": "VideoContent",
                                                    "id": "series:mcp:4904",
                                                    "title": "El extraño retorno de Diana Salazar",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "id": "n3",
                                                "heroTarget": {
                                                    "__typename": "VideoContent",
                                                    "id": "video:mcp:4925083",
                                                    "title": "Tráiler: Ninel Conde sin filtro",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "id": "n4",
                                                "heroTarget": {
                                                    "__typename": "VideoContent",
                                                    "id": "series:mcp:5724",
                                                    "title": "Guardián de mi vida",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        },
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiLiveVideoCarousel",
                                "moduleType": "LIVE_VIDEO_CAROUSEL",
                                "title": "La Casa en vivo 24/7",
                                "id": "live-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "live-1",
                                                "channel": {
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    hero = [r for r in rows if r.carousel_y == 1]
    assert hero[0].title == "Acceso Total 24/7"
    assert hero[0].carousel_x == 1
    assert hero[0].is_hero == "true"
    guardian = next(r for r in hero if "Guardi" in r.title)
    assert guardian.carousel_x == 4
    casa = [r for r in rows if r.carousel_y == 2]
    assert casa[0].title == "Acceso Total 24/7"
    assert casa[0].carousel_x == 1
    assert casa[0].row_title == "La Casa en vivo 24/7"


def test_multipage_content_does_not_shift_y():
    """Content pagination must not re-number sibling modules."""
    page1 = {
        "data": {
            "uiPage": {
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "MicrOs",
                                "id": "m1",
                                "isPlaylist": True,
                                "contents": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "a",
                                                "video": {"id": "v1", "title": "One", "videoType": "SERIES"},
                                            }
                                        }
                                    ],
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Other",
                                "id": "m2",
                                "contents": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "b",
                                                "video": {"id": "v2", "title": "Two", "videoType": "SERIES"},
                                            }
                                        }
                                    ],
                                },
                            }
                        },
                    ]
                }
            }
        }
    }
    page2 = {
        "data": {
            "uiPage": {
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "MicrOs",
                                "id": "m1",
                                "isPlaylist": True,
                                "contents": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "c",
                                                "video": {"id": "v3", "title": "Three", "videoType": "SERIES"},
                                            }
                                        }
                                    ],
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Other",
                                "id": "m2",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "b2",
                                                "video": {"id": "v2b", "title": "Dup", "videoType": "SERIES"},
                                            }
                                        }
                                    ],
                                },
                            }
                        },
                    ]
                }
            }
        }
    }
    first = TitleExtractor().extract(page1, page_url_path="/ondemandplus", module_y_base=0)
    cont = TitleExtractor().extract(
        page2,
        page_url_path="/ondemandplus",
        module_y_base=1,
        content_x_offset=1,
        continuing_content=True,
    )
    assert [r.title for r in first] == ["One", "Two"]
    assert first[0].carousel_y == 1 and first[1].carousel_y == 2
    assert len(cont) == 1
    assert cont[0].title == "Three"
    assert cont[0].carousel_y == 1
    assert cont[0].carousel_x == 2


def test_live_channel_and_epg_hero_extracted():
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "hero-channel",
                                                "heroTarget": {
                                                    "__typename": "EpgChannel",
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiLiveVideoCarousel",
                                "moduleType": "LIVE_VIDEO_CAROUSEL",
                                "title": "La Casa en vivo 24/7",
                                "id": "live-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "__typename": "UiLiveVideoCard",
                                                "id": "live-card-1",
                                                "channelId": "channel:mcp:callsign:LCDLF01_SVOD",
                                                "channel": {
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiRecommendedForYouCarousel",
                                "moduleType": "RECOMMENDED_FOR_YOU_CAROUSEL",
                                "title": "Recomendado para ti",
                                "id": "reco-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "n1",
                                                "video": {
                                                    "id": "v-reco",
                                                    "title": "Reco Show",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiContinueWatchingCarousel",
                                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                                "title": "Seguir viendo",
                                "id": "cw-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "n-cw",
                                                "video": {
                                                    "id": "v-cw",
                                                    "title": "Continue Show",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiRecentChannelsCarousel",
                                "moduleType": "RECENT_CHANNELS_CAROUSEL",
                                "title": "Canales vistos recientemente",
                                "id": "rc-1",
                                "contents": {"edges": []},
                            }
                        },
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    assert [r.row_title for r in rows] == [
        "Hero",
        "La Casa en vivo 24/7",
        "Recomendado para ti",
        "Seguir viendo",
        "Canales vistos recientemente",
    ]
    assert rows[0].title == "Acceso Total 24/7"
    assert rows[0].video_type == "LIVE_CHANNEL"
    assert rows[1].title == "Acceso Total 24/7"
    assert rows[1].module_type == "LIVE_VIDEO_CAROUSEL"
    assert rows[2].title == "Reco Show"
    assert rows[3].title == "Continue Show"
    assert rows[3].module_type == "CONTINUE_WATCHING_CAROUSEL"
    assert rows[3].carousel_y == 4
    recent = rows[4]
    assert recent.row_title == "Canales vistos recientemente"
    assert recent.video_type == "EMPTY"
    assert recent.carousel_y == 5
    assert recent.title == "(empty)"


def test_continue_watching_titled_item_is_visible_row():
    """CW with ≥1 titled item must be a visible Seguir viendo row (incl. episode resume)."""
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "h1",
                                                "heroTarget": {
                                                    "__typename": "VideoContent",
                                                    "id": "video:mcp:1",
                                                    "title": "Hero Title",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiContinueWatchingCarousel",
                                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                                "title": "Seguir viendo",
                                "id": "cw-1",
                                "contents": {
                                    "totalCount": 1,
                                    "edges": [
                                        {
                                            "node": {
                                                "__typename": "UiVideoCarouselItem",
                                                "id": "cw-card-1",
                                                "title": "",
                                                "video": {
                                                    "id": "video:mcp:ep-1",
                                                    "title": "Episodio 3",
                                                    "videoType": "EPISODE",
                                                    "videoTypeData": {
                                                        "__typename": "VideoTypeEpisodeData",
                                                        "episodeNumber": 3,
                                                        "series": {
                                                            "id": "series:mcp:5780",
                                                            "title": "Un gol del paraíso",
                                                        },
                                                    },
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado en México",
                                "id": "v-lo",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "n1",
                                                "video": {
                                                    "id": "v1",
                                                    "title": "Other",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    cw_module = payload["data"]["uiPage"]["uiModules"]["edges"][1]["node"]
    assert is_visible_web_row(cw_module)
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    cw = [r for r in rows if r.row_title == "Seguir viendo"]
    assert len(cw) == 1
    assert cw[0].title == "Un gol del paraíso"
    assert cw[0].content_id == "series:mcp:5780"
    assert cw[0].carousel_y == 2
    assert cw[0].carousel_x == 1
    assert cw[0].module_type == "CONTINUE_WATCHING_CAROUSEL"
    buscado = [r for r in rows if r.row_title == "Lo más buscado en México"]
    assert buscado and buscado[0].carousel_y == 3


def test_continue_watching_total_count_marks_visible_even_without_edges():
    module = {
        "__typename": "UiContinueWatchingCarousel",
        "moduleType": "CONTINUE_WATCHING_CAROUSEL",
        "title": "Seguir viendo",
        "contents": {"totalCount": 2, "edges": []},
    }
    assert is_visible_web_row(module)
    empty_no_count = {
        "__typename": "UiContinueWatchingCarousel",
        "moduleType": "CONTINUE_WATCHING_CAROUSEL",
        "title": "Seguir viendo",
        "contents": {"edges": []},
    }
    assert is_visible_web_row(empty_no_count)


def test_all_empty_content_module_kinds_occupy_a_row():
    """CMS LAYOUTS rows stay numbered when empty — not only Reco / CW / Porque viste."""
    kinds = [
        ("UiHeroCarousel", "HERO_CAROUSEL"),
        ("UiLiveVideoCarousel", "LIVE_VIDEO_CAROUSEL"),
        ("UiSportsEventCarousel", "SPORTS_EVENT_CAROUSEL"),
        ("UiRecommendedForYouCarousel", "RECOMMENDED_FOR_YOU_CAROUSEL"),
        ("UiContinueWatchingCarousel", "CONTINUE_WATCHING_CAROUSEL"),
        ("UiBecauseYouCarousel", "BECAUSE_YOU_CAROUSEL"),
        ("UiWatchlistCarousel", "WATCH_LIST_CAROUSEL"),
        ("UiRecentChannelsCarousel", "RECENT_CHANNELS_CAROUSEL"),
        ("UiVideoCarousel", "VIDEO_CAROUSEL"),
        ("UiTrendingNowCarousel", "TRENDING_NOW_CAROUSEL"),
        ("UiMixedContentCarousel", "MIXED_CONTENT_CAROUSEL"),
        ("UiMixedContentCarousel", "MIXED_LIST_CAROUSEL"),
        ("UiPageCarousel", "PAGE_CAROUSEL"),
    ]
    for typename, module_type in kinds:
        module = {
            "__typename": typename,
            "moduleType": module_type,
            "title": module_type,
            "contents": {"edges": []},
        }
        assert is_visible_web_row(module), f"{module_type} must occupy a row when empty"
    assert not is_visible_web_row(_inline_edge("inline-chrome")["node"])
    assert not is_visible_web_row(
        {"__typename": "UiUnknownDecoration", "moduleType": "DECORATION", "id": "skip"}
    )


def test_odp_prefix_with_cw_puts_lo_mas_buscado_at_y6():
    """Web ODP order: Hero, En vivo, La Casa, Reco, Seguir viendo, Lo más buscado."""
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "h1",
                                                "heroTargetContentType": "EPG_CHANNEL",
                                                "heroTarget": {
                                                    "__typename": "EpgChannel",
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiSportsEventCarousel",
                                "moduleType": "SPORTS_EVENT_CAROUSEL",
                                "title": "En vivo",
                                "id": "sports-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "s1",
                                                "sportsEventId": "mlb-1",
                                                "localTeamName": "Reds",
                                                "awayTeamName": "White Sox",
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiLiveVideoCarousel",
                                "moduleType": "LIVE_VIDEO_CAROUSEL",
                                "title": "La Casa en vivo 24/7",
                                "id": "live-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "live-1",
                                                "channel": {
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiRecommendedForYouCarousel",
                                "moduleType": "RECOMMENDED_FOR_YOU_CAROUSEL",
                                "title": "Recomendado para ti",
                                "id": "reco-1",
                                "contents": {"edges": []},
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiContinueWatchingCarousel",
                                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                                "title": "Seguir viendo",
                                "id": "cw-1",
                                "contents": {
                                    "totalCount": 1,
                                    "edges": [
                                        {
                                            "node": {
                                                "__typename": "UiVideoCarouselItem",
                                                "id": "cw-1",
                                                "video": {
                                                    "id": "video:mcp:ep-1",
                                                    "title": "Episodio 3",
                                                    "videoType": "EPISODE",
                                                    "videoTypeData": {
                                                        "__typename": "VideoTypeEpisodeData",
                                                        "series": {
                                                            "id": "series:mcp:5780",
                                                            "title": "Un gol del paraíso",
                                                        },
                                                    },
                                                },
                                            }
                                        }
                                    ],
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado en México",
                                "id": "v-lo",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "n1",
                                                "video": {
                                                    "id": "v1",
                                                    "title": "La esclava Isaura",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    by_y = {}
    for r in rows:
        by_y.setdefault(r.carousel_y, r.row_title)
    assert by_y[1] == "Hero"
    assert by_y[2] == "En vivo"
    assert by_y[3] == "La Casa en vivo 24/7"
    assert by_y[4] == "Recomendado para ti"
    assert by_y[5] == "Seguir viendo"
    assert by_y[6] == "Lo más buscado en México"
    buscado = [r for r in rows if r.row_title == "Lo más buscado en México"]
    assert buscado[0].carousel_y == 6


def _hero_payload(edges: list[dict], *, row_title: str = "Hero") -> dict:
    return {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": row_title,
                                "id": "hero-1",
                                "contents": {"edges": edges},
                            }
                        }
                    ]
                },
            }
        }
    }


def test_hero_keeps_leading_sports_event_ninel_is_second():
    """First page tile is a SportsEvent; dropping it made Ninel Conde 1st instead of 2nd."""
    edges = [
        {
            "node": {
                "id": "hero:transmission-mlb81426-mx",
                "heroTargetContentType": "SPORTS_EVENT",
                "heroTarget": {
                    "__typename": "SportsEvent",
                    "id": "transmission:matchid:MLB81426_MX",
                    "name": "Cincinnati Reds vs. Chicago White Sox",
                    "localTeamName": "Cincinnati Reds",
                    "awayTeamName": "Chicago White Sox",
                },
            }
        },
        {
            "node": {
                "id": "h-ninel",
                "heroTargetContentType": "VIDEO",
                "heroTarget": {
                    "__typename": "VideoContent",
                    "id": "series:mcp:5767",
                    "title": "Ninel Conde sin filtro",
                    "videoType": "SERIES",
                },
            }
        },
        {
            "node": {
                "id": "h-acceso",
                "heroTargetContentType": "EPG_CHANNEL",
                "heroTarget": {
                    "__typename": "EpgChannel",
                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                    "title": "Acceso Total 24/7",
                },
            }
        },
    ]
    rows = TitleExtractor().extract(_hero_payload(edges), page_url_path="/ondemandplus")
    hero = [r for r in rows if r.carousel_y == 1]
    assert [r.title for r in hero] == [
        "Cincinnati Reds vs. Chicago White Sox",
        "Ninel Conde sin filtro",
        "Acceso Total 24/7",
    ]
    assert [r.carousel_x for r in hero] == [1, 2, 3]
    ninel = next(r for r in hero if "Ninel Conde" in r.title)
    assert ninel.carousel_x == 2
    assert ninel.position == 2


def test_hero_keeps_sparse_sports_event_so_later_titles_do_not_shift():
    """Prod dumps often return SportsEvent with only __typename — still a visible first tile."""
    edges = [
        {
            "node": {
                "id": "722c73bfc3e9439749e9f3669ff4eba6aa2e9267:transmission-mlb81426-mx",
                "textTitle": None,
                "heroTargetContentType": "SPORTS_EVENT",
                "heroTarget": {"__typename": "SportsEvent"},
            }
        },
        {
            "node": {
                "id": "h-ninel",
                "heroTargetContentType": "VIDEO",
                "heroTarget": {
                    "__typename": "VideoContent",
                    "id": "series:mcp:5767",
                    "title": "Ninel Conde sin filtro",
                    "videoType": "SERIES",
                },
            }
        },
    ]
    rows = TitleExtractor().extract(_hero_payload(edges, row_title="Hero Móvil"), page_url_path="/ondemandplus")
    hero = [r for r in rows if r.carousel_y == 1]
    assert len(hero) == 2
    assert hero[0].carousel_x == 1
    assert hero[0].video_type == "SPORTS"
    assert hero[0].content_id.endswith("transmission-mlb81426-mx") or "transmission" in hero[0].content_id
    assert hero[1].title == "Ninel Conde sin filtro"
    assert hero[1].carousel_x == 2


def test_hero_keeps_eleven_slots_when_sports_leads():
    """API may return 11 hero edges; the leading SportsEvent is a real first-page title."""
    edges = [
        {
            "node": {
                "id": "hero-sports",
                "heroTargetContentType": "SPORTS_EVENT",
                "heroTarget": {
                    "__typename": "SportsEvent",
                    "id": "transmission:matchid:MLB81426_MX",
                    "name": "Cincinnati Reds vs. Chicago White Sox",
                },
            }
        }
    ]
    titles = [
        ("Acceso Total 24/7", "EpgChannel", "channel:mcp:callsign:LCDLF01_SVOD", "LIVE_CHANNEL"),
        ("Pulseras rojas", "VideoContent", "series:mcp:5771", "SERIES"),
        ("El extraño retorno de Diana Salazar", "VideoContent", "series:mcp:4904", "SERIES"),
        ("Tráiler: Ninel Conde sin filtro", "VideoContent", "video:mcp:4925083", "MOVIE"),
        ("Guardián de mi vida", "VideoContent", "series:mcp:5724", "SERIES"),
        ("LALOLA", "VideoContent", "series:mcp:4653", "SERIES"),
        ("La Jefa", "VideoContent", "series:mcp:5349", "SERIES"),
        ("Una Familia Complicada", "VideoContent", "series:mcp:5164", "SERIES"),
        ("Doña Bárbara", "VideoContent", "series:mcp:5569", "SERIES"),
        ("El Chema", "VideoContent", "series:mcp:4204", "SERIES"),
    ]
    for title, tn, cid, vt in titles:
        if tn == "EpgChannel":
            edges.append(
                {
                    "node": {
                        "id": f"h-{cid}",
                        "heroTargetContentType": "EPG_CHANNEL",
                        "heroTarget": {"__typename": tn, "id": cid, "title": title},
                    }
                }
            )
        else:
            edges.append(
                {
                    "node": {
                        "id": f"h-{cid}",
                        "heroTargetContentType": "VIDEO",
                        "heroTarget": {
                            "__typename": tn,
                            "id": cid,
                            "title": title,
                            "videoType": vt,
                        },
                    }
                }
            )
    assert len(edges) == 11
    rows = TitleExtractor().extract(_hero_payload(edges), page_url_path="/ondemandplus")
    hero = [r for r in rows if r.carousel_y == 1]
    assert len(hero) == 11
    assert [r.carousel_x for r in hero] == list(range(1, 12))
    assert "Reds" in hero[0].title or "White Sox" in hero[0].title
    assert hero[0].carousel_x == 1
    assert hero[1].title == "Acceso Total 24/7"
    assert hero[1].carousel_x == 2
    assert hero[-1].title == "El Chema"
    assert hero[-1].carousel_x == 11


def test_recomendado_extracts_la_rosa_at_position_2():
    """Reco rail must extract UiVideoCard + UiVideoCarouselItem (CW-class bug)."""
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "h1",
                                                "heroTarget": {
                                                    "__typename": "VideoContent",
                                                    "id": "video:mcp:1",
                                                    "title": "Hero Title",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiLiveVideoCarousel",
                                "moduleType": "LIVE_VIDEO_CAROUSEL",
                                "title": "La Casa en vivo 24/7",
                                "id": "live-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "live-card-1",
                                                "channel": {
                                                    "id": "channel:mcp:callsign:LCDLF01_SVOD",
                                                    "title": "Acceso Total 24/7",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiRecommendedForYouCarousel",
                                "moduleType": "RECOMMENDED_FOR_YOU_CAROUSEL",
                                "title": "Recomendado para ti",
                                "id": "reco-1",
                                "contents": {
                                    "totalCount": 2,
                                    "edges": [
                                        {
                                            "node": {
                                                "__typename": "UiVideoCard",
                                                "id": "reco-card-1",
                                                "title": "Other Reco",
                                                "video": {
                                                    "id": "series:mcp:9999",
                                                    "title": "Other Reco",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "__typename": "UiVideoCarouselItem",
                                                "id": "reco-card-2",
                                                "title": "",
                                                "video": {
                                                    "id": "series:mcp:502",
                                                    "title": "La Rosa de Guadalupe",
                                                    "videoType": "SERIES",
                                                },
                                            }
                                        },
                                    ],
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    assert is_visible_web_row(
        payload["data"]["uiPage"]["uiModules"]["edges"][2]["node"]
    )
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    reco = [r for r in rows if r.row_title == "Recomendado para ti"]
    assert len(reco) == 2
    assert reco[0].carousel_y == 3
    assert reco[0].carousel_x == 1
    assert reco[0].title == "Other Reco"
    assert reco[1].title == "La Rosa de Guadalupe"
    assert reco[1].content_id == "series:mcp:502"
    assert reco[1].carousel_y == 3
    assert reco[1].carousel_x == 2
    assert reco[1].module_type == "RECOMMENDED_FOR_YOU_CAROUSEL"


def test_pending_cursors():
    cursors = pending_content_cursors(SAMPLE)
    assert cursors["Populares"] == "cursor-a"


def test_deduplicate():
    rows = TitleExtractor().extract(SAMPLE)
    unique = deduplicate(rows)
    # hero + video + two page cards
    assert len(unique) == 4
    assert unique[0].position == 1


def test_discover_paths():
    paths = extract_paths_from_page_payload(SAMPLE)
    assert "/ondemandplus/series" in paths
    assert "/ondemandplus/novelas" in paths
    assert normalize_path("https://vix.com/es-es/micro-dramas") == "/es-es/micro-dramas"


def test_csv_fields_exclude_images():
    row = TitleExtractor().extract(SAMPLE)[0].as_csv_row()
    for banned in ("poster_url", "hero_image_url", "logo_image_url", "image_urls", "canonical_url"):
        assert banned not in row
    for required in (
        "page_url_path",
        "position",
        "carousel_x",
        "carousel_y",
        "row_title",
        "module_id",
        "module_type",
        "is_hero",
        "id",
        "title",
        "videoType",
        "genres",
    ):
        assert required in row


def test_video_carousel_compacts_x_after_unresolved_edges():
    """Non-hero rails must match web order: skip unresolved nodes and compact x.

    Fixture models Selección especial-style rails where GraphQL may include
    blank/unresolvable edges that the web UI does not render as tiles.
    """
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Selección especial",
                                "id": "sel-1",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "n1",
                                                "video": {
                                                    "id": "video:mcp:1",
                                                    "title": "Spider-Man: Lejos de casa",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "id": "n-blank",
                                                # No video / title / resolvable identity
                                            }
                                        },
                                        {"node": None},
                                        {
                                            "node": {
                                                "id": "n3",
                                                "video": {
                                                    "id": "video:mcp:4045721",
                                                    "title": "San Andreas",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        },
                                        {
                                            "node": {
                                                "id": "n4",
                                                "video": {
                                                    "id": "video:mcp:4",
                                                    "title": "No manches Frida",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        },
                                    ]
                                },
                            }
                        }
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    assert [r.title for r in rows] == [
        "Spider-Man: Lejos de casa",
        "San Andreas",
        "No manches Frida",
    ]
    assert [r.carousel_x for r in rows] == [1, 2, 3]
    assert all(r.carousel_y == 1 for r in rows)
    assert rows[1].title == "San Andreas"
    assert rows[1].carousel_x == 2
    assert not any(r.video_type == "UNKNOWN" for r in rows)


def test_empty_module_does_not_shift_later_row_slots():
    """Empty CMS rails keep y; later row starts at carousel_x=1 on the next Row #."""
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiHeroCarousel",
                                "moduleType": "HERO_CAROUSEL",
                                "title": "Hero",
                                "id": "hero-empty",
                                "contents": {"edges": []},
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiContinueWatchingCarousel",
                                "moduleType": "CONTINUE_WATCHING_CAROUSEL",
                                "title": "Seguir viendo",
                                "id": "cw-empty",
                                "contents": {"edges": []},
                            }
                        },
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado",
                                "id": "row-2",
                                "contents": {
                                    "edges": [
                                        {
                                            "node": {
                                                "id": "c1",
                                                "video": {
                                                    "id": "video:mcp:1",
                                                    "title": "First after empty",
                                                    "videoType": "MOVIE",
                                                },
                                            }
                                        }
                                    ]
                                },
                            }
                        },
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    assert all(r.carousel_x >= 1 for r in rows)
    assert not any(r.carousel_x == 0 for r in rows)
    empty_hero = next(r for r in rows if r.row_title == "Hero")
    assert empty_hero.video_type == "EMPTY"
    assert empty_hero.carousel_y == 1
    assert empty_hero.carousel_x == 1
    empty_cw = next(r for r in rows if r.row_title == "Seguir viendo")
    assert empty_cw.video_type == "EMPTY"
    assert empty_cw.carousel_y == 2
    later = next(r for r in rows if r.title == "First after empty")
    assert later.carousel_y == 3
    assert later.carousel_x == 1
    assert later.row_title == "Lo más buscado"


def test_empty_personalized_and_editorial_rails_keep_y_sequence():
    """Several empty CMS module kinds on one page: each keeps carousel_y; later row is not shifted."""

    def empty_edge(typename: str, module_type: str, title: str, mid: str) -> dict:
        return {
            "node": {
                "__typename": typename,
                "moduleType": module_type,
                "title": title,
                "id": mid,
                "contents": {"edges": []},
            }
        }

    edges = [
        empty_edge("UiHeroCarousel", "HERO_CAROUSEL", "Hero", "hero-empty"),
        empty_edge(
            "UiRecommendedForYouCarousel",
            "RECOMMENDED_FOR_YOU_CAROUSEL",
            "Recomendado para ti",
            "reco-empty",
        ),
        empty_edge(
            "UiContinueWatchingCarousel",
            "CONTINUE_WATCHING_CAROUSEL",
            "Seguir viendo",
            "cw-empty",
        ),
        empty_edge(
            "UiBecauseYouCarousel",
            "BECAUSE_YOU_CAROUSEL",
            "Porque viste La Rosa de Guadalupe",
            "byou-empty",
        ),
        empty_edge("UiWatchlistCarousel", "WATCH_LIST_CAROUSEL", "Mi lista", "wl-empty"),
        empty_edge(
            "UiRecentChannelsCarousel",
            "RECENT_CHANNELS_CAROUSEL",
            "Canales vistos recientemente",
            "rc-empty",
        ),
        empty_edge("UiVideoCarousel", "VIDEO_CAROUSEL", "Colección vacía", "col-empty"),
        empty_edge("UiTrendingNowCarousel", "TRENDING_NOW_CAROUSEL", "Tendencias", "tr-empty"),
        empty_edge("UiMixedContentCarousel", "MIXED_LIST_CAROUSEL", "Mixto vacío", "mix-empty"),
        empty_edge("UiLiveVideoCarousel", "LIVE_VIDEO_CAROUSEL", "Canales en vivo", "live-empty"),
        empty_edge(
            "UiSportsEventCarousel",
            "SPORTS_EVENT_CAROUSEL",
            "En vivo y Próximamente",
            "sports-empty",
        ),
        empty_edge("UiPageCarousel", "PAGE_CAROUSEL", "Hubs", "page-empty"),
        _inline_edge("inline-ver-mas"),
        {
            "node": {
                "__typename": "UiUnknownDecoration",
                "moduleType": "DECORATION",
                "id": "skip-me",
            }
        },
        _video_edge("Lo más buscado en México", "v-lo"),
    ]
    payload = {"data": {"uiPage": {"urlPath": "/ondemandplus", "uiModules": {"edges": edges}}}}
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    enrich_layout_fields(rows)
    by_y = {}
    for r in rows:
        by_y.setdefault(r.carousel_y, r)
    expected = [
        "Hero",
        "Recomendado para ti",
        "Seguir viendo",
        "Porque viste La Rosa de Guadalupe",
        "Mi lista",
        "Canales vistos recientemente",
        "Colección vacía",
        "Tendencias",
        "Mixto vacío",
        "Canales en vivo",
        "En vivo y Próximamente",
        "Hubs",
        "Lo más buscado en México",
    ]
    assert [by_y[i].row_title for i in range(1, 14)] == expected
    for y in range(1, 13):
        assert by_y[y].video_type == "EMPTY"
        assert by_y[y].title == "(empty)"
        assert by_y[y].row_size == 0
        assert by_y[y].carousel_x == 1
    later = by_y[13]
    assert later.row_title == "Lo más buscado en México"
    assert later.title == "Lo más buscado en México item"
    assert later.carousel_x == 1
    assert not any(r.module_type == "INLINE_PAGE" for r in rows)
    assert not any(r.row_title == "Ver más" for r in rows)


def test_pagination_continues_x_within_row_not_next_row():
    def rail(title: str, mid: str, names: list[str]) -> dict:
        return {
            "node": {
                "__typename": "UiVideoCarousel",
                "moduleType": "VIDEO_CAROUSEL",
                "title": title,
                "id": mid,
                "isPlaylist": True,
                "contents": {
                    "edges": [
                        {
                            "node": {
                                "id": f"{mid}-{i}",
                                "video": {
                                    "id": f"v-{mid}-{i}",
                                    "title": name,
                                    "videoType": "MOVIE",
                                },
                            }
                        }
                        for i, name in enumerate(names, start=1)
                    ]
                },
            }
        }

    first_page = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {"edges": [rail("Populares", "pop", ["A", "B"])]},
            }
        }
    }
    more_same_row = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {"edges": [rail("Populares", "pop", ["C", "D"])]},
            }
        }
    }
    next_row_page = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {"edges": [rail("Novedades", "new", ["E"])]},
            }
        }
    }
    first = TitleExtractor().extract(first_page, page_url_path="/ondemandplus")
    assert [r.carousel_x for r in first] == [1, 2]
    cont = TitleExtractor().extract(
        more_same_row,
        page_url_path="/ondemandplus",
        module_y_base=1,
        content_x_offset=2,
        continuing_content=True,
    )
    assert all(r.carousel_y == 1 for r in cont)
    assert [r.carousel_x for r in cont] == [3, 4]
    nxt = TitleExtractor().extract(
        next_row_page,
        page_url_path="/ondemandplus",
        module_y_base=1,
    )
    assert nxt[0].row_title == "Novedades"
    assert nxt[0].carousel_y == 2
    assert nxt[0].carousel_x == 1


def test_extractor_never_emits_carousel_x_zero_for_real_tiles():
    rows = TitleExtractor().extract(SAMPLE, page_url_path="/micro-dramas")
    assert rows
    assert all(r.carousel_x >= 1 for r in rows)


def _collection_payload(edges: list[dict]) -> dict:
    return {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandplus",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado en México",
                                "id": "mas-buscado",
                                "contents": {"edges": edges},
                            }
                        }
                    ]
                },
            }
        }
    }


def test_video_carousel_leading_carousel_item_keeps_slot_one():
    """VIDEO_CAROUSEL first edge can be UiVideoCarouselItem; omitting it made live #1 appear as x=2."""
    edges = [
        {
            "node": {
                "__typename": "UiVideoCarouselItem",
                "id": "item-first",
                "title": "Es por su bien",
                "video": {
                    "id": "video:mcp:4562401",
                    "title": "Es por su bien",
                    "videoType": "MOVIE",
                },
            }
        },
        {
            "node": {
                "__typename": "UiVideoCard",
                "id": "card-second",
                "video": {
                    "id": "series:mcp:4635",
                    "title": "El Gallo de Oro",
                    "videoType": "SERIES",
                },
            }
        },
    ]
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado",
                                "id": "mas-buscado",
                                "contents": {"edges": edges},
                            }
                        }
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    rail = [r for r in rows if r.row_title == "Lo más buscado"]
    assert [r.title for r in rail] == ["Es por su bien", "El Gallo de Oro"]
    assert [r.carousel_x for r in rail] == [1, 2]
    assert rail[0].carousel_x == 1


def test_video_carousel_empty_first_edge_does_not_claim_slot_one_when_unresolved():
    """A blank union member must not be extracted as a fake title; resolvable cards keep order."""
    edges = [
        {"node": {}},
        {
            "node": {
                "__typename": "UiVideoCard",
                "id": "card-live-first",
                "video": {
                    "id": "video:mcp:4562401",
                    "title": "Es por su bien",
                    "videoType": "MOVIE",
                },
            }
        },
    ]
    rows = TitleExtractor().extract(_collection_payload(edges), page_url_path="/ondemandplus")
    rail = [r for r in rows if r.carousel_y == 1]
    assert rail[0].title == "Es por su bien"
    assert rail[0].carousel_x == 1


def test_collection_keeps_leading_item_when_video_null_ninel_is_second():
    """CMS collection slot 1 must not vanish when video is geo-null; Ninel stays 2nd."""
    edges = [
        {
            "node": {
                "id": "card-ninos",
                "title": None,
                "video": None,
                "clickTrackingJson": {
                    "ui_content_title": "No dejes a los niños solos",
                    "ui_content_id": "video:mcp:ninos",
                },
            }
        },
        {
            "node": {
                "id": "card-ninel",
                "video": {
                    "id": "series:mcp:5767",
                    "title": "Ninel Conde sin filtro",
                    "videoType": "SERIES",
                },
            }
        },
    ]
    rows = TitleExtractor().extract(_collection_payload(edges), page_url_path="/ondemandplus")
    rail = [r for r in rows if r.carousel_y == 1]
    assert [r.title for r in rail] == [
        "No dejes a los niños solos",
        "Ninel Conde sin filtro",
    ]
    assert [r.carousel_x for r in rail] == [1, 2]
    assert rail[0].content_id
    assert rail[1].carousel_x == 2


def test_collection_keeps_leading_sports_event_card():
    """World-cup collection tiles can be SportsEvent; dropping them shifts later movies."""
    edges = [
        {
            "node": {
                "__typename": "SportsEvent",
                "id": "transmission:gol-de-vesta",
                "title": "Gol de Vesta",
                "sportsEventId": "vesta-1",
            }
        },
        {
            "node": {
                "id": "card-fan",
                "video": {
                    "id": "video:mcp:fan",
                    "title": "Soy tu fan: La película",
                    "videoType": "MOVIE",
                },
            }
        },
    ]
    rows = TitleExtractor().extract(_collection_payload(edges), page_url_path="/ondemandplus")
    assert [r.title for r in rows] == ["Gol de Vesta", "Soy tu fan: La película"]
    assert [r.carousel_x for r in rows] == [1, 2]
    assert rows[0].video_type == "SPORTS"


def test_collection_carousel_x_is_1based_input_order():
    """In-row Position is 1-based GraphQL edge order, not the page-wide CSV position."""
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    edges = [
        {
            "node": {
                "id": f"card-{i}",
                "video": {"id": f"video:mcp:{i}", "title": name, "videoType": "MOVIE"},
            }
        }
        for i, name in enumerate(names, start=1)
    ]
    rows = TitleExtractor().extract(_collection_payload(edges), page_url_path="/ondemandplus")
    assert [r.title for r in rows] == names
    assert [r.carousel_x for r in rows] == [1, 2, 3, 4, 5]
    assert [r.position for r in rows] == [1, 2, 3, 4, 5]
    assert all(r.carousel_y == 1 for r in rows)
    assert rows[0].row_title == "Lo más buscado en México"


def _paged_video_edge(i: int, title: str, *, mcp: str | None = None) -> dict:
    return {
        "node": {
            "id": f"n{i}",
            "video": {
                "id": f"v{i}",
                "mcpId": mcp or str(i),
                "title": title,
                "videoType": "MOVIE",
            },
        }
    }


def _rail_payload(
    title: str,
    module_id: str,
    edges: list[dict],
    *,
    has_next: bool = False,
    end_cursor: str | None = None,
    total: int | None = None,
    sibling: dict | None = None,
) -> dict:
    contents: dict = {
        "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
        "edges": edges,
    }
    if total is not None:
        contents["totalCount"] = total
    rail = {
        "node": {
            "__typename": "UiVideoCarousel",
            "moduleType": "VIDEO_CAROUSEL",
            "title": title,
            "id": module_id,
            "isPlaylist": True,
            "contents": contents,
        }
    }
    modules = [rail]
    if sibling is not None:
        modules.append(sibling)
    return {"data": {"uiPage": {"urlPath": "/ondemandplus", "uiModules": {"edges": modules}}}}


def test_first_page_item_count_is_not_a_layout_cap():
    """pageInfo.itemCount is this GraphQL page advertisement, not CMS rail length.

    If the payload already contains 20 edges (totalCount=62), emit all 20. Truncating
    to itemCount=15 invented a display limit the schema does not expose.
    """
    payload = _rail_payload(
        "Nuevo en ViX",
        "nuevo",
        [_paged_video_edge(i, f"T{i}") for i in range(1, 21)],
        has_next=True,
        end_cursor="keep-going",
        total=62,
    )
    payload["data"]["uiPage"]["uiModules"]["edges"][0]["node"]["contents"]["pageInfo"][
        "itemCount"
    ] = 15
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandplus")
    enrich_layout_fields(rows)
    assert [r.title for r in rows] == [f"T{i}" for i in range(1, 21)]
    assert rows[0].row_size == 20
    from vix_scraper.extractor import contents_has_more, pending_content_cursors

    conn = payload["data"]["uiPage"]["uiModules"]["edges"][0]["node"]["contents"]
    assert contents_has_more(conn) is True
    assert pending_content_cursors(payload)


def test_contents_has_more_stops_when_edges_cover_total_count():
    from vix_scraper.extractor import contents_has_more, pending_content_cursors

    payload = _rail_payload(
        "Nuevo en ViX",
        "nuevo",
        [_paged_video_edge(i, f"T{i}") for i in range(1, 4)],
        has_next=True,
        end_cursor="keep-going",
        total=3,
    )
    conn = payload["data"]["uiPage"]["uiModules"]["edges"][0]["node"]["contents"]
    assert contents_has_more(conn) is False
    assert pending_content_cursors(payload) == {}


def test_continuation_skips_overlapping_cursor_items():
    """Cursor pages that re-emit the last tile must not inflate row_size."""
    first = _rail_payload(
        "Micros",
        "micros",
        [_paged_video_edge(1, "One", mcp="a"), _paged_video_edge(2, "Two", mcp="b")],
        has_next=True,
        end_cursor="c1",
        total=3,
    )
    cont = _rail_payload(
        "Micros",
        "micros",
        [_paged_video_edge(2, "Two", mcp="b"), _paged_video_edge(3, "Three", mcp="c")],
        has_next=False,
        total=3,
    )
    rows = TitleExtractor().extract(first, page_url_path="/ondemandplus")
    more = TitleExtractor().extract(
        cont,
        page_url_path="/ondemandplus",
        module_y_base=1,
        content_x_offset=2,
        continuing_content=True,
        continuing_module_id="micros",
        skip_keys={"a", "b"},
    )
    enrich_layout_fields(rows)
    all_rows = rows + more
    enrich_layout_fields(all_rows)
    assert [r.title for r in all_rows] == ["One", "Two", "Three"]
    assert [r.carousel_x for r in all_rows] == [1, 2, 3]
    assert all_rows[0].row_size == 3
    assert len({r.content_id for r in all_rows}) == 3


def test_continuation_trims_to_graphql_total_count():
    """Do not keep an extra unique tile past contents.totalCount."""
    first = _rail_payload(
        "Exclusivo en ViX",
        "ex",
        [_paged_video_edge(1, "A", mcp="1"), _paged_video_edge(2, "B", mcp="2")],
        has_next=True,
        end_cursor="c1",
        total=3,
    )
    cont = _rail_payload(
        "Exclusivo en ViX",
        "ex",
        [_paged_video_edge(3, "C", mcp="3"), _paged_video_edge(4, "Extra", mcp="4")],
        has_next=True,
        end_cursor="c2",
        total=3,
    )
    rows = TitleExtractor().extract(first, page_url_path="/ondemandplus")
    more = TitleExtractor().extract(
        cont,
        page_url_path="/ondemandplus",
        module_y_base=1,
        content_x_offset=2,
        continuing_content=True,
        continuing_module_id="ex",
        skip_keys={"1", "2"},
    )
    all_rows = rows + more
    enrich_layout_fields(all_rows)
    assert [r.title for r in all_rows] == ["A", "B", "C"]
    assert all_rows[0].row_size == 3


def test_last_content_page_does_not_glue_sibling_module():
    sibling = {
        "node": {
            "__typename": "UiVideoCarousel",
            "moduleType": "VIDEO_CAROUSEL",
            "title": "Other",
            "id": "other",
            "contents": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [_paged_video_edge(9, "Sibling", mcp="sib")],
            },
        }
    }
    first = _rail_payload(
        "Nuevo en ViX",
        "nuevo",
        [_paged_video_edge(1, "One", mcp="a")],
        has_next=True,
        end_cursor="c1",
        total=2,
        sibling=sibling,
    )
    last = _rail_payload(
        "Nuevo en ViX",
        "nuevo",
        [_paged_video_edge(2, "Two", mcp="b")],
        has_next=False,
        total=2,
        sibling=sibling,
    )
    rows = TitleExtractor().extract(first, page_url_path="/ondemandplus")
    more = TitleExtractor().extract(
        last,
        page_url_path="/ondemandplus",
        module_y_base=1,
        content_x_offset=1,
        continuing_content=True,
        continuing_module_id="nuevo",
    )
    all_rows = rows + more
    enrich_layout_fields(all_rows)
    nuevo = [r for r in all_rows if r.row_title == "Nuevo en ViX"]
    assert [r.title for r in nuevo] == ["One", "Two"]
    assert nuevo[0].row_size == 2
    assert all(r.title != "Sibling" for r in nuevo)


def test_module_layout_length_prefers_display_limit():
    from vix_scraper.extractor import module_layout_length

    module = {
        "displayLimit": 15,
        "contents": {"totalCount": 62},
        "collection": {"totalCount": 62},
    }
    assert module_layout_length(module) == 15


def test_module_layout_length_uses_contents_when_smaller_than_collection():
    from vix_scraper.extractor import module_layout_length

    module = {
        "contents": {"totalCount": 26},
        "collection": {"totalCount": 200},
    }
    assert module_layout_length(module) == 26


def test_module_layout_length_matches_contents_when_totals_equal():
    from vix_scraper.extractor import module_layout_length

    module = {
        "isPlaylist": True,
        "contents": {"totalCount": 62},
        "collection": {"totalCount": 62},
    }
    assert module_layout_length(module) == 62


def test_module_layout_length_prefers_editorial_items_over_collection_dump():
    from vix_scraper.extractor import module_layout_length

    module = {
        "items": [
            {"video": {"id": "a", "title": "A"}},
            {"video": {"id": "b", "title": "B"}},
        ],
        "contents": {
            "totalCount": 30,
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "edges": [{"node": {"video": {"id": f"n{i}", "title": f"N{i}"}}} for i in range(30)],
        },
        "collection": {"totalCount": 30},
    }
    assert module_layout_length(module) == 2


def test_extract_editorial_items_cms_order_not_ranked_contents():
    """Lo más buscado CMS pin list vs ranked contents dump (El Señor at x=14, not 19)."""
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
    ranked = [
        "María de Todos Los Ángeles",
        "Radical",
        "¡Qué despadre!",
        "Quiero tu vida",
        "Spider-Man: Lejos de casa",
        "La Familia P. Luche",
        "El candidato honesto",
        "Los Héroes del Norte",
        "Bodas, S.A.",
        "El hilo rojo",
        "Archivo negro sombras del crimen",
        "El Hombre Araña 3",
        "El show, crónica de un asesinato",
        "Es por su bien",
        "LALOLA",
        "¿Quieres ser mi... novia?",
        "Juntos pero no revueltos",
        "Instintos",
        "El Señor de los Cielos",
        "Consuelo",
    ]
    items = [
        {
            "__typename": "UiVideoCard",
            "id": f"item-{i}",
            "title": title,
            "video": {"id": f"video:{i}", "title": title, "videoType": "SERIES", "mcpId": str(i)},
        }
        for i, title in enumerate(cms, start=1)
    ]
    contents_edges = [
        {
            "node": {
                "__typename": "UiVideoCard",
                "id": f"dump-{i}",
                "title": title,
                "video": {"id": f"dump:{i}", "title": title, "videoType": "SERIES", "mcpId": f"d{i}"},
            }
        }
        for i, title in enumerate(ranked, start=1)
    ]
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado",
                                "id": "9e5c14fe3b79a275e26c5c5857b47e4e51de914d",
                                "isPlaylist": True,
                                "items": items,
                                "contents": {
                                    "totalCount": 30,
                                    "pageInfo": {"hasNextPage": True, "endCursor": "c30"},
                                    "edges": contents_edges,
                                },
                            }
                        }
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    rail = [r for r in rows if r.row_title == "Lo más buscado" and r.video_type != "EMPTY"]
    assert [r.title for r in rail] == cms
    assert [r.carousel_x for r in rail] == list(range(1, 15))
    senor = next(r for r in rail if "Señor de los Cielos" in r.title)
    assert senor.carousel_x == 14
    assert senor.carousel_y == 1
    assert rail[0].title == "Los Héroes del Norte"
    assert all(r.title != "María de Todos Los Ángeles" for r in rail)


def test_editorial_items_of_14_do_not_emit_30_contents():
    from vix_scraper.extractor import module_contents_has_more, module_layout_length

    items = [{"video": {"id": str(i), "title": f"P{i}", "mcpId": str(i)}} for i in range(1, 15)]
    dump = [
        {
            "node": {
                "video": {"id": f"d{i}", "title": f"D{i}", "mcpId": f"d{i}"},
            }
        }
        for i in range(1, 31)
    ]
    module = {
        "__typename": "UiVideoCarousel",
        "items": items,
        "contents": {
            "totalCount": 30,
            "pageInfo": {"hasNextPage": True, "endCursor": "c30"},
            "edges": dump,
        },
    }
    assert module_layout_length(module) == 14
    assert module_contents_has_more(module) is False
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                **module,
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Lo más buscado",
                                "id": "buscado",
                            }
                        }
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    rail = [r for r in rows if r.video_type != "EMPTY"]
    assert len(rail) == 14
    assert rail[-1].carousel_x == 14
    assert all(not t.startswith("D") for t in [r.title for r in rail])


def test_exclusivo_items_n_not_contents_total_163():
    """Catalog dump of 163 cannot place El Señor at x=119 unless pins actually have 119."""
    pins = [{"video": {"id": str(i), "title": f"Pin {i}", "mcpId": str(i)}} for i in range(1, 21)]
    dump = []
    for i in range(1, 164):
        title = "El Señor de los Cielos" if i == 119 else f"Dump {i}"
        dump.append(
            {
                "node": {
                    "video": {"id": f"c{i}", "title": title, "mcpId": f"c{i}"},
                }
            }
        )
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "UiVideoCarousel",
                                "moduleType": "VIDEO_CAROUSEL",
                                "title": "Exclusivo en ViX",
                                "id": "ea0227efe49a9a60ac41fc54d7e0200608327790",
                                "items": pins,
                                "contents": {
                                    "totalCount": 163,
                                    "pageInfo": {"hasNextPage": True, "endCursor": "c80"},
                                    "edges": dump,
                                },
                            }
                        }
                    ]
                },
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    rail = [r for r in rows if r.row_title == "Exclusivo en ViX" and r.video_type != "EMPTY"]
    assert len(rail) == 20
    assert [r.carousel_x for r in rail] == list(range(1, 21))
    assert all(r.carousel_x != 119 for r in rail)
    assert all("Señor de los Cielos" not in r.title for r in rail)


def test_exclusivo_layout_window_not_catalog_total_count():
    """No items: first contents page (no after) is the visible rail, not totalCount=163."""
    from vix_scraper.extractor import module_contents_has_more, module_layout_length

    edges = [
        {
            "node": {
                "video": {"id": str(i), "title": f"Pin {i}", "mcpId": str(i)},
            }
        }
        for i in range(1, 27)
    ]
    module = {
        "__typename": "UiVideoCarousel",
        "moduleType": "VIDEO_CAROUSEL",
        "isPlaylist": False,
        "contents": {
            "totalCount": 163,
            "pageInfo": {"hasNextPage": True, "endCursor": "c26"},
            "edges": edges,
        },
    }
    assert module_layout_length(module) == 26
    assert module_contents_has_more(module) is False
    payload = {
        "data": {
            "uiPage": {
                "urlPath": "/ondemandpluswc",
                "uiModules": {"edges": [{"node": {**module, "title": "Exclusivo en ViX", "id": "ex"}}]},
            }
        }
    }
    rows = TitleExtractor().extract(payload, page_url_path="/ondemandpluswc")
    rail = [r for r in rows if r.video_type != "EMPTY"]
    assert len(rail) == 26
    assert rail[-1].carousel_x == 26
    assert all(r.carousel_x < 119 for r in rail)


def test_playlist_without_items_still_uses_contents_total_count():
    from vix_scraper.extractor import module_layout_length, module_walks_contents_catalog

    module = {
        "__typename": "UiVideoCarousel",
        "isPlaylist": True,
        "contents": {
            "totalCount": 163,
            "pageInfo": {"hasNextPage": True, "endCursor": "c80"},
            "edges": [{"node": {"video": {"id": "1", "title": "A", "mcpId": "1"}}}],
        },
    }
    assert module_walks_contents_catalog(module) is True
    assert module_layout_length(module) == 163

