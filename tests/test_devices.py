"""Device catalog mapping and GraphQL header identity (no tokens)."""

from __future__ import annotations

import pytest

from vix_scraper.client import GraphQLClient
from vix_scraper.devices import (
    DEVICES,
    DEVICE_TYPE_OPTIONS,
    PLATFORM_OPTIONS,
    apply_device,
    creds_block_from_payload,
    devices_from_payload,
    env_suffix,
    resolve_device,
)
from vix_scraper.errors import ConfigError
from vix_scraper.models import ScrapeConfig


EXPECTED_LABELS = [
    "Fire Tablet",
    "Android",
    "Android TV",
    "Comcast TV",
    "Fire TV",
    "iOS",
    "LG TV",
    "Roku",
    "Samsung Galaxy",
    "Samsung TV",
    "Tigo STB",
    "tvOS",
    "Vega",
    "Vidaa TV",
    "Vizio TV",
    "Web",
    "Web TV",
]


def test_catalog_covers_requested_display_names():
    assert [d.label for d in DEVICES] == EXPECTED_LABELS
    assert len({d.id for d in DEVICES}) == len(DEVICES)


def test_resolve_display_names_and_ids():
    assert resolve_device("Web").id == "web"
    assert resolve_device("Fire TV").platform == "firetv"
    assert resolve_device("roku").device_type == "smarttv"
    assert resolve_device("iOS").platform == "ios"


def test_web_is_confirmed_desktop_not_device_type_web():
    web = resolve_device("web")
    assert web.confirmed is True
    assert web.platform == "web"
    assert web.device_type == "desktop"
    assert web.device_type != "web"


def test_unknown_device_raises():
    with pytest.raises(ConfigError, match="Unknown device"):
        resolve_device("playstation")


def test_devices_from_payload_defaults_to_web():
    devices = devices_from_payload({})
    assert [d.id for d in devices] == ["web"]


def test_devices_from_payload_multi_select_dedupes():
    devices = devices_from_payload({"devices": ["web", "Roku", "web", "roku"]})
    assert [d.id for d in devices] == ["web", "roku"]


def test_devices_from_payload_empty_list_errors():
    with pytest.raises(ConfigError, match="at least one"):
        devices_from_payload({"devices": []})


def test_apply_device_changes_graphql_headers():
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint="https://client-api.vix.com/gql/v2",
        auth_token="dummy-token",
    )
    apply_device(cfg, "roku")
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "roku"
    assert headers["x-vix-device-type"] == "smarttv"
    assert headers["x-vix-device-type"] != "tv"
    assert headers["User-Agent"] == resolve_device("roku").user_agent
    assert headers["x-vix-app-version"] == "5.0.0"
    assert headers["x-vix-country"] == "MX"
    assert headers["x-vix-geo-country"] == "MX"
    assert headers["Accept-Language"].startswith("es-MX")
    assert "dummy-token" in headers["Authorization"]
    assert "AUTH_TOKEN" not in str(headers.keys())

    apply_device(cfg, "web")
    web_headers = GraphQLClient._build_headers(cfg)
    assert web_headers["x-vix-platform"] == "web"
    assert web_headers["x-vix-device-type"] == "desktop"
    assert web_headers["User-Agent"] != headers["User-Agent"]
    assert web_headers["x-vix-device-type"] != "web"


def test_android_identity_differs_from_web():
    cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2", auth_token="dummy")
    apply_device(cfg, "Android")
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "android"
    assert headers["x-vix-device-type"] == "mobile"


def test_devices_from_payload_accepts_object_items():
    devices = devices_from_payload({"devices": [{"id": "web"}, {"id": "roku"}]})
    assert [d.id for d in devices] == ["web", "roku"]


def test_creds_block_from_payload_reads_device_creds():
    block = creds_block_from_payload(
        {"device_creds": {"roku": {"auth_token": "roku-secret", "platform": "roku"}}},
        "roku",
    )
    assert block["auth_token"] == "roku-secret"
    assert creds_block_from_payload({}, "roku") == {}


def test_env_suffix_for_compound_ids():
    assert env_suffix("fire_tv") == "FIRE_TV"
    assert env_suffix("web") == "WEB"


def test_ui_overrides_apply_per_device_headers():
    web_cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2")
    apply_device(
        web_cfg,
        "web",
        {
            "auth_token": "web-token",
            "platform": "web",
            "device_type": "desktop",
            "user_agent": "UA-WEB",
            "app_version": "5.0.0",
        },
    )
    roku_cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2")
    apply_device(
        roku_cfg,
        "roku",
        {
            "auth_token": "roku-token",
            "x_vix_user_token": "roku-user",
            "installation_id": "roku-install",
            "platform": "roku",
            "device_type": "smarttv",
            "user_agent": "UA-ROKU",
            "app_version": "4.2.0",
        },
    )
    web_headers = GraphQLClient._build_headers(web_cfg)
    roku_headers = GraphQLClient._build_headers(roku_cfg)
    assert web_headers["x-vix-platform"] == "web"
    assert web_headers["Authorization"] == "Bearer web-token"
    assert roku_headers["x-vix-platform"] == "roku"
    assert roku_headers["x-vix-device-type"] == "smarttv"
    assert roku_headers["User-Agent"] == "UA-ROKU"
    assert roku_headers["x-vix-app-version"] == "4.2.0"
    assert roku_headers["Authorization"] == "Bearer roku-token"
    assert roku_headers["x-vix-user-token"] == "roku-user"
    assert roku_headers["x-vix-installation-id"] == "roku-install"
    assert roku_cfg.auth_token != web_cfg.auth_token
    assert "AUTH_TOKEN" not in str(roku_headers.keys())


def test_roku_overrides_do_not_keep_web_identity():
    cfg = ScrapeConfig(
        endpoint="https://client-api.vix.com/gql/v2",
        auth_token="web-token",
        platform="web",
        device_type="desktop",
        user_agent="UA-WEB",
    )
    apply_device(cfg, "roku", {"auth_token": "roku-token", "user_agent": "UA-ROKU"})
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "roku"
    assert headers["x-vix-device-type"] == "smarttv"
    assert headers["x-vix-device-type"] != "tv"
    assert headers["User-Agent"] == "UA-ROKU"
    assert headers["Authorization"] == "Bearer roku-token"


def test_cli_device_flag_not_clobbered_by_env(monkeypatch, tmp_path):
    from vix_scraper.cli import args_to_page_config, build_parser

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("VIX_PLATFORM", "web")
    monkeypatch.setenv("VIX_DEVICE_TYPE", "desktop")
    parser = build_parser()
    args = parser.parse_args(
        ["page", "--url-path", "/ondemandplus", "--device", "roku", "--env-file", str(env_file)]
    )
    cfg = args_to_page_config(args)
    assert cfg.platform == "roku"
    assert cfg.device_type == "smarttv"
    assert cfg.device_id == "roku"
    assert cfg.user_agent == resolve_device("roku").user_agent


EXPECTED_PLATFORM_BY_ID = {
    "fire_tablet": ("firetablet", "tablet"),
    "android": ("android", "mobile"),
    "android_tv": ("androidtv", "smarttv"),
    "comcast_tv": ("comcasttv", "smarttv"),
    "fire_tv": ("firetv", "smarttv"),
    "ios": ("ios", "mobile"),
    "lg_tv": ("lgtv", "smarttv"),
    "roku": ("roku", "smarttv"),
    "samsung_galaxy": ("samsung_galaxy", "mobile"),
    "samsung_tv": ("samsungtv", "smarttv"),
    "tigo_stb": ("web", "smarttv"),
    "tvos": ("tvos", "smarttv"),
    "vega": ("web", "smarttv"),
    "vidaa_tv": ("vidaatv", "smarttv"),
    "vizio_tv": ("viziotv", "smarttv"),
    "web": ("web", "desktop"),
    "web_tv": ("web", "smarttv"),
}


def test_header_option_lists_are_exact():
    assert list(PLATFORM_OPTIONS) == [
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
    ]
    assert list(DEVICE_TYPE_OPTIONS) == ["mobile", "tablet", "smarttv", "desktop"]


def test_catalog_defaults_match_header_option_strings():
    for device in DEVICES:
        expected = EXPECTED_PLATFORM_BY_ID[device.id]
        assert (device.platform, device.device_type) == expected
        assert device.platform in PLATFORM_OPTIONS
        assert device.device_type in DEVICE_TYPE_OPTIONS
        assert device.device_type != "tv"


def test_tv_catalog_devices_send_smarttv_not_tv():
    cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2", auth_token="dummy")
    apply_device(cfg, "roku")
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "roku"
    assert headers["x-vix-device-type"] == "smarttv"
    assert headers["x-vix-device-type"] != "tv"

    apply_device(cfg, "lg_tv")
    lg_headers = GraphQLClient._build_headers(cfg)
    assert lg_headers["x-vix-platform"] == "lgtv"
    assert lg_headers["x-vix-device-type"] == "smarttv"


def test_dropdown_override_is_what_scraper_sends():
    cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2", auth_token="dummy")
    apply_device(
        cfg,
        "roku",
        {"platform": "androidtv", "device_type": "tablet", "user_agent": "UA-OVERRIDE"},
    )
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "androidtv"
    assert headers["x-vix-device-type"] == "tablet"
    assert headers["x-vix-platform"] != resolve_device("roku").platform


def test_old_tv_slug_maps_to_smarttv_on_headers():
    cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2", auth_token="dummy")
    apply_device(cfg, "roku", {"platform": "roku", "device_type": "tv"})
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-device-type"] == "smarttv"
    assert headers["x-vix-device-type"] != "tv"


def test_unmatched_catalog_devices_default_web_smarttv():
    for device_id in ("tigo_stb", "vega", "web_tv"):
        device = resolve_device(device_id)
        assert device.platform == "web"
        assert device.device_type == "smarttv"
        cfg = ScrapeConfig(endpoint="https://client-api.vix.com/gql/v2", auth_token="dummy")
        apply_device(cfg, device_id)
        headers = GraphQLClient._build_headers(cfg)
        assert headers["x-vix-platform"] == "web"
        assert headers["x-vix-device-type"] == "smarttv"
