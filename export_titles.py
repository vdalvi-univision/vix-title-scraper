#!/usr/bin/env python3
"""Backward-compatible CLI entrypoint (same flags as the original export_titles.py)."""

from vix_scraper.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
