"""Regression tests for two real StatsBomb data quirks found in the 2015/16 smoke test.

1. Chelsea v Swansea (3754078): Courtois is sent off at 51:37 but the lineup keeps him on the
   pitch (as an attacking midfielder) until the final whistle: about 43 phantom minutes.
2. Crystal Palace v Norwich (3754138): a player leaves for treatment at 64:02 and returns at
   64:20 (`Player Off`/`Player On`), so the team legitimately played 0.3 minutes short.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ingest.statsbomb.checks import check_match, team_minutes, temporary_absences
from src.ingest.statsbomb.ingest import process_match
from src.ingest.statsbomb.parse import (
    LineupError,
    dismissal_times,
    find_corrections,
    parse_match,
    period_lengths,
    player_spells,
)
from tests.statsbomb_fixtures import _pos
from tests.statsbomb_full_match import _event, build_full_match


def _sent_off_player_stays_on(lineups: list[dict[str, Any]]) -> None:
    """Reproduce the Courtois flaw: player 211 is sent off at 70:00 but his spell never ends."""
    victim = next(p for t in lineups for p in t["lineup"] if p["player_id"] == 211)
    victim["positions"] = [_pos("Center Midfield", "00:00", 1, None, None)]


def test_a_correct_lineup_needs_no_correction() -> None:
    events, lineups, _ = build_full_match()  # 211's spell already ends at 70:00
    assert find_corrections(events, lineups) == []


def test_dismissal_times_are_elapsed_minutes() -> None:
    events, _, _ = build_full_match()
    assert dismissal_times(events, period_lengths(events)) == {211: pytest.approx(70.5)}


def test_minutes_after_a_dismissal_are_removed_and_reported() -> None:
    events, lineups, match = build_full_match()
    _sent_off_player_stays_on(lineups)

    lengths = period_lengths(events)
    assert player_spells(lineups, lengths)[211] == [("Center Midfield", 0.0, pytest.approx(90.5))]
    cut = player_spells(lineups, lengths, dismissal_times(events, lengths))[211]
    assert cut == [("Center Midfield", 0.0, pytest.approx(70.5))]

    corrections = find_corrections(events, lineups)
    assert len(corrections) == 1
    assert corrections[0].kind == "dismissal_cut"
    assert "P211" in corrections[0].message
    assert "20.0 min" in corrections[0].message

    records = {r.statsbomb_player_id: r for r in parse_match(events, lineups, match)}
    assert records[211].minutes == pytest.approx(70.5)
    assert records[211].red_cards == 1


def test_a_corrected_match_passes_every_invariant() -> None:
    events, lineups, match = build_full_match()
    _sent_off_player_stays_on(lineups)
    processed = process_match(
        events, lineups, match.model_copy(update={"league": "X"}), normalize=False
    )
    assert processed.violations == []
    assert len(processed.corrections) == 1  # visible, not silent


def test_a_lineup_spell_that_starts_after_the_dismissal_is_dropped() -> None:
    """The Courtois case exactly: his GK spell runs past the red card, then a second spell
    (as an outfielder) opens after it. Both must be cut back."""
    events, lineups, _ = build_full_match()
    victim = next(p for t in lineups for p in t["lineup"] if p["player_id"] == 211)
    victim["positions"] = [
        _pos("Goalkeeper", "00:00", 1, "72:00", 2),
        _pos("Center Attacking Midfield", "72:00", 2, None, None),
    ]
    lengths = period_lengths(events)
    spells = player_spells(lineups, lengths, dismissal_times(events, lengths))[211]
    assert spells == [("Goalkeeper", 0.0, pytest.approx(70.5))]  # the second spell is gone
    (correction,) = find_corrections(events, lineups)
    assert correction.kind == "dismissal_cut" and "P211" in correction.message


def _bench_player_sent_off(events: list[dict[str, Any]], lineups: list[dict[str, Any]]) -> None:
    bench = {"player_id": 299, "player_name": "Bench", "player_nickname": "Bench", "positions": []}
    lineups[1]["lineup"].append(bench)
    events.append(
        _event(2, 80, 0, "Bad Behaviour", 2, 299, bad_behaviour={"card": {"name": "Red Card"}})
    )


def test_a_card_shown_to_an_unused_substitute_costs_no_expected_minutes() -> None:
    events, lineups, _ = build_full_match()
    before = team_minutes(events, lineups)
    _bench_player_sent_off(events, lineups)
    assert team_minutes(events, lineups) == before  # he was never on the pitch


def test_an_unused_substitute_with_events_fails_the_match_loudly() -> None:
    events, lineups, match = build_full_match()
    _bench_player_sent_off(events, lineups)
    with pytest.raises(LineupError, match="Player 299"):
        parse_match(events, lineups, match)


# --- temporary absences -----------------------------------------------------------------------


def _treatment_break(events: list[dict[str, Any]], lineups: list[dict[str, Any]]) -> None:
    """Player 105 leaves at 10:00 and returns at 10:30 of period 1 (a 0.5 minute gap)."""
    events += [
        _event(1, 10, 0, "Player Off", 1, 105),
        _event(1, 10, 30, "Player On", 1, 105),
    ]
    player = next(p for t in lineups for p in t["lineup"] if p["player_id"] == 105)
    player["positions"] = [
        _pos("Center Midfield", "00:00", 1, "10:00", 1),
        _pos("Center Midfield", "10:30", 1, None, None),
    ]


def test_temporary_absences_are_measured_from_the_events() -> None:
    events, lineups, _ = build_full_match()
    _treatment_break(events, lineups)
    gaps = temporary_absences(events, period_lengths(events))
    assert gaps == {1: pytest.approx(0.5)}


def test_a_treatment_break_is_not_a_violation() -> None:
    events, lineups, match = build_full_match()
    _treatment_break(events, lineups)
    records = parse_match(events, lineups, match)
    assert check_match(events, lineups, match, records) == []


def test_a_gap_in_the_lineup_without_a_matching_event_is_still_a_violation() -> None:
    events, lineups, match = build_full_match()
    _treatment_break(events, lineups)
    events[:] = [e for e in events if e["type"]["name"] not in {"Player Off", "Player On"}]
    records = parse_match(events, lineups, match)
    problems = check_match(events, lineups, match, records)
    assert len(problems) == 1
    assert "Team A minutes" in problems[0]
    assert "-0.50" in problems[0]
