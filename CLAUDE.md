# CLAUDE.md — Football Intel

## Purpose

Football Intel is a football intelligence platform for scouting and squad
planning: search players by season or career, view their stats and
style profile, find similar players, analyse a club's squad needs, and
evaluate player-club fit across tactics, budget, and situation. It targets
men's football first, is built entirely on free data sources, and is
designed so its analytics can eventually support real scouting and
sporting-director workflows.

## Tech stack and why

- **soccerdata** (v1.9.1, verified installed) — unified scraper/wrapper for
  free football data sources. Exposes 8 source classes: `FBref`, `Understat`,
  `WhoScored`, `Sofascore`, `ClubElo`, `ESPN`, `SoFIFA`, `MatchHistory`
  (football-data.co.uk). Avoids hand-rolling scrapers for each site.
- **pandas / numpy** — tabular data wrangling and numeric computation.
- **scikit-learn** — percentile scaling, clustering, similarity search for
  player comparisons.
- **plotly** — interactive charts (radar/percentile charts, etc.) that work
  natively inside Streamlit.
- **streamlit** — fastest path to an interactive analyst-facing UI with zero
  frontend build tooling.
- **pydantic (v2) / pydantic-settings** — typed schemas for every data record
  and typed, validated app configuration from environment variables.
- **python-dotenv** — local `.env` loading (via pydantic-settings).
- **requests / beautifulsoup4 / lxml** — fallback scraping/parsing for
  sources soccerdata doesn't cover.
- **sqlalchemy** — typed access layer to the local SQLite database.
- **pyarrow** — Parquet read/write for processed datasets.
- **pytest** — tests. **ruff** — linting/formatting. **mypy** — static typing.
- **loguru** — structured, low-ceremony logging.

## Folder responsibilities

- `/data/raw` — cached raw scraper/API output, one file per fetch. Gitignored.
  Never mutated after write; re-fetch instead of editing.
- `/data/processed` — cleaned, normalized tables (Parquet/SQLite), the output
  of `/src/transform`. Safe to regenerate from `/data/raw` at any time.
- `/data/football.db` — local SQLite database. Gitignored.
- `/src/ingest` — data collection only. Talks to soccerdata / requests /
  BeautifulSoup, writes raw output to `/data/raw`. No cleaning, no analysis.
- `/src/transform` — cleaning and normalization. Reads `/data/raw`, writes
  `/data/processed`. Enforces the schemas in `/src/models`.
- `/src/analytics` — all metrics, percentiles, similarity search, squad-needs
  and fit-evaluation logic. This is the only place analytics logic may live.
- `/src/models` — pydantic dataclasses/schemas shared across layers.
- `/src/app` — Streamlit UI. Presentation only: reads processed data and
  calls into `/src/analytics`. Never computes stats itself.
- `/src/reports` — PDF/CSV export of analytics results.
- `/src/utils` — config, logging, rate limiting/caching, and other shared
  infrastructure with no domain logic.
- `/tests` — pytest suite, mirrors `/src` structure.
- `/notebooks` — exploration only. Never import notebook code into `/src`,
  and never put production logic in a notebook.
- `/docs` — project documentation.

## Coding conventions

- Type hints on every function signature, including return types.
- Use pydantic models (`/src/models`) for any structured data crossing a
  module boundary — no bare dicts for domain records.
- Use `pathlib.Path` for all filesystem paths. No hardcoded path strings.
- No hardcoded paths: import `settings` from `src.utils.config` for
  DB path, cache dir, etc.
- No secrets in code, ever. Secrets live in `.env` (gitignored), surfaced
  via `src.utils.config.settings`. `.env.example` documents the keys.
- Prefer small, pure functions in `/src/analytics` and `/src/transform` so
  they're easy to unit test without network access.

## Data conventions

- Every scraper/ingest function caches its raw output to `/data/raw` before
  any further processing — re-running ingest should hit the cache, not the
  network, unless explicitly forced.
- Every processed table includes at minimum: `player_id`, `season`,
  `league`, `source` columns, so records can always be traced back to where
  they came from and cross-referenced across sources.
- Respect source rate limits: any outbound call to an external source goes
  through `src.utils.ratelimit.rate_limited`.

## Hard rules

1. **Never write analytics logic in `/src/app`.** The app layer only renders
   what `/src/analytics` computes.
2. **Always check for an existing file before creating a new one.** Extend
   or edit what's there rather than duplicating it.
3. **Always run tests before declaring a task done.** `pytest` must pass.

## How to run

```bash
# Install (editable + dev deps)
pip install -e ".[dev]"

# Configure
cp .env.example .env   # then fill in any values you need

# Test
pytest

# Launch the app
streamlit run src/app/main.py
```
