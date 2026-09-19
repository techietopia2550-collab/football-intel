# Football Intel

A football intelligence platform: search players, see stats/strengths/
weaknesses, find similar players, analyse squad needs, and evaluate
player-club fit. Free data sources only, men's football first.

**Project state:** foundation + two working ingest sources (Understat
player-season stats, football-data.co.uk match results). Analytics and the
Streamlit app are still stubs. FBref is blocked (see below). See
[CLAUDE.md](./CLAUDE.md) for the conventions every change should follow.

## Requirements

- Python 3.11+ (developed on 3.12)
- pip (or `uv`)

## Install and configure

```bash
pip install -e ".[dev]"
cp .env.example .env   # all values have local defaults
```

## Data sources

| Source | Table | Status | Notes |
|---|---|---|---|
| Understat (via `soccerdata`) | `player_season_stats` | **Working** | Top-5 leagues. xG, xA, shots, key passes, xG chain/buildup. Plain HTTP, but needs the `tls_requests` native DLL (see BLOCKERS.md). |
| football-data.co.uk CSV | `match_results` | **Working** | Match-level only: goals, shots, corners, cards. Plain `requests`, no scraper. No player data. |
| FBref | – | **BLOCKED** | `soccerdata.FBref` is Selenium-based. Code quarantined in `src/ingest/_blocked/`. See [BLOCKERS.md](./BLOCKERS.md). |

WhoScored and Sofascore (also Selenium-based in soccerdata) are treated as
blocked for the same reason.

### Run an ingest

Leagues use soccerdata names for Understat; the CSV ingest takes short names.
Raw downloads are cached in `data/raw/`; re-runs hit the cache unless
`--force` is passed. Runs are idempotent (upserts) and logged to `ingest_log`.

```bash
# Understat: player-season stats
python -m src.ingest.understat --season 2024 --league "ENG-Premier League"

# football-data.co.uk: match results (EPL, LALIGA, SERIEA, BUNDESLIGA, LIGUE1)
python -m src.ingest.football_data_csv --season 2024 --league EPL
```

`--season` is the season start year (2024 = 2024/25). Both write to
`data/football.db` (SQLite, gitignored).

## Run the tests

```bash
pytest
```

Tests are offline and fixture-based (`tests/fixtures/`); they never touch the
network or the real database.

## Run the app

```bash
streamlit run src/app/main.py
```

The app is a placeholder; no analytics are wired in yet.

## Known issues

Full details in [BLOCKERS.md](./BLOCKERS.md). Headlines:

- FBref ingest blocked (Selenium).
- Understat `position_group` is NULL for most outfield players (position
  mapping bug, covered by a documenting test).
- Team names differ between sources (`Man United` vs `Manchester United`);
  no cross-source team/player identity resolution exists yet.
- No `identity.py`; player IDs are source-prefixed (`understat:<id>`).
