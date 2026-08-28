"""Guard GraphQL query files so editorial rails request every visible card type."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = (ROOT / "queries" / "layout.graphql").read_text(encoding="utf-8")
REQUEST = (ROOT / "queries" / "request.graphql").read_text(encoding="utf-8")


def _carousel_block(source: str, typename: str) -> str:
    needle = f"... on {typename} {{"
    start = source.index(needle)
    depth = 0
    for index, char in enumerate(source[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unclosed {typename} fragment")


def test_editorial_video_carousel_queries_card_and_carousel_item():
    for source in (LAYOUT, REQUEST):
        block = _carousel_block(source, "UiVideoCarousel")
        assert "... on UiVideoCard" in block
        assert "... on UiVideoCarouselItem" in block
        assert "VideoCarouselItemFields" in block


def test_trending_and_mixed_carousels_query_carousel_item():
    for source in (LAYOUT, REQUEST):
        for typename in ("UiTrendingNowCarousel", "UiMixedContentCarousel"):
            block = _carousel_block(source, typename)
            assert "... on UiVideoCard" in block
            assert "... on UiVideoCarouselItem" in block


def test_video_carousel_item_fragment_is_lean():
    """Do not duplicate full VideoTitleFields onto UiVideoCarouselItem (complexity)."""
    marker = "fragment VideoCarouselItemFields on UiVideoCarouselItem"
    for source in (LAYOUT, REQUEST):
        start = source.index(marker)
        end = source.index("\nquery ", start)
        block = source[start:end]
        assert "LeanVideoIdentityFields" in block
        assert "...VideoTitleFields" not in block


def test_layout_query_is_identity_only():
    """Layout CSV needs title + ids + module placement — not the fat explore graph."""
    assert "fragment VideoTitleFields" not in LAYOUT
    card = LAYOUT[LAYOUT.index("fragment VideoCardFields") : LAYOUT.index("fragment VideoCarouselItemFields")]
    assert "LeanVideoIdentityFields" in card
    assert "...VideoTitleFields" not in card
    hero = _carousel_block(LAYOUT, "UiHeroCarousel")
    assert "LeanVideoIdentityFields" in hero
    assert "...VideoTitleFields" not in hero
    live = _carousel_block(LAYOUT, "UiLiveVideoCarousel")
    assert "LeanVideoIdentityFields" in live
    assert "...VideoTitleFields" not in live
    forbidden = (
        "contributors",
        "imageAssets",
        "ImageAssetFields",
        "genresV2",
        "detailPageMetadata",
        "playbackData",
        "headline",
        "dateReleased",
        "yearReleased",
        "keywords",
        "badges",
        "ratings",
        "resizedLink",
        "heroImage",
        "logoImage",
        "clickTrackingJson",
        "trackingMetadataJson",
        "compositeImageLink",
        "description",
    )
    for name in forbidden:
        for line in LAYOUT.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert name not in stripped, f"layout.graphql must not query {name}: {stripped}"


def test_layout_query_does_not_request_optional_limit_fields():
    """displayLimit/maxItems/visibleCount are not on the production schema — querying
    them fails the whole scrape. Extractor still reads them if the API ever returns them.
    """
    for name in ("displayLimit", "maxItems", "visibleCount", "previewCount", "itemLimit"):
        for line in LAYOUT.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert name not in stripped, f"layout.graphql must not query {name}: {stripped}"


def test_layout_keeps_sports_event_and_carousel_item():
    assert "... on SportsEvent" in LAYOUT
    assert "... on UiVideoCarouselItem" in LAYOUT
    hero = _carousel_block(LAYOUT, "UiHeroCarousel")
    assert "... on SportsEvent" in hero


def test_layout_video_carousel_requests_editorial_items_and_playlist_fields():
    block = _carousel_block(LAYOUT, "UiVideoCarousel")
    assert "isPlaylist" in block
    assert "collectionId" in block
    assert "treatment" in block
    assert "items {" in block
    assert "... on UiVideoCard" in block
    assert "collection {" in block
    req = _carousel_block(REQUEST, "UiVideoCarousel")
    assert "items {" in req
    assert "isPlaylist" in req
    assert "collection {" in req


def test_layout_query_comments_resolve_editorial_vs_catalog():
    assert "backing catalog" in LAYOUT.lower() or "pin list" in LAYOUT.lower()
    assert "isPlaylist" in LAYOUT


def test_layout_because_you_uses_lean_identity():
    block = _carousel_block(LAYOUT, "UiBecauseYouCarousel")
    assert "LeanVideoIdentityFields" in block
    assert "...VideoTitleFields" not in block
