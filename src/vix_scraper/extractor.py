"""Extract title/page/image rows from a uiPage GraphQL payload."""

from __future__ import annotations

from typing import Any

from vix_scraper.errors import ExtractError
from vix_scraper.models import (
    CAROUSEL_MODULE_TYPES,
    CAROUSEL_TYPENAMES,
    HERO_MODULE_TYPES,
    HERO_TYPENAMES,
    INLINE_MODULE_TYPES,
    INLINE_TYPENAMES,
    VIDEO_CAROUSEL_TYPENAMES,
    ExportedImage,
    ExportedPage,
    ExportedTitle,
)
from vix_scraper.util import dumps_json, first_text


def is_hero_module(module: dict[str, Any]) -> bool:
    module_type = first_text(module.get("moduleType"))
    typename = first_text(module.get("__typename"))
    return module_type in HERO_MODULE_TYPES or typename in HERO_TYPENAMES


def is_inline_module(module: dict[str, Any]) -> bool:
    module_type = first_text(module.get("moduleType"))
    typename = first_text(module.get("__typename"))
    return module_type in INLINE_MODULE_TYPES or typename in INLINE_TYPENAMES


def enrich_layout_fields(
    rows: list[ExportedTitle],
    *,
    auth_profile: str = "",
) -> list[ExportedTitle]:
    """Backfill row_size and optional auth_profile on title placements."""
    counts: dict[tuple[str, int], int] = {}
    for row in rows:
        if row.video_type == "EMPTY":
            continue
        key = (row.page_url_path, row.carousel_y)
        counts[key] = counts.get(key, 0) + 1
    for row in rows:
        row.row_size = counts.get((row.page_url_path, row.carousel_y), 0)
        if auth_profile and not row.auth_profile:
            row.auth_profile = auth_profile
    return rows


def list_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = first_text(item.get("name"), item.get("title"))
        else:
            name = first_text(item)
        if name:
            names.append(name)
    return names


def page_info(connection: Any) -> tuple[bool, str]:
    if not isinstance(connection, dict):
        return False, ""
    info = connection.get("pageInfo")
    if not isinstance(info, dict):
        return False, ""
    has_next = bool(info.get("hasNextPage"))
    cursor = first_text(info.get("endCursor"))
    return has_next and bool(cursor), cursor


def _positive_int(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def contents_total_count(connection: Any) -> int | None:
    """GraphQL contents.totalCount (full collection / catalog), or None when omitted."""
    if not isinstance(connection, dict):
        return None
    raw = connection.get("totalCount")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def contents_page_item_count(connection: Any) -> int | None:
    """This contents page's pageInfo.itemCount (this GraphQL page), not catalog totalCount."""
    if not isinstance(connection, dict):
        return None
    info = connection.get("pageInfo")
    if not isinstance(info, dict):
        return None
    raw = info.get("itemCount")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def contents_edge_count(connection: Any) -> int:
    if not isinstance(connection, dict):
        return 0
    edges = connection.get("edges")
    return len(edges) if isinstance(edges, list) else 0


# CMS LAYOUTS length fields when the schema exposes them. Never treat
# PaginationParams.first's GraphQL default (10) as one of these.
_LAYOUT_LIMIT_KEYS = (
    "displayLimit",
    "maxItems",
    "visibleCount",
    "previewCount",
    "itemLimit",
    "contentLimit",
    "maxVisible",
    "displayCount",
)


def collection_total_count(module: Any) -> int | None:
    """Backing collection size when the module exposes it (not contents.totalCount)."""
    if not isinstance(module, dict):
        return None
    coll = module.get("collection")
    if isinstance(coll, dict):
        return contents_total_count(coll)
    return _positive_int(module.get("collectionTotalCount"))


# Personalized / live rails whose contents connection is the product (scroll until
# exhausted). Editorial VIDEO_CAROUSEL pins live on ``items`` or the first layout
# page; walking contents.totalCount there dumps the backing catalog (Exclusivo 163).
_CATALOG_CONTENTS_TYPENAMES = frozenset(
    {
        "UiHeroCarousel",
        "UiContinueWatchingCarousel",
        "UiRecommendedForYouCarousel",
        "UiBecauseYouCarousel",
        "UiWatchlistCarousel",
        "UiRecentChannelsCarousel",
        "UiLiveVideoCarousel",
        "UiSportsEventCarousel",
    }
)
_PLAYLIST_TREATMENTS = frozenset(
    {
        "RANKED",
        "PLAYLIST",
        "DYNAMIC",
        "ALGORITHM",
        "INFINITE",
        "RECOMMENDATION",
        "RECO",
        "PERSONALIZED",
    }
)


def module_walks_contents_catalog(module: Any) -> bool:
    """True when product length is the contents connection until it ends.

    Historical rule: don't cap on pageInfo.itemCount / first:100; walk
    contents.totalCount / hasNextPage. That is correct for ranked playlists,
    CW, reco, and true infinite connections.

    It is wrong for CMS editorial rails. Prefer ``items`` (pin list) when
    present. Without items, ``isPlaylist`` / ranked ``treatment`` / personalized
    typenames still walk contents. A finite UiVideoCarousel with neither items
    nor those flags uses the first layout page's contents.edges (the visible
    window), not catalog totalCount. Never invent a 30/119 cap.
    """
    if not isinstance(module, dict):
        return False
    if editorial_items_present(module):
        return False
    typename = first_text(module.get("__typename"))
    if typename in _CATALOG_CONTENTS_TYPENAMES:
        return True
    if module.get("isPlaylist") is True:
        return True
    treatment = first_text(module.get("treatment")).upper().replace(" ", "_")
    if treatment in _PLAYLIST_TREATMENTS:
        return True
    return False


def editorial_items_present(module: Any) -> bool:
    """True when GraphQL returned the module's CMS ``items`` list (including empty).

    ``items`` missing from the payload means the field was omitted or stripped.
    An explicit empty list is an empty editorial rail, not a cue to dump
    ``contents`` (the backing collection / ranked catalog).
    """
    if not isinstance(module, dict) or "items" not in module:
        return False
    items = module.get("items")
    if items is None:
        return False
    if isinstance(items, list):
        return True
    if isinstance(items, dict) and isinstance(items.get("edges"), list):
        return True
    return False


def _edges_from_item_list(items: list[Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for entry in items:
        if isinstance(entry, dict) and "node" in entry:
            edges.append(entry)
        else:
            edges.append({"node": entry})
    return edges


def module_layout_connection(module: Any) -> dict[str, Any]:
    """CMS LAYOUTS tile connection for this module.

    Prefer ``items`` (editorial / manually ordered pins) when GraphQL returns
    it. ``contents`` is the carousel connection and may be the backing
    collection (longer, ranked, or rotated) — do not treat that dump as the
    layout list when ``items`` is present.
    """
    if not isinstance(module, dict):
        return {}
    if editorial_items_present(module):
        items = module.get("items")
        if isinstance(items, list):
            edges = _edges_from_item_list(items)
            return {
                "totalCount": len(edges),
                "pageInfo": {
                    "hasNextPage": False,
                    "endCursor": None,
                    "itemCount": len(edges),
                },
                "edges": edges,
            }
        if isinstance(items, dict):
            return items
    contents = module.get("contents")
    return contents if isinstance(contents, dict) else {}


def module_display_limit(module: Any) -> int | None:
    """Explicit CMS/module display length, or None when GraphQL omitted it."""
    if not isinstance(module, dict):
        return None
    for key in _LAYOUT_LIMIT_KEYS:
        value = _positive_int(module.get(key))
        if value is not None:
            return value
    tracking = module.get("trackingMetadataJson")
    if isinstance(tracking, dict):
        for key in _LAYOUT_LIMIT_KEYS:
            value = _positive_int(tracking.get(key))
            if value is not None:
                return value
    return None


def module_layout_length(module: Any) -> int | None:
    """Layout-attached item count for this carousel module. Not a guessed cap.

    Prefer CMS ``items`` when present (editorial pin list). Then
    displayLimit/maxItems/visibleCount. Otherwise use contents.totalCount when
    it is smaller than collection.totalCount (the carousel list vs the backing
    catalog). Ranked/playlist/infinite connections still use contents.totalCount.

    Editorial UiVideoCarousel without items: the first layout page's
    contents.edges is the visible rail; contents.totalCount is the backing
    catalog (Exclusivo 163). Return the edge window, not totalCount, and do
    not invent a 30/119 cap.
    """
    if not isinstance(module, dict):
        return None
    if editorial_items_present(module):
        conn = module_layout_connection(module)
        total = contents_total_count(conn)
        if total is not None:
            return total
        return contents_edge_count(conn)
    contents = module.get("contents")
    contents_total = contents_total_count(contents) if isinstance(contents, dict) else None
    explicit = module_display_limit(module)
    if explicit is not None:
        if contents_total is not None:
            return min(explicit, contents_total)
        return explicit
    collection_total = collection_total_count(module)
    if (
        contents_total is not None
        and collection_total is not None
        and contents_total < collection_total
    ):
        return contents_total
    if module_walks_contents_catalog(module):
        return contents_total
    # Finite layout window: this page's edges, not catalog totalCount.
    if isinstance(contents, dict):
        n = contents_edge_count(contents)
        if n > 0:
            return n
    return contents_total


def contents_has_more(connection: Any) -> bool:
    """True when this contents connection still has another page.

    Honors pageInfo.hasNextPage + endCursor, but stops once this page's
    edges already cover totalCount (API sometimes leaves hasNextPage true).
    pageInfo.itemCount is this GraphQL page's size, not a number we pick.
    Do not treat PaginationParams' schema default first=10 as the rail length;
    callers must keep paging the same module until this is false or unique
    edges stabilize.
    """
    has_next, cursor = page_info(connection)
    if not (has_next and cursor):
        return False
    total = contents_total_count(connection)
    if total is not None and contents_edge_count(connection) >= total:
        return False
    return True


def module_contents_has_more(module: Any) -> bool:
    """True when the *layout* connection still has another page.

    Editorial ``items`` (list or connection) never continues into ``contents``
    — contentPagination.after would page the catalog, not the pin list.
    Ranked/playlist/infinite connections page contents until exhausted.
    contents.totalCount < collection.totalCount pages the carousel list.
    displayLimit still unfilled continues. Otherwise the first layout page's
    contents.edges is the visible window (do not walk catalog totalCount).
    """
    if not isinstance(module, dict):
        return False
    if editorial_items_present(module):
        return False
    conn = module_layout_connection(module)
    if not contents_has_more(conn):
        return False
    limit = module_layout_length(module)
    if limit is not None and contents_edge_count(conn) >= limit:
        return False
    if module_walks_contents_catalog(module):
        return True
    collection_total = collection_total_count(module)
    contents = module.get("contents")
    contents_total = contents_total_count(contents) if isinstance(contents, dict) else None
    if (
        contents_total is not None
        and collection_total is not None
        and contents_total < collection_total
    ):
        return True
    if module_display_limit(module) is not None:
        return True
    return False


def content_edge_keys(module: dict[str, Any]) -> list[str]:
    """Stable tile keys from the layout connection (mcp / video id / node id)."""
    contents = module_layout_connection(module)
    if not isinstance(contents, dict):
        return []
    edges = contents.get("edges")
    if not isinstance(edges, list):
        return []
    keys: list[str] = []
    for edge in edges:
        node = (edge or {}).get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict):
            continue
        video = node.get("video") if isinstance(node.get("video"), dict) else {}
        key = first_text(video.get("mcpId"), video.get("id"), node.get("id"))
        if key:
            keys.append(key)
    return keys


def tile_dedupe_key(*, mcp_id: str = "", content_id: str = "") -> str:
    """Identity used to drop overlapping cursor-pagination tiles, not series collapse."""
    return first_text(mcp_id, content_id)


def is_carousel_module(module: dict[str, Any]) -> bool:
    module_type = first_text(module.get("moduleType"))
    typename = module.get("__typename")
    return module_type in CAROUSEL_MODULE_TYPES or typename in CAROUSEL_TYPENAMES


def is_video_carousel_module(module: dict[str, Any]) -> bool:
    """Backward-compatible name: True for any module we extract as a content rail."""
    return is_content_module(module)


def is_content_module(module: dict[str, Any]) -> bool:
    """Prefer allow-all: keep modules with contents or inline CTA targets."""
    if not isinstance(module, dict) or not module:
        return False
    typename = first_text(module.get("__typename"))
    module_type = first_text(module.get("moduleType"))
    if typename in VIDEO_CAROUSEL_TYPENAMES or typename in INLINE_TYPENAMES:
        return True
    if module_type in CAROUSEL_MODULE_TYPES:
        return True
    if is_inline_module(module):
        return True
    contents = module.get("contents")
    if isinstance(contents, dict) and isinstance(contents.get("edges"), list):
        return True
    if editorial_items_present(module):
        return True
    # Unknown typename but still a carousel-shaped node.
    if typename in CAROUSEL_TYPENAMES:
        return True
    return False


def module_has_content_edges(module: dict[str, Any]) -> bool:
    conn = module_layout_connection(module)
    if not isinstance(conn, dict):
        return False
    edges = conn.get("edges")
    if isinstance(edges, list) and len(edges) > 0:
        return True
    # Personalized rails (CW) may advertise totalCount before edges are present.
    total = conn.get("totalCount")
    try:
        return int(total) > 0
    except (TypeError, ValueError):
        return False


def is_visible_web_row(module: dict[str, Any]) -> bool:
    """True iff the module is a numbered Row N matching CMS LAYOUTS.

    Every uiPage content module occupies a ``carousel_y`` slot even with 0 titles
    (personalized, geo, entitlements, empty collection / reco / CW / Mi lista /
    Porque viste / Recent Channels). Empty means an empty row, not an omitted one.
    INLINE_PAGE / INLINE_PROMO are chrome (Ver más / promo embeds), not numbered
    rows. Unknown non-content decoration is omitted.
    """
    if not isinstance(module, dict) or not module:
        return False
    if is_inline_module(module):
        return False
    return is_content_module(module)


# Backward-compatible alias.
is_visible_row_module = is_visible_web_row


def ui_modules_edges(payload: dict[str, Any]) -> list[Any]:
    page = (((payload.get("data") or {}).get("uiPage")) or {})
    if not page:
        return []
    modules = (((page.get("uiModules") or {}).get("edges")) or [])
    if not isinstance(modules, list):
        raise ExtractError("Response missing data.uiPage.uiModules.edges")
    return modules


def visible_row_number_for_raw_index(
    payload: dict[str, Any],
    raw_index_1based: int,
    *,
    y_base: int = 0,
) -> int:
    """Map a 1-based raw uiModules.edges index to absolute visible carousel_y."""
    y = y_base
    for index, edge in enumerate(ui_modules_edges(payload), start=1):
        module = (edge or {}).get("node") or {}
        if not is_visible_web_row(module):
            if index == raw_index_1based:
                return y
            continue
        y += 1
        if index == raw_index_1based:
            return y
    return y


def _module_id(module: dict[str, Any]) -> str:
    return first_text(module.get("id"), module.get("trackingId"))


def pending_content_cursors(payload: dict[str, Any]) -> dict[str, str]:
    """Return pending content cursors keyed by carousel title/id."""
    cursors: dict[str, str] = {}
    for edge in ui_modules_edges(payload):
        module = (edge or {}).get("node") or {}
        if not is_content_module(module) or is_inline_module(module):
            continue
        if module_contents_has_more(module):
            _, cursor = page_info(module_layout_connection(module))
            key = first_text(
                module.get("title"),
                module.get("textTitle"),
                module.get("id"),
                "(no row)",
            )
            cursors[key] = cursor
    return cursors


def pending_content_module_index(payload: dict[str, Any]) -> int | None:
    """1-based index within this payload's uiModules.edges of the first rail with more pages."""
    for index, edge in enumerate(ui_modules_edges(payload), start=1):
        module = (edge or {}).get("node") or {}
        if not is_content_module(module) or is_inline_module(module):
            continue
        if module_contents_has_more(module):
            return index
    return None


def pending_content_module_id(payload: dict[str, Any]) -> str:
    """Module id of the rail that still has a contents cursor, else empty."""
    idx = pending_content_module_index(payload)
    if idx is None:
        return ""
    edges = ui_modules_edges(payload)
    module = (edges[idx - 1] or {}).get("node") or {}
    return _module_id(module)


def module_index_by_id(payload: dict[str, Any], module_id: str) -> int | None:
    """1-based uiModules.edges index of the module with this id."""
    want = first_text(module_id)
    if not want:
        return None
    for index, edge in enumerate(ui_modules_edges(payload), start=1):
        module = (edge or {}).get("node") or {}
        if _module_id(module) == want:
            return index
    return None


def _humanize_channel_id(content_id: str) -> str:
    """Best-effort label when EpgChannel title fields are missing."""
    raw = first_text(content_id)
    if not raw:
        return ""
    # channel:mcp:callsign:LCDLF01_SVOD or …:channel-lcdlf01-svod
    tail = raw.split(":")[-1]
    tail = tail.split("/")[-1]
    if "channel-" in tail:
        tail = tail.split("channel-", 1)[-1]
    tail = tail.replace("_", " ").replace("-", " ").strip()
    return tail.title() if tail else ""


def _image_url(asset: Any) -> str:
    if not isinstance(asset, dict):
        return ""
    return first_text(asset.get("link"), asset.get("resizedLink"), asset.get("filePath"))


def _collect_image_assets(
    assets: Any,
    *,
    content_id: str,
    page_url_path: str,
    source: str,
) -> list[ExportedImage]:
    rows: list[ExportedImage] = []
    if isinstance(assets, dict):
        assets = [assets]
    if not isinstance(assets, list):
        return rows
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        url = _image_url(asset)
        if not url:
            continue
        rows.append(
            ExportedImage(
                content_id=content_id,
                page_url_path=page_url_path,
                image_role=first_text(asset.get("imageRole"), source),
                url=url,
                source=source,
                media_type=first_text(asset.get("mediaType")),
                file_path=first_text(asset.get("filePath")),
            )
        )
    return rows


def _format_ratings(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rating = first_text(item.get("ratingValue"))
        subs = item.get("ratingSubValues")
        if isinstance(subs, list) and subs:
            rating = f"{rating} ({', '.join(str(s) for s in subs)})" if rating else ", ".join(str(s) for s in subs)
        if rating:
            parts.append(rating)
    return "; ".join(parts)


def _format_cast(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = first_text(item.get("name"))
        roles = item.get("roles")
        if isinstance(roles, list) and roles:
            role_text = "/".join(str(r) for r in roles if r)
            parts.append(f"{name} ({role_text})" if name and role_text else name)
        elif name:
            parts.append(name)
    return "; ".join(parts)


def _video_type_fields(video: dict[str, Any]) -> dict[str, str]:
    data = video.get("videoTypeData") or {}
    if not isinstance(data, dict):
        return {
            "seasons_count": "",
            "episodes_count": "",
            "episode_number": "",
            "duration_seconds": "",
        }
    playback = data.get("playbackData") or {}
    stream = playback.get("streamMetadata") if isinstance(playback, dict) else {}
    duration = ""
    if isinstance(stream, dict) and stream.get("duration") is not None:
        duration = str(stream.get("duration"))
    return {
        "seasons_count": first_text(data.get("seasonsCount")),
        "episodes_count": first_text(data.get("episodesCount")),
        "episode_number": first_text(data.get("episodeNumber")),
        "duration_seconds": duration,
    }


def _channel_dict(node: dict[str, Any]) -> dict[str, Any]:
    for key in ("channel", "liveChannel", "heroTarget"):
        value = node.get(key)
        if isinstance(value, dict) and first_text(value.get("__typename")) in {
            "EpgChannel",
            "LiveChannel",
            "Channel",
            "",
        }:
            # heroTarget may also be VideoContent — only treat channel-like nodes here.
            if key == "heroTarget" and first_text(value.get("__typename")) not in {
                "EpgChannel",
                "LiveChannel",
                "Channel",
            }:
                continue
            return value
    return {}


def _series_from_video(video: dict[str, Any]) -> dict[str, Any]:
    """Pull parent series from episode-shaped videoTypeData when present."""
    data = video.get("videoTypeData")
    if not isinstance(data, dict):
        return {}
    series = data.get("series")
    return series if isinstance(series, dict) else {}


def _tracking_dict(node: dict[str, Any]) -> dict[str, Any]:
    tracking = node.get("clickTrackingJson")
    return tracking if isinstance(tracking, dict) else {}


def _tracking_title(node: dict[str, Any]) -> str:
    """Best-effort title from click-tracking when heroTarget fields are sparse."""
    tracking = _tracking_dict(node)
    return first_text(
        tracking.get("ui_content_title"),
        tracking.get("ui_object_title"),
        tracking.get("content_title"),
        tracking.get("title"),
        tracking.get("name"),
    )


def _tracking_content_id(node: dict[str, Any]) -> str:
    tracking = _tracking_dict(node)
    return first_text(
        tracking.get("ui_content_id"),
        tracking.get("content_id"),
        tracking.get("ui_object_id"),
    )


def _is_sports_event_node(node: dict[str, Any], hero: dict[str, Any] | None = None) -> bool:
    if first_text(node.get("heroTargetContentType")) == "SPORTS_EVENT":
        return True
    if first_text(node.get("__typename")) in {"SportsEvent", "UiSportsEventCard"}:
        return True
    target = hero if isinstance(hero, dict) else node.get("heroTarget")
    return isinstance(target, dict) and first_text(target.get("__typename")) == "SportsEvent"


def _sports_identity(node: dict[str, Any], hero: dict[str, Any] | None) -> tuple[str, str]:
    """Resolve a SportsEvent tile even when GraphQL returns only __typename."""
    target = hero if isinstance(hero, dict) else {}
    local = first_text(target.get("localTeamName"), node.get("localTeamName"))
    away = first_text(target.get("awayTeamName"), node.get("awayTeamName"))
    sports_title = f"{local} vs {away}" if local and away else local or away
    content_id = first_text(
        target.get("id"),
        target.get("sportsEventId"),
        node.get("sportsEventId"),
        node.get("id"),
    )
    title = first_text(
        target.get("title"),
        target.get("name"),
        sports_title,
        node.get("textTitle"),
        node.get("title"),
        node.get("name"),
        _tracking_title(node),
        _humanize_channel_id(content_id),
    )
    return title, content_id


def _item_identity(node: dict[str, Any]) -> tuple[str, str, dict[str, Any], str]:
    """Return (title, content_id, rich_video_or_empty, video_type_hint)."""
    if not isinstance(node, dict):
        return "", "", {}, ""

    video = node.get("video")
    if isinstance(video, dict) and first_text(video.get("title"), video.get("id")):
        # Continue Watching often resumes an episode; prefer series title/id for the rail.
        series = _series_from_video(video)
        series_title = first_text(series.get("title"))
        series_id = first_text(series.get("id"))
        if series_title or series_id:
            return (
                first_text(series_title, video.get("title"), node.get("title"), node.get("textTitle")),
                first_text(series_id, video.get("id"), node.get("id")),
                video,
                first_text(video.get("videoType"), "SERIES"),
            )
        return (
            first_text(video.get("title"), node.get("title"), node.get("textTitle")),
            first_text(video.get("id"), node.get("id")),
            video,
            first_text(video.get("videoType"), "VIDEO"),
        )

    # Progress / resume wrappers that nest series beside video.
    series = node.get("series")
    if isinstance(series, dict) and first_text(series.get("title"), series.get("id")):
        return (
            first_text(series.get("title"), node.get("title"), node.get("textTitle")),
            first_text(series.get("id"), node.get("id")),
            series if first_text(series.get("videoType")) else {},
            first_text(series.get("videoType"), "SERIES"),
        )

    hero = node.get("heroTarget")
    if _is_sports_event_node(node, hero if isinstance(hero, dict) else None):
        title, content_id = _sports_identity(node, hero if isinstance(hero, dict) else None)
        if content_id:
            return (
                title or _humanize_channel_id(content_id) or "Sports event",
                content_id,
                {},
                "SPORTS",
            )

    if isinstance(hero, dict):
        hero_type = first_text(hero.get("__typename"))
        if hero_type in {"VideoContent", ""} and first_text(hero.get("title"), hero.get("id")):
            return (
                first_text(hero.get("title"), node.get("textTitle"), node.get("title")),
                first_text(hero.get("id"), node.get("id")),
                hero if hero_type == "VideoContent" else {},
                first_text(hero.get("videoType"), "VIDEO"),
            )
        if hero_type == "EpgChannel" or (
            hero_type not in {"SportsEvent", "UiPage", "UiHeroPage", "UiHeroPromo", "UiHeroClips"}
            and first_text(hero.get("title"), hero.get("callSign"), hero.get("name"))
        ):
            content_id = first_text(hero.get("id"), node.get("channelId"), node.get("id"))
            title = first_text(
                hero.get("title"),
                hero.get("name"),
                hero.get("callSign"),
                node.get("textTitle"),
                _humanize_channel_id(content_id),
            )
            return (title, content_id, {}, "LIVE_CHANNEL")
        if hero_type in {"UiPage", "UiHeroPage", "UiHeroPromo", "UiHeroClips"}:
            return (
                first_text(
                    hero.get("pageName"),
                    hero.get("ctaText"),
                    node.get("textTitle"),
                    hero.get("ctaUrlPath"),
                    hero.get("urlPath"),
                ),
                first_text(hero.get("urlPath"), hero.get("ctaUrlPath"), node.get("id")),
                {},
                hero_type or "PAGE",
            )

    channel = _channel_dict(node)
    if channel:
        return (
            first_text(
                channel.get("title"),
                channel.get("name"),
                channel.get("callSign"),
                node.get("title"),
                node.get("textTitle"),
                node.get("name"),
            ),
            first_text(channel.get("id"), node.get("channelId"), node.get("id")),
            {},
            "LIVE_CHANNEL",
        )

    # Sports / page cards / generic labeled nodes
    local = first_text(node.get("localTeamName"))
    away = first_text(node.get("awayTeamName"))
    if local and away:
        sports_title = f"{local} vs {away}"
    else:
        sports_title = local or away
    title = first_text(
        node.get("title"),
        node.get("textTitle"),
        node.get("name"),
        sports_title,
        _tracking_title(node),
    )
    content_id = first_text(
        node.get("id"),
        node.get("channelId"),
        node.get("sportsEventId"),
        node.get("urlPath"),
        _tracking_content_id(node),
    )
    if title and content_id:
        hint = "SPORTS" if node.get("sportsEventId") else ("PAGE" if node.get("urlPath") else "ITEM")
        return title, content_id, {}, hint
    return "", "", {}, ""


def extract_page(
    payload: dict[str, Any],
    *,
    url_path: str,
    depth: int = 0,
    discovered_from: str = "",
    status: str = "ok",
) -> ExportedPage:
    page = ((payload.get("data") or {}).get("uiPage")) or {}
    if not isinstance(page, dict):
        page = {}
    meta = page.get("pageMetadata") if isinstance(page.get("pageMetadata"), dict) else {}
    og = meta.get("og") if isinstance(meta.get("og"), dict) else {}
    twitter = meta.get("twitter") if isinstance(meta.get("twitter"), dict) else {}
    og_images = og.get("image") if isinstance(og.get("image"), list) else []
    og_image_url = ""
    if og_images and isinstance(og_images[0], dict):
        og_image_url = first_text(og_images[0].get("url"))
    analytics = page.get("pageAnalyticsMetadata") if isinstance(page.get("pageAnalyticsMetadata"), dict) else {}
    modules = page.get("uiModules") if isinstance(page.get("uiModules"), dict) else {}
    return ExportedPage(
        url_path=first_text(page.get("urlPath"), url_path),
        page_name=first_text(page.get("pageName")),
        provider=first_text(page.get("provider")),
        title=first_text(meta.get("title")),
        description=first_text(meta.get("description")),
        canonical_url=first_text(meta.get("canonicalUrl")),
        alt_urls="; ".join(str(x) for x in (meta.get("altUrls") or []) if x),
        breadcrumbs_json=dumps_json(meta.get("breadcrumbs") or []),
        module_count=first_text(modules.get("totalCount")),
        status=status,
        depth=depth,
        discovered_from=discovered_from,
        og_image_url=og_image_url,
        twitter_image_url=first_text(twitter.get("image")),
        analytics_json=dumps_json(analytics.get("keyValues") or []),
    )


class TitleExtractor:
    """Stateless extractor — safe to reuse across pages and scrapers."""

    def extract(
        self,
        payload: dict[str, Any],
        start_position: int = 1,
        *,
        page_url_path: str = "",
        start_carousel_y: int = 0,
        module_y_base: int | None = None,
        content_x_offset: int = 0,
        continuing_content: bool = False,
        continuing_module_id: str = "",
        skip_keys: set[str] | None = None,
        collect_images: bool = True,
        auth_profile: str = "",
    ) -> list[ExportedTitle]:
        """Extract title placements with stable layout coordinates.

        ``carousel_y`` is the CMS LAYOUTS row number (1-based), offset by
        ``module_y_base`` (visible rows fully completed on prior GraphQL pages).
        Every uiPage content module consumes a slot even when empty; INLINE chrome
        and non-content decoration do not.

        When ``continuing_content`` is True, only the rail still being paginated is
        extracted (keeps ``module_y_base`` as ``carousel_y``; ``carousel_x`` continues
        from ``content_x_offset``). Sibling modules in the same payload are ignored so
        multi-module pages cannot shift row numbers during content pagination.
        Prefer ``continuing_module_id`` so the last content page (hasNextPage=false)
        does not fall back to the first sibling rail.

        ``start_carousel_y`` is accepted as a deprecated alias for ``module_y_base``.
        """
        rows: list[ExportedTitle] = []
        images: list[ExportedImage] = []
        page = ((payload.get("data") or {}).get("uiPage")) or {}
        if not page_url_path:
            page_url_path = first_text(page.get("urlPath")) if isinstance(page, dict) else ""

        y_base = start_carousel_y if module_y_base is None else module_y_base
        module_edges = ui_modules_edges(payload)
        self.last_modules_in_payload = len(module_edges)
        self.last_raw_module_count = len(module_edges)

        if continuing_content:
            target_index = module_index_by_id(payload, continuing_module_id)
            if target_index is None:
                target_index = pending_content_module_index(payload)
            if target_index is None:
                # Last content page often has hasNextPage=false.
                visible_indexes = [
                    i
                    for i, edge in enumerate(module_edges, start=1)
                    if is_visible_web_row((edge or {}).get("node") or {})
                ]
                if continuing_module_id:
                    # Pinned rail missing from payload — do not glue a sibling.
                    target_index = None
                elif visible_indexes:
                    target_index = visible_indexes[0]
            if target_index is None:
                self.last_images = images
                self.last_max_carousel_y = y_base
                self.last_visible_modules_in_payload = 0
                return rows
            edge = module_edges[target_index - 1]
            module = (edge or {}).get("node") or {}
            rows.extend(
                self._extract_module(
                    module,
                    page_url_path=page_url_path,
                    start_position=start_position,
                    row_number=y_base,
                    x_offset=content_x_offset,
                    already_emitted=content_x_offset,
                    skip_keys=skip_keys,
                    allow_empty=False,
                    collect_images=collect_images,
                    images=images,
                    auth_profile=auth_profile,
                )
            )
            self.last_images = images
            self.last_max_carousel_y = y_base
            self.last_visible_modules_in_payload = 1 if rows else 0
            return rows

        visible_y = y_base
        visible_in_payload = 0
        for module_edge in module_edges:
            module = (module_edge or {}).get("node") or {}
            if not is_visible_web_row(module):
                if collect_images:
                    images.extend(self._module_images(module, page_url_path=page_url_path))
                continue
            visible_y += 1
            visible_in_payload += 1
            rows.extend(
                self._extract_module(
                    module,
                    page_url_path=page_url_path,
                    start_position=start_position + len(rows),
                    row_number=visible_y,
                    x_offset=0,
                    collect_images=collect_images,
                    images=images,
                    auth_profile=auth_profile,
                )
            )

        self.last_images = images
        self.last_max_carousel_y = visible_y
        self.last_visible_modules_in_payload = visible_in_payload
        return rows

    def _extract_module(
        self,
        module: dict[str, Any],
        *,
        page_url_path: str,
        start_position: int,
        row_number: int,
        x_offset: int,
        collect_images: bool,
        images: list[ExportedImage],
        auth_profile: str,
        already_emitted: int = 0,
        skip_keys: set[str] | None = None,
        allow_empty: bool = True,
    ) -> list[ExportedTitle]:
        rows: list[ExportedTitle] = []
        if is_inline_module(module):
            inline_row = self._extract_inline(
                module,
                page_url_path=page_url_path,
                start_position=start_position,
                row_number=row_number,
                auth_profile=auth_profile,
            )
            if inline_row:
                rows.append(inline_row)
            elif collect_images:
                images.extend(self._module_images(module, page_url_path=page_url_path))
            return rows

        tracking = module.get("trackingMetadataJson")
        if not isinstance(tracking, dict):
            tracking = {}
        row_title = first_text(
            module.get("title"),
            module.get("textTitle"),
            tracking.get("ui_module_title"),
        )
        module_type = first_text(module.get("moduleType"))
        module_typename = first_text(module.get("__typename"))
        module_id = first_text(module.get("id"), module.get("trackingId"))
        tracking_json = dumps_json(module.get("trackingMetadataJson"))
        hero_flag = "true" if is_hero_module(module) else "false"
        connection = module_layout_connection(module)
        edges = connection.get("edges") if isinstance(connection, dict) else []
        if not isinstance(edges, list):
            edges = []
        total = contents_total_count(connection) if isinstance(connection, dict) else None
        layout_len = module_layout_length(module)
        cap: int | None = None
        # Emit every resolvable edge on this GraphQL page. pageInfo.itemCount is
        # this page's advertised size, not CMS LAYOUTS length — using it as a cap
        # dropped Radical at x=21 when itemCount stayed 10/15 while edges (or
        # totalCount) were longer. Further pages use contents after until the
        # connection ends. Never treat schema default first=10 as the rail.
        stop_at = layout_len if layout_len is not None else total
        if already_emitted == 0:
            cap = stop_at
        elif stop_at is not None:
            remaining = stop_at - max(0, already_emitted)
            if remaining <= 0 and already_emitted > 0:
                return rows
            if remaining > 0:
                cap = remaining

        if not edges:
            if not allow_empty:
                return rows
            rows.append(
                ExportedTitle(
                    position=start_position,
                    title="(empty)",
                    row_title=row_title or "(untitled)",
                    carousel_x=1,
                    carousel_y=row_number,
                    description="",
                    date_released="",
                    genres="",
                    content_id=f"empty:{module_id or row_number}",
                    video_type="EMPTY",
                    page_url_path=page_url_path,
                    module_type=module_type or module_typename,
                    module_typename=module_typename,
                    module_id=module_id,
                    is_hero=hero_flag,
                    auth_profile=auth_profile,
                    tracking_json=tracking_json,
                )
            )
            return rows

        # Keep every resolvable tile, including a leading SportsEvent hero.
        # Dropping that first edge shifts later titles up (Ninel Conde 2nd→1st).
        emitted_for_module = 0
        for _local_index, edge in enumerate(edges, start=1):
            node = (edge or {}).get("node") or {}
            if not isinstance(node, dict):
                # Not a visible tile — do not consume a horizontal slot.
                continue

            title, content_id, video, type_hint = _item_identity(node)
            if not title or not content_id:
                if collect_images:
                    images.extend(
                        self._node_images(
                            node,
                            content_id=first_text(node.get("id")),
                            page_url_path=page_url_path,
                        )
                    )
                # Unresolved identity is not a visible web slot — compact x.
                continue

            mcp_id = first_text(video.get("mcpId")) if video else ""
            key = tile_dedupe_key(mcp_id=mcp_id, content_id=content_id)
            if skip_keys and key and key in skip_keys:
                continue
            if cap is not None and emitted_for_module >= cap:
                break

            slot_x = max(1, x_offset + emitted_for_module + 1)

            if video:
                genre_names = list_names(video.get("genresV2")) or list_names(video.get("genres"))
                if collect_images:
                    asset_images = _collect_image_assets(
                        video.get("imageAssets"),
                        content_id=content_id,
                        page_url_path=page_url_path,
                        source="video.imageAssets",
                    )
                    asset_images.extend(
                        self._node_images(node, content_id=content_id, page_url_path=page_url_path)
                    )
                    images.extend(asset_images)
                type_fields = _video_type_fields(video)
                keywords = video.get("keywords") if isinstance(video.get("keywords"), list) else []
                badges = video.get("badges") if isinstance(video.get("badges"), list) else []
                rows.append(
                    ExportedTitle(
                        position=start_position + len(rows),
                        title=title,
                        row_title=row_title,
                        carousel_x=slot_x,
                        carousel_y=row_number,
                        description=first_text(video.get("description"), video.get("headline")),
                        date_released=first_text(video.get("dateReleased")),
                        genres=", ".join(genre_names),
                        content_id=content_id,
                        video_type=first_text(video.get("videoType"), type_hint),
                        page_url_path=page_url_path,
                        mcp_id=first_text(video.get("mcpId")),
                        headline=first_text(video.get("headline")),
                        year_released=first_text(video.get("yearReleased")),
                        language=first_text(video.get("language")),
                        is_kids=first_text(video.get("isKids")),
                        requires_subscription=first_text(video.get("requiresSubscription")),
                        content_vertical=first_text(video.get("contentVertical")),
                        keywords=", ".join(str(k) for k in keywords if k),
                        badges=", ".join(str(b) for b in badges if b),
                        ratings=_format_ratings(video.get("ratings")),
                        cast=_format_cast(video.get("contributors")),
                        seasons_count=type_fields["seasons_count"],
                        episodes_count=type_fields["episodes_count"],
                        episode_number=type_fields["episode_number"],
                        duration_seconds=type_fields["duration_seconds"],
                        module_type=module_type or module_typename,
                        module_typename=module_typename,
                        module_id=module_id,
                        is_hero=hero_flag,
                        auth_profile=auth_profile,
                        tracking_json=tracking_json,
                    )
                )
                emitted_for_module += 1
            else:
                if collect_images:
                    images.extend(
                        self._node_images(node, content_id=content_id, page_url_path=page_url_path)
                    )
                rows.append(
                    ExportedTitle(
                        position=start_position + len(rows),
                        title=title,
                        row_title=row_title,
                        carousel_x=slot_x,
                        carousel_y=row_number,
                        description=first_text(node.get("description"), node.get("ctaText")),
                        date_released="",
                        genres="",
                        content_id=content_id,
                        video_type=type_hint,
                        page_url_path=page_url_path,
                        mcp_id="",
                        headline="",
                        year_released="",
                        language="",
                        is_kids="",
                        requires_subscription="",
                        content_vertical="",
                        keywords="",
                        badges="",
                        ratings="",
                        cast="",
                        seasons_count="",
                        episodes_count="",
                        episode_number="",
                        duration_seconds="",
                        module_type=module_type or module_typename,
                        module_typename=module_typename,
                        module_id=module_id,
                        is_hero=hero_flag,
                        auth_profile=auth_profile,
                        tracking_json=tracking_json,
                    )
                )
                emitted_for_module += 1

        if emitted_for_module == 0:
            if not allow_empty:
                return rows
            rows.append(
                ExportedTitle(
                    position=start_position,
                    title="(empty)",
                    row_title=row_title or "(untitled)",
                    carousel_x=1,
                    carousel_y=row_number,
                    description="",
                    date_released="",
                    genres="",
                    content_id=f"empty:{module_id or row_number}",
                    video_type="EMPTY",
                    page_url_path=page_url_path,
                    module_type=module_type or module_typename,
                    module_typename=module_typename,
                    module_id=module_id,
                    is_hero=hero_flag,
                    auth_profile=auth_profile,
                    tracking_json=tracking_json,
                )
            )
        if collect_images:
            images.extend(self._module_images(module, page_url_path=page_url_path))
        return rows

    def _extract_inline(
        self,
        module: dict[str, Any],
        *,
        page_url_path: str,
        start_position: int,
        row_number: int,
        auth_profile: str,
    ) -> ExportedTitle | None:
        tracking = module.get("trackingMetadataJson")
        if not isinstance(tracking, dict):
            tracking = {}
        title = first_text(
            module.get("ctaText"),
            module.get("title"),
            module.get("textTitle"),
            tracking.get("ui_module_title"),
            module.get("ctaUrlPath"),
        )
        content_id = first_text(module.get("ctaUrlPath"), module.get("id"), module.get("trackingId"))
        if not title or not content_id:
            return None
        return ExportedTitle(
            position=start_position,
            title=title,
            row_title=first_text(
                module.get("title"),
                module.get("textTitle"),
                tracking.get("ui_module_title"),
                module.get("ctaText"),
                "Inline",
            ),
            carousel_x=1,
            carousel_y=row_number,
            description=first_text(module.get("ctaUrlPath")),
            date_released="",
            genres="",
            content_id=content_id,
            video_type="INLINE",
            page_url_path=page_url_path,
            module_type=first_text(module.get("moduleType"), module.get("__typename")),
            module_typename=first_text(module.get("__typename")),
            module_id=first_text(module.get("id"), module.get("trackingId")),
            is_hero="false",
            auth_profile=auth_profile,
            tracking_json=dumps_json(module.get("trackingMetadataJson")),
        )

    def extract_images(
        self,
        payload: dict[str, Any],
        *,
        page_url_path: str = "",
    ) -> list[ExportedImage]:
        self.extract(payload, page_url_path=page_url_path)
        page_images = list(getattr(self, "last_images", []))
        page = ((payload.get("data") or {}).get("uiPage")) or {}
        meta = page.get("pageMetadata") if isinstance(page.get("pageMetadata"), dict) else {}
        og = meta.get("og") if isinstance(meta.get("og"), dict) else {}
        for image in og.get("image") or []:
            if isinstance(image, dict) and image.get("url"):
                page_images.append(
                    ExportedImage(
                        content_id="",
                        page_url_path=page_url_path or first_text(page.get("urlPath")),
                        image_role="OG",
                        url=first_text(image.get("url")),
                        source="pageMetadata.og",
                    )
                )
        twitter = meta.get("twitter") if isinstance(meta.get("twitter"), dict) else {}
        if twitter.get("image"):
            page_images.append(
                ExportedImage(
                    content_id="",
                    page_url_path=page_url_path or first_text(page.get("urlPath")),
                    image_role="TWITTER",
                    url=first_text(twitter.get("image")),
                    source="pageMetadata.twitter",
                )
            )
        return page_images

    def _module_images(self, module: dict[str, Any], *, page_url_path: str) -> list[ExportedImage]:
        module_id = first_text(module.get("id"), module.get("trackingId"))
        images: list[ExportedImage] = []
        for key in (
            "portraitFillImage",
            "landscapeFillImage",
            "mobileFillImage",
            "ctvFillImage",
            "image",
            "cardImage",
            "logoImage",
        ):
            images.extend(
                _collect_image_assets(
                    module.get(key),
                    content_id=module_id,
                    page_url_path=page_url_path,
                    source=f"module.{key}",
                )
            )
        connection = module_layout_connection(module)
        edges = connection.get("edges") if isinstance(connection, dict) else []
        if isinstance(edges, list):
            for edge in edges:
                node = (edge or {}).get("node") or {}
                if isinstance(node, dict):
                    images.extend(
                        self._node_images(
                            node,
                            content_id=first_text(node.get("id"), node.get("urlPath"), module_id),
                            page_url_path=page_url_path,
                        )
                    )
                    if node.get("compositeImageLink"):
                        images.append(
                            ExportedImage(
                                content_id=first_text(node.get("id"), node.get("sportsEventId")),
                                page_url_path=page_url_path,
                                image_role="COMPOSITE",
                                url=first_text(node.get("compositeImageLink")),
                                source="sports.compositeImageLink",
                            )
                        )
        return images

    def _node_images(
        self,
        node: dict[str, Any],
        *,
        content_id: str,
        page_url_path: str,
    ) -> list[ExportedImage]:
        images: list[ExportedImage] = []
        for key in (
            "image",
            "heroImage",
            "logoImage",
            "cardImage",
            "portraitFillImage",
            "landscapeFillImage",
            "mobileFillImage",
            "ctvFillImage",
            "tournamentLogo",
            "tournamentCardBackground",
            "localTeamLogo",
            "awayTeamLogo",
        ):
            images.extend(
                _collect_image_assets(
                    node.get(key),
                    content_id=content_id,
                    page_url_path=page_url_path,
                    source=f"card.{key}",
                )
            )
        return images
