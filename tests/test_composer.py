"""Tactical Composer: style-fit scores, formation templates, fit scoring and the greedy build.
Offline, synthetic pools only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analytics import composer as c
from src.analytics import style_fit as sf
from src.analytics.formations import FORMATIONS, get_formation
from src.analytics.team_style import AXIS_COLUMNS

SLOT_GROUPS = {
    "4-3-3": ["GK", "FB", "CB", "CB", "FB", "DM", "CM", "CM", "W", "W", "ST"],
    "4-2-3-1": ["GK", "FB", "CB", "CB", "FB", "DM", "DM", "AM", "W", "W", "ST"],
    "4-4-2": ["GK", "FB", "CB", "CB", "FB", "CM", "CM", "W", "W", "ST", "ST"],
    "3-5-2": ["GK", "CB", "CB", "CB", "FB", "FB", "DM", "CM", "CM", "ST", "ST"],
    "5-3-2": ["GK", "CB", "CB", "CB", "FB", "FB", "DM", "CM", "CM", "ST", "ST"],
}


@pytest.mark.parametrize("name", list(SLOT_GROUPS))
def test_formations_match_the_spec(name: str) -> None:
    assert [s.group for s in get_formation(name).slots] == SLOT_GROUPS[name]


def test_unknown_formation_raises() -> None:
    with pytest.raises(KeyError):
        get_formation("2-3-5")


# ---- style fit ---------------------------------------------------------------------------------


def test_group_percentile_ranks_within_group_only() -> None:
    v = pd.Series([1.0, 2.0, 3.0, 100.0, np.nan])
    g = pd.Series(["CB", "CB", "CB", "ST", "CB"])
    pct = sf.group_percentile(v, g)
    assert list(pct[:3]) == pytest.approx([100 / 6, 50.0, 500 / 6], abs=0.1)
    assert pct[3] == 50.0 and np.isnan(pct[4])


def test_axis_scores_average_mapped_percentiles_and_leave_team_axes_null() -> None:
    pct = pd.DataFrame(
        {"pressures_p90": [80.0, np.nan], "tackles_p90": [40.0, np.nan],
         "gk_sweeper_actions_p90": [np.nan, 90.0]}
    )  # fmt: skip
    out = sf.axis_scores(pct, pd.Series([False, True]))
    assert out.loc[0, "ppda"] == pytest.approx(0.6)
    assert np.isnan(out.loc[1, "ppda"])  # goalkeepers have no pressing mapping
    assert out.loc[1, "def_line_height"] == pytest.approx(0.9)
    for axis in sf.TEAM_ONLY_AXES:
        assert out[axis].isna().all()


def test_team_only_axes_are_the_three_unmeasurable_ones() -> None:
    assert set(sf.TEAM_ONLY_AXES) == {"width", "crosses_p90", "set_piece_xg_pct"}
    assert len(sf.PLAYER_AXES) == 9


def test_compute_style_fit_keeps_only_valued_players() -> None:
    stats = pd.DataFrame({
        "player_id": ["a", "b"], "player_name": ["A", "B"], "team": ["X", "Y"],
        "season": ["2015"] * 2, "league": ["ESP-La Liga"] * 2, "source": ["statsbomb"] * 2,
        "position_group_fine": ["CB"] * 2, "minutes_total": [1000.0, 2000.0],
        "passes_attempted": [500, 900],
    })  # fmt: skip
    pct = pd.DataFrame({"player_id": ["a", "b"], "metric": ["pressures_p90"] * 2,
                        "percentile": [25.0, 75.0]})  # fmt: skip
    values = pd.DataFrame({"player_id": ["a", "b"], "market_value_eur": [1e6, None]})
    out = sf.compute_style_fit(stats, pct, values)
    assert list(out["player_id"]) == ["a"]
    assert set(AXIS_COLUMNS) <= set(out.columns) and out.loc[0, "market_value_eur"] == 1e6


# ---- composer ----------------------------------------------------------------------------------


def _pool(n_per_group: int = 6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for g in ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST"):
        for i in range(n_per_group):
            row = {
                "player_id": f"{g}{i}", "player_name": f"{g} {i}", "team": f"Club {i % 4}",
                "season": "2015", "league": "ESP-La Liga" if i % 2 else "ITA-Serie A",
                "source": "t", "position_group_fine": g, "minutes_total": 1500.0,
                "market_value_eur": float((i + 1) * 1e6),
            }  # fmt: skip
            for a in AXIS_COLUMNS:
                row[a] = np.nan if a in sf.TEAM_ONLY_AXES else float(rng.uniform())
            rows.append(row)
    return pd.DataFrame(rows)


def test_fit_rewards_the_target_direction() -> None:
    pool = _pool().iloc[:2].copy()
    for a in sf.PLAYER_AXES:
        pool.loc[0, a], pool.loc[1, a] = 0.9, 0.1
    target = {a: 90.0 for a in AXIS_COLUMNS}
    fit = c.fit_scores(pool, target)["fit"]
    assert fit[0] == pytest.approx(1.0) and fit[1] == pytest.approx(0.0)


def test_flat_target_uses_closeness() -> None:
    pool = _pool().iloc[:2].copy()
    for a in sf.PLAYER_AXES:
        pool.loc[0, a], pool.loc[1, a] = 0.5, 0.9
    fit = c.fit_scores(pool, {a: 50.0 for a in AXIS_COLUMNS})["fit"]
    assert fit[0] == pytest.approx(1.0) and fit[0] > fit[1]


@pytest.mark.parametrize("formation", list(FORMATIONS))
def test_build_fills_every_slot_within_budget(formation: str) -> None:
    pool = _pool()
    sq = c.build_squad(c.PRESETS["High-press direct"], 30e6, formation, pool)
    assert len(sq.slots) == 11 and len({s.player_id for s in sq.slots}) == 11
    assert sq.total_value_eur <= 30e6
    assert [s.group for s in sq.slots] == SLOT_GROUPS[formation]
    assert len(sq.top_axes) == 3


def test_tight_budget_is_still_respected() -> None:
    pool = _pool()
    cheapest = 11e6 + 4e6  # 4-3-3 needs two each of CB, FB, CM, W: each second one costs €2m
    sq = c.build_squad(c.PRESETS["Possession control"], cheapest, "4-3-3", pool)
    assert sq.total_value_eur <= cheapest


def test_infeasible_budget_raises() -> None:
    with pytest.raises(c.InfeasibleBudget):
        c.build_squad(c.PRESETS["Balanced"], 5e6, "4-3-3", _pool())


def test_min_minutes_and_league_filter() -> None:
    pool = _pool(n_per_group=8)
    pool.loc[pool["player_id"] == "ST0", "minutes_total"] = 500.0
    sq = c.build_squad(c.PRESETS["Balanced"], 1e9, "4-4-2", pool, ["ESP-La Liga"], 900)
    assert all(s.league == "ESP-La Liga" for s in sq.slots)
    assert "ST0" not in {s.player_id for s in sq.slots}


def test_variants() -> None:
    pool = _pool(n_per_group=10)
    best, lean, balanced = c.build_squad_variants(
        c.PRESETS["Low-block counter"], 80e6, "4-3-3", pool
    )
    assert [best.variant, lean.variant, balanced.variant] == [
        "best_fit",
        "budget_lean",
        "most_balanced",
    ]
    assert lean.total_value_eur <= best.total_value_eur
    assert best.avg_fit >= lean.avg_fit
    for sq in (best, lean, balanced):
        assert sq.total_value_eur <= 80e6


def test_most_balanced_limits_clubs() -> None:
    pool = _pool(n_per_group=12)
    pool["team"] = [f"Club {i}" for i in range(len(pool))]  # plenty of clubs to spread over
    pool.loc[pool.index % 2 == 0, "team"] = "Big Club"
    sq = c.build_squad(c.PRESETS["Balanced"], 1e9, "4-3-3", pool, variant="most_balanced")
    assert sum(s.team == "Big Club" for s in sq.slots) <= c.MAX_PER_CLUB_BALANCED
