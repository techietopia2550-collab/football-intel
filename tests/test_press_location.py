"""Press location re-parse (`src.transform.press_location`): pure helpers, synthetic events."""

from __future__ import annotations

import pytest

from src.transform import press_location as pl


def _ev(kind: str, pid: int, x: float, **extra: object) -> dict[str, object]:
    return {"type": {"name": kind}, "player": {"id": pid}, "location": [x, 40.0], "period": 1,
            **extra}  # fmt: skip


def test_press_kind_recognises_only_the_three_actions() -> None:
    assert pl.press_kind(_ev("Pressure", 1, 50)) == "pressure"
    assert pl.press_kind(_ev("Interception", 1, 50)) == "interception"
    assert pl.press_kind(_ev("Duel", 1, 50, duel={"type": {"name": "Tackle"}})) == "tackle"
    assert pl.press_kind(_ev("Duel", 1, 50, duel={"type": {"name": "Aerial Lost"}})) is None
    assert pl.press_kind(_ev("Clearance", 1, 50)) is None


def test_summarise_gives_per_kind_and_count_weighted_means() -> None:
    events = [
        _ev("Pressure", 7, 60),
        _ev("Pressure", 7, 40),
        _ev("Duel", 7, 20, duel={"type": {"name": "Tackle"}}),
        _ev("Interception", 7, 30),
        _ev("Pass", 7, 30, **{"pass": {"end_location": [50.0, 40.0]}}),
        _ev("Pass", 7, 60, **{"pass": {"end_location": [50.0, 40.0]}}),
        _ev("Pressure", 7, 110, period=5),  # shoot-out: skipped
        {"type": {"name": "Pressure"}, "player": {"id": 7}, "period": 1},  # no location: skipped
    ]
    row = pl.summarise(pl.accumulate_press_x(events)).iloc[0]
    assert row["pressures"] == 2 and row["avg_pressure_x"] == 50.0
    assert row["avg_tackle_x"] == 20.0 and row["avg_interception_x"] == 30.0
    assert row["press_actions"] == 4
    assert row["avg_press_action_x"] == pytest.approx((60 + 40 + 20 + 30) / 4)
    assert row["passes"] == 2 and row["avg_pass_dx"] == pytest.approx((20 - 10) / 2)


def test_summarise_leaves_missing_kinds_null() -> None:
    row = pl.summarise(pl.accumulate_press_x([_ev("Pressure", 3, 80)])).iloc[0]
    assert row["avg_tackle_x"] is None and row["avg_pass_dx"] is None
    assert row["avg_press_action_x"] == 80.0
