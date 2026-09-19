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
from src.ingest.team_identity import CANONICAL_TEAMS, is_known_team, normalize_team_name
from src.ingest.understat import _to_records as understat_records
from src.ingest.understat import _upsert_records as upsert_players
from src.models.schema import Team, init_db

FIXTURES = Path(__file__).parent / "fixtures"
LEAGUE = "ENG-Premier League"

# Every distinct team spelling observed in each source for EPL 2024/25.
UNDERSTAT_NAMES = [
    "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Chelsea",
    "Crystal Palace", "Everton", "Fulham", "Ipswich", "Leicester", "Liverpool",
    "Manchester City", "Manchester United", "Newcastle United", "Nottingham Forest",
    "Southampton", "Tottenham", "West Ham", "Wolverhampton Wanderers",
]
FOOTBALL_DATA_NAMES = [
    "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Chelsea",
    "Crystal Palace", "Everton", "Fulham", "Ipswich", "Leicester", "Liverpool",
    "Man City", "Man United", "Newcastle", "Nott'm Forest", "Southampton", "Tottenham",
    "West Ham", "Wolves",
]


def test_there_are_20_canonical_teams() -> None:
    assert len(CANONICAL_TEAMS) == 20


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
    assert understat == football_data == set(CANONICAL_TEAMS)


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'test.db'}")


def test_teams_table_has_one_row_per_club_across_sources(engine: Engine) -> None:
    init_db(engine)
    u = understat_records(pd.read_parquet(FIXTURES / "understat_sample.parquet"), season=2024, league=LEAGUE)
    m = football_data_records(parse_csv((FIXTURES / "football_data_sample.csv").read_bytes()), season=2024, league="EPL")
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
