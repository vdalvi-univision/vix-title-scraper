"""Catalog of ViX client devices for GraphQL request identity.

Only **Web** (`x-vix-platform=web`, `x-vix-device-type=desktop`) is confirmed in this
repo. Other platform / device-type / User-Agent values are best-effort slugs so the
chooser actually changes request headers. Correct them when a Network-tab dump lands.

Tigo STB, Vega, and Web TV have no matching `x-vix-platform` option; they stay in the
chooser and default to `web` + `smarttv` so the dropdowns remain valid.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from vix_scraper.errors import ConfigError
from vix_scraper.models import ScrapeConfig

DEFAULT_APP_VERSION = "5.0.0"
WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_DEVICE_ID = "web"

# Exact GraphQL header values for x-vix-platform / x-vix-device-type.
PLATFORM_OPTIONS: tuple[str, ...] = (
    "android",
    "androidtv",
    "firetv",
    "firetablet",
    "roku",
    "ios",
    "tvos",
    "web",
    "samsungtv",
    "lgtv",
    "viziotv",
    "vidaatv",
    "samsung_galaxy",
    "comcasttv",
)
DEVICE_TYPE_OPTIONS: tuple[str, ...] = (
    "mobile",
    "tablet",
    "smarttv",
    "desktop",
)

# Old catalog slugs → the option set above. Unlisted platforms (Tigo/Vega/Web TV)
# default to `web` so CLI and the UI dropdown still send a valid header.
_PLATFORM_ALIASES: dict[str, str] = {
    "lg": "lgtv",
    "samsung": "samsung_galaxy",
    "vizio": "viziotv",
    "vidaa": "vidaatv",
    "comcast": "comcasttv",
    "tigo": "web",
    "vega": "web",
    "webtv": "web",
}
_DEVICE_TYPE_ALIASES: dict[str, str] = {
    "tv": "smarttv",
    "stb": "smarttv",
}


@dataclass(frozen=True, slots=True)
class ClientDevice:
    """One selectable client identity sent as GraphQL headers."""

    id: str
    label: str
    platform: str
    device_type: str
    user_agent: str
    app_version: str = DEFAULT_APP_VERSION
    confirmed: bool = False

    def as_public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ua_android_phone() -> str:
    return (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
    )


def _ua_android_tablet() -> str:
    return (
        "Mozilla/5.0 (Linux; Android 13; KFTRWI) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )


def _ua_ios() -> str:
    return (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    )


def _ua_tvos() -> str:
    return (
        "AppleCoreMedia/1.0.0.21L227 (Apple TV; U; CPU OS 17_6 like Mac OS X; en_us)"
    )


def _ua_android_tv() -> str:
    return (
        "Mozilla/5.0 (Linux; Android 12; SHIELD Android TV) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )


def _ua_ctv(name: str) -> str:
    return f"ViX/{DEFAULT_APP_VERSION} ({name})"


# Display order matches the product list. `confirmed` is True only when this repo
# has validated the header pair against the GraphQL API.
DEVICES: tuple[ClientDevice, ...] = (
    ClientDevice(
        id="fire_tablet",
        label="Fire Tablet",
        platform="firetablet",
        device_type="tablet",
        user_agent=_ua_android_tablet(),
    ),
    ClientDevice(
        id="android",
        label="Android",
        platform="android",
        device_type="mobile",
        user_agent=_ua_android_phone(),
    ),
    ClientDevice(
        id="android_tv",
        label="Android TV",
        platform="androidtv",
        device_type="smarttv",
        user_agent=_ua_android_tv(),
    ),
    ClientDevice(
        id="comcast_tv",
        label="Comcast TV",
        platform="comcasttv",
        device_type="smarttv",
        user_agent=_ua_ctv("Comcast"),
    ),
    ClientDevice(
        id="fire_tv",
        label="Fire TV",
        platform="firetv",
        device_type="smarttv",
        user_agent=_ua_ctv("Fire TV"),
    ),
    ClientDevice(
        id="ios",
        label="iOS",
        platform="ios",
        device_type="mobile",
        user_agent=_ua_ios(),
    ),
    ClientDevice(
        id="lg_tv",
        label="LG TV",
        platform="lgtv",
        device_type="smarttv",
        user_agent=_ua_ctv("webOS"),
    ),
    ClientDevice(
        id="roku",
        label="Roku",
        platform="roku",
        device_type="smarttv",
        user_agent="Roku/DVP-13.0 (13.0.0)",
    ),
    ClientDevice(
        id="samsung_galaxy",
        label="Samsung Galaxy",
        platform="samsung_galaxy",
        device_type="mobile",
        user_agent=(
            "Mozilla/5.0 (Linux; Android 14; SM-S921U) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
        ),
    ),
    ClientDevice(
        id="samsung_tv",
        label="Samsung TV",
        platform="samsungtv",
        device_type="smarttv",
        user_agent=_ua_ctv("Tizen"),
    ),
    ClientDevice(
        id="tigo_stb",
        label="Tigo STB",
        platform="web",
        device_type="smarttv",
        user_agent=_ua_ctv("Tigo STB"),
    ),
    ClientDevice(
        id="tvos",
        label="tvOS",
        platform="tvos",
        device_type="smarttv",
        user_agent=_ua_tvos(),
    ),
    ClientDevice(
        id="vega",
        label="Vega",
        platform="web",
        device_type="smarttv",
        user_agent=_ua_ctv("Vega"),
    ),
    ClientDevice(
        id="vidaa_tv",
        label="Vidaa TV",
        platform="vidaatv",
        device_type="smarttv",
        user_agent=_ua_ctv("VIDAA"),
    ),
    ClientDevice(
        id="vizio_tv",
        label="Vizio TV",
        platform="viziotv",
        device_type="smarttv",
        user_agent=_ua_ctv("Vizio"),
    ),
    ClientDevice(
        id="web",
        label="Web",
        platform="web",
        device_type="desktop",
        user_agent=WEB_USER_AGENT,
        confirmed=True,
    ),
    ClientDevice(
        id="web_tv",
        label="Web TV",
        platform="web",
        device_type="smarttv",
        user_agent=(
            "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/79.0.3945.79 Safari/537.36"
        ),
    ),
)

_DEVICES_BY_ID = {d.id: d for d in DEVICES}


def _norm(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


_ALIAS_TO_ID: dict[str, str] = {}


def _register_alias(alias: str, device_id: str) -> None:
    key = _norm(alias)
    if key and key not in _ALIAS_TO_ID:
        _ALIAS_TO_ID[key] = device_id


for _device in DEVICES:
    _register_alias(_device.id, _device.id)
for _device in DEVICES:
    _register_alias(_device.label, _device.id)
for _device in DEVICES:
    _register_alias(_device.platform, _device.id)

# Keep resolving old platform slugs after catalog strings changed.
for _alias, _device_id in (
    ("lg", "lg_tv"),
    ("comcast", "comcast_tv"),
    ("samsung", "samsung_galaxy"),
    ("vidaa", "vidaa_tv"),
    ("vizio", "vizio_tv"),
    ("tigo", "tigo_stb"),
    ("webtv", "web_tv"),
):
    _register_alias(_alias, _device_id)


def canonical_platform(value: str | None) -> str:
    """Map a stored/catalog slug onto an x-vix-platform option."""
    raw = str(value or "").strip()
    if raw in PLATFORM_OPTIONS:
        return raw
    return _PLATFORM_ALIASES.get(raw.lower(), "")


def canonical_device_type(value: str | None) -> str:
    """Map a stored/catalog slug onto an x-vix-device-type option."""
    raw = str(value or "").strip()
    if raw in DEVICE_TYPE_OPTIONS:
        return raw
    return _DEVICE_TYPE_ALIASES.get(raw.lower(), "")


def header_option_lists() -> dict[str, list[str]]:
    return {
        "platform_options": list(PLATFORM_OPTIONS),
        "device_type_options": list(DEVICE_TYPE_OPTIONS),
    }


def list_devices() -> list[ClientDevice]:
    return list(DEVICES)


def catalog_payload() -> list[dict[str, Any]]:
    return [d.as_public_dict() for d in DEVICES]


def resolve_device(value: str | ClientDevice | None) -> ClientDevice:
    """Resolve a catalog id, display name, or platform slug."""
    if isinstance(value, ClientDevice):
        return value
    raw = str(value or "").strip()
    if not raw:
        return _DEVICES_BY_ID[DEFAULT_DEVICE_ID]
    if raw in _DEVICES_BY_ID:
        return _DEVICES_BY_ID[raw]
    mapped = _ALIAS_TO_ID.get(_norm(raw))
    if mapped:
        return _DEVICES_BY_ID[mapped]
    known = ", ".join(d.label for d in DEVICES)
    raise ConfigError(f"Unknown device {raw!r}. Choose from: {known}")


def devices_from_payload(payload: dict[str, Any] | None) -> list[ClientDevice]:
    """Read `devices` (list) from a UI payload; fall back to platform/device_type."""
    data = payload or {}
    raw = data.get("devices")
    if raw is None or raw == "":
        platform = str(data.get("platform") or "").strip()
        device_type = str(data.get("device_type") or "").strip()
        if not platform and not device_type:
            return [resolve_device(DEFAULT_DEVICE_ID)]
        return [_device_from_header_fields(platform, device_type, data.get("user_agent"))]

    if isinstance(raw, str):
        values: Iterable[Any] = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        values = raw
    else:
        raise ConfigError("devices must be a list of device ids")

    seen: set[str] = set()
    out: list[ClientDevice] = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("id") or item.get("device") or item.get("device_id") or ""
        device = resolve_device(str(item))
        if device.id in seen:
            continue
        seen.add(device.id)
        out.append(device)
    if not out:
        raise ConfigError("Select at least one device")
    return out


def _device_from_header_fields(
    platform: str,
    device_type: str,
    user_agent: Any,
) -> ClientDevice:
    plat = platform or "web"
    dtype = device_type or "desktop"
    for device in DEVICES:
        if device.platform == plat and device.device_type == dtype:
            return device
    ua = str(user_agent or "").strip() or WEB_USER_AGENT
    return ClientDevice(
        id=plat,
        label=plat,
        platform=plat,
        device_type=dtype,
        user_agent=ua,
        confirmed=False,
    )


def env_suffix(device_id: str) -> str:
    """``fire_tv`` → ``FIRE_TV`` for AUTH_TOKEN_FIRE_TV-style env keys."""
    return "".join(ch.upper() if ch.isalnum() else "_" for ch in str(device_id or "")).strip("_")


def creds_block_from_payload(payload: dict[str, Any] | None, device_id: str) -> dict[str, Any]:
    """Return the UI block for one device id (empty dict if absent)."""
    data = payload or {}
    blocks = data.get("device_creds")
    if isinstance(blocks, dict):
        raw = blocks.get(device_id)
        if isinstance(raw, dict):
            return raw
    raw_list = data.get("devices")
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or item.get("device") or item.get("device_id") or "").strip()
            if item_id and resolve_device(item_id).id == device_id:
                return item
    return {}


def apply_device(
    config: ScrapeConfig,
    device: ClientDevice | str,
    overrides: dict[str, Any] | None = None,
) -> ScrapeConfig:
    """Copy catalog identity onto a scrape config, then apply optional UI overrides.

    Overrides never pull another device's identity — missing keys keep the catalog
    values for *this* device (including User-Agent and app version). Tokens are
    applied only when the override provides them.
    """
    resolved = resolve_device(device)
    ovr = overrides or {}
    raw_platform = str(ovr.get("platform") or resolved.platform).strip() or resolved.platform
    raw_type = str(ovr.get("device_type") or resolved.device_type).strip() or resolved.device_type
    config.platform = canonical_platform(raw_platform) or raw_platform
    config.device_type = canonical_device_type(raw_type) or raw_type
    config.user_agent = str(ovr.get("user_agent") or resolved.user_agent).strip() or resolved.user_agent
    config.app_version = str(ovr.get("app_version") or resolved.app_version).strip() or resolved.app_version
    config.device_id = resolved.id
    config.device_label = resolved.label
    auth = str(ovr.get("auth_token") or "").strip()
    if auth:
        config.auth_token = auth
    user = ovr.get("x_vix_user_token")
    if user is not None and str(user).strip():
        config.x_vix_user_token = str(user).strip()
    install = ovr.get("installation_id")
    if install is not None and str(install).strip():
        config.installation_id = str(install).strip()
    country = str(ovr.get("country") or getattr(config, "country", "") or "").strip()
    if country:
        config.country = country.upper()
    accept_language = str(
        ovr.get("accept_language") or getattr(config, "accept_language", "") or ""
    ).strip()
    if accept_language:
        config.accept_language = accept_language
    return config
