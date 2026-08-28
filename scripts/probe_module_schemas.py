"""Introspect fields for module types seen in inventory. Never prints tokens."""

from __future__ import annotations

import os
from pathlib import Path

from vix_scraper.auth import apply_auth_profile
from vix_scraper.client import GraphQLClient
from vix_scraper.config import load_dotenv
from vix_scraper.models import PRODUCTION_ENDPOINT, ScrapeConfig

TYPES = [
    "UiContinueWatchingCarousel",
    "UiWatchlistCarousel",
    "UiRecentChannelsCarousel",
    "UiLiveVideoCarousel",
    "UiLiveVideoCard",
    "UiContinueWatchingCard",
    "UiWatchlistCard",
    "UiRecentChannelCard",
    "UiChannelCard",
    "UiSportsEventCarousel",
    "UiSportsEventCard",
    "UiInlinePage",
    "UiPageCarousel",
    "UiPageCard",
    "UiMixedContentCarousel",
]


def main() -> None:
    for k in list(os.environ):
        if k.startswith(("AUTH_", "X_VIX_", "VIX_")):
            os.environ.pop(k, None)
    load_dotenv(Path(".env"))
    cfg = ScrapeConfig(
        endpoint=PRODUCTION_ENDPOINT,
        timeout=40,
        retries=1,
        device_type="desktop",
        platform="web",
        user_agent=os.getenv("VIX_USER_AGENT"),
        installation_id=os.getenv("VIX_INSTALLATION_ID"),
    )
    apply_auth_profile(cfg, "wc")
    client = GraphQLClient(cfg)
    q = """
    query($n: String!) {
      __type(name: $n) {
        name
        fields {
          name
          type { name kind ofType { name kind ofType { name kind ofType { name } } } }
        }
      }
    }
    """
    for name in TYPES:
        payload = client.execute(q, {"n": name}, allow_errors=True)
        t = ((payload.get("data") or {}).get("__type"))
        if not t:
            print(f"{name}: NOT FOUND")
            continue
        fields = [f["name"] for f in (t.get("fields") or [])]
        print(f"{name}: {fields}")


if __name__ == "__main__":
    main()
