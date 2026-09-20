"""Offline tests for StatsBomb discovery, identity, migration and database writes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from src.ingest.statsbomb import discovery
from src.ingest.statsbomb.identity import resolve_player, statsbomb_player_id
from src.ingest.statsbomb.ingest import upsert_match, upsert_player_match_stats
from src.ingest.statsbomb.parse import parse_match
from src.models.schema import Match, Player, PlayerMatchStats, init_db
from tests.statsbomb_fixtures import MATCH, build_events, build_lineups

# --- discovery --------------------------------------------------------------------------

FAKE_COMPETITIONS: list[dict[str, Any]] = [
    {
        "competition_id": 2,
        "season_id": 27,
        "competition_name": "Premier League",
        "country_name": "England",
        "competition_gender": "male",
        "season_name": "2015/2016",
    },
    {  # a same-named season for a different competition must not be picked up
        "competition_id": 99,
        "season_id": 27,
        "competition_name": "Premier League",
        "country_name": "Egypt",
        "competition_gender": "male",
        "season_name": "2015/2016",
    },
    {
        "competition_id": 11,
        "season_id": 27,
        "competition_name": "La Liga",
        "country_name": "Spain",
        "competition_gender": "male",
        "season_name": "2015/2016",
    },
]


@pytest.fixture()
def fake_competitions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "fetch_competitions", lambda **_kw: FAKE_COMPETITIONS)


def test_ids_are_read_from_competitions_json(fake_competitions: None) -> None:
    assert discovery.resolve_competition("EPL", 2015) == (2, 27)
    assert discovery.resolve_competition("La Liga", 2015) == (11, 27)


def test_unavailable_league_season_raises(fake_competitions: None) -> None:
    with pytest.raises(LookupError, match="not available"):
        discovery.resolve_competition("EPL", 2024)  # no 2024/25 in the open data
    with pytest.raises(LookupError):
        discovery.resolve_competition("SERIEA", 2015)  # absent from the fake list


@pytest.mark.parametrize("name", ["EPL", "epl", "ENG-Premier League", "Premier League"])
def test_league_aliases(name: str) -> None:
    assert discovery.resolve_league(name)[:2] == ("EPL", "ENG-Premier League")


def test_unsupported_league() -> None:
    with pytest.raises(ValueError, match="Unsupported league"):
        discovery.resolve_league("Eredivisie")


def test_match_metadata_is_parsed_and_team_names_normalized() -> None:
    raw = {
        "match_id": 42,
        "match_date": "2016-02-06",
        "home_team": {"home_team_name": "Manchester City"},
        "away_team": {"away_team_name": "Tottenham Hotspur"},
        "home_score": 1,
        "away_score": 3,
        "match_week": 25,
        "competition": {"competition_id": 2},
        "season": {"season_id": 27},
    }
    m = discovery._parse_match(raw, league="ENG-Premier League", season=2015)
    assert (m.match_id, m.season, m.match_week) == (42, "2015", 25)
    assert (m.statsbomb_competition_id, m.statsbomb_season_id) == (2, 27)
    assert (m.home_score, m.away_score) == (1, 3)


CACHED_COMPETITIONS = Path("data/raw/statsbomb/competitions.json")


@pytest.mark.skipif(not CACHED_COMPETITIONS.exists(), reason="competitions.json not cached locally")
def test_expected_2015_ids_match_the_real_competitions_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = json.loads(CACHED_COMPETITIONS.read_text(encoding="utf-8"))
    monkeypatch.setattr(discovery, "fetch_competitions", lambda **_kw: real)
    for code, ids in discovery.EXPECTED_IDS_2015.items():
        assert discovery.resolve_competition(code, 2015) == ids


# --- database ---------------------------------------------------------------------------


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    return eng


def test_new_tables_and_columns_exist(engine: Engine) -> None:
    insp = inspect(engine)
    assert {"matches", "player_match_stats"} <= set(insp.get_table_names())
    assert "statsbomb_id" in {c["name"] for c in insp.get_columns("players")}
    stats_cols = {c["name"] for c in insp.get_columns("player_match_stats")}
    assert {
        "match_id", "player_id", "player_name", "team", "season", "league", "opponent",
        "position_played", "minutes", "passes_attempted", "progressive_passes", "carries",
        "pressures", "tackles", "interceptions", "blocks", "clearances", "ball_recoveries",
        "dribbles_attempted", "shots", "goals", "xg", "fouls_committed", "yellow_cards",
        "red_cards",
    } <= stats_cols  # fmt: skip
    match_cols = {c["name"] for c in insp.get_columns("matches")}
    assert {
        "statsbomb_competition_id",
        "statsbomb_season_id",
        "home_score",
        "away_score",
    } <= match_cols


def test_migration_adds_statsbomb_id_to_an_existing_players_table(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as conn:  # the players table as it was before StatsBomb
        conn.execute(
            text(
                "CREATE TABLE players (player_id VARCHAR PRIMARY KEY, canonical_name VARCHAR "
                "NOT NULL, fbref_id VARCHAR, understat_id VARCHAR, transfermarkt_id VARCHAR, "
                "birth_year INTEGER, nationality VARCHAR, position_group VARCHAR)"
            )
        )
        conn.execute(
            text("INSERT INTO players (player_id, canonical_name) VALUES ('understat:1', 'Old')")
        )

    init_db(eng)
    init_db(eng)  # idempotent: a second run must not fail on the existing column

    assert "statsbomb_id" in {c["name"] for c in inspect(eng).get_columns("players")}
    with Session(eng) as session:
        assert session.scalar(select(Player.canonical_name)) == "Old"  # data preserved


def test_resolve_player_creates_a_statsbomb_row(engine: Engine) -> None:
    with Session(engine) as session:
        pid = resolve_player(session, 3961, "N'Golo Kanté")
        session.commit()
        row = session.get(Player, pid)
        assert pid == statsbomb_player_id(3961) == "statsbomb:3961"
        assert (row.canonical_name, row.statsbomb_id, row.understat_id) == (
            "N'Golo Kanté",
            "3961",
            None,
        )


def test_resolve_player_is_stable_and_respects_an_existing_link(engine: Engine) -> None:
    with Session(engine) as session:
        assert resolve_player(session, 5, "A") == resolve_player(session, 5, "A")
        # a future cross-source link: an Understat row that already carries the StatsBomb id
        session.add(Player(player_id="understat:77", canonical_name="Linked", statsbomb_id="9"))
        session.commit()
        assert resolve_player(session, 9, "Whatever") == "understat:77"
        assert session.scalar(select(func.count()).select_from(Player)) == 2  # no duplicate created


def test_match_and_player_stats_upserts_are_idempotent(engine: Engine) -> None:
    records = parse_match(build_events(), build_lineups(), MATCH)
    with Session(engine) as session:
        for _ in range(2):
            upsert_match(session, MATCH)
            assert upsert_player_match_stats(session, records) == len(records) == 5
            session.commit()
        assert session.scalar(select(func.count()).select_from(Match)) == 1
        assert session.scalar(select(func.count()).select_from(PlayerMatchStats)) == 5
        assert session.scalar(select(func.count()).select_from(Player)) == 5
        alpha = session.scalar(
            select(PlayerMatchStats).where(PlayerMatchStats.player_name == "Alpha")
        )
        assert (alpha.player_id, alpha.passes_completed, alpha.xg, alpha.minutes) == (
            "statsbomb:10", 5, 0.85, 90.5,
        )  # fmt: skip


def test_rerun_updates_changed_values_instead_of_duplicating(engine: Engine) -> None:
    records = parse_match(build_events(), build_lineups(), MATCH)
    with Session(engine) as session:
        upsert_player_match_stats(session, records)
        changed = [r.model_copy(update={"tackles": 99}) for r in records]
        upsert_player_match_stats(session, changed)
        session.commit()
        assert session.scalar(select(func.count()).select_from(PlayerMatchStats)) == 5
        assert session.scalar(select(func.max(PlayerMatchStats.tackles))) == 99


def test_statsbomb_players_stay_out_of_the_understat_id_space(engine: Engine) -> None:
    records = parse_match(build_events(), build_lineups(), MATCH)
    with Session(engine) as session:
        upsert_player_match_stats(session, records)
        session.commit()
        assert (
            session.scalar(
                select(func.count()).select_from(Player).where(Player.understat_id.is_not(None))
            )
            == 0
        )
