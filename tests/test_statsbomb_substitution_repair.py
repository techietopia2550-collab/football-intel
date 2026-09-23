"""The substitution lineup repair (real StatsBomb flaw, 14 of 760 EPL + La Liga matches).

Synthetic match: Team A (ids 101-111) plays the whole match except player 102, who is
substituted off at 60:00 of period 2 (elapsed 60.5 min; the match is 90.5 min long) for player 150.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ingest.statsbomb.checks import check_match, team_minutes
from src.ingest.statsbomb.ingest import SeasonResult, _write_reports, process_match
from src.ingest.statsbomb.parse import (
    DISMISSAL_CUT,
    SUBSTITUTION_CUT,
    check_against_substitutions,
    find_corrections,
    parse_match,
    period_lengths,
    player_spells,
    substitution_cuts,
    substitution_times,
)
from tests.statsbomb_fixtures import _player, _pos
from tests.statsbomb_full_match import _event, build_full_match

SUB_AT = 60.5  # elapsed minutes of a substitution at 60:00 of period 2
FULL = 90.5


def _substitution(
    events: list[dict[str, Any]], lineups: list[dict[str, Any]], *, spell_to: str | None,
    injured_at: str | None = None, on_at: str | None = None,
) -> None:  # fmt: skip
    """Player 102 is replaced by 150 at 60:00 (period 2). `spell_to` is where his lineup spell
    ends (None = the flaw: it runs to the final whistle). With `injured_at` he leaves at that
    time of period 2 first, and the lineup opens a spell at `on_at` (the nominal `Player On`)."""
    team_a = lineups[0]["lineup"]
    p102 = next(p for p in team_a if p["player_id"] == 102)
    if injured_at is None:
        p102["positions"] = [_pos("Center Midfield", "00:00", 1, spell_to, 2 if spell_to else None)]
    else:
        p102["positions"] = [
            _pos("Center Midfield", "00:00", 1, injured_at, 2),
            _pos("Center Midfield", on_at or "60:00", 2, spell_to, 2 if spell_to else None),
        ]
        minute, second = (int(x) for x in injured_at.split(":"))
        events.append(_event(2, minute, second, "Player Off", 1, 102))
        if on_at:
            m2, s2 = (int(x) for x in on_at.split(":"))
            events.append(_event(2, m2, s2, "Player On", 1, 102))
    team_a.append(_player(150, "P150", [_pos("Center Midfield", "60:00", 2, None, None)]))
    events.append(
        _event(
            2,
            60,
            0,
            "Substitution",
            1,
            102,
            substitution={"replacement": {"id": 150, "name": "P150"}},
        )
    )


def _minutes(events, lineups, match) -> dict[int, float]:  # type: ignore[no-untyped-def]
    return {r.statsbomb_player_id: r.minutes for r in parse_match(events, lineups, match)}


# --- the flaw and its repair ---------------------------------------------------------------


def test_a_substituted_player_whose_spell_runs_to_the_whistle_is_cut_at_the_substitution() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to=None)
    lengths = period_lengths(events)

    assert substitution_times(events, lengths) == {102: pytest.approx(SUB_AT)}
    assert player_spells(lineups, lengths)[102][0][2] == pytest.approx(FULL)  # the flaw
    assert substitution_cuts(events, lineups, lengths) == {102: pytest.approx(SUB_AT)}

    minutes = _minutes(events, lineups, match)
    assert minutes[102] == pytest.approx(SUB_AT)  # ends at the substitution event
    assert minutes[150] == pytest.approx(FULL - SUB_AT)  # the replacement starts there, unchanged
    assert minutes[102] + minutes[150] == pytest.approx(FULL)  # one player on the pitch throughout


def test_players_never_substituted_off_keep_full_minutes() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to=None)
    minutes = _minutes(events, lineups, match)
    for pid in (101, 103, 104, 105, 106, 107, 108, 109, 110, 111):
        assert minutes[pid] == pytest.approx(FULL), pid
    assert minutes[201] == pytest.approx(FULL)  # Team B, untouched
    assert minutes[211] == pytest.approx(70.5)  # the sent-off player, unchanged


def test_normalized_minutes_of_a_full_match_player_stay_90() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to=None)
    processed = process_match(
        events, lineups, match.model_copy(update={"league": "X"}), normalize=True
    )
    by_id = {r.statsbomb_player_id: r.minutes for r in processed.records}
    assert by_id[101] == pytest.approx(90.0)
    assert by_id[102] + by_id[150] == pytest.approx(90.0)


def test_the_cut_is_logged_with_its_own_kind() -> None:
    events, lineups, _ = build_full_match()
    _substitution(events, lineups, spell_to=None)
    (correction,) = find_corrections(events, lineups)
    assert correction.kind == SUBSTITUTION_CUT == "spell_cut_at_substitution"
    assert "P102" in correction.message and "30.00 min" in correction.message
    assert "substitution at 60.5 min" in correction.message


def test_a_repaired_match_passes_every_invariant() -> None:
    events, lineups, match = build_full_match()
    assert check_against_substitutions(events, lineups) == []  # no substitution yet
    _substitution(events, lineups, spell_to=None)
    assert check_against_substitutions(events, lineups) == []  # the flaw no longer fails the match
    processed = process_match(
        events, lineups, match.model_copy(update={"league": "X"}), normalize=False
    )
    assert processed.violations == []
    assert [c.kind for c in processed.corrections] == [SUBSTITUTION_CUT]


def test_a_lineup_that_already_ends_at_the_substitution_needs_no_correction() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to="60:00")  # a correct lineup
    lengths = period_lengths(events)
    assert substitution_cuts(events, lineups, lengths) == {}
    assert find_corrections(events, lineups) == []
    assert _minutes(events, lineups, match)[102] == pytest.approx(SUB_AT)


def test_a_substitution_time_contradicted_by_the_players_events_is_not_trusted() -> None:
    """He has an event 15 minutes after his 'substitution': the time is wrong, so nothing is
    cut and the match still fails loudly rather than silently losing his minutes."""
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to=None)
    events.append(_event(2, 75, 0, "Ball Receipt*", 1, 102))
    lengths = period_lengths(events)
    assert substitution_cuts(events, lineups, lengths) == {}
    assert len(check_against_substitutions(events, lineups)) == 1
    with pytest.raises(ValueError, match="disagree with substitution events"):
        process_match(events, lineups, match.model_copy(update={"league": "X"}), normalize=False)


# --- the injured player: Player Off, then a nominal Player On at the substitution ------------


def test_an_injured_player_keeps_only_the_minutes_before_his_player_off() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to=None, injured_at="55:00", on_at="60:00")
    minutes = _minutes(events, lineups, match)
    assert minutes[102] == pytest.approx(55.5)  # left at 55:00, not at the substitution
    assert minutes[150] == pytest.approx(FULL - SUB_AT)
    (correction,) = find_corrections(events, lineups)
    assert correction.kind == SUBSTITUTION_CUT and "30.00 min" in correction.message  # the phantom


def test_the_gap_between_player_off_and_the_replacement_is_not_a_violation() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to=None, injured_at="55:00", on_at="60:00")
    assert check_against_substitutions(events, lineups) == []
    actual, expected = team_minutes(events, lineups)[1]
    assert actual == pytest.approx(11 * FULL - 5.0)  # Team A played 5 minutes short
    assert expected == pytest.approx(actual)
    processed = process_match(
        events, lineups, match.model_copy(update={"league": "X"}), normalize=False
    )
    assert processed.violations == []


def test_a_few_seconds_long_nominal_spell_is_dropped_and_logged() -> None:
    events, lineups, match = build_full_match()
    _substitution(events, lineups, spell_to="60:00", injured_at="55:00", on_at="59:50")
    minutes = _minutes(events, lineups, match)
    assert minutes[102] == pytest.approx(55.5)  # the 3-second sliver is not credited
    (correction,) = find_corrections(events, lineups)
    assert correction.kind == SUBSTITUTION_CUT and "0.17 min" in correction.message
    assert check_match(events, lineups, match, parse_match(events, lineups, match)) == []


def test_an_injured_player_who_is_never_replaced_leaves_the_team_short_without_a_violation() -> (
    None
):
    events, lineups, match = build_full_match()
    events.append(_event(2, 70, 0, "Player Off", 1, 103))  # no Player On, no substitution
    p103 = next(p for p in lineups[0]["lineup"] if p["player_id"] == 103)
    p103["positions"] = [_pos("Center Midfield", "00:00", 1, "70:00", 2)]
    actual, expected = team_minutes(events, lineups)[1]
    assert actual == pytest.approx(11 * FULL - 20.0)  # 90.5 - 70.5 = 20 minutes a man down
    assert expected == pytest.approx(actual)
    assert check_match(events, lineups, match, parse_match(events, lineups, match)) == []


def test_no_gap_is_subtracted_when_the_lineup_kept_the_left_player_on() -> None:
    """A `Player Off` the lineup ignores (his spell runs to the end) must not lower the expected
    minutes: the gap is only counted where the lineup really ends at the `Player Off`."""
    events, lineups, _ = build_full_match()
    events.append(_event(2, 70, 0, "Player Off", 1, 103))
    actual, expected = team_minutes(events, lineups)[1]
    assert actual == pytest.approx(11 * FULL) and expected == pytest.approx(11 * FULL)


# --- reporting -------------------------------------------------------------------------------


def test_dismissal_and_substitution_cuts_are_reported_as_different_kinds() -> None:
    events, lineups, _ = build_full_match()
    _substitution(events, lineups, spell_to=None)
    victim = next(p for t in lineups for p in t["lineup"] if p["player_id"] == 211)
    victim["positions"] = [_pos("Center Midfield", "00:00", 1, None, None)]  # the Courtois flaw
    kinds = sorted(c.kind for c in find_corrections(events, lineups))
    assert kinds == sorted([DISMISSAL_CUT, SUBSTITUTION_CUT])


def test_substitution_cuts_are_written_to_the_corrections_csv(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import pandas as pd

    from src.ingest.statsbomb import ingest

    monkeypatch.setattr(ingest.settings, "processed_dir", tmp_path)
    events, lineups, _ = build_full_match()
    _substitution(events, lineups, spell_to=None)
    result = SeasonResult(corrections={7: find_corrections(events, lineups)})
    _write_reports(result, "ESP-La Liga", 2015)
    df = pd.read_csv(tmp_path / "statsbomb_corrections.csv")
    assert list(df["kind"]) == ["spell_cut_at_substitution"]
    assert list(df["match_id"]) == [7] and "P102" in df["correction"].iloc[0]
