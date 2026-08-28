"""High-level reusable scraper orchestrating client, pagination, and extraction."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterator

from vix_scraper.auth import apply_auth_profile, missing_profile_help, resolve_auth_profile, warn_jwt_catalog_country
from vix_scraper.client import GraphQLClient
from vix_scraper.errors import ExtractError, ScraperError
from vix_scraper.exporter import CsvExporter, deduplicate
from vix_scraper.extractor import (
    TitleExtractor,
    enrich_layout_fields,
    pending_content_cursors,
    pending_content_module_id,
    pending_content_module_index,
    tile_dedupe_key,
    visible_row_number_for_raw_index,
)
from vix_scraper.models import PRODUCTION_ENDPOINT, ExportedTitle, ScrapeConfig
from vix_scraper.pagination import PagePaginator

ProgressCallback = Callable[[dict], None]


class TitleScraper:
    """Scalable entry point: scrape one or many urlPaths into title rows."""

    def __init__(
        self,
        config: ScrapeConfig,
        *,
        client: GraphQLClient | None = None,
        extractor: TitleExtractor | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        self.config = config
        self.extractor = extractor or TitleExtractor()
        self._client = client
        self.progress_callback = progress_callback

    def _get_client(self) -> GraphQLClient:
        if self._client is None:
            self._client = GraphQLClient(self.config)
        return self._client

    def reset_client(self) -> None:
        """Drop cached client so auth/endpoint changes take effect."""
        self._client = None

    def load_json(self, path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExtractError(f"Could not read JSON {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ExtractError("JSON response must be an object")
        return payload

    def iter_titles(self) -> Iterator[ExportedTitle]:
        """Yield titles with stable carousel coordinates across module/content cursors."""
        position = 1
        collect_images = bool(self.config.download_images)
        if self.config.input_json is not None:
            payload = self.load_json(self.config.input_json)
            rows = self.extractor.extract(
                payload,
                start_position=position,
                page_url_path=self.config.url_path,
                collect_images=collect_images,
                auth_profile=self.config.auth_profile,
            )
            yield from rows
            return

        query = self.config.resolve_query()
        warn_jwt_catalog_country(self.config.auth_token, getattr(self.config, "country", None))
        paginator = PagePaginator(self._get_client(), self.config, query)
        # Absolute count of *visible* rails already numbered (for carousel_y).
        visible_rows_completed = 0
        # Absolute carousel_y of the rail currently content-paginating (0 = none).
        continuing_y = 0
        continuing_module_id = ""
        content_x_offset = 0
        skip_keys: set[str] = set()

        for _page_number, cursor, payload in paginator.iter_pages():
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

            for row in page_rows:
                yield row
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
                if self.progress_callback:
                    self.progress_callback(
                        {
                            "modules_done": visible_rows_completed,
                            "modules_total": paginator.last_modules_total_count or 0,
                            "items_done": position - 1,
                            "url_path": self.config.url_path,
                        }
                    )
                continue

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
                # All visible rails on this GraphQL page already numbered.
                visible_rows_completed = self.extractor.last_max_carousel_y
            else:
                visible_rows_completed = self.extractor.last_max_carousel_y
                content_x_offset = 0
                continuing_y = 0
                continuing_module_id = ""
                skip_keys = set()

            if self.progress_callback:
                self.progress_callback(
                    {
                        "modules_done": visible_rows_completed,
                        "modules_total": paginator.last_modules_total_count or 0,
                        "items_done": position - 1,
                        "url_path": self.config.url_path,
                    }
                )

    def scrape(self) -> list[ExportedTitle]:
        rows = list(self.iter_titles())
        enrich_layout_fields(rows, auth_profile=self.config.auth_profile)
        for idx, row in enumerate(rows, start=1):
            row.position = idx
        if self.config.deduplicate:
            rows = deduplicate(rows)
        return rows

    def scrape_to_csv(self, output: Path | str | None = None) -> tuple[Path, int]:
        path = Path(output) if output else self.config.output
        rows = self.scrape()
        count = CsvExporter(path).write(rows)
        return path, count

    def scrape_paths(
        self,
        url_paths: list[str],
        *,
        auth_profile_map: dict[str, str] | None = None,
        default_profile: str | None = None,
    ) -> dict[str, list[ExportedTitle]]:
        """Reuse one scraper across many pages; swap auth profile per path when mapped."""
        results: dict[str, list[ExportedTitle]] = {}
        original = self.config.url_path
        original_profile = self.config.auth_profile
        original_token = self.config.auth_token
        original_user = self.config.x_vix_user_token
        profile_map = auth_profile_map or self.config.auth_profile_map or {}
        try:
            for path in url_paths:
                profile = profile_map.get(path) or default_profile or self.config.auth_profile or "default"
                creds = resolve_auth_profile(profile)
                if not creds.has_auth_token:
                    raise ScraperError(missing_profile_help(profile) + f" (needed for {path})")
                apply_auth_profile(self.config, profile)
                self.reset_client()
                self.config.url_path = path
                results[path] = self.scrape()
        finally:
            self.config.url_path = original
            self.config.auth_profile = original_profile
            self.config.auth_token = original_token
            self.config.x_vix_user_token = original_user
            self.reset_client()
        return results


def run_scrape(config: ScrapeConfig) -> tuple[Path, int]:
    """Library convenience wrapper used by the CLI."""
    try:
        if not config.endpoint:
            config.endpoint = PRODUCTION_ENDPOINT
        return TitleScraper(config).scrape_to_csv()
    except ScraperError:
        raise
    except OSError as exc:
        raise ScraperError(str(exc)) from exc
