"""Site-wide GraphQL explorer: discover pages, extract titles/images, resume crawls."""

from __future__ import annotations

import csv
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque

from vix_scraper.client import GraphQLClient, graphql_error_messages
from vix_scraper.discovery import (
    extract_paths_from_navigation,
    extract_paths_from_page_payload,
    navigation_rows,
    normalize_path,
)
from vix_scraper.errors import GraphQLError, ScraperError
from vix_scraper.exporter import CsvExporter, deduplicate
from vix_scraper.extractor import (
    TitleExtractor,
    enrich_layout_fields,
    extract_page,
    pending_content_cursors,
    pending_content_module_id,
    pending_content_module_index,
    tile_dedupe_key,
    visible_row_number_for_raw_index,
)
from vix_scraper.images import PRIMARY_IMAGE_ROLES, ImageDownloader
from vix_scraper.models import (
    DEFAULT_SEED_PATHS,
    PRODUCTION_ENDPOINT,
    STAGING_ENDPOINT,
    ExportedImage,
    ExportedPage,
    ExportedTitle,
    ScrapeConfig,
)
from vix_scraper.pagination import PagePaginator
from vix_scraper.state import ExploreState, StateStore


@dataclass(slots=True)
class ExploreResult:
    output_dir: Path
    pages_visited: int
    titles: int
    images_downloaded: int
    images_failed: int
    endpoint: str
    auth_note: str = ""


class SiteExplorer:
    """BFS crawl over uiPage urlPaths discovered via GraphQL."""

    def __init__(self, config: ScrapeConfig) -> None:
        self.config = config
        self.extractor = TitleExtractor()
        self.output_dir = Path(config.output_dir)
        self.state_path = config.state_file or (self.output_dir / "explore_state.json")
        self._client: GraphQLClient | None = None
        self.auth_note = ""

    def _get_client(self) -> GraphQLClient:
        if self._client is None:
            self._client = GraphQLClient(self.config)
        return self._client

    def _probe_endpoint(self, endpoint: str) -> tuple[bool, str]:
        cfg = ScrapeConfig(
            url_path="/ondemandplus",
            endpoint=endpoint,
            auth_token=self.config.auth_token,
            x_vix_user_token=self.config.x_vix_user_token,
            app_version=self.config.app_version,
            device_type=self.config.device_type,
            platform=self.config.platform,
            user_agent=self.config.user_agent,
            timeout=min(self.config.timeout, 30),
            retries=1,
            extra_headers=dict(self.config.extra_headers),
        )
        client = GraphQLClient(cfg)
        try:
            payload = client.execute(
                "query($urlPath: ID!) { uiPage(urlPath: $urlPath) { urlPath pageName } }",
                {"urlPath": "/ondemandplus"},
                allow_errors=True,
            )
        except GraphQLError as exc:
            return False, str(exc)
        errors = graphql_error_messages(payload)
        page = ((payload.get("data") or {}).get("uiPage"))
        if page:
            return True, ""
        joined = "; ".join(errors) if errors else "uiPage returned null"
        authish = any(
            token in joined.lower()
            for token in ("unauth", "forbidden", "401", "403", "jwt", "token", "expired", "issuer")
        )
        return False, ("auth/access: " if authish else "") + joined

    def resolve_endpoint(self) -> str:
        preferred = (self.config.endpoint or PRODUCTION_ENDPOINT).rstrip("/")
        candidates = [preferred]
        if preferred != STAGING_ENDPOINT.rstrip("/"):
            candidates.append(STAGING_ENDPOINT)
        if preferred != PRODUCTION_ENDPOINT.rstrip("/") and PRODUCTION_ENDPOINT not in candidates:
            candidates.append(PRODUCTION_ENDPOINT)

        notes: list[str] = []
        for endpoint in candidates:
            ok, detail = self._probe_endpoint(endpoint)
            if ok:
                if endpoint != preferred:
                    self.auth_note = (
                        f"Preferred endpoint failed ({preferred}: {notes[-1] if notes else 'error'}). "
                        f"Fell back to {endpoint}."
                    )
                else:
                    self.auth_note = f"Using endpoint {endpoint}."
                self.config.endpoint = endpoint
                self._client = None
                return endpoint
            notes.append(f"{endpoint}: {detail}")
        raise GraphQLError("All GraphQL endpoints failed auth/access checks: " + " | ".join(notes))

    def bootstrap_seeds(self) -> list[tuple[str, int, str]]:
        seeds: list[tuple[str, int, str]] = []
        seen: set[str] = set()

        def add(path: str | None, source: str) -> None:
            normalized = normalize_path(path)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            seeds.append((normalized, 0, source))

        for path in self.config.seed_paths or []:
            add(path, "seed")
        if self.config.url_path:
            add(self.config.url_path, "start")

        client = self._get_client()
        nav_payload = client.execute(self.config.resolve_navigation_query(), allow_errors=True)
        for path in sorted(extract_paths_from_navigation(nav_payload)):
            add(path, "navigation")
        # Persist navigation snapshot for debugging/reuse.
        nav_path = self.output_dir / "navigation.csv"
        CsvExporter(nav_path).write_dicts(
            navigation_rows(nav_payload),
            [
                "menu_type",
                "parent",
                "text",
                "url_path",
                "action",
                "item_type",
                "icon_name",
                "icon_link",
            ],
        )

        for path in DEFAULT_SEED_PATHS:
            add(path, "default")
        return seeds

    def explore(self) -> ExploreResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        endpoint = self.resolve_endpoint()
        store = StateStore(self.state_path)
        state = store.load() if self.config.resume else None

        queue: Deque[tuple[str, int, str]] = deque()
        visited: set[str] = set()
        failed: set[str] = set()

        if state and state.endpoint == endpoint and state.queue:
            for item in state.queue:
                if len(item) >= 1:
                    path = normalize_path(item[0])
                    if not path:
                        continue
                    depth = int(item[1]) if len(item) > 1 else 0
                    source = str(item[2]) if len(item) > 2 else "resume"
                    queue.append((path, depth, source))
            visited.update(p for p in state.visited if normalize_path(p))
            failed.update(state.failed or [])
            if self.config.debug:
                print(f"debug resumed queue={len(queue)} visited={len(visited)}", file=sys.stderr)
        else:
            for item in self.bootstrap_seeds():
                queue.append(item)

        pages: list[ExportedPage] = []
        titles: list[ExportedTitle] = []
        images: list[ExportedImage] = []
        if state and state.endpoint == endpoint:
            pages, titles, images = self._load_partial_exports()
        query = self.config.resolve_query()
        downloader = ImageDownloader(
            self.output_dir / "images",
            timeout=min(self.config.timeout, 20),
            user_agent=self.config.user_agent,
            enabled=self.config.download_images,
            download_roles=None if self.config.download_all_image_roles else PRIMARY_IMAGE_ROLES,
        )
        # Avoid re-downloading URLs already persisted in images.csv / files.
        for image in images:
            if image.url:
                downloader._seen_urls.add(image.url)

        pages_visited = len(visited) if state and state.endpoint == endpoint else 0
        target_pages = self.config.max_pages
        while queue and pages_visited < target_pages:
            url_path, depth, discovered_from = queue.popleft()
            if url_path in visited:
                continue
            if depth > self.config.max_depth:
                continue
            visited.add(url_path)
            pages_visited += 1

            if self.config.debug:
                print(
                    f"debug explore page={pages_visited}/{target_pages} path={url_path} "
                    f"depth={depth} queue={len(queue)} titles={len(titles)} "
                    f"imgs_dl={downloader.downloaded}",
                    file=sys.stderr,
                )

            self.config.url_path = url_path
            page_row: ExportedPage
            try:
                page_titles, page_images, discovered = self._scrape_page(query)
                page_payload_ok = True
                status = "ok"
            except ScraperError as exc:
                page_titles, page_images, discovered = [], [], set()
                page_payload_ok = False
                status = f"error: {exc}"
                failed.add(url_path)
                page_row = ExportedPage(
                    url_path=url_path,
                    status=status,
                    depth=depth,
                    discovered_from=discovered_from,
                )
                pages.append(page_row)
                self._flush_exports(pages, titles, images)
                self._persist(store, endpoint, queue, visited, failed, pages_visited, titles, images)
                continue

            page_row = getattr(self, "_last_page_row", None) or ExportedPage(
                url_path=url_path,
                status=status,
                depth=depth,
                discovered_from=discovered_from,
            )
            page_row.depth = depth
            page_row.discovered_from = discovered_from
            page_row.status = status if page_payload_ok else page_row.status
            pages.append(page_row)

            for title in page_titles:
                title.position = len(titles) + 1
                titles.append(title)
            images.extend(downloader.process(page_images))

            if depth < self.config.max_depth:
                for path in sorted(discovered):
                    if path not in visited and all(path != q[0] for q in queue):
                        queue.append((path, depth + 1, url_path))

            self._flush_exports(pages, titles, images)
            self._persist(store, endpoint, queue, visited, failed, pages_visited, titles, images)

        if self.config.deduplicate:
            titles = deduplicate(titles)
            self._flush_exports(pages, titles, images)

        self._persist(store, endpoint, queue, visited, failed, pages_visited, titles, images, final=True)

        return ExploreResult(
            output_dir=self.output_dir,
            pages_visited=pages_visited,
            titles=len(titles),
            images_downloaded=downloader.downloaded,
            images_failed=downloader.failed,
            endpoint=endpoint,
            auth_note=self.auth_note,
        )

    def _scrape_page(self, query: str) -> tuple[list[ExportedTitle], list[ExportedImage], set[str]]:
        client = self._get_client()
        paginator = PagePaginator(client, self.config, query)
        titles: list[ExportedTitle] = []
        images: list[ExportedImage] = []
        discovered: set[str] = set()
        position = 1
        visible_rows_completed = 0
        continuing_y = 0
        continuing_module_id = ""
        content_x_offset = 0
        skip_keys: set[str] = set()
        first_payload = None
        collect_images = bool(self.config.download_images)

        for _page_number, cursor, payload in paginator.iter_pages_allow_missing():
            if first_payload is None:
                first_payload = payload
            errors = graphql_error_messages(payload)
            page = ((payload.get("data") or {}).get("uiPage"))
            if page is None:
                message = "; ".join(errors) if errors else "uiPage not found"
                self._last_page_row = ExportedPage(
                    url_path=self.config.url_path,
                    status=f"missing: {message}",
                )
                return [], [], set()

            continuing = bool(cursor.content_after)
            if continuing:
                page_rows = self.extractor.extract(
                    payload,
                    start_position=position,
                    page_url_path=self.config.url_path,
                    module_y_base=continuing_y,
                    content_x_offset=content_x_offset,
                    continuing_content=True,
                    continuing_module_id=cursor.content_module_id or continuing_module_id,
                    skip_keys=skip_keys,
                    collect_images=collect_images,
                    auth_profile=self.config.auth_profile,
                )
            else:
                page_rows = self.extractor.extract(
                    payload,
                    start_position=position,
                    page_url_path=self.config.url_path,
                    module_y_base=visible_rows_completed,
                    content_x_offset=0,
                    continuing_content=False,
                    collect_images=collect_images,
                    auth_profile=self.config.auth_profile,
                )
            titles.extend(page_rows)
            position += len(page_rows)
            content_pending = pending_content_cursors(payload)
            if continuing:
                for row in page_rows:
                    if row.video_type == "EMPTY":
                        continue
                    key = tile_dedupe_key(mcp_id=row.mcp_id, content_id=row.content_id)
                    if key:
                        skip_keys.add(key)
                content_x_offset += sum(1 for r in page_rows if r.video_type != "EMPTY")
                if not content_pending:
                    content_x_offset = 0
                    continuing_y = 0
                    continuing_module_id = ""
                    skip_keys = set()
            else:
                pending_idx = pending_content_module_index(payload)
                if pending_idx is not None and content_pending:
                    continuing_y = visible_row_number_for_raw_index(
                        payload,
                        pending_idx,
                        y_base=visible_rows_completed,
                    )
                    continuing_module_id = pending_content_module_id(payload)
                    row_items = [
                        r
                        for r in page_rows
                        if r.carousel_y == continuing_y and r.video_type != "EMPTY"
                    ]
                    content_x_offset = len(row_items)
                    skip_keys = {
                        key
                        for r in row_items
                        if (key := tile_dedupe_key(mcp_id=r.mcp_id, content_id=r.content_id))
                    }
                    visible_rows_completed = self.extractor.last_max_carousel_y
                else:
                    visible_rows_completed = self.extractor.last_max_carousel_y
                    content_x_offset = 0
                    continuing_y = 0
                    continuing_module_id = ""
                    skip_keys = set()
            if collect_images:
                images.extend(self.extractor.extract_images(payload, page_url_path=self.config.url_path))
            discovered |= extract_paths_from_page_payload(payload)

        enrich_layout_fields(titles, auth_profile=self.config.auth_profile)
        for idx, row in enumerate(titles, start=1):
            row.position = idx

        if first_payload is not None:
            self._last_page_row = extract_page(
                first_payload,
                url_path=self.config.url_path,
            )
        else:
            self._last_page_row = ExportedPage(url_path=self.config.url_path, status="empty")
        return titles, images, discovered

    def _flush_exports(
        self,
        pages: list[ExportedPage],
        titles: list[ExportedTitle],
        images: list[ExportedImage],
    ) -> None:
        CsvExporter(self.output_dir / "titles.csv").write(titles)
        CsvExporter(self.output_dir / "pages.csv").write_pages(pages)
        CsvExporter(self.output_dir / "images.csv").write_images(images)

    def _load_partial_exports(
        self,
    ) -> tuple[list[ExportedPage], list[ExportedTitle], list[ExportedImage]]:
        pages: list[ExportedPage] = []
        titles: list[ExportedTitle] = []
        images: list[ExportedImage] = []
        pages_path = self.output_dir / "pages.csv"
        titles_path = self.output_dir / "titles.csv"
        images_path = self.output_dir / "images.csv"
        if pages_path.is_file():
            with pages_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    pages.append(
                        ExportedPage(
                            url_path=row.get("url_path", ""),
                            page_name=row.get("page_name", ""),
                            provider=row.get("provider", ""),
                            title=row.get("title", ""),
                            description=row.get("description", ""),
                            canonical_url=row.get("canonical_url", ""),
                            alt_urls=row.get("alt_urls", ""),
                            breadcrumbs_json=row.get("breadcrumbs_json", ""),
                            module_count=row.get("module_count", ""),
                            status=row.get("status", ""),
                            depth=int(row.get("depth") or 0),
                            discovered_from=row.get("discovered_from", ""),
                            og_image_url=row.get("og_image_url", ""),
                            twitter_image_url=row.get("twitter_image_url", ""),
                            analytics_json=row.get("analytics_json", ""),
                        )
                    )
        if titles_path.is_file():
            with titles_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    titles.append(
                        ExportedTitle(
                            position=int(row.get("position") or 0),
                            title=row.get("title", ""),
                            row_title=row.get("row_title", ""),
                            carousel_x=int(row.get("carousel_x") or 0),
                            carousel_y=int(row.get("carousel_y") or 0),
                            description=row.get("description", ""),
                            date_released=row.get("dateReleased", ""),
                            genres=row.get("genres", ""),
                            content_id=row.get("id", ""),
                            video_type=row.get("videoType", ""),
                            page_url_path=row.get("page_url_path", ""),
                            mcp_id=row.get("mcp_id", ""),
                            headline=row.get("headline", ""),
                            year_released=row.get("year_released", ""),
                            language=row.get("language", ""),
                            is_kids=row.get("is_kids", ""),
                            requires_subscription=row.get("requires_subscription", ""),
                            content_vertical=row.get("content_vertical", ""),
                            keywords=row.get("keywords", ""),
                            badges=row.get("badges", ""),
                            ratings=row.get("ratings", ""),
                            cast=row.get("cast", ""),
                            seasons_count=row.get("seasons_count", ""),
                            episodes_count=row.get("episodes_count", ""),
                            episode_number=row.get("episode_number", ""),
                            duration_seconds=row.get("duration_seconds", ""),
                            module_type=row.get("module_type", ""),
                            module_typename=row.get("module_typename", ""),
                            module_id=row.get("module_id", ""),
                            is_hero=row.get("is_hero", ""),
                            row_size=int(row.get("row_size") or 0),
                            auth_profile=row.get("auth_profile", ""),
                            tracking_json=row.get("tracking_json", ""),
                        )
                    )
        if images_path.is_file():
            with images_path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    images.append(
                        ExportedImage(
                            content_id=row.get("content_id", ""),
                            page_url_path=row.get("page_url_path", ""),
                            image_role=row.get("image_role", ""),
                            url=row.get("url", ""),
                            local_path=row.get("local_path", ""),
                            source=row.get("source", ""),
                            media_type=row.get("media_type", ""),
                            file_path=row.get("file_path", ""),
                        )
                    )
        return pages, titles, images

    def _persist(
        self,
        store: StateStore,
        endpoint: str,
        queue: Deque[tuple[str, int, str]],
        visited: set[str],
        failed: set[str],
        pages_visited: int,
        titles: list[ExportedTitle],
        images: list[ExportedImage],
        *,
        final: bool = False,
    ) -> None:
        state = ExploreState(
            endpoint=endpoint,
            queue=[[p, d, s] for p, d, s in queue],
            visited=sorted(visited),
            failed=sorted(failed),
            stats={
                "pages_visited": pages_visited,
                "titles": len(titles),
                "images": len(images),
                "queue": len(queue),
                "final": 1 if final else 0,
            },
        )
        store.save(state)


def run_explore(config: ScrapeConfig) -> ExploreResult:
    return SiteExplorer(config).explore()
