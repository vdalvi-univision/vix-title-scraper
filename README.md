# ViX GraphQL title scraper & website explorer

Scalable, reusable GraphQL scraper for Windows/macOS/Linux. **Zero third-party
dependencies** (Python stdlib only) for maximum compatibility on any PC with
Python 3.10+.

Project path: `C:\Users\vdalvi\Projects\vix-title-scraper`

## Install (once)

```powershell
cd C:\Users\vdalvi\Projects\vix-title-scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Optional tests:

```powershell
python -m pip install -e ".[dev]"
pytest
```

## Configure

```powershell
copy .env.example .env
# Edit .env — set AUTH_TOKEN (and X_VIX_USER_TOKEN if needed)
```

Tokens are sent only as HTTP headers. They are never printed, written to CSV, or logged.

Default production endpoint:

`https://client-api.vix.com/gql/v2`

Staging (often needed when the token issuer is `id-stg`):

`https://client-api.stg.vix.tv/gql/v2`

`explore` tries the configured/production endpoint first and **falls back to staging** if auth/access fails.

## Single-page scrape

```powershell
vix-scraper page `
  --url-path /micro-dramas `
  --query-file queries\request.graphql `
  --output micro_dramas.csv `
  --debug
```

Legacy (still works):

```powershell
vix-scraper --url-path /micro-dramas --output micro_dramas.csv
```

## Batch / layout compare (no images)

Scrapes one or many `urlPath`s into layout-friendly title CSVs (rich metadata, no image/CDN columns). Supports named auth profiles for pages that need a different token.

```powershell
# .env: AUTH_TOKEN=... and optionally AUTH_TOKEN_WC=...
vix-scraper batch `
  --url-paths /ondemandplus,/ondemandpluswc `
  --auth-profile-map /ondemandpluswc=wc `
  --output-dir output\layout_compare `
  --module-page-size 1
```

| Flag | Meaning |
|------|---------|
| `--url-paths` | Comma-separated pages |
| `--auth-profile` | `default` → `AUTH_TOKEN`; `wc` → `AUTH_TOKEN_WC` |
| `--auth-profile-map` | Per-path profile, e.g. `/ondemandpluswc=wc` |
| `--output-dir` | Writes `<slug>_titles.csv`, `combined_titles.csv`, optional `layout_diff_summary.md` |

## Website explore (recommended)

Discovers paths from `uiNavigation` + `clientConfig.defaultUrlPath`, then BFS-crawls
every reachable `uiPage`, paginating modules/content (GraphQL “scroll”), extracting
rich title metadata and downloading images.

```powershell
vix-scraper explore `
  --start-path /ondemandplus `
  --output-dir output\explore `
  --max-pages 500 `
  --max-depth 6 `
  --deduplicate `
  --debug
```

Useful flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--start-path` | none | Extra primary seed |
| `--seed-paths` | common hubs | Comma-separated seeds |
| `--max-pages` | 500 | Visit cap |
| `--max-depth` | 6 | Link-follow depth |
| `--no-images` | off | Skip downloads |
| `--no-resume` | off | Ignore `explore_state.json` |
| `--module-page-size` | 1 (explore) | Modules per request |

### Output layout

```
output/explore/
  pages.csv
  titles.csv
  images.csv
  navigation.csv
  explore_state.json
  images/
    <content>__<role>__<hash>.jpg
```

Progress is saved to `explore_state.json` so long crawls can resume.

## Title layout compare UI (ViX-branded)

Local web app to **scrape / refresh** up to **4** ViX page layouts, keep **scrape history**
(separate CSV folders per run), show live **progress**, and **compare / look up** a title
across those pages. Accent-insensitive (e.g. `guardian` → `Guardián`).

```powershell
# Easiest (uses .venv, frees port 8765, opens browser)
.\tools\run_title_lookup.ps1
# or: tools\run_title_lookup.bat

# Or manually:
.\.venv\Scripts\python.exe tools\title_lookup.py --port 8765
# CLI one-shot:
.\.venv\Scripts\python.exe tools\title_lookup.py "guardian de mi vida"
```

Open **http://127.0.0.1:8765/** — do not open as `file://`.

| Path | Role |
|------|------|
| `tools/title_lookup.py` | UI server + CLI |
| `tools/static/` | ViX logo, HTML/CSS/JS |
| `output/layout_compare/runs/<run_id>/` | Per-scrape CSVs + `scrape_meta.json` |
| `output/layout_compare/latest/` | Copy of the newest successful run |
| `output/layout_compare/history.json` | Sidebar history index (newest first) |

Paste Authorization + User token (+ Installation ID for gated WC) in the form, edit pages
(defaults `/ondemandplus`, `/ondemandpluswc`), then **Scrape / Refresh**. Progress polls
`/api/scrape/status`. “Data as of …” updates only after a scrape finishes successfully.
Optional save to `.env.local` (gitignored). Tokens are never logged.

## Snapshot (offline, no HTTP)

```powershell
vix-scraper page --url-path /micro-dramas --input-json snapshot.json --output out.csv
```

## Library reuse

```python
from pathlib import Path
from vix_scraper import ScrapeConfig, TitleScraper, run_explore

# Single page
cfg = ScrapeConfig(
    url_path="/micro-dramas",
    endpoint="https://client-api.vix.com/gql/v2",
    query_file=Path("queries/request.graphql"),
    output=Path("titles.csv"),
)
path, count = TitleScraper(cfg).scrape_to_csv()

# Full site explore
explore_cfg = ScrapeConfig(
    endpoint="https://client-api.vix.com/gql/v2",
    query_file=Path("queries/request.graphql"),
    navigation_query_file=Path("queries/navigation.graphql"),
    output_dir=Path("output/explore"),
    max_pages=200,
    download_images=True,
)
result = run_explore(explore_cfg)
print(result.pages_visited, result.titles, result.images_downloaded)
```

## Architecture

| Module | Role |
|--------|------|
| `client` | Stdlib HTTP GraphQL POST + retries |
| `pagination` | Module + content cursor walk |
| `discovery` | Navigation / linked `urlPath` extraction |
| `extractor` | JSON → titles / pages / images |
| `images` | Deduped image download |
| `explorer` | BFS site crawl + resume state |
| `exporter` | CSV writers |
| `cli` | `page` and `explore` commands |

## GraphQL notes

- `$urlPath` is `ID!`
- Pagination uses `PaginationParams` (`first` / `after`)
- Rich fields include posters/artwork, cast, ratings, seasons/episodes, duration,
  deeplink/canonical URLs, module tracking metadata, page carousels for discovery
