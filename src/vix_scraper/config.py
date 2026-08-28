"""Load scrape settings from CLI args and environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.errors import ConfigError
from vix_scraper.models import ScrapeConfig


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def load_dotenv(path: Path | None = None, *, override: bool = False) -> None:
    """Minimal .env loader (no dependency).

    By default does not override existing env vars. Pass ``override=True`` for
    gitignored overlays like ``.env.local`` so UI-saved tokens win over stale ``.env``.
    """
    env_path = path or Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def config_from_env(**overrides: object) -> ScrapeConfig:
    """Build ScrapeConfig from env vars, then apply explicit overrides."""
    query_file = overrides.pop("query_file", None)
    input_json = overrides.pop("input_json", None)
    output = overrides.pop("output", None)
    url_path = overrides.pop("url_path", None)

    cfg = ScrapeConfig(
        url_path=str(url_path or ""),
        output=Path(str(output)) if output else Path("titles.csv"),
        endpoint=_env("VIX_GRAPHQL_ENDPOINT"),
        query=_env("VIX_GRAPHQL_QUERY"),
        query_file=Path(str(query_file)) if query_file else None,
        input_json=Path(str(input_json)) if input_json else None,
        auth_token=_env("AUTH_TOKEN"),
        x_vix_user_token=_env("X_VIX_USER_TOKEN"),
        installation_id=_env("VIX_INSTALLATION_ID"),
        auth_profile="default",
        app_version=_env("VIX_APP_VERSION", "5.0.0") or "5.0.0",
        device_type=_env("VIX_DEVICE_TYPE", "desktop") or "desktop",
        platform=_env("VIX_PLATFORM", "web") or "web",
        user_agent=_env(
            "VIX_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        )
        or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        country=(_env("VIX_COUNTRY", "MX") or "MX").upper(),
        accept_language=_env("VIX_ACCEPT_LANGUAGE", "es-MX,es;q=0.9") or "es-MX,es;q=0.9",
    )

    for key, value in overrides.items():
        if value is None:
            continue
        if not hasattr(cfg, key):
            raise ConfigError(f"Unknown config key: {key}")
        setattr(cfg, key, value)
    return cfg


def validate_config(cfg: ScrapeConfig) -> None:
    if not cfg.url_path:
        raise ConfigError("--url-path is required")
    if cfg.page_size < 1:
        raise ConfigError("--page-size must be >= 1")
    if cfg.module_page_size < 1:
        raise ConfigError("--module-page-size must be >= 1")
    if cfg.retries < 0:
        raise ConfigError("--retries cannot be negative")
    if cfg.timeout < 1:
        raise ConfigError("--timeout must be >= 1")
    if cfg.input_json is None and not cfg.endpoint:
        raise ConfigError("Missing --endpoint or VIX_GRAPHQL_ENDPOINT")


def validate_explore_config(cfg: ScrapeConfig) -> None:
    if cfg.page_size < 1:
        raise ConfigError("--page-size must be >= 1")
    if cfg.module_page_size < 1:
        raise ConfigError("--module-page-size must be >= 1")
    if cfg.retries < 0:
        raise ConfigError("--retries cannot be negative")
    if cfg.timeout < 1:
        raise ConfigError("--timeout must be >= 1")
    if cfg.max_pages < 1:
        raise ConfigError("--max-pages must be >= 1")
    if cfg.max_depth < 0:
        raise ConfigError("--max-depth cannot be negative")
    if not cfg.endpoint:
        raise ConfigError("Missing --endpoint or VIX_GRAPHQL_ENDPOINT")
    if not cfg.auth_token and cfg.input_json is None:
        raise ConfigError("Missing AUTH_TOKEN / --auth-token for live explore")
