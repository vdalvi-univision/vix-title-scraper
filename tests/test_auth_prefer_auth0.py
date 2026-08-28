"""Auth profile resolution tests. Never assert raw token values."""

from __future__ import annotations

import base64
import json
import time

from vix_scraper.auth import (
    apply_auth_profile,
    assert_jwt_matches_catalog_country,
    is_jwt_expired,
    jwt_country,
    jwt_safe_claims,
    resolve_auth_profile,
)


def _jwt(exp: int, country: str | None = None) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload: dict = {"exp": exp}
    if country:
        payload["country"] = country
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("ascii")).decode().rstrip("=")
    return f"{header}.{body}.sig"


def test_default_prefers_auth0_when_present(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "self-mint-token")
    monkeypatch.setenv("AUTH_TOKEN_AUTH0", "auth0-bearer-token")
    monkeypatch.delenv("X_VIX_USER_TOKEN", raising=False)
    creds = resolve_auth_profile("default")
    assert creds.auth_token == "auth0-bearer-token"


def test_default_skips_expired_auth0_for_live_profile_token(monkeypatch):
    live = _jwt(int(time.time()) + 3600)
    monkeypatch.setenv("AUTH_TOKEN", live)
    monkeypatch.setenv("AUTH_TOKEN_AUTH0", _jwt(int(time.time()) - 10))
    monkeypatch.delenv("X_VIX_USER_TOKEN", raising=False)
    creds = resolve_auth_profile("default")
    assert creds.auth_token == live
    assert not is_jwt_expired(creds.auth_token)


def test_wc_keeps_profile_token_when_unexpired(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_WC", _jwt(int(time.time()) + 3600))
    monkeypatch.setenv("AUTH_TOKEN_AUTH0", "auth0-bearer-token")
    monkeypatch.delenv("X_VIX_USER_TOKEN_WC", raising=False)
    creds = resolve_auth_profile("wc")
    assert creds.auth_token != "auth0-bearer-token"
    assert not is_jwt_expired(creds.auth_token)


def test_jwt_safe_claims_country_without_token_leak():
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + 60, "country": "us", "sub": "secret-user"}).encode("ascii")
    ).decode().rstrip("=")
    token = f"{header}.{payload}.sig"
    meta = jwt_safe_claims(token)
    assert meta["present"] is True
    assert meta["country"] == "US"
    assert meta["expired"] is False
    dumped = json.dumps(meta)
    assert token not in dumped
    assert "secret-user" not in dumped
    assert is_jwt_expired(token) is False


def test_wc_falls_back_to_auth0_when_expired(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_WC", _jwt(int(time.time()) - 10))
    monkeypatch.setenv("AUTH_TOKEN_AUTH0", "auth0-bearer-token")
    monkeypatch.delenv("X_VIX_USER_TOKEN_WC", raising=False)
    creds = resolve_auth_profile("wc")
    assert creds.auth_token == "auth0-bearer-token"


def test_default_prefers_live_token_matching_catalog_country(monkeypatch):
    mx = _jwt(int(time.time()) + 3600, country="MX")
    us = _jwt(int(time.time()) + 3600, country="US")
    monkeypatch.setenv("VIX_COUNTRY", "MX")
    monkeypatch.setenv("AUTH_TOKEN", mx)
    monkeypatch.setenv("AUTH_TOKEN_AUTH0", us)
    monkeypatch.delenv("X_VIX_USER_TOKEN", raising=False)
    creds = resolve_auth_profile("default")
    assert jwt_country(creds.auth_token) == "MX"
    assert creds.auth_token == mx


def test_apply_auth_profile_keeps_live_us_token_when_auth0_is_mx(monkeypatch):
    """UI paste / session JWT wins. Country mismatch is a warning, not a swap."""
    from vix_scraper.models import ScrapeConfig

    us = _jwt(int(time.time()) + 3600, country="US")
    mx = _jwt(int(time.time()) + 3600, country="MX")
    monkeypatch.setenv("VIX_COUNTRY", "MX")
    monkeypatch.setenv("AUTH_TOKEN", mx)
    monkeypatch.setenv("AUTH_TOKEN_AUTH0", mx)
    cfg = ScrapeConfig(auth_token=us, country="MX")
    apply_auth_profile(cfg, "default")
    assert cfg.auth_token == us
    assert jwt_country(cfg.auth_token) == "US"


def test_assert_jwt_matches_catalog_country_warns_us_for_mx(capsys):
    us = _jwt(int(time.time()) + 60, country="US")
    warning = assert_jwt_matches_catalog_country(us, "MX")
    assert warning is not None
    assert "JWT country=US" in warning
    assert "catalog country=MX" in warning
    assert "Warning" in warning
    err = capsys.readouterr().err
    assert "JWT country=US" in err
    assert us not in err
    assert assert_jwt_matches_catalog_country(_jwt(int(time.time()) + 60, country="MX"), "MX") is None
    assert assert_jwt_matches_catalog_country(_jwt(int(time.time()) + 60), "MX") is None


def test_title_scraper_warns_mismatched_jwt_country(monkeypatch, capsys):
    from vix_scraper.models import ScrapeConfig
    from vix_scraper.pagination import PagePaginator
    from vix_scraper.scraper import TitleScraper

    cfg = ScrapeConfig(
        url_path="/ondemandplus",
        endpoint="https://example.invalid/gql",
        query="query { __typename }",
        auth_token=_jwt(int(time.time()) + 60, country="US"),
        country="MX",
    )
    monkeypatch.setattr(PagePaginator, "iter_pages", lambda self: iter(()))
    rows = TitleScraper(cfg).scrape()
    assert rows == []
    err = capsys.readouterr().err
    assert "JWT country=US" in err
    assert "catalog country=MX" in err
    assert cfg.auth_token not in err

