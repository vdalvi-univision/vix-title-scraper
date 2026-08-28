"""Auth profile resolution from env / CLI. Never logs token values."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from dataclasses import dataclass

from vix_scraper.errors import ConfigError
from vix_scraper.models import AUTH_PROFILE_ENV


@dataclass(frozen=True, slots=True)
class AuthCredentials:
    profile: str
    auth_token: str | None
    x_vix_user_token: str | None

    @property
    def has_auth_token(self) -> bool:
        return bool(self.auth_token and self.auth_token.strip())


def list_auth_profiles() -> list[str]:
    return sorted(AUTH_PROFILE_ENV.keys())


def jwt_safe_claims(token: str | None) -> dict[str, object]:
    """Return non-secret JWT metadata. Never includes the token, email, or subject."""
    if not token or not str(token).strip():
        return {"present": False}
    raw = str(token).strip()
    if raw.count(".") != 2:
        return {"present": True, "is_jwt": False}
    try:
        payload = raw.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        if not isinstance(claims, dict):
            return {"present": True, "is_jwt": True}
        now = int(time.time())
        exp = int(claims.get("exp", 0) or 0)
        iss = str(claims.get("iss") or "")
        host = iss.split("://", 1)[-1].split("/", 1)[0] if iss else ""
        country = str(claims.get("country") or "").strip().upper()
        return {
            "present": True,
            "is_jwt": True,
            "expired": bool(exp and exp < now),
            "exp_in_sec": (exp - now) if exp else None,
            "country": country,
            "iss_host": host,
        }
    except Exception:  # noqa: BLE001
        return {"present": True, "is_jwt": True}


def is_jwt_expired(token: str | None) -> bool:
    """True when token is a JWT with exp in the past. Non-JWTs are treated as not expired."""
    meta = jwt_safe_claims(token)
    return bool(meta.get("is_jwt") and meta.get("expired"))


def jwt_country(token: str | None) -> str:
    """Uppercase JWT ``country`` claim, or empty when absent / not a JWT."""
    return str(jwt_safe_claims(token).get("country") or "").strip().upper()


def catalog_country_mismatch_message(requested: str, token_country: str) -> str:
    req = (requested or "").strip().upper()
    got = (token_country or "").strip().upper()
    return (
        f"Warning: session JWT country={got} but scrape catalog country={req}. "
        f"Continuing scrape with catalog geo headers (x-vix-country={req}). "
        "JWT country does not block the scrape."
    )


def jwt_catalog_country_warning(token: str | None, requested: str | None) -> str | None:
    """Return a warning when JWT country contradicts catalog country; never raises.

    Missing country claims are allowed (some Auth0 tokens omit it). An explicit
    mismatch is recorded so callers can log / show UI / scrape_meta notes, but
    US JWT + MX headers can still load the MX catalog (e.g. /ondemandplus).
    """
    req = (requested or "").strip().upper()
    got = jwt_country(token)
    if req and got and got != req:
        return catalog_country_mismatch_message(req, got)
    return None


def warn_jwt_catalog_country(token: str | None, requested: str | None) -> str | None:
    """Log a JWT/catalog country mismatch to stderr. Never prints the token."""
    msg = jwt_catalog_country_warning(token, requested)
    if msg:
        print(msg, file=sys.stderr)
    return msg


def assert_jwt_matches_catalog_country(token: str | None, requested: str | None) -> str | None:
    """Warn (do not refuse) when JWT country contradicts the chosen catalog country.

    Kept for call-site compatibility. Returns the warning message or None.
    """
    return warn_jwt_catalog_country(token, requested)


def pin_token_to_catalog_country(config: object, preferred_token: str | None = None) -> None:
    """Restore a wiped caller token. Never swap a live session because JWT country differs.

    US JWT + MX geo headers can still load the MX catalog. Mismatch is a warning.
    """
    current = getattr(config, "auth_token", None)
    if current and str(current).strip():
        return
    if preferred_token and str(preferred_token).strip() and not is_jwt_expired(preferred_token):
        setattr(config, "auth_token", preferred_token)


def resolve_auth_profile(profile: str) -> AuthCredentials:
    """Load tokens for a named profile. Tokens are never returned to logs by callers."""
    key = (profile or "default").strip().lower()
    if key not in AUTH_PROFILE_ENV:
        known = ", ".join(list_auth_profiles())
        raise ConfigError(f"Unknown auth profile {profile!r}. Known: {known}")
    auth_env, user_env = AUTH_PROFILE_ENV[key]
    auth = os.getenv(auth_env)
    user = os.getenv(user_env)
    # Prefer a live token whose JWT country matches VIX_COUNTRY / catalog country.
    # Otherwise prefer a *live* Auth0 bearer for web/default scrapes: profile AUTH_TOKEN
    # is often a self-mint that is JWT-unexpired but already revoked (403 INVALID_TOKEN).
    # Never prefer an expired Auth0 token over a live profile token — that returns
    # uiPage: null with no GraphQL errors (the UI used to map that to a false VPN warning).
    # Keep x-vix-user-token even when falling back — Auth0 alone cannot populate
    # Continue Watching / personalized rails.
    profile_auth = auth.strip() if auth and auth.strip() else ""
    auth0 = (os.getenv("AUTH_TOKEN_AUTH0") or "").strip()
    profile_live = bool(profile_auth) and not is_jwt_expired(profile_auth)
    auth0_live = bool(auth0) and not is_jwt_expired(auth0)
    requested = (os.getenv("VIX_COUNTRY") or "").strip().upper()
    # A live token whose JWT country matches the catalog wins over AUTH0-default.
    # AUTH0-first otherwise still applies when neither token has a country claim.
    if key == "default":
        ordered = [auth0, profile_auth]
    else:
        ordered = [profile_auth, auth0]
    country_hits = [
        tok
        for tok in ordered
        if tok and not is_jwt_expired(tok) and requested and jwt_country(tok) == requested
    ]
    if country_hits:
        auth = country_hits[0]
    elif key == "default" and auth0_live:
        auth = auth0
    elif profile_live:
        auth = profile_auth
    elif auth0_live:
        auth = auth0
    elif profile_auth:
        auth = profile_auth
    elif auth0:
        auth = auth0
    else:
        auth = None
    # Do not drop expired user tokens here: slightly-stale JWTs still authenticate
    # some personalized rails, and blanking them forces empty Seguir viendo which
    # shifts every carousel_y below it.
    return AuthCredentials(
        profile=key,
        auth_token=auth.strip() if auth and auth.strip() else None,
        x_vix_user_token=user.strip() if user and user.strip() else None,
    )


def apply_auth_profile(config: object, profile: str) -> AuthCredentials:
    """Mutate a ScrapeConfig-like object with credentials for ``profile``.

    A caller-supplied live token wins. JWT country mismatch is a warning, not a swap
    onto AUTH_TOKEN_AUTH0 / a catalog-country env token.
    """
    preferred = getattr(config, "auth_token", None)
    preferred_user = getattr(config, "x_vix_user_token", None)
    creds = resolve_auth_profile(profile)
    setattr(config, "auth_profile", creds.profile)
    live_preferred = bool(preferred and str(preferred).strip() and not is_jwt_expired(preferred))
    if live_preferred:
        setattr(config, "auth_token", preferred)
    else:
        setattr(config, "auth_token", creds.auth_token)
        pin_token_to_catalog_country(config, preferred)
    if preferred_user and str(preferred_user).strip():
        setattr(config, "x_vix_user_token", preferred_user)
    else:
        setattr(config, "x_vix_user_token", creds.x_vix_user_token)
    return creds


def parse_auth_profile_map(raw: str | None) -> dict[str, str]:
    """Parse ``/path=profile,/other=wc`` mappings."""
    mapping: dict[str, str] = {}
    if not raw or not str(raw).strip():
        return mapping
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ConfigError(
                f"Invalid --auth-profile-map entry {part!r}. Expected /urlPath=profile"
            )
        path, _, profile = part.partition("=")
        path = path.strip()
        profile = profile.strip().lower()
        if not path.startswith("/"):
            path = "/" + path
        if profile not in AUTH_PROFILE_ENV:
            raise ConfigError(
                f"Unknown auth profile {profile!r} in map for {path}. "
                f"Known: {', '.join(list_auth_profiles())}"
            )
        mapping[path] = profile
    return mapping


def missing_profile_help(profile: str) -> str:
    key = (profile or "default").strip().lower()
    auth_env, user_env = AUTH_PROFILE_ENV.get(key, ("AUTH_TOKEN", "X_VIX_USER_TOKEN"))
    return (
        f"Auth profile '{key}' has no {auth_env} in the environment/.env. "
        f"Add {auth_env}=... (and optionally {user_env}=...) then re-run with "
        f"--auth-profile {key} or --auth-profile-map /path={key}. "
        f"Tokens are sent only as Authorization / x-vix-user-token headers."
    )
