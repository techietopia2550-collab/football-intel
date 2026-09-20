# Football Intel

A football intelligence platform: search players, see stats/strengths/
weaknesses, find similar players, analyse squad needs, and evaluate
player-club fit. Free data sources only, men's football first.

**Project state:** foundation plus three data sources (Understat, football-data.co.uk,
StatsBomb Open Data). The StatsBomb ingest now covers the **full 2015/16 EPL season**
(374/380 matches; see below). Analytics and the Streamlit app are still stubs. See
[CLAUDE.md](./CLAUDE.md) for the conventions every change should follow.

## Data attribution

<img src="src/app/static/statsbomb-logo.png" alt="StatsBomb" width="320">

**Data provided by [StatsBomb](https://statsbomb.com).** Event data comes from the
[StatsBomb Open Data](https://github.com/statsbomb/open-data) repository and is used
under its user agreement, which requires crediting StatsBomb and using their logo on
anything published from this data. Any report, chart or UI built from it must carry the
same credit and logo (the file is `src/app/static/statsbomb-logo.png`).

## Data lineage

There are three sources, each covering a different slice. They are **not** interchangeable
and are not yet linked to one another.

| Source | Table(s) | Coverage | What it gives | Status |
|---|---|---|---|---|
| Understat | `player_season_stats` | **2024/25** EPL ingested (top-5 leagues supported) | xG, xA, shots, key passes, xG chain/buildup, minutes, derived position group. Attacking output only. | Working |
| football-data.co.uk | `match_results` | **2024/25** EPL ingested | Match results: goals, shots, corners, cards | Working |
| StatsBomb Open Data | `matches`, `player_match_stats` | **2015/16 EPL ingested (374/380 matches)**; La Liga, Serie A, Ligue 1 available, not yet ingested | Full event-derived metrics: passing, progressive passes/carries, pressures, tackles, interceptions, blocks, clearances, recoveries, dribbles, xG | Working (EPL) |
| FBref | – | – | – | **Dead** (see below) |

**Honest note: no free current-season defensive data exists.** The only source with defensive
and advanced passing detail that we can use freely and legally is StatsBomb Open Data, and its
full-league coverage is **2015/16**. That means defender and midfielder profiles can be built
and validated on 2015/16 players, but **current (2024/25) defenders cannot be profiled** with
tackles, interceptions or progressive actions. For current players we only have Understat's
attacking numbers. Details, and why the alternatives were ruled out, are in
[BLOCKERS.md](./BLOCKERS.md).

FBref is dead, not just blocked: it sits behind a Cloudflare challenge that plain HTTP cannot
pass, its `soccerdata` reader needs Selenium (which the project forbids), and in January 2026
FBref lost the Opta advanced data anyway. The quarantined code is in `src/ingest/_blocked/`.
WhoScored and Sofascore are Selenium-based too and are treated as blocked.

## Requirements

- Python 3.11+ (developed on 3.12)
- pip (or `uv`)

## Install and configure

```bash
pip install -e ".[dev]"
cp .env.example .env   # all values have local defaults
```

All downloaded data (raw caches, the SQLite database) is written under `data/` in the project
folder. Nothing is stored outside it except `soccerdata`'s own cache under your home directory.

## Run an ingest

Raw downloads are cached in `data/raw/`; re-runs hit the cache unless `--force` is passed.
Runs are idempotent (upserts) and logged to `ingest_log`. `--season` is the season start year
(2024 = 2024/25). All write to `data/football.db` (SQLite, gitignored).

```bash
# Understat: player-season stats
python -m src.ingest.understat --season 2024 --league "ENG-Premier League"

# football-data.co.uk: match results (EPL, LALIGA, SERIEA, BUNDESLIGA, LIGUE1)
python -m src.ingest.football_data_csv --season 2024 --league EPL

# Understat: position groups from per-match rosters (~380 requests, ~25 min,
# resumable). Run AFTER the Understat season ingest.
python -m src.ingest.understat_positions --season 2024 --league "ENG-Premier League"
```

### StatsBomb (2015/16)

```bash
# Download and cache one match (events + lineups) without touching the database
python -m src.ingest.statsbomb.fetch --match-id 3754290

# Fetch, parse and store one match (Manchester City 1-3 Leicester City, 2016-02-06)
python -m src.ingest.statsbomb.ingest --league EPL --match-id 3754290

# Fetch, parse and store a whole league-season (resumable from cache, ~25 min for EPL)
python -m src.ingest.statsbomb.ingest --league EPL --all
```

Leagues: `EPL`, `LALIGA`, `SERIEA`, `LIGUE1`. Competition and season ids are read from
StatsBomb's `competitions.json`, not hardcoded. Downloads are checked before they are trusted:
each file must parse as JSON of the expected shape, is written atomically, and a corrupt cache
file raises an error instead of being used. Requests are spaced 2 s apart with retry and backoff.
A season run never stops for one bad match: failures, invariant violations and lineup
corrections are logged to `data/processed/statsbomb_{failures,violations,corrections}.csv`
instead (see [BLOCKERS.md](./BLOCKERS.md) section 7 for what has actually shown up in the
2015/16 EPL data).

StatsBomb players are stored as `statsbomb:<id>` (their id is also kept in `players.statsbomb_id`)
and are deliberately **not** linked to Understat players. Metric definitions (progressive
threshold, final third, box, etc.) are documented at the top of `src/ingest/statsbomb/parse.py`.

### Analytics on the stored 2015/16 data

Run in this order after the ingest above (each step re-runs safely; all offline):

```bash
python -m src.transform.normalize          # season table, 900+ minutes, fine position groups
python -m src.analytics.percentiles        # within-position percentiles (8 groups)
python -m src.analytics.heatmaps           # player_heatmaps: 12x8 grid JSON per player and type
python -m src.analytics.evaluate_similarity  # writes docs/similarity_eval.md
```

```python
from src.analytics.profile import profile
from src.analytics.similarity import find_similar

profile("Wes Morgan")                                    # strengths / weaknesses, within CB
find_similar("N'Golo Kanté", "2015", n=10,               # heuristic, same position group only
             filters={"exclude_same_team": True})
```

Position groups are `GK, CB, FB, DM, CM, AM, W, ST` (`position_group_fine`). Metric labels (fact /
proxy / approximation) and what each metric does NOT measure are in
[ANALYTICS_DECISIONS.md](./ANALYTICS_DECISIONS.md). Nothing here claims to measure leadership, trust
or positioning intelligence.

### Disk discipline

Every cache, temp dir and library default is pointed at `.cache/` (or `data/raw/soccerdata`) on the
project drive by `src/utils/disk_bootstrap.py`, imported first by `src/__init__.py`, every CLI,
`main.py` and `tests/conftest.py`. See [docs/DISK.md](./docs/DISK.md).

## Run the tests

```bash
pytest
```

Tests are offline (`tests/fixtures/` and small hand-built matches); they never touch the network
or the real database. One test additionally checks the real cached StatsBomb match when it is
present locally, and is skipped otherwise.

## Run the app

```bash
streamlit run src/app/main.py
```

The app is a placeholder; no analytics are wired in yet.

## Known issues

Full details in [BLOCKERS.md](./BLOCKERS.md). Headlines:

- **No current-season defensive data** (see the honest note above).
- **StatsBomb EPL 2015/16 is ingested (374/380 matches).** 6 matches failed on a lineup/
  substitution-time mismatch and are not in the database; 1 stored match has an unresolved
  minutes violation (an injured player left the pitch with no substitute available). Full
  detail, exact match ids and root causes are in [BLOCKERS.md](./BLOCKERS.md) section 7.
  La Liga, Serie A and Ligue 1 2015/16 are available from the same source but not yet ingested.
- `position_group` (GK/DEF/MID/FWD) on Understat players comes from per-match Understat rosters
  (most starter minutes wins). It is NULL for 70 substitute-only players (reasons in
  `data/processed/position_nulls.csv`); exclude those from position-based analysis and apply a
  minutes threshold. StatsBomb players get `position_group` (GK/DEF/MID/FWD) and `position_group_fine`
  (GK/CB/FB/DM/CM/AM/W/ST) mapped from the on-pitch `position_played`; 0 of the 10,302 stored
  player-match rows have a NULL value in either. Ranking and similarity use the fine group.
- Team names are normalized for 26 clubs across both the EPL 2024/25 and 2015/16 seasons
  (`src/ingest/team_identity.py`); other leagues, and clubs outside those two seasons, pass
  through unchanged.
- No player identity resolution across sources. Understat (2024/25) and StatsBomb (2015/16)
  players are separate id spaces.
