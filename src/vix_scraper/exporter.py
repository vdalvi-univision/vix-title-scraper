"""CSV export helpers."""



from __future__ import annotations



import csv

import os

import time

from pathlib import Path

from typing import Iterable, Sequence



from vix_scraper.errors import ScraperError

from vix_scraper.models import (

    CSV_FIELDS,

    IMAGE_CSV_FIELDS,

    PAGE_CSV_FIELDS,

    ExportedImage,

    ExportedPage,

    ExportedTitle,

)





def deduplicate(rows: Sequence[ExportedTitle]) -> list[ExportedTitle]:

    """Keep first occurrence of each content id and renumber page indexes."""

    seen: set[str] = set()

    output: list[ExportedTitle] = []

    for row in rows:

        if row.content_id in seen:

            continue

        seen.add(row.content_id)

        output.append(row)

    for position, row in enumerate(output, start=1):

        row.position = position

    return output





def atomic_replace(tmp_path: Path, final_path: Path, *, retries: int = 8) -> None:

    """Replace ``final_path`` with ``tmp_path``, retrying on Windows file locks."""

    last_exc: OSError | None = None

    for attempt in range(retries):

        try:

            os.replace(tmp_path, final_path)

            return

        except OSError as exc:

            last_exc = exc

            # WinError 32: file in use — brief backoff then retry.

            time.sleep(0.15 * (attempt + 1))

    raise ScraperError(

        f"Could not replace {final_path} (file locked?). Close Excel/Sheets and retry. "

        f"Last error: {last_exc}"

    ) from last_exc





class CsvExporter:

    """Write title rows to CSV. Tokens are never written. Overwrites atomically."""



    def __init__(self, path: Path | str, fieldnames: Sequence[str] | None = None) -> None:

        self.path = Path(path)

        self.fieldnames = list(fieldnames or CSV_FIELDS)



    def write(self, rows: Iterable[ExportedTitle]) -> int:

        return self._write(rows)



    def write_pages(self, rows: Iterable[ExportedPage]) -> int:

        self.fieldnames = list(PAGE_CSV_FIELDS)

        return self._write(rows)



    def write_images(self, rows: Iterable[ExportedImage]) -> int:

        self.fieldnames = list(IMAGE_CSV_FIELDS)

        return self._write(rows)



    def write_dicts(self, rows: Iterable[dict], fieldnames: Sequence[str]) -> int:

        self.fieldnames = list(fieldnames)

        return self._write(rows)



    def _write(self, rows: Iterable[object]) -> int:

        try:

            self.path.parent.mkdir(parents=True, exist_ok=True)

            tmp_path = self.path.with_name(self.path.name + ".tmp")

            count = 0

            with tmp_path.open("w", encoding="utf-8", newline="") as handle:

                writer = csv.DictWriter(handle, fieldnames=self.fieldnames, extrasaction="ignore")

                writer.writeheader()

                for row in rows:

                    if hasattr(row, "as_csv_row"):

                        writer.writerow(row.as_csv_row())

                    elif isinstance(row, dict):

                        writer.writerow(row)

                    else:

                        raise ScraperError(f"Unsupported CSV row type: {type(row)!r}")

                    count += 1

            atomic_replace(tmp_path, self.path)

            return count

        except ScraperError:

            raise

        except OSError as exc:

            raise ScraperError(f"Could not write CSV {self.path}: {exc}") from exc


