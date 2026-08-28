"""Download and dedupe image assets (stdlib urllib)."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from vix_scraper.models import ExportedImage

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Default: download one useful poster/hero/logo per title, not every artwork variant.
PRIMARY_IMAGE_ROLES = frozenset(
    {
        "VERTICAL_POSTER",
        "SERIES_COVER_ART",
        "SQUARE_POSTER",
        "HERO_POSTER",
        "LOGO_TRANSPARENT",
        "OG",
        "TWITTER",
    }
)


def _safe_name(text: str, fallback: str = "image") -> str:
    cleaned = _UNSAFE.sub("_", text.strip())[:80]
    return cleaned or fallback


def url_fingerprint(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def guess_extension(url: str, content_type: str | None = None) -> str:
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif"}:
        return suffix
    return ".jpg"


class ImageDownloader:
    """Download unique image URLs into output/images with resume-friendly skips."""

    def __init__(
        self,
        directory: Path | str,
        *,
        timeout: int = 30,
        user_agent: str = "insomnia/9.3.1",
        enabled: bool = True,
        download_roles: frozenset[str] | None = PRIMARY_IMAGE_ROLES,
    ) -> None:
        self.directory = Path(directory)
        self.timeout = timeout
        self.user_agent = user_agent
        self.enabled = enabled
        # None means download all roles; a set means only those roles are fetched.
        self.download_roles = download_roles
        self.downloaded = 0
        self.skipped = 0
        self.failed = 0
        self._seen_urls: set[str] = set()
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    def process(self, images: Iterable[ExportedImage]) -> list[ExportedImage]:
        output: list[ExportedImage] = []
        for image in images:
            url = (image.url or "").strip()
            if not url:
                continue
            if url in self._seen_urls:
                self.skipped += 1
                # Keep first-seen row only in export list.
                continue
            self._seen_urls.add(url)
            local = ""
            should_download = self.enabled and self._role_allowed(image.image_role)
            if should_download:
                local = self._download(url, image)
            elif self.enabled:
                self.skipped += 1
            image.local_path = local
            output.append(image)
        return output

    def _role_allowed(self, role: str) -> bool:
        if self.download_roles is None:
            return True
        return (role or "").upper() in self.download_roles

    def _download(self, url: str, image: ExportedImage) -> str:
        role = _safe_name(image.image_role or "image")
        content = _safe_name(image.content_id or "page", fallback="page")
        fingerprint = url_fingerprint(url)
        # Prefer existing file with any known extension.
        matches = list(self.directory.glob(f"{content}__{role}__{fingerprint}.*"))
        if matches:
            self.skipped += 1
            return str(matches[0].as_posix())

        headers = {"User-Agent": self.user_agent, "Accept": "image/*,*/*"}
        request = Request(url, headers=headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = response.read()
                content_type = response.headers.get("Content-Type")
            ext = guess_extension(url, content_type)
            path = self.directory / f"{content}__{role}__{fingerprint}{ext}"
            path.write_bytes(data)
            self.downloaded += 1
            return str(path.as_posix())
        except (HTTPError, URLError, TimeoutError, OSError):
            self.failed += 1
            return ""
