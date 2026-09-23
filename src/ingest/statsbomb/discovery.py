"""StatsBomb discovery: which competitions/seasons/matches exist.

Competition and season IDs are read from `competitions.json`, never hardcoded:
`resolve_competition` looks a league up by (competition name, country, season
name) and returns the IDs it finds. The IDs we expect for 2015/16 are kept in
`EXPECTED_IDS_2015` only so tests can flag a change upstream.
"""

from __future__ import annotations

from typing import Any

from src.ingest.statsbomb.fetch import fetch_json, statsbomb_cache_root
from src.ingest.team_identity import normalize_team_name
from src.models.statsbomb import StatsBombMatch

# Our league code -> (canonical league name, StatsBomb competition_name, StatsBomb country_name).
STATSBOMB_LEAGUES: dict[str, tuple[str, str, str]] = {
    "EPL": ("ENG-Premier League", "Premier League", "England"),
    "LALIGA": ("ESP-La Liga", "La Liga", "Spain"),
    "SERIEA": ("ITA-Serie A", "Serie A", "Italy"),
    "BUNDESLIGA": ("GER-Bundesliga", "1. Bundesliga", "Germany"),
    "LIGUE1": ("FRA-Ligue 1", "Ligue 1", "France"),
}

# (competition_id, season_id) confirmed against competitions.json for season 2015/16.
EXPECTED_IDS_2015: dict[str, tuple[int, int]] = {
    "EPL": (2, 27),
    "LALIGA": (11, 27),
    "SERIEA": (12, 27),
    "BUNDESLIGA": (9, 27),
    "LIGUE1": (7, 27),
}


def fetch_competitions(*, force: bool = False) -> list[dict[str, Any]]:
    """Fetch (or load cached) `competitions.json`."""
    return fetch_json(
        "competitions.json", statsbomb_cache_root() / "competitions.json", list, force=force
    )


def resolve_league(league: str) -> tuple[str, str, str, str]:
    """Return (our code, canonical name, StatsBomb competition name, country) for a league.

    Accepts our code ("EPL"), StatsBomb's name ("La Liga") or the canonical name.
    """
    key = league.strip().replace(" ", "").upper()
    for code, (canonical, sb_name, country) in STATSBOMB_LEAGUES.items():
        names = {code, sb_name.replace(" ", "").upper(), canonical.replace(" ", "").upper()}
        if key in names:
            return code, canonical, sb_name, country
    raise ValueError(f"Unsupported league '{league}'. Supported: {sorted(STATSBOMB_LEAGUES)}")


def resolve_competition(league: str, season: int, *, force: bool = False) -> tuple[int, int]:
    """Look up (competition_id, season_id) for a men's league season in competitions.json."""
    _, _, sb_name, country = resolve_league(league)
    season_name = f"{season}/{season + 1}"
    found = [
        c
        for c in fetch_competitions(force=force)
        if c["competition_name"] == sb_name
        and c["country_name"] == country
        and c["competition_gender"] == "male"
        and c["season_name"] == season_name
    ]
    if len(found) != 1:
        raise LookupError(
            f"StatsBomb has {len(found)} entries for {sb_name} ({country}) {season_name}; "
            "expected exactly 1. That league-season is not available in the open data."
        )
    return int(found[0]["competition_id"]), int(found[0]["season_id"])


def _parse_match(raw: dict[str, Any], *, league: str, season: int) -> StatsBombMatch:
    return StatsBombMatch(
        match_id=raw["match_id"],
        match_date=raw["match_date"],
        season=str(season),
        league=league,
        home_team=normalize_team_name(raw["home_team"]["home_team_name"]),
        away_team=normalize_team_name(raw["away_team"]["away_team_name"]),
        home_score=raw.get("home_score"),
        away_score=raw.get("away_score"),
        match_week=raw.get("match_week"),
        statsbomb_competition_id=raw["competition"]["competition_id"],
        statsbomb_season_id=raw["season"]["season_id"],
    )


def list_available(league: str, season: int = 2015, *, force: bool = False) -> list[StatsBombMatch]:
    """List every match StatsBomb publishes for a league-season, ordered by date.

    Discovery only: this reads match metadata (one small file, cached under
    `data/raw/statsbomb/matches/`) and does not fetch any events.
    """
    _, canonical, _, _ = resolve_league(league)
    competition_id, season_id = resolve_competition(league, season, force=force)
    raw_matches = fetch_json(
        f"matches/{competition_id}/{season_id}.json",
        statsbomb_cache_root() / "matches" / f"{competition_id}_{season_id}.json",
        list,
        force=force,
    )
    matches = [_parse_match(m, league=canonical, season=season) for m in raw_matches]
    return sorted(matches, key=lambda m: (m.match_date, m.match_id))
