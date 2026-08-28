"""Verify web platform headers used by the GraphQL client."""

from __future__ import annotations

from vix_scraper.client import GraphQLClient
from vix_scraper.models import ScrapeConfig


def test_web_desktop_headers():
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint="https://client-api.vix.com/gql/v2",
        platform="web",
        device_type="desktop",
        auth_token="dummy-token",
        x_vix_user_token="dummy-user",
        installation_id="install-123",
    )
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "web"
    assert headers["x-vix-device-type"] == "desktop"
    assert headers["Authorization"] == "Bearer dummy-token"
    assert headers["x-vix-user-token"] == "dummy-user"
    assert headers["x-vix-installation-id"] == "install-123"
    # device-type must NOT be the string "web" (API rejects as MALFORMED_REQUEST)
    assert headers["x-vix-device-type"] != "web"
    assert headers["x-vix-country"] == "MX"
    assert headers["cloudfront-viewer-country"] == "MX"
    assert headers["x-vix-geo-country"] == "MX"
    assert headers["Accept-Language"].startswith("es-MX")


def test_chosen_device_flows_into_headers():
    from vix_scraper.devices import apply_device

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint="https://client-api.vix.com/gql/v2",
        auth_token="dummy-token",
    )
    apply_device(cfg, "ios")
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-platform"] == "ios"
    assert headers["x-vix-device-type"] == "mobile"
    assert "iPhone" in headers["User-Agent"]
    assert cfg.device_label == "iOS"
    assert headers["x-vix-country"] == "MX"
    assert headers["Accept-Language"].startswith("es-MX")


def test_country_override_changes_geo_headers():
    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint="https://client-api.vix.com/gql/v2",
        country="US",
        accept_language="en-US,en;q=0.9",
    )
    headers = GraphQLClient._build_headers(cfg)
    assert headers["x-vix-country"] == "US"
    assert headers["cloudfront-viewer-country"] == "US"
    assert headers["x-vix-geo-country"] == "US"
    assert headers["Accept-Language"] == "en-US,en;q=0.9"


def test_scrape_meta_helpers_roundtrip(tmp_path):
    from vix_scraper.layout_compare import format_ran_at_local, load_scrape_meta, write_scrape_meta

    iso, human = format_ran_at_local()
    assert "T" in iso or iso.endswith("Z")
    assert human
    path = write_scrape_meta(
        tmp_path,
        {
            "ran_at": iso,
            "ran_at_local": human,
            "endpoint": "https://client-api.vix.com/gql/v2",
            "platform": "web",
            "device": "desktop",
            "pages": {
                "/ondemandplus": {
                    "ran_at": iso,
                    "ran_at_local": human,
                    "row_count": 10,
                    "status": "ok",
                    "auth_profile": "default",
                }
            },
            "row_counts": {"/ondemandplus": 10},
            "auth_profiles_used": ["default"],
            "install_id_present": True,
            "duration_seconds": 1.2,
            "errors": [],
        },
    )
    assert path.name == "scrape_meta.json"
    loaded = load_scrape_meta(tmp_path)
    assert loaded is not None
    assert loaded["platform"] == "web"
    assert loaded["install_id_present"] is True
    assert "AUTH_TOKEN" not in path.read_text(encoding="utf-8")
    assert (tmp_path / "ondemandplus_scrape_meta.json").is_file()
