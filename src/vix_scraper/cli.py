"""Command-line interface for the ViX title scraper / site explorer."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from vix_scraper.auth import apply_auth_profile, missing_profile_help, parse_auth_profile_map, resolve_auth_profile
from vix_scraper.config import load_dotenv, validate_config, validate_explore_config
from vix_scraper.errors import ScraperError
from vix_scraper.explorer import run_explore
from vix_scraper.layout_compare import run_batch_scrape
from vix_scraper.models import DEFAULT_SEED_PATHS, PRODUCTION_ENDPOINT, ScrapeConfig
from vix_scraper.scraper import run_scrape


def _add_common_args(parser: argparse.ArgumentParser, *, module_page_size: int = 2) -> None:
    parser.add_argument("--endpoint", default=None, help="Overrides VIX_GRAPHQL_ENDPOINT")
    parser.add_argument("--query-file", type=Path, default=Path("queries/request.graphql"))
    parser.add_argument(
        "--navigation-query-file",
        type=Path,
        default=Path("queries/navigation.graphql"),
    )
    parser.add_argument("--auth-token", default=None, help="Overrides AUTH_TOKEN for this run")
    parser.add_argument("--x-vix-user-token", default=None, help="Overrides X_VIX_USER_TOKEN")
    parser.add_argument(
        "--auth-profile",
        default="default",
        help="Auth profile name: default (AUTH_TOKEN) or wc (AUTH_TOKEN_WC)",
    )
    parser.add_argument("--app-version", default=None)
    parser.add_argument(
        "--device",
        default=None,
        help="Catalog device (e.g. web, roku, 'Fire TV'). Sets platform, device-type, and User-Agent.",
    )
    parser.add_argument("--device-type", default=None)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--user-agent", default=None)
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="contents first for catalog crawl (explore/page). Layout uses each module's contents.totalCount instead.",
    )
    parser.add_argument("--module-page-size", type=int, default=module_page_size)
    parser.add_argument(
        "--paginate-contents",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Follow contents endCursor. Layout/batch keeps this on and sizes first from the module total.",
    )
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--deduplicate", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Optional .env path")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vix-scraper",
        description="ViX GraphQL title scraper and website explorer (stdlib-only).",
    )
    sub = parser.add_subparsers(dest="command")

    page = sub.add_parser("page", help="Scrape a single urlPath to CSV")
    page.add_argument("--url-path", required=True, help="Page path, e.g. /micro-dramas")
    page.add_argument("--output", default="titles.csv", help="Output CSV path")
    page.add_argument("--input-json", type=Path, help="Offline snapshot instead of HTTP")
    _add_common_args(page)

    batch = sub.add_parser(
        "batch",
        help="Scrape multiple urlPaths (layout-compare friendly, no images)",
    )
    batch.add_argument(
        "--url-paths",
        required=True,
        help="Comma-separated urlPaths, e.g. /ondemandplus,/ondemandpluswc",
    )
    batch.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/layout_compare"),
        help="Directory for per-page + combined CSVs",
    )
    batch.add_argument(
        "--auth-profile-map",
        default="",
        help="Per-path profiles, e.g. /ondemandpluswc=wc,/ondemandplus=default",
    )
    batch.add_argument(
        "--write-diff",
        action="store_true",
        default=True,
        help="Write layout_diff_summary.md when >=2 pages succeed (default on)",
    )
    _add_common_args(batch, module_page_size=1)
    batch.set_defaults(
        query_file=Path("queries/layout.graphql"),
        deduplicate=False,
        paginate_contents=True,
        contents_first_from_total=True,
    )

    explore = sub.add_parser("explore", help="Discover and crawl reachable uiPage paths")
    explore.add_argument("--start-path", default=None, help="Primary seed path (also enqueued)")
    explore.add_argument(
        "--seed-paths",
        default=",".join(DEFAULT_SEED_PATHS),
        help="Comma-separated seed paths (defaults include nav hubs)",
    )
    explore.add_argument("--output-dir", type=Path, default=Path("output/explore"))
    explore.add_argument("--state-file", type=Path, default=None)
    explore.add_argument("--max-pages", type=int, default=500)
    explore.add_argument("--max-depth", type=int, default=6)
    explore.add_argument("--no-images", action="store_true", help="Skip image downloads")
    explore.add_argument(
        "--all-image-roles",
        action="store_true",
        help="Download every artwork role (slow). Default downloads primary posters/heroes only.",
    )
    explore.add_argument("--no-resume", action="store_true", help="Ignore prior explore_state.json")
    _add_common_args(explore, module_page_size=1)

    # Legacy single-page mode without subcommand.
    parser.add_argument("--url-path", default=None, help="Page path (legacy single-page mode)")
    parser.add_argument("--output", default="titles.csv", help="Output CSV (legacy mode)")
    parser.add_argument("--input-json", type=Path, default=None, help="Offline snapshot (legacy)")
    _add_common_args(parser)
    return parser


def _apply_env(cfg: ScrapeConfig, args: argparse.Namespace) -> ScrapeConfig:
    if cfg.endpoint is None:
        cfg.endpoint = os.getenv("VIX_GRAPHQL_ENDPOINT")
    if cfg.query is None and getattr(args, "command", None) != "batch":
        cfg.query = os.getenv("VIX_GRAPHQL_QUERY")

    profile = (getattr(args, "auth_profile", None) or "default").strip().lower()
    # Load profile tokens first, then allow explicit CLI overrides.
    apply_auth_profile(cfg, profile)
    if getattr(args, "auth_token", None):
        cfg.auth_token = args.auth_token
    if getattr(args, "x_vix_user_token", None):
        cfg.x_vix_user_token = args.x_vix_user_token

    device_arg = (getattr(args, "device", None) or os.getenv("VIX_DEVICE") or "").strip()
    if device_arg:
        from vix_scraper.devices import apply_device

        apply_device(cfg, device_arg)

    # Explicit CLI flags always win. Env identity is skipped when --device mapped a catalog client.
    if getattr(args, "app_version", None):
        cfg.app_version = args.app_version
    elif not device_arg:
        cfg.app_version = os.getenv("VIX_APP_VERSION", cfg.app_version)
    if getattr(args, "device_type", None):
        cfg.device_type = args.device_type
    elif not device_arg:
        cfg.device_type = os.getenv("VIX_DEVICE_TYPE", cfg.device_type)
    if getattr(args, "platform", None):
        cfg.platform = args.platform
    elif not device_arg:
        cfg.platform = os.getenv("VIX_PLATFORM", cfg.platform)
    if getattr(args, "user_agent", None):
        cfg.user_agent = args.user_agent
    elif not device_arg:
        cfg.user_agent = os.getenv("VIX_USER_AGENT", cfg.user_agent)
    install = os.getenv("VIX_INSTALLATION_ID")
    if install and install.strip():
        cfg.installation_id = install.strip()
    country = (os.getenv("VIX_COUNTRY") or getattr(cfg, "country", None) or "MX").strip()
    if country:
        cfg.country = country.upper()
    accept_language = (
        os.getenv("VIX_ACCEPT_LANGUAGE") or getattr(cfg, "accept_language", None) or "es-MX,es;q=0.9"
    ).strip()
    if accept_language:
        cfg.accept_language = accept_language
    return cfg


def args_to_page_config(args: argparse.Namespace) -> ScrapeConfig:
    load_dotenv(args.env_file)
    cfg = ScrapeConfig(
        url_path=args.url_path,
        output=Path(args.output),
        endpoint=args.endpoint,
        query_file=args.query_file,
        navigation_query_file=getattr(args, "navigation_query_file", None),
        input_json=getattr(args, "input_json", None),
        page_size=args.page_size,
        module_page_size=args.module_page_size,
        paginate_contents=bool(getattr(args, "paginate_contents", True)),
        timeout=args.timeout,
        retries=args.retries,
        deduplicate=args.deduplicate,
        debug=args.debug,
        download_images=False,
    )
    return _apply_env(cfg, args)


def args_to_batch_config(args: argparse.Namespace) -> ScrapeConfig:
    load_dotenv(args.env_file)
    paths = [p.strip() for p in str(args.url_paths).split(",") if p.strip()]
    query_file = args.query_file
    layout_q = Path("queries/layout.graphql")
    if (query_file is None or Path(query_file) == Path("queries/request.graphql")) and layout_q.is_file():
        # Prefer lean layout query for batch/compare scrapes.
        if getattr(args, "command", None) == "batch":
            query_file = layout_q
    cfg = ScrapeConfig(
        url_path=paths[0] if paths else "",
        url_paths=paths,
        endpoint=args.endpoint,
        query_file=query_file,
        navigation_query_file=args.navigation_query_file,
        page_size=args.page_size,
        module_page_size=args.module_page_size,
        paginate_contents=bool(getattr(args, "paginate_contents", True)),
        contents_first_from_total=bool(getattr(args, "contents_first_from_total", False)),
        timeout=args.timeout,
        retries=args.retries,
        deduplicate=False,
        debug=args.debug,
        download_images=False,
        output_dir=Path(args.output_dir),
        auth_profile_map=parse_auth_profile_map(getattr(args, "auth_profile_map", "")),
    )
    return _apply_env(cfg, args)


def args_to_explore_config(args: argparse.Namespace) -> ScrapeConfig:
    load_dotenv(args.env_file)
    seeds = [p.strip() for p in str(args.seed_paths).split(",") if p.strip()]
    cfg = ScrapeConfig(
        url_path=args.start_path or "",
        endpoint=args.endpoint,
        query_file=args.query_file,
        navigation_query_file=args.navigation_query_file,
        page_size=args.page_size,
        module_page_size=args.module_page_size,
        paginate_contents=bool(getattr(args, "paginate_contents", True)),
        timeout=args.timeout,
        retries=args.retries,
        deduplicate=args.deduplicate,
        debug=args.debug,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        download_images=not args.no_images,
        download_all_image_roles=bool(getattr(args, "all_image_roles", False)),
        resume=not args.no_resume,
        seed_paths=seeds,
        output_dir=Path(args.output_dir),
        state_file=args.state_file,
    )
    cfg = _apply_env(cfg, args)
    if not cfg.endpoint:
        cfg.endpoint = PRODUCTION_ENDPOINT
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        command = args.command
        if command is None and args.url_path:
            command = "page"
        if command == "explore":
            config = args_to_explore_config(args)
            validate_explore_config(config)
            result = run_explore(config)
            print(f"Explore complete via {result.endpoint}")
            if result.auth_note:
                print(result.auth_note)
            print(f"Output dir: {result.output_dir}")
            print(f"Pages visited: {result.pages_visited}")
            print(f"Titles: {result.titles}")
            print(f"Images downloaded: {result.images_downloaded}")
            print(f"Images failed: {result.images_failed}")
            return 0
        if command == "batch":
            config = args_to_batch_config(args)
            if not config.auth_token and not resolve_auth_profile(config.auth_profile).has_auth_token:
                print(missing_profile_help(config.auth_profile), file=sys.stderr)
                return 1
            result = run_batch_scrape(config)
            print(f"Batch complete via {result.endpoint}")
            for note in result.notes:
                print(note)
            for outcome in result.outcomes:
                print(
                    f"[{outcome.status}] {outcome.url_path} "
                    f"profile={outcome.auth_profile} rows={len(outcome.titles)}"
                    + (f" — {outcome.message}" if outcome.message else "")
                )
            if result.combined_path:
                print(f"Combined: {result.combined_path}")
            if result.summary_path:
                print(f"Diff summary: {result.summary_path}")
            # Non-zero if any requested path failed/skipped (partial success still useful)
            hard_fail = any(o.status in {"error", "skipped_missing_auth"} for o in result.outcomes)
            missing = any(o.status == "missing" for o in result.outcomes)
            if hard_fail:
                return 2
            if missing:
                return 3
            return 0
        if command == "page":
            config = args_to_page_config(args)
            validate_config(config)
            path, count = run_scrape(config)
            print(f"Exported {count} titles to {path}")
            return 0
        parser.print_help()
        print(
            "\nExamples:\n"
            "  vix-scraper page --url-path /micro-dramas --output titles.csv\n"
            "  vix-scraper batch --url-paths /ondemandplus,/ondemandpluswc "
            "--auth-profile-map /ondemandpluswc=wc --output-dir output/layout_compare\n"
            "  vix-scraper explore --start-path /ondemandplus --output-dir output/explore --no-images\n",
            file=sys.stderr,
        )
        return 2
    except ScraperError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
