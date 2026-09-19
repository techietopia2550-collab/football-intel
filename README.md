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

# Understat: position groups from per-match rosters (~380 requests, ~25 min,
# resumable). Run AFTER the Understat season ingest.
python -m src.ingest.understat_positions --season 2024 --league "ENG-Premier League"
```

`--season` is the season start year (2024 = 2024/25). All write to
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
- `position_group` (GK/DEF/MID/FWD) comes from per-match Understat rosters
  (most starter minutes wins). It is NULL for 70 substitute-only players
  (reasons in `data/processed/position_nulls.csv`); exclude those from
  position-based analysis and apply a minutes threshold (DEF is 172 overall
  but 108 at 900+ starter minutes). Limitations in BLOCKERS.md.
- Team names are normalized for the 20 EPL 2024/25 clubs only
  (`src/ingest/team_identity.py`); other leagues pass through unchanged.
  There is no player identity resolution across sources yet.
