"""Team-name normalization, and that both sources land on one teams row per club."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session

from src.ingest.football_data_csv import _to_records as football_data_records
from src.ingest.football_data_csv import _upsert_records as upsert_matches
from src.ingest.football_data_csv import parse_csv
from src.ingest.team_identity import (
    CANONICAL_TEAMS,
    EPL_2015_16,
    EPL_2024_25,
    is_known_team,
    normalize_team_name,
)
from src.ingest.understat import _to_records as understat_records
from src.ingest.understat import _upsert_records as upsert_players
from src.models.schema import Team, init_db

FIXTURES = Path(__file__).parent / "fixtures"
LEAGUE = "ENG-Premier League"

# Every distinct team spelling observed in each source for EPL 2024/25.
UNDERSTAT_NAMES = [
    "Arsenal",
    "Aston Villa",
    "Bournemouth",
    "Brentford",
    "Brighton",
    "Chelsea",
    "Crystal Palace",
    "Everton",
    "Fulham",
    "Ipswich",
    "Leicester",
    "Liverpool",
    "Manchester City",
    "Manchester United",
    "Newcastle United",
    "Nottingham Forest",
    "Southampton",
    "Tottenham",
    "West Ham",
    "Wolverhampton Wanderers",
]
FOOTBALL_DATA_NAMES = [
    "Arsenal",
    "Aston Villa",
    "Bournemouth",
    "Brentford",
    "Brighton",
    "Chelsea",
    "Crystal Palace",
    "Everton",
    "Fulham",
    "Ipswich",
    "Leicester",
    "Liverpool",
    "Man City",
    "Man United",
    "Newcastle",
    "Nott'm Forest",
    "Southampton",
    "Tottenham",
    "West Ham",
    "Wolves",
]


def test_each_season_has_20_clubs_and_the_canonical_set_is_their_union() -> None:
    assert len(EPL_2015_16) == len(EPL_2024_25) == 20
    assert CANONICAL_TEAMS == EPL_2015_16 | EPL_2024_25
    assert len(CANONICAL_TEAMS) == 26  # 14 clubs are in both seasons
    assert len(EPL_2015_16 & EPL_2024_25) == 14


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("Man United", "Manchester United"),
        ("Manchester United", "Manchester United"),
        ("Man City", "Manchester City"),
        ("Nott'm Forest", "Nottingham Forest"),
        ("Nott\u2019m Forest", "Nottingham Forest"),
        ("Nottingham Forest", "Nottingham Forest"),
        ("Spurs", "Tottenham Hotspur"),
        ("Tottenham", "Tottenham Hotspur"),
        ("  wolves ", "Wolverhampton Wanderers"),
    ],
)
def test_aliases_resolve(raw: str, canonical: str) -> None:
    assert normalize_team_name(raw) == canonical


def test_unknown_team_passes_through_stripped() -> None:
    assert normalize_team_name("  Ajax ") == "Ajax"
    assert not is_known_team("Ajax")


def test_normalization_is_idempotent() -> None:
    for name in CANONICAL_TEAMS:
        assert normalize_team_name(name) == name


def test_both_sources_normalize_to_the_same_20_teams() -> None:
    understat = {normalize_team_name(n) for n in UNDERSTAT_NAMES}
    football_data = {normalize_team_name(n) for n in FOOTBALL_DATA_NAMES}
    assert understat == football_data == set(EPL_2024_25)


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'test.db'}")


def test_teams_table_has_one_row_per_club_across_sources(engine: Engine) -> None:
    init_db(engine)
    u = understat_records(
        pd.read_parquet(FIXTURES / "understat_sample.parquet"), season=2024, league=LEAGUE
    )
    m = football_data_records(
        parse_csv((FIXTURES / "football_data_sample.csv").read_bytes()), season=2024, league="EPL"
    )
    expected = {r.team for r in u if r.team} | {t for r in m for t in (r.home_team, r.away_team)}

    with Session(engine) as session:
        upsert_players(session, u)
        upsert_matches(session, m)
        upsert_matches(session, m)  # rerun must not add rows
        names = list(session.scalars(select(Team.name)))
        assert session.scalar(select(func.count()).select_from(Team)) == len(expected)

    assert len(names) == len(set(names))  # no club twice
    assert {"Manchester United", "Newcastle United", "Wolverhampton Wanderers"} <= set(names)
    assert not {"Man United", "Newcastle", "Wolves"} & set(names)


# --- 2015/16 (StatsBomb) ------------------------------------------------------------------

# Every distinct team name in StatsBomb's 2015/16 Premier League matches.json (20 names, 38
# matches each), read from the real file.
STATSBOMB_2015_16_NAMES = [
    "AFC Bournemouth",
    "Arsenal",
    "Aston Villa",
    "Chelsea",
    "Crystal Palace",
    "Everton",
    "Leicester City",
    "Liverpool",
    "Manchester City",
    "Manchester United",
    "Newcastle United",
    "Norwich City",
    "Southampton",
    "Stoke City",
    "Sunderland",
    "Swansea City",
    "Tottenham Hotspur",
    "Watford",
    "West Bromwich Albion",
    "West Ham United",
]


def test_statsbomb_2015_16_names_normalize_to_the_20_clubs() -> None:
    normalized = {normalize_team_name(n) for n in STATSBOMB_2015_16_NAMES}
    assert normalized == set(EPL_2015_16)
    assert all(is_known_team(n) for n in STATSBOMB_2015_16_NAMES)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("West Brom", "West Bromwich Albion"),
        ("west bromwich albion", "West Bromwich Albion"),
        ("Norwich", "Norwich City"),
        ("Stoke", "Stoke City"),
        ("Swansea", "Swansea City"),
        ("Leicester", "Leicester City"),
        ("AFC Bournemouth", "Bournemouth"),
        ("  WATFORD ", "Watford"),
    ],
)
def test_2015_16_aliases_resolve(raw: str, canonical: str) -> None:
    assert normalize_team_name(raw) == canonical


CACHED_MATCHES = Path("data/raw/statsbomb/matches/2_27.json")


@pytest.mark.skipif(not CACHED_MATCHES.exists(), reason="matches.json not cached locally")
def test_every_team_in_the_real_matches_file_normalizes_to_the_20_clubs() -> None:
    import json

    matches = json.loads(CACHED_MATCHES.read_text(encoding="utf-8"))
    assert len(matches) == 380
    names = {m["home_team"]["home_team_name"] for m in matches}
    names |= {m["away_team"]["away_team_name"] for m in matches}
    assert {normalize_team_name(n) for n in names} == set(EPL_2015_16)
    assert names == set(STATSBOMB_2015_16_NAMES)  # the hard-coded list matches the real file
