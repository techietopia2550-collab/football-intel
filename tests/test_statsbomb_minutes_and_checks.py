"""Minutes normalization, invariant checks, and the resumable season loop (all offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from src.ingest.statsbomb import ingest
from src.ingest.statsbomb.checks import check_match, team_goals, team_minutes
from src.ingest.statsbomb.ingest import process_match, store_match
from src.ingest.statsbomb.parse import match_minutes_scale, parse_match
from src.ingest.team_identity import EPL_2015_16
from src.models.schema import IngestLog, Match, PlayerMatchStats, init_db
from src.utils.config import Settings, settings
from tests.statsbomb_fixtures import MATCH, build_events, build_lineups
from tests.statsbomb_full_match import build_full_match


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    return eng


# --- minutes normalization ---


def test_minutes_normalize_defaults_to_true() -> None:
    assert Settings().minutes_normalize is True
    assert settings.minutes_normalize is True


def test_scale_factor_makes_a_full_match_90() -> None:
    events = build_events()  # match length is 90.5
    assert match_minutes_scale(events, normalize=True) == pytest.approx(90.0 / 90.5)
    assert match_minutes_scale(events, normalize=False) == 1.0


def test_normalized_minutes_for_a_full_match_player_are_90(engine: Engine) -> None:
    processed = process_match(build_events(), build_lineups(), MATCH, normalize=True)
    with Session(engine) as session:
        store_match(session, processed)
        session.commit()
        alpha = session.scalar(
            select(PlayerMatchStats.minutes).where(PlayerMatchStats.player_name == "Alpha")
        )
        bravo = session.scalar(
            select(PlayerMatchStats.minutes).where(PlayerMatchStats.player_name == "Bravo")
        )
        factor = session.scalar(select(Match.minutes_scale_factor))
    assert alpha == pytest.approx(90.0, abs=0.5)
    assert bravo == pytest.approx(
        80.5 * 90.0 / 90.5, abs=0.01
    )  # everyone scaled by the same factor
    assert factor == pytest.approx(90.0 / 90.5)


def test_normalization_off_keeps_event_clock_minutes_and_records_factor_1(engine: Engine) -> None:
    processed = process_match(build_events(), build_lineups(), MATCH, normalize=False)
    with Session(engine) as session:
        store_match(session, processed)
        session.commit()
        assert session.scalar(select(func.max(PlayerMatchStats.minutes))) == pytest.approx(90.5)
        assert session.scalar(select(Match.minutes_scale_factor)) == 1.0


def test_migration_adds_the_new_columns_to_existing_tables(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    init_db(eng)
    with eng.begin() as conn:  # put the tables back the way they were before these columns
        conn.execute(text("ALTER TABLE matches DROP COLUMN minutes_scale_factor"))
        conn.execute(text("ALTER TABLE player_match_stats DROP COLUMN position_group"))
    insp = inspect(eng)
    assert "minutes_scale_factor" not in {c["name"] for c in insp.get_columns("matches")}

    init_db(eng)
    init_db(eng)  # idempotent

    insp = inspect(eng)
    assert "minutes_scale_factor" in {c["name"] for c in insp.get_columns("matches")}
    assert "position_group" in {c["name"] for c in insp.get_columns("player_match_stats")}


REAL_EVENTS = Path("data/raw/statsbomb/events/3754290.json")
REAL_LINEUPS = Path("data/raw/statsbomb/lineups/3754290.json")


@pytest.mark.skipif(
    not (REAL_EVENTS.exists() and REAL_LINEUPS.exists()), reason="real match not cached locally"
)
def test_real_match_full_match_players_are_90_after_ingest(engine: Engine) -> None:
    from src.ingest.statsbomb.discovery import list_available

    events = json.loads(REAL_EVENTS.read_text(encoding="utf-8"))
    lineups = json.loads(REAL_LINEUPS.read_text(encoding="utf-8"))
    match = next(m for m in list_available("EPL", 2015) if m.match_id == 3754290)
    processed = process_match(events, lineups, match, normalize=True)
    assert processed.violations == []
    with Session(engine) as session:
        store_match(session, processed)
        session.commit()
        full = list(
            session.scalars(select(PlayerMatchStats.minutes).where(PlayerMatchStats.minutes > 85))
        )
        factor = session.scalar(select(Match.minutes_scale_factor))
    assert len(full) >= 15  # 15 players played the whole match
    assert max(full) == pytest.approx(90.0, abs=0.5)
    assert factor == pytest.approx(90.0 / 97.23, abs=0.001)  # the 0.926 quoted in BLOCKERS.md


# --- invariant checks ---


def _check(events, lineups, match, **kwargs) -> list[str]:  # noqa: ANN001, ANN003
    records = parse_match(events, lineups, match)
    return check_match(events, lineups, match, records, **kwargs)


def test_a_valid_match_has_no_violations() -> None:
    events, lineups, match = build_full_match()
    assert _check(events, lineups, match) == []


def test_red_card_shortens_the_expected_team_minutes() -> None:
    events, lineups, _ = build_full_match()
    minutes = team_minutes(events, lineups)
    assert minutes[1] == pytest.approx((11 * 90.5, 11 * 90.5))
    # 10 players for 90.5 plus one sent off after 70.5, and the expectation agrees exactly
    assert minutes[2] == pytest.approx((10 * 90.5 + 70.5, 11 * 90.5 - 20.0))


def test_goals_include_own_goals_and_ignore_the_shootout() -> None:
    events, _, _ = build_full_match()
    assert team_goals(events) == {1: 2}  # one shot scored + one Own Goal For
    shootout = {
        "index": 999,
        "period": 5,
        "minute": 120,
        "second": 0,
        "type": {"name": "Shot"},
        "team": {"id": 2},
        "shot": {"outcome": {"name": "Goal"}},
    }
    assert team_goals([*events, shootout]) == {1: 2}


def test_too_few_players_is_reported() -> None:
    events, lineups, match = build_full_match()
    lineups[0]["lineup"] = lineups[0]["lineup"][:8]
    problems = _check(events, lineups, match)
    assert any("only 19 players" in p for p in problems)
    assert any("Team A minutes" in p for p in problems)  # and the minutes no longer add up


def test_team_minutes_mismatch_is_reported() -> None:
    events, lineups, match = build_full_match()
    lineups[0]["lineup"][3]["positions"][0]["to"] = "80:00"  # a player leaves 10 minutes early
    lineups[0]["lineup"][3]["positions"][0]["to_period"] = 2
    problems = _check(events, lineups, match)
    assert len(problems) == 1
    assert "Team A minutes" in problems[0]


def test_score_mismatch_is_reported() -> None:
    events, lineups, match = build_full_match()
    problems = _check(events, lineups, match.model_copy(update={"home_score": 3}))
    assert problems == ["Team A goals 2 differ from score 3"]


def test_missing_position_group_is_reported() -> None:
    events, lineups, match = build_full_match()
    lineups[0]["lineup"][2]["positions"][0]["position"] = "Sweeper"
    problems = _check(events, lineups, match)
    assert len(problems) == 2  # one per grouping: coarse and fine
    assert "has no position_group (Sweeper)" in problems[0]
    assert "has no position_group_fine (Sweeper)" in problems[1]


def test_unknown_team_is_reported_only_when_a_club_list_is_given() -> None:
    events, lineups, match = build_full_match()
    assert _check(events, lineups, match) == []
    problems = _check(events, lineups, match, known_teams=EPL_2015_16)
    assert len(problems) == 2  # "Team A" and "Team B" are not 2015/16 clubs
    assert all("not in the expected club list" in p for p in problems)


# --- the season loop ---


@pytest.fixture()
def season(engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch the season loop to use a temp database, temp reports and fake fetches."""
    events, lineups, match = build_full_match()
    matches = [match.model_copy(update={"match_id": i, "league": "TEST-League"}) for i in (1, 2, 3)]
    state: dict[str, object] = {"fail": {2}, "events": events, "matches": matches}

    def fake_events(match_id: int, **_kw: object) -> list[dict]:
        if match_id in state["fail"]:  # type: ignore[operator]
            raise RuntimeError(f"simulated download failure for {match_id}")
        return events

    monkeypatch.setattr(ingest, "init_db", lambda: engine)
    monkeypatch.setattr(ingest, "get_session_factory", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr(ingest, "list_available", lambda *_a, **_kw: state["matches"])
    monkeypatch.setattr(ingest, "fetch_match_events", fake_events)
    monkeypatch.setattr(ingest, "fetch_match_lineups", lambda *_a, **_kw: lineups)
    monkeypatch.setattr(ingest.settings, "processed_dir", tmp_path / "processed")
    state["engine"] = engine
    return state


def test_a_failed_match_is_recorded_loudly_and_the_rest_are_stored(season: dict) -> None:
    result = ingest.ingest_season("EPL", 2015)

    assert result.attempted == 3
    assert result.stored == [1, 3]
    assert list(result.failed) == [2]
    assert "simulated download failure for 2" in result.failed[2]
    with Session(season["engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Match)) == 2
        assert session.scalar(select(func.count()).select_from(PlayerMatchStats)) == 44
        log = session.scalars(select(IngestLog).where(IngestLog.source == "statsbomb")).one()
        assert log.status == "partial"
        assert "[2]" in log.error


def test_failures_are_written_to_a_csv(season: dict) -> None:
    ingest.ingest_season("EPL", 2015)
    failures = pd.read_csv(ingest.settings.processed_dir / "statsbomb_failures.csv")
    assert list(failures["match_id"]) == [2]
    assert "simulated download failure" in failures["error"][0]


def test_rerun_resumes_and_completes_without_duplicates(season: dict) -> None:
    ingest.ingest_season("EPL", 2015)
    season["fail"] = set()  # the transient problem is fixed
    result = ingest.ingest_season("EPL", 2015)

    assert result.stored == [1, 2, 3]
    assert result.failed == {}
    with Session(season["engine"]) as session:
        assert session.scalar(select(func.count()).select_from(Match)) == 3
        assert session.scalar(select(func.count()).select_from(PlayerMatchStats)) == 66
        statuses = list(session.scalars(select(IngestLog.status).order_by(IngestLog.id)))
    assert statuses == ["partial", "success"]
    failures = pd.read_csv(ingest.settings.processed_dir / "statsbomb_failures.csv")
    assert failures.empty  # the report reflects the latest run


def test_limit_ingests_only_the_first_n_matches(season: dict) -> None:
    season["fail"] = set()
    result = ingest.ingest_season("EPL", 2015, limit=2)
    assert (result.attempted, result.stored) == (2, [1, 2])


def test_invariant_violations_are_reported_but_the_match_is_still_stored(season: dict) -> None:
    season["fail"] = set()
    bad = season["matches"][0].model_copy(update={"home_score": 5})
    season["matches"] = [bad, *season["matches"][1:]]

    result = ingest.ingest_season("EPL", 2015)

    assert result.stored == [1, 2, 3]  # not dropped
    assert list(result.violations) == [1]
    assert result.violations[1] == ["Team A goals 2 differ from score 5"]
    violations = pd.read_csv(ingest.settings.processed_dir / "statsbomb_violations.csv")
    assert list(violations["match_id"]) == [1]
