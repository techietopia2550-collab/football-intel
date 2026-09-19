"""Offline tests for per-match position derivation (no network, temp DB only)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from src.ingest.understat import _upsert_records as upsert_players
from src.ingest.understat_positions import (
    NULLS_COLUMNS,
    POSITION_CODE_TO_GROUP,
    apply_position_groups,
    derive_position_groups,
)
from src.models.player import PlayerSeasonRecord
from src.models.schema import Player, PlayerSeasonStats, init_db

FIXTURE = Path(__file__).parent / "fixtures" / "understat_match_positions_sample.parquet"
LEAGUE = "ENG-Premier League"


def _rows(*appearances: tuple[int, str, str, int]) -> pd.DataFrame:
    """(player_id, player, position, minutes) tuples -> per-match frame."""
    return pd.DataFrame(appearances, columns=["player_id", "player", "position", "minutes"])


# --- The five known players, derived from real 2024/25 per-match rosters ------------

@pytest.mark.parametrize(
    ("player", "expected"),
    [
        ("Mohamed Salah", "FWD"),
        ("Virgil van Dijk", "DEF"),
        ("Rodri", "MID"),
        ("Alisson", "GK"),
        ("Bryan Mbeumo", "FWD"),
    ],
)
def test_known_player_position_group(player: str, expected: str) -> None:
    assignments = {a.player_name: a for a in derive_position_groups(pd.read_parquet(FIXTURE))}
    assert assignments[player].position_group == expected, assignments[player].minutes_by_group


# --- Derivation rules ---------------------------------------------------------------

def test_group_with_most_starter_minutes_wins() -> None:
    df = _rows((1, "A", "DC", 90), (1, "A", "DMC", 90), (1, "A", "DMC", 90))
    (a,) = derive_position_groups(df)
    assert a.position_group == "MID"
    assert a.minutes_by_group == {"DEF": 90, "MID": 180}


def test_bench_appearances_do_not_count() -> None:
    df = _rows((1, "A", "Sub", 60), (1, "A", "Sub", 60), (1, "A", "GK", 90))
    (a,) = derive_position_groups(df)
    assert a.position_group == "GK"
    assert a.starter_minutes == 90


def test_substitute_only_player_is_null_with_reason() -> None:
    (a,) = derive_position_groups(_rows((1, "A", "Sub", 30), (1, "A", "Sub", 12)))
    assert a.position_group is None
    assert "substitute" in (a.null_reason or "")


def test_exact_tie_is_null_not_guessed() -> None:
    (a,) = derive_position_groups(_rows((1, "A", "DC", 90), (1, "A", "FW", 90)))
    assert a.position_group is None
    assert "tie" in (a.null_reason or "")


def test_unmapped_position_code_is_ignored_not_guessed() -> None:
    (a,) = derive_position_groups(_rows((1, "A", "XYZ", 90)))
    assert a.position_group is None


def test_code_map_only_uses_known_groups() -> None:
    assert set(POSITION_CODE_TO_GROUP.values()) == {"GK", "DEF", "MID", "FWD"}


def test_fixture_has_no_unmapped_codes() -> None:
    codes = set(pd.read_parquet(FIXTURE)["position"].dropna()) - {"Sub"}
    assert codes <= set(POSITION_CODE_TO_GROUP)


# --- Applying to the DB -------------------------------------------------------------

@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    return eng


def _record(player_id: int, name: str, position: str) -> PlayerSeasonRecord:
    return PlayerSeasonRecord(
        player_id=f"understat:{player_id}",
        canonical_name=name,
        source="understat",
        season="2024",
        league=LEAGUE,
        team="Arsenal",
        position=position,
        minutes=900,
        raw_stats={},
    )


def test_apply_sets_groups_clears_stale_and_logs_nulls(engine: Engine) -> None:
    records = [_record(1, "Starter", "D S"), _record(2, "Bench Only", "S"), _record(3, "Missing", "F")]
    assignments = derive_position_groups(_rows((1, "Starter", "DC", 90), (2, "Bench Only", "Sub", 20)))
    with Session(engine) as session:
        upsert_players(session, records)
        session.execute(PlayerSeasonStats.__table__.update().values(position_group="DEF"))  # stale labels
        nulls = apply_position_groups(session, assignments, season=2024, league=LEAGUE)
        groups = dict(session.execute(select(PlayerSeasonStats.player_id, PlayerSeasonStats.position_group)).all())

    assert groups == {"understat:1": "DEF", "understat:2": None, "understat:3": None}
    assert list(nulls.columns) == NULLS_COLUMNS
    reasons = dict(zip(nulls["player_id"], nulls["reason"], strict=True))
    assert set(reasons) == {"understat:2", "understat:3"}
    assert "substitute" in reasons["understat:2"]
    assert "not present" in reasons["understat:3"]


def test_reingest_does_not_clobber_derived_position_group(engine: Engine) -> None:
    records = [_record(1, "Starter", "D S")]
    assignments = derive_position_groups(_rows((1, "Starter", "FW", 90)))
    with Session(engine) as session:
        upsert_players(session, records)
        apply_position_groups(session, assignments, season=2024, league=LEAGUE)
        upsert_players(session, records)  # what a re-run of the season ingest does
        assert session.scalar(select(PlayerSeasonStats.position_group)) == "FWD"
        assert session.scalar(select(Player.position_group)) == "FWD"
