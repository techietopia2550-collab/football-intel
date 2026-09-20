"""Fine position groups and the advanced event counts (aerials, receiving, defensive location).

Every event here is hand-built; expected counts are derived from the StatsBomb definitions in
`src/ingest/statsbomb/parse.py`, not from running the code on real data.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.ingest.statsbomb.parse import (
    FINE_GROUPS,
    FINE_POSITION_GROUPS,
    POSITION_GROUPS,
    _top_fine_group,
    aggregate_events,
    aggregate_extra_events,
    fine_position_group,
    parse_match,
)
from tests.statsbomb_fixtures import MATCH, build_events, build_lineups

_n = 0


def ev(kind: str, pid: int, period: int = 1, **extra: Any) -> dict[str, Any]:
    global _n
    _n += 1
    return {
        "id": extra.pop("id", f"e{_n}"),
        "period": period,
        "minute": 10,
        "second": 0,
        "type": {"name": kind},
        "player": {"id": pid, "name": f"P{pid}"},
        **extra,
    }


# --- fine position groups ----------------------------------------------------------------------


def test_every_statsbomb_position_has_a_fine_group() -> None:
    assert set(FINE_POSITION_GROUPS) == set(POSITION_GROUPS)  # the same 25 names
    assert set(FINE_POSITION_GROUPS.values()) == set(FINE_GROUPS)  # and all 8 groups are used


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("Goalkeeper", "GK"),
        ("Center Back", "CB"),
        ("Left Center Back", "CB"),
        ("Right Center Back", "CB"),
        ("Left Back", "FB"),
        ("Right Wing Back", "FB"),
        ("Left Defensive Midfield", "DM"),
        ("Center Defensive Midfield", "DM"),
        ("Right Center Midfield", "CM"),
        ("Left Midfield", "CM"),  # the brief: Left/Right Midfield -> CM
        ("Right Midfield", "CM"),
        ("Left Attacking Midfield", "AM"),
        ("Center Attacking Midfield", "AM"),
        ("Left Wing", "W"),
        ("Right Wing", "W"),
        ("Left Center Forward", "ST"),
        ("Center Forward", "ST"),
        ("Secondary Striker", "ST"),
    ],
)
def test_fine_mapping_follows_the_brief(name: str, group: str) -> None:
    assert fine_position_group(name) == group


def test_unmapped_position_has_no_fine_group() -> None:
    assert fine_position_group("Sweeper") is None
    assert _top_fine_group([("Sweeper", 0.0, 90.0)]) is None


def test_fine_group_is_the_one_with_most_minutes_and_ties_go_to_the_earliest() -> None:
    # 40 + 40 min as full-back beat 50 min in central midfield
    spells = [("Right Back", 0, 40), ("Left Back", 40, 80), ("Center Midfield", 80, 130)]
    assert _top_fine_group(spells) == "FB"
    assert _top_fine_group([("Center Midfield", 0.0, 45.0), ("Center Forward", 45.0, 90.0)]) == "CM"


def test_parse_match_records_the_fine_group_and_the_extra_counts() -> None:
    records = {r.player_name: r for r in parse_match(build_events(), build_lineups(), MATCH)}
    assert records["Alpha"].position_group_fine == "CB"
    assert records["Bravo"].position_group_fine == "W"  # Right Wing then Left Wing
    assert records["Charlie"].position_group_fine == "W"
    assert records["Delta"].position_group_fine == "GK"
    assert records["Echo"].position_group_fine == "ST"
    assert records["Alpha"].aerials_lost == 1  # the fixture's one `Aerial Lost` duel
    assert records["Alpha"].def_actions == 0  # fixture events carry no location


# --- aerials -----------------------------------------------------------------------------------


def test_aerials_lost_come_from_duels_and_won_from_the_aerial_won_flag() -> None:
    events = [
        ev("Duel", 1, duel={"type": {"name": "Aerial Lost"}}),
        ev("Duel", 1, duel={"type": {"name": "Aerial Lost"}}),
        ev(
            "Pass", 2, location=[30, 40], **{"pass": {"end_location": [40, 40], "aerial_won": True}}
        ),
        ev("Clearance", 2, location=[10, 40], clearance={"aerial_won": True}),
        ev("Shot", 2, location=[100, 40], shot={"aerial_won": True, "outcome": {"name": "Off T"}}),
        ev("Miscontrol", 3, miscontrol={"aerial_won": True}),
        ev("Pass", 2, location=[30, 40], **{"pass": {"end_location": [40, 40]}}),  # no flag
        ev("Clearance", 3, location=[10, 40], clearance={"body_part": {"name": "Head"}}),
    ]
    out = aggregate_extra_events(events)
    assert out[1]["aerials_lost"] == 2 and out[1]["aerials_won"] == 0
    assert out[2]["aerials_won"] == 3 and out[2]["aerials_lost"] == 0
    assert out[3]["aerials_won"] == 1


def test_a_won_aerial_is_only_counted_on_the_event_types_that_carry_the_flag() -> None:
    out = aggregate_extra_events([ev("Ball Recovery", 1, ball_recovery={"aerial_won": True})])
    assert out[1]["aerials_won"] == 0


# --- receiving ---------------------------------------------------------------------------------


def _pass(passer: int, recipient: int, end: list[float], **body: Any) -> dict[str, Any]:
    return ev(
        "Pass", passer, location=[50, 40],
        **{"pass": {"end_location": end, "recipient": {"id": recipient}, **body}},
    )  # fmt: skip


def test_passes_received_counts_completed_passes_to_the_recipient() -> None:
    events = [
        _pass(1, 2, [65, 40]),  # completed, 15 yards closer to goal: progressive
        _pass(1, 2, [55, 40]),  # completed, 5 yards: not progressive
        _pass(1, 2, [70, 40], outcome={"name": "Incomplete"}),  # not received
        _pass(3, 1, [60, 40]),  # exactly 10 yards closer: progressive
    ]
    out = aggregate_extra_events(events)
    assert out[2]["passes_received"] == 2 and out[2]["progressive_passes_received"] == 1
    assert out[1]["passes_received"] == 1 and out[1]["progressive_passes_received"] == 1


def test_a_receiver_without_own_events_is_not_an_actor() -> None:
    events = [_pass(1, 2, [65, 40])]
    assert 2 in aggregate_extra_events(events)
    stats, _ = aggregate_events(events)
    assert 2 not in stats  # so parse_match will not treat him as "events but no minutes"


def test_under_pressure_receipts_need_a_completed_receipt_flagged_under_pressure() -> None:
    events = [
        ev("Ball Receipt*", 2, under_pressure=True),
        ev("Ball Receipt*", 2, under_pressure=True),
        ev("Ball Receipt*", 2),  # not under pressure
        ev(
            "Ball Receipt*",
            2,
            under_pressure=True,
            ball_receipt={"outcome": {"name": "Incomplete"}},
        ),
    ]
    assert aggregate_extra_events(events)[2]["passes_received_under_pressure"] == 2


# --- defensive location proxies ----------------------------------------------------------------


def test_defensive_action_location_and_own_box_counts() -> None:
    tackle = {"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}
    events = [
        ev("Duel", 1, location=[10, 40], duel=tackle),  # own box
        ev("Interception", 1, location=[50, 10]),
        ev("Clearance", 1, location=[17.9, 61.9]),  # own box (just inside)
        ev("Clearance", 1, location=[17.9, 62.5]),  # just outside the box (y > 62)
        ev("Block", 1, location=[100, 40]),
        ev("Ball Recovery", 1, location=[10, 40]),  # not a counted defensive action
        ev("Duel", 1, location=[10, 40], duel={"type": {"name": "Aerial Lost"}}),  # neither
        ev("Interception", 1),  # no location: cannot be placed
    ]
    s = aggregate_extra_events(events)[1]
    assert s["def_actions"] == 5
    assert s["def_actions_own_box"] == 2
    assert s["def_action_x_sum"] == pytest.approx(10 + 50 + 17.9 + 17.9 + 100)


def test_cross_clearances_link_a_clearance_to_a_cross_pass() -> None:
    events = [
        ev(
            "Pass",
            9,
            id="cross-1",
            location=[100, 10],
            **{"pass": {"end_location": [110, 40], "cross": True}},
        ),
        ev("Pass", 9, id="plain-1", location=[60, 10], **{"pass": {"end_location": [70, 40]}}),
        ev("Clearance", 1, location=[8, 40], related_events=["cross-1"]),
        ev("Clearance", 1, location=[8, 40], related_events=["plain-1"]),
        ev("Clearance", 1, location=[8, 40]),  # no related event: cannot be classified
    ]
    s = aggregate_extra_events(events)[1]
    assert s["cross_clearances"] == 1 and s["def_actions"] == 3


def test_shootout_events_are_ignored() -> None:
    events = [ev("Duel", 1, period=5, duel={"type": {"name": "Aerial Lost"}})]
    assert aggregate_extra_events(events) == {}
