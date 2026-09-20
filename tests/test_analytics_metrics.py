"""Metric registry and `compute_metrics`."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.analytics.metrics import (
    COUNT_COLUMNS,
    METRICS,
    METRICS_BY_NAME,
    POSITION_GROUPS,
    UNAVAILABLE,
    compute_metrics,
)


def _totals(**overrides: object) -> pd.DataFrame:
    base: dict[str, object] = {c: 0.0 for c in COUNT_COLUMNS}
    base.update(minutes_total=900.0, position_group_fine="ST", pressure_duration_total=0.0)
    base.update(overrides)
    return pd.DataFrame([base])


def test_registry_is_consistent() -> None:
    assert len({m.name for m in METRICS}) == len(METRICS)
    assert all(m.status != "unavailable" for m in METRICS)  # unavailable ones live in UNAVAILABLE
    assert UNAVAILABLE
    for m in METRICS:
        assert (m.kind == "ratio") == (m.denominator is not None), m.name
        assert m.scope == ("gk" if m.group == "GK" else "outfield"), m.name


def test_rate_is_per_90_of_the_season_total() -> None:
    out = compute_metrics(_totals(goals=10.0, minutes_total=1800.0))
    assert out["goals_p90"].iloc[0] == pytest.approx(0.5)


def test_ratio_uses_season_totals_and_scale() -> None:
    out = compute_metrics(_totals(shots=20.0, shots_on_target=8.0, passes_attempted=400.0,
                                  passes_completed=300.0, xg=4.0))  # fmt: skip
    assert out["shot_accuracy"].iloc[0] == pytest.approx(40.0)
    assert out["pass_completion_pct"].iloc[0] == pytest.approx(75.0)
    assert out["xg_per_shot"].iloc[0] == pytest.approx(0.2)


def test_ratio_is_nan_below_its_sample_size_guard() -> None:
    guard = METRICS_BY_NAME["shot_accuracy"].min_denominator
    out = compute_metrics(_totals(shots=guard - 1, shots_on_target=guard - 1))
    assert math.isnan(out["shot_accuracy"].iloc[0])  # a perfect 100% on a handful of shots
    out = compute_metrics(_totals(shots=guard, shots_on_target=guard))
    assert out["shot_accuracy"].iloc[0] == pytest.approx(100.0)


def test_zero_minutes_gives_nan_not_infinity() -> None:
    out = compute_metrics(_totals(goals=1.0, minutes_total=0.0))
    assert math.isnan(out["goals_p90"].iloc[0])


def test_goalkeeper_and_outfield_metrics_are_never_mixed() -> None:
    gk_stats = {"gk_saves": 60.0, "gk_goals_conceded": 30.0, "gk_xg_on_target_faced": 40.0}
    out = compute_metrics(pd.concat([_totals(position_group_fine="GK", **gk_stats), _totals()]))
    gk, fwd = out.iloc[0], out.iloc[1]
    assert gk["gk_save_pct"] == pytest.approx(100 * 60 / 90)
    assert gk["gk_goals_prevented_p90"] == pytest.approx((40 - 30) / 900 * 90)
    assert math.isnan(gk["goals_p90"]) and math.isnan(gk["tackles_p90"])
    assert math.isnan(fwd["gk_save_pct"]) and math.isnan(fwd["gk_saves_p90"])


def test_pressure_duration_is_pressure_weighted() -> None:
    out = compute_metrics(_totals(pressures=200.0, pressure_duration_total=300.0))
    assert out["pressure_duration_avg"].iloc[0] == pytest.approx(1.5)


def test_aerial_metrics_use_won_and_lost_duels() -> None:
    out = compute_metrics(_totals(aerials_won=30.0, aerials_lost=10.0, minutes_total=1800.0))
    assert out["aerials_attempted_p90"].iloc[0] == pytest.approx(40 / 1800 * 90)
    assert out["aerials_won_p90"].iloc[0] == pytest.approx(30 / 1800 * 90)
    assert out["aerial_win_pct"].iloc[0] == pytest.approx(75.0)  # won / (won + lost)


def test_aerial_win_pct_is_empty_on_a_tiny_sample() -> None:
    guard = METRICS_BY_NAME["aerial_win_pct"].min_denominator
    out = compute_metrics(_totals(aerials_won=guard - 1, aerials_lost=0.0))
    assert math.isnan(out["aerial_win_pct"].iloc[0])  # 100% of 19 aerials is not ranked


def test_average_defensive_action_x_is_the_mean_of_the_summed_coordinates() -> None:
    out = compute_metrics(_totals(def_actions=100.0, def_action_x_sum=3000.0))
    assert out["avg_defensive_action_x"].iloc[0] == pytest.approx(30.0)
    few = compute_metrics(_totals(def_actions=10.0, def_action_x_sum=300.0))
    assert math.isnan(few["avg_defensive_action_x"].iloc[0])


def test_cross_clearance_pct_is_the_share_of_clearances() -> None:
    out = compute_metrics(_totals(cross_clearances=20.0, clearances=80.0))
    assert out["cross_clearance_pct"].iloc[0] == pytest.approx(25.0)


def test_receiving_metrics_are_per_90() -> None:
    out = compute_metrics(
        _totals(passes_received=900.0, passes_received_under_pressure=90.0,
                progressive_passes_received=180.0, minutes_total=900.0)
    )  # fmt: skip
    assert out["passes_received_p90"].iloc[0] == pytest.approx(90.0)
    assert out["passes_received_under_pressure_p90"].iloc[0] == pytest.approx(9.0)
    assert out["progressive_passes_received_p90"].iloc[0] == pytest.approx(18.0)


def test_proxy_metrics_are_descriptive_and_labelled_as_proxies() -> None:
    proxies = [m for m in METRICS if m.group in {"POSITIONING", "RECEIVING"}]
    assert len(proxies) == 7
    for m in proxies:
        assert m.higher_is_better is None, m.name  # ranked, never called a strength or weakness
        assert m.note.startswith("PROXY"), m.name
    assert not any("leader" in m.name or "trust" in m.name for m in METRICS)


def test_metrics_that_cannot_be_computed_are_listed_not_invented() -> None:
    names = {m.name for m in METRICS}
    assert "last_line_events_p90" in UNAVAILABLE and "last_line_events_p90" not in names
    assert "is_captain" in UNAVAILABLE and "is_captain" not in names
    assert "aerial_duels_won_pct" not in UNAVAILABLE  # it IS computable (aerial_won flags)


def test_position_groups_are_the_eight_fine_groups() -> None:
    assert POSITION_GROUPS == ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")
