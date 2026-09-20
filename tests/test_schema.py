"""Offline schema tests against a throwaway SQLite file (never the real DB)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from src.ingest.football_data_csv import _upsert_records as upsert_matches
from src.ingest.understat import _upsert_records as upsert_players
from src.models.match import MatchRecord
from src.models.player import PlayerSeasonRecord
from src.models.schema import MatchResult, Player, PlayerSeasonStats, init_db

EXPECTED_TABLES = {"players", "player_season_stats", "teams", "match_results", "ingest_log"}


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    return create_engine(f"sqlite:///{tmp_path / 'test.db'}")


def test_init_db_is_idempotent(engine: Engine) -> None:
    init_db(engine)
    init_db(engine)
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_tables_have_expected_columns(engine: Engine) -> None:
    init_db(engine)
    insp = inspect(engine)

    def cols(table: str) -> set[str]:
        return {c["name"] for c in insp.get_columns(table)}

    # Every processed stats table carries the traceability columns (CLAUDE.md).
    assert {"player_id", "season", "league", "source"} <= cols("player_season_stats")
    assert {"season", "league", "source"} <= cols("match_results")
    assert {
        "match_date",
        "home_team",
        "away_team",
        "home_goals",
        "away_goals",
        "home_shots",
        "away_shots",
        "home_shots_on_target",
        "away_shots_on_target",
        "home_corners",
        "away_corners",
        "home_cards_yellow",
        "away_cards_yellow",
        "home_cards_red",
        "away_cards_red",
    } <= cols("match_results")
    assert {"source", "season", "league", "rows", "status", "started_at", "error"} <= cols(
        "ingest_log"
    )


def _player_record(name: str = "Test Player", minutes: int = 900) -> PlayerSeasonRecord:
    return PlayerSeasonRecord(
        player_id="understat:1",
        canonical_name=name,
        source="understat",
        season="2024",
        league="ENG-Premier League",
        team="Testham",
        minutes=minutes,
        raw_stats={"xg": 1.5},
    )


def test_player_upsert_does_not_duplicate(engine: Engine) -> None:
    init_db(engine)
    with Session(engine) as session:
        upsert_players(session, [_player_record()])
        upsert_players(session, [_player_record(minutes=1200)])  # rerun with a changed value
        assert session.scalar(select(func.count()).select_from(PlayerSeasonStats)) == 1
        assert session.scalar(select(func.count()).select_from(Player)) == 1
        assert session.scalar(select(PlayerSeasonStats.minutes)) == 1200  # updated, not duplicated


def test_match_upsert_does_not_duplicate(engine: Engine) -> None:
    init_db(engine)
    rec = MatchRecord(
        source="football_data_csv",
        season="2024",
        league="ENG-Premier League",
        match_date="2024-08-16",
        home_team="A",
        away_team="B",
        home_goals=1,
        away_goals=0,
    )
    with Session(engine) as session:
        upsert_matches(session, [rec])
        upsert_matches(session, [rec.model_copy(update={"home_goals": 3})])
        assert session.scalar(select(func.count()).select_from(MatchResult)) == 1
        assert session.scalar(select(MatchResult.home_goals)) == 3


def test_migration_adds_fine_position_and_advanced_columns_idempotently(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    init_db(eng)
    new_columns = ["position_group_fine", "aerials_won", "aerials_lost", "passes_received",
                   "passes_received_under_pressure", "progressive_passes_received", "def_actions",
                   "def_action_x_sum", "def_actions_own_box", "cross_clearances"]  # fmt: skip
    with eng.begin() as conn:  # an older database, from before these columns
        for column in new_columns:
            conn.execute(text(f"ALTER TABLE player_match_stats DROP COLUMN {column}"))
    for _ in range(2):  # the second run must be a no-op
        init_db(eng)
    have = {c["name"] for c in inspect(eng).get_columns("player_match_stats")}
    assert set(new_columns) <= have
