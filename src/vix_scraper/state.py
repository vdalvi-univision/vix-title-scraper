"""Persist explore crawl progress for resume."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from vix_scraper.errors import ScraperError


@dataclass
class ExploreState:
    endpoint: str = ""
    queue: list[list[Any]] = field(default_factory=list)  # [path, depth, discovered_from]
    visited: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExploreState":
        return cls(
            endpoint=str(data.get("endpoint") or ""),
            queue=[list(item) for item in (data.get("queue") or [])],
            visited=list(data.get("visited") or []),
            failed=list(data.get("failed") or []),
            stats=dict(data.get("stats") or {}),
        )


class StateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> ExploreState | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScraperError(f"Could not read state file {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ScraperError("State file must contain a JSON object")
        return ExploreState.from_dict(payload)

    def save(self, state: ExploreState) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            raise ScraperError(f"Could not write state file {self.path}: {exc}") from exc
