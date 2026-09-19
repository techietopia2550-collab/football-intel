"""Offline tests for StatsBomb parsing, using a hand-built match with exactly known answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.statsbomb.parse import (
    LineupError,
    check_against_substitutions,
    elapsed_minutes,
    parse_match,
    period_lengths,
    player_spells,
)
from tests.statsbomb_fixtures import MATCH, build_events, build_lineups


@pytest.fixture()
def records() -> dict[str, object]:
    return {r.player_name: r for r in parse_match(build_events(), build_lineups(), MATCH)}


def test_period_lengths_and_elapsed_conversion() -> None:
    lengths = period_lengths(build_events())
    assert lengths == {1: pytest.approx(45.5), 2: pytest.approx(45.0)}
    assert elapsed_minutes(1, 30.0, lengths) == pytest.approx(30.0)
    # the clock restarts at 45:00 in the second half, after 45.5 minutes of period 1
    assert elapsed_minutes(2, 60.0, lengths) == pytest.approx(60.5)


def test_shootout_events_do_not_extend_period_lengths() -> None:
    assert 5 not in period_lengths(build_events())


# --- minutes and position ---------------------------------------------------------------


def test_minutes_from_lineup_spells(records) -> None:
    assert records["Alpha"].minutes == pytest.approx(90.5)
    assert records["Bravo"].minutes == pytest.approx(80.5)  # subbed off at 80:00
    assert records["Charlie"].minutes == pytest.approx(10.0)  # came on at 80:00
    assert records["Delta"].minutes == pytest.approx(70.5)  # sent off at 70:00


def test_unused_substitute_has_no_row(records) -> None:
    assert "Unused" not in records
    assert len(records) == 5


def test_position_played_is_the_one_with_most_minutes(records) -> None:
    assert records["Bravo"].position_played == "Right Wing"  # 60.5 min vs 20.0 min on the left
    assert records["Alpha"].position_played == "Center Back"


def test_team_and_opponent(records) -> None:
    assert (records["Alpha"].team, records["Alpha"].opponent) == ("Team A", "Team B")
    assert (records["Delta"].team, records["Delta"].opponent) == ("Team B", "Team A")
    assert records["Alpha"].season == "2015"
    assert records["Alpha"].league == "ENG-Premier League"


def test_player_with_events_but_no_minutes_fails_loudly() -> None:
    lineups = build_lineups()
    lineups[0]["lineup"][0]["positions"] = []  # Alpha has plenty of events
    with pytest.raises(LineupError):
        parse_match(build_events(), lineups, MATCH)


def test_substitution_cross_check_passes_and_detects_mismatch() -> None:
    assert check_against_substitutions(build_events(), build_lineups()) == []
    lineups = build_lineups()
    lineups[0]["lineup"][2]["positions"][0]["from"] = "70:00"  # Charlie "on" ten minutes early
    assert len(check_against_substitutions(build_events(), lineups)) == 1


def test_spells_use_elapsed_time() -> None:
    lengths = period_lengths(build_events())
    spells = player_spells(build_lineups(), lengths)
    assert spells[11] == [
        ("Right Wing", 0.0, pytest.approx(60.5)),
        ("Left Wing", pytest.approx(60.5), pytest.approx(80.5)),
    ]


# --- passing ---------------------------------------------------------------------------


def test_pass_counts_and_completion(records) -> None:
    a = records["Alpha"]
    assert (a.passes_attempted, a.passes_completed) == (6, 5)
    assert a.pass_completion_pct == pytest.approx(83.3)


def test_progressive_pass_threshold_is_10_yards_inclusive(records) -> None:
    # gains 12, 9.5 (no), exactly 10, 15, ~27: four progressive
    assert records["Alpha"].progressive_passes == 4


def test_final_third_and_box_passes(records) -> None:
    a = records["Alpha"]
    assert a.passes_into_final_third == 1  # only [70,40] -> [85,40] crosses x=80
    assert a.passes_into_box == 1  # [90,10] -> [105,40]


def test_pass_completion_is_none_without_passes(records) -> None:
    assert records["Delta"].pass_completion_pct is None


# --- carrying and pressing --------------------------------------------------------------


def test_carries(records) -> None:
    a = records["Alpha"]
    assert a.carries == 2
    assert a.progressive_carries == 1
    assert a.carry_distance == pytest.approx(20.8, abs=0.05)  # 15 + sqrt(34)


def test_pressures(records) -> None:
    a = records["Alpha"]
    assert a.pressures == 3
    assert a.counterpressures == 1
    assert a.pressure_duration_avg == pytest.approx(2.0)
    assert records["Bravo"].pressure_duration_avg is None


# --- defending -------------------------------------------------------------------------


def test_defensive_actions(records) -> None:
    a = records["Alpha"]
    assert (a.tackles, a.tackles_won) == (2, 1)  # the aerial duel is not a tackle
    assert a.interceptions == 1
    assert a.blocks == 1
    assert a.clearances == 2
    assert a.ball_recoveries == 1  # the failed recovery is excluded


# --- dribbling, shooting, discipline ------------------------------------------------------


def test_dribbles(records) -> None:
    assert (records["Alpha"].dribbles_attempted, records["Alpha"].dribbles_completed) == (2, 1)


def test_shots_goals_xg_ignore_the_shootout(records) -> None:
    a = records["Alpha"]
    assert (a.shots, a.goals) == (2, 1)
    assert a.xg == pytest.approx(0.75)


def test_fouls_and_cards(records) -> None:
    a = records["Alpha"]
    assert (a.fouls_committed, a.fouls_won, a.yellow_cards, a.red_cards) == (1, 2, 1, 0)
    d = records["Delta"]  # second yellow counts as a yellow and a red
    assert (d.fouls_committed, d.yellow_cards, d.red_cards) == (1, 1, 1)
    e = records["Echo"]  # straight red via Bad Behaviour is not a foul
    assert (e.fouls_committed, e.yellow_cards, e.red_cards) == (0, 0, 1)


# --- real match (runs only when it is in the local cache) -------------------------------------

REAL_EVENTS = Path("data/raw/statsbomb/events/3754290.json")
REAL_LINEUPS = Path("data/raw/statsbomb/lineups/3754290.json")


@pytest.mark.skipif(
    not (REAL_EVENTS.exists() and REAL_LINEUPS.exists()), reason="real match not cached locally"
)
def test_real_match_reconciles_with_the_raw_events() -> None:
    from src.ingest.statsbomb.discovery import list_available

    events = json.loads(REAL_EVENTS.read_text(encoding="utf-8"))
    lineups = json.loads(REAL_LINEUPS.read_text(encoding="utf-8"))
    match = next(m for m in list_available("EPL", 2015) if m.match_id == 3754290)
    rows = parse_match(events, lineups, match)

    assert check_against_substitutions(events, lineups) == []
    assert len(rows) == 28
    total = sum(period_lengths(events).values())
    for team in {r.team for r in rows}:
        assert sum(r.minutes for r in rows if r.team == team) == pytest.approx(11 * total, abs=0.1)
    goals = {t: sum(r.goals for r in rows if r.team == t) for t in {r.team for r in rows}}
    assert goals == {"Manchester City": match.home_score, "Leicester City": match.away_score}
    assert sum(r.passes_attempted for r in rows) == sum(
        1 for e in events if e["type"]["name"] == "Pass"
    )
