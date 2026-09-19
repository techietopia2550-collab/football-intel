"""Position-group mapping for StatsBomb positions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.statsbomb.parse import (
    POSITION_GROUPS,
    _minutes_and_position,
    parse_match,
    position_group,
)
from tests.statsbomb_fixtures import MATCH, build_events, build_lineups

# StatsBomb's 25 positions by id. Ids 1, 2, 3, 5, 6, 12, 13, 15, 16, 22, 24 have been seen in real
# data; the rest come from StatsBomb's documented list.
ALL_25 = {
    1: "Goalkeeper",
    2: "Right Back",
    3: "Right Center Back",
    4: "Center Back",
    5: "Left Center Back",
    6: "Left Back",
    7: "Right Wing Back",
    8: "Left Wing Back",
    9: "Right Defensive Midfield",
    10: "Center Defensive Midfield",
    11: "Left Defensive Midfield",
    12: "Right Midfield",
    13: "Right Center Midfield",
    14: "Center Midfield",
    15: "Left Center Midfield",
    16: "Left Midfield",
    17: "Right Wing",
    18: "Right Attacking Midfield",
    19: "Center Attacking Midfield",
    20: "Left Attacking Midfield",
    21: "Left Wing",
    22: "Right Center Forward",
    23: "Center Forward",
    24: "Left Center Forward",
    25: "Secondary Striker",
}


def test_all_25_positions_are_mapped_to_a_group() -> None:
    assert set(ALL_25.values()) == set(POSITION_GROUPS)
    assert set(POSITION_GROUPS.values()) == {"GK", "DEF", "MID", "FWD"}


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("Goalkeeper", "GK"),
        ("Right Back", "DEF"),
        ("Left Center Back", "DEF"),
        ("Right Wing Back", "DEF"),  # documented choice: wing-backs are DEF
        ("Center Defensive Midfield", "MID"),
        ("Right Midfield", "MID"),  # documented choice: wide midfielders are MID
        ("Center Attacking Midfield", "MID"),
        ("Left Attacking Midfield", "FWD"),  # documented choice: flank attacking mids are FWD
        ("Right Wing", "FWD"),
        ("Left Center Forward", "FWD"),
        ("Secondary Striker", "FWD"),
    ],
)
def test_mapping_choices(name: str, group: str) -> None:
    assert position_group(name) == group


def test_unmapped_position_is_none_not_guessed() -> None:
    assert position_group("Sweeper") is None
    assert position_group("Sub") is None  # StatsBomb has no Sub position


# --- group by most minutes ---------------------------------------------------------------------


def test_group_is_the_one_with_most_minutes() -> None:
    spells = [("Right Back", 0.0, 60.0), ("Right Midfield", 60.0, 90.0)]
    assert _minutes_and_position(spells) == (90.0, "Right Back", "DEF")


def test_group_minutes_add_across_positions_of_the_same_group() -> None:
    # 40 + 40 min in two DEF positions beat 50 min in MID, although MID is the single most
    # played position.
    spells = [("Right Back", 0, 40), ("Left Back", 40, 80), ("Center Midfield", 80, 130)]
    minutes, position, group = _minutes_and_position(spells)
    assert (position, group) == ("Center Midfield", "DEF")
    assert minutes == 130


def test_group_tie_goes_to_the_earliest_spell() -> None:
    spells = [("Center Midfield", 0.0, 45.0), ("Center Forward", 45.0, 90.0)]
    assert _minutes_and_position(spells)[2] == "MID"


def test_unmapped_minutes_do_not_count_towards_any_group() -> None:
    assert _minutes_and_position([("Sweeper", 0.0, 90.0)]) == (90.0, "Sweeper", None)
    assert _minutes_and_position([("Sweeper", 0.0, 60.0), ("Right Back", 60.0, 90.0)])[2] == "DEF"


def test_a_substitute_gets_a_group_from_his_real_position() -> None:
    records = {r.player_name: r for r in parse_match(build_events(), build_lineups(), MATCH)}
    assert records["Charlie"].position_group == "FWD"  # came on at Left Wing
    assert records["Bravo"].position_group == "FWD"
    assert records["Alpha"].position_group == "DEF"
    assert records["Delta"].position_group == "GK"


# --- the real cached match ---------------------------------------------------------------------

REAL_EVENTS = Path("data/raw/statsbomb/events/3754290.json")
REAL_LINEUPS = Path("data/raw/statsbomb/lineups/3754290.json")
needs_real_match = pytest.mark.skipif(
    not (REAL_EVENTS.exists() and REAL_LINEUPS.exists()), reason="real match not cached locally"
)


@needs_real_match
def test_real_match_named_players_and_full_resolution() -> None:
    from src.ingest.statsbomb.discovery import list_available

    events = json.loads(REAL_EVENTS.read_text(encoding="utf-8"))
    lineups = json.loads(REAL_LINEUPS.read_text(encoding="utf-8"))
    match = next(m for m in list_available("EPL", 2015) if m.match_id == 3754290)
    rows = {r.player_name: r for r in parse_match(events, lineups, match)}

    assert rows["N'Golo Kanté"].position_group == "MID"  # Left Center Midfield
    assert rows["Wes Morgan"].position_group == "DEF"  # Right Center Back
    assert rows["Kasper Schmeichel"].position_group == "GK"
    assert rows["Jamie Vardy"].position_group == "FWD"  # Left Center Forward
    assert len(rows) == 28
    assert all(r.position_group is not None for r in rows.values())  # 28 of 28 resolve


@needs_real_match
def test_every_position_seen_in_the_real_match_is_mapped() -> None:
    lineups = json.loads(REAL_LINEUPS.read_text(encoding="utf-8"))
    seen = {p["position"] for t in lineups for pl in t["lineup"] for p in pl["positions"]}
    assert seen
    assert seen <= set(POSITION_GROUPS)
    for t in lineups:  # and the ids agree with the names
        for pl in t["lineup"]:
            for p in pl["positions"]:
                assert ALL_25[p["position_id"]] == p["position"]
