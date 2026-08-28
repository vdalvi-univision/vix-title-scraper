"""Data models and CSV field definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vix_scraper.errors import ConfigError


# Layout-comparison-friendly title export: rich metadata, no image/CDN URL clutter.
CSV_FIELDS = [
    "page_url_path",
    "position",
    "carousel_y",
    "carousel_x",
    "row_title",
    "row_size",
    "is_hero",
    "module_id",
    "module_type",
    "module_typename",
    "title",
    "id",
    "videoType",
    "description",
    "headline",
    "dateReleased",
    "year_released",
    "genres",
    "content_vertical",
    "language",
    "is_kids",
    "requires_subscription",
    "keywords",
    "badges",
    "ratings",
    "cast",
    "seasons_count",
    "episodes_count",
    "episode_number",
    "duration_seconds",
    "mcp_id",
    "auth_profile",
    "tracking_json",
]

PAGE_CSV_FIELDS = [
    "url_path",
    "page_name",
    "provider",
    "title",
    "description",
    "canonical_url",
    "alt_urls",
    "breadcrumbs_json",
    "module_count",
    "status",
    "depth",
    "discovered_from",
    "og_image_url",
    "twitter_image_url",
    "analytics_json",
]

IMAGE_CSV_FIELDS = [
    "content_id",
    "page_url_path",
    "image_role",
    "url",
    "local_path",
    "source",
    "media_type",
    "file_path",
]

CAROUSEL_MODULE_TYPES = frozenset(
    {
        "VIDEO_CAROUSEL",
        "HERO_CAROUSEL",
        "TRENDING_NOW_CAROUSEL",
        "RECOMMENDED_FOR_YOU_CAROUSEL",
        "MIXED_CONTENT_CAROUSEL",
        "MIXED_LIST_CAROUSEL",
        "BECAUSE_YOU_CAROUSEL",
        "PAGE_CAROUSEL",
        "LIVE_VIDEO_CAROUSEL",
        "SPORTS_EVENT_CAROUSEL",
        "CONTINUE_WATCHING_CAROUSEL",
        "WATCH_LIST_CAROUSEL",
        "RECENT_CHANNELS_CAROUSEL",
        "INLINE_PAGE",
        "INLINE_PROMO",
        "",
    }
)
CAROUSEL_TYPENAMES = frozenset(
    {
        "UiVideoCarousel",
        "UiHeroCarousel",
        "UiTrendingNowCarousel",
        "UiRecommendedForYouCarousel",
        "UiMixedContentCarousel",
        "UiBecauseYouCarousel",
        "UiPageCarousel",
        "UiLiveVideoCarousel",
        "UiSportsEventCarousel",
        "UiContinueWatchingCarousel",
        "UiWatchlistCarousel",
        "UiRecentChannelsCarousel",
        "UiInlinePage",
        "UiInlinePromo",
    }
)
# Historically video-only; extractor now prefers allow-all content modules.
VIDEO_CAROUSEL_TYPENAMES = frozenset(
    {
        "UiVideoCarousel",
        "UiHeroCarousel",
        "UiTrendingNowCarousel",
        "UiRecommendedForYouCarousel",
        "UiMixedContentCarousel",
        "UiBecauseYouCarousel",
        "UiContinueWatchingCarousel",
        "UiWatchlistCarousel",
        "UiLiveVideoCarousel",
        "UiSportsEventCarousel",
        "UiRecentChannelsCarousel",
        "UiPageCarousel",
    }
)
HERO_MODULE_TYPES = frozenset({"HERO_CAROUSEL"})
HERO_TYPENAMES = frozenset({"UiHeroCarousel"})
INLINE_TYPENAMES = frozenset({"UiInlinePage", "UiInlinePromo"})
INLINE_MODULE_TYPES = frozenset({"INLINE_PAGE", "INLINE_PROMO"})

DEFAULT_SEED_PATHS = (
    "/ondemandplus",
    "/ondemand",
    "/home",
    "/micro-dramas",
    "/deportes",
    "/canales",
    "/clips",
    "/noticias",
)

PRODUCTION_ENDPOINT = "https://client-api.vix.com/gql/v2"
STAGING_ENDPOINT = "https://client-api.stg.vix.tv/gql/v2"

# Named auth profiles → env var suffixes (default profile uses unsuffixed names).
AUTH_PROFILE_ENV = {
    "default": ("AUTH_TOKEN", "X_VIX_USER_TOKEN"),
    "wc": ("AUTH_TOKEN_WC", "X_VIX_USER_TOKEN_WC"),
}


@dataclass(slots=True)
class ExportedTitle:
    position: int  # 1-based global index on the page (not the slot in the row)
    title: str
    row_title: str
    carousel_x: int  # 1-based index within the row (first poster = 1)
    carousel_y: int  # 1-based visible web row number
    description: str
    date_released: str
    genres: str
    content_id: str
    video_type: str
    page_url_path: str = ""
    mcp_id: str = ""
    headline: str = ""
    year_released: str = ""
    language: str = ""
    is_kids: str = ""
    requires_subscription: str = ""
    content_vertical: str = ""
    keywords: str = ""
    badges: str = ""
    ratings: str = ""
    cast: str = ""
    seasons_count: str = ""
    episodes_count: str = ""
    episode_number: str = ""
    duration_seconds: str = ""
    module_type: str = ""
    module_typename: str = ""
    module_id: str = ""
    is_hero: str = ""
    row_size: int = 0
    auth_profile: str = ""
    tracking_json: str = ""

    def as_csv_row(self) -> dict[str, Any]:
        return {
            "page_url_path": self.page_url_path,
            "position": self.position,
            "carousel_y": self.carousel_y,
            "carousel_x": self.carousel_x,
            "row_title": self.row_title,
            "row_size": self.row_size,
            "is_hero": self.is_hero,
            "module_id": self.module_id,
            "module_type": self.module_type,
            "module_typename": self.module_typename,
            "title": self.title,
            "id": self.content_id,
            "videoType": self.video_type,
            "description": self.description,
            "headline": self.headline,
            "dateReleased": self.date_released,
            "year_released": self.year_released,
            "genres": self.genres,
            "content_vertical": self.content_vertical,
            "language": self.language,
            "is_kids": self.is_kids,
            "requires_subscription": self.requires_subscription,
            "keywords": self.keywords,
            "badges": self.badges,
            "ratings": self.ratings,
            "cast": self.cast,
            "seasons_count": self.seasons_count,
            "episodes_count": self.episodes_count,
            "episode_number": self.episode_number,
            "duration_seconds": self.duration_seconds,
            "mcp_id": self.mcp_id,
            "auth_profile": self.auth_profile,
            "tracking_json": self.tracking_json,
        }


@dataclass(slots=True)
class ExportedPage:
    url_path: str
    page_name: str = ""
    provider: str = ""
    title: str = ""
    description: str = ""
    canonical_url: str = ""
    alt_urls: str = ""
    breadcrumbs_json: str = ""
    module_count: str = ""
    status: str = "ok"
    depth: int = 0
    discovered_from: str = ""
    og_image_url: str = ""
    twitter_image_url: str = ""
    analytics_json: str = ""

    def as_csv_row(self) -> dict[str, Any]:
        return {
            "url_path": self.url_path,
            "page_name": self.page_name,
            "provider": self.provider,
            "title": self.title,
            "description": self.description,
            "canonical_url": self.canonical_url,
            "alt_urls": self.alt_urls,
            "breadcrumbs_json": self.breadcrumbs_json,
            "module_count": self.module_count,
            "status": self.status,
            "depth": self.depth,
            "discovered_from": self.discovered_from,
            "og_image_url": self.og_image_url,
            "twitter_image_url": self.twitter_image_url,
            "analytics_json": self.analytics_json,
        }


@dataclass(slots=True)
class ExportedImage:
    content_id: str
    page_url_path: str
    image_role: str
    url: str
    local_path: str = ""
    source: str = ""
    media_type: str = ""
    file_path: str = ""

    def as_csv_row(self) -> dict[str, Any]:
        return {
            "content_id": self.content_id,
            "page_url_path": self.page_url_path,
            "image_role": self.image_role,
            "url": self.url,
            "local_path": self.local_path,
            "source": self.source,
            "media_type": self.media_type,
            "file_path": self.file_path,
        }


@dataclass
class ScrapeConfig:
    """Runtime settings for live or offline scrapes."""

    url_path: str = ""
    output: Path = Path("titles.csv")
    endpoint: str | None = None
    query: str | None = None
    query_file: Path | None = None
    navigation_query_file: Path | None = None
    input_json: Path | None = None
    auth_token: str | None = None
    x_vix_user_token: str | None = None
    installation_id: str | None = None
    auth_profile: str = "default"
    app_version: str = "5.0.0"
    device_type: str = "desktop"
    platform: str = "web"
    device_id: str | None = None
    device_label: str | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    # Product catalog is Mexico. Omitting these silently follows JWT/IP (often US).
    country: str = "MX"
    accept_language: str = "es-MX,es;q=0.9"
    # Catalog crawl page size (explore/page). Layout/batch does not use this
    # as contents `first` — that would be a guessed cap (do not send 15/30/100).
    page_size: int = 50
    module_page_size: int = 2
    # True: follow contents pageInfo.endCursor until the connection ends or
    # unique edges stabilize. Layout/batch sets this True so rails are not
    # truncated to PaginationParams' schema default first=10.
    paginate_contents: bool = True
    # True (layout/batch): size contents `first` from remaining contents.totalCount
    # for ranked/playlist/infinite rails. Editorial rails use `items` or the first
    # layout page window — not catalog totalCount. Clamp only to contents_page_max.
    # Never omit PaginationParams (schema default 10) and never send first:100 as
    # a guessed catalog dump.
    contents_first_from_total: bool = False
    # Transport ceiling for contents `first` (lean layout query vs 100k budget).
    # Playlist/ranked product length is the contents connection until it ends.
    # Editorial length is items / first-page edges, not this number.
    contents_page_max: int = 80
    timeout: int = 30
    retries: int = 3
    deduplicate: bool = False
    debug: bool = False
    min_count: int = 3
    hours: int = 2
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Explorer settings
    max_pages: int = 500
    max_depth: int = 6
    download_images: bool = True
    download_all_image_roles: bool = False
    resume: bool = True
    seed_paths: list[str] = field(default_factory=list)
    output_dir: Path = Path("output/explore")
    state_file: Path | None = None
    allow_external_paths: bool = False
    # Batch / layout-compare
    url_paths: list[str] = field(default_factory=list)
    auth_profile_map: dict[str, str] = field(default_factory=dict)

    def resolve_query(self) -> str:
        if self.query and self.query.strip():
            return self.query.strip()
        if self.query_file is not None:
            try:
                return self.query_file.read_text(encoding="utf-8")
            except OSError as exc:
                raise ConfigError(f"Could not read query file: {exc}") from exc
        raise ConfigError(
            "Missing GraphQL query. Pass query_file / --query-file or VIX_GRAPHQL_QUERY."
        )

    def resolve_navigation_query(self) -> str:
        path = self.navigation_query_file or Path("queries/navigation.graphql")
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read navigation query file: {exc}") from exc
