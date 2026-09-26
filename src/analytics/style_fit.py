"""Player style fit: how strongly a player's own numbers lean towards each team-style axis.

A heuristic, not a measurement of a player's tactical fit. For each axis a small set of player
metrics stands in for the team pattern (e.g. PPDA -> pressures and tackles per 90); the player's
score on the axis is the mean of his within-position percentiles on those metrics, divided by 100
(0-1). A player is ranked against his own position group only (pooled across the four leagues),
as in `player_percentiles`, so a centre-back is compared with centre-backs.

Every axis is oriented like the Team Fingerprint radar (`team_profile.axis_scores`): a high score
means "more of the labelled thing", so PPDA is read as pressing intensity and transition seconds as
transition speed. Three axes have no player-level counterpart in the stored data (crossing volume,
width, set-piece reliance): their score is NULL for everyone and they never enter a fit.
ANALYTICS_DECISIONS.md section 28; BLOCKERS.md section 24.

Only players with a market value are stored (the Composer needs a price).

    python -m src.analytics.style_fit --rebuild
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse

import numpy as np
import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.analytics.metrics import GK
from src.analytics.team_style import AXIS_COLUMNS
from src.models.schema import get_engine, init_db
from src.transform.normalize import DEFAULT_SEASON, replace_season_rows, season_table_name
from src.utils.logging import logger

STYLE_FIT_TABLE = "player_style_fit"
SOURCE = "statsbomb+transfermarkt"
PASSES_P90 = "passes_p90"  # not in the metric registry: ranked here from passes_attempted

# axis -> player metrics whose within-position percentiles are averaged. Empty = team-level only.
OUTFIELD_MAP: dict[str, tuple[str, ...]] = {
    "possession_pct": ("pass_completion_pct", PASSES_P90),
    "ppda": ("pressures_p90", "tackles_p90"),
    "direct_speed": ("progressive_carries_p90",),
    "buildup_length": (PASSES_P90, "pass_completion_pct"),
    "def_line_height": ("avg_defensive_action_x",),
    "width": (),
    "crosses_p90": (),
    "progressive_pass_rate": ("progressive_passes_p90",),
    "xg_per_shot": ("xg_per_shot",),
    "shots_pm": ("shots_p90",),
    "transition_secs": ("progressive_carries_p90", "shots_p90"),
    "set_piece_xg_pct": (),
}
# Goalkeepers have no outfield metrics; only these axes have a goalkeeper counterpart.
GK_MAP: dict[str, tuple[str, ...]] = {
    "possession_pct": ("gk_pass_completion_pct",),
    "buildup_length": ("gk_pass_completion_pct",),
    "def_line_height": ("gk_sweeper_actions_p90",),
}
TEAM_ONLY_AXES: tuple[str, ...] = tuple(a for a, m in OUTFIELD_MAP.items() if not m)
PLAYER_AXES: tuple[str, ...] = tuple(a for a in AXIS_COLUMNS if a not in TEAM_ONLY_AXES)
_META = ["player_id", "player_name", "team", "season", "league", "source", "position_group_fine",
         "minutes_total", "market_value_eur"]  # fmt: skip


def group_percentile(values: pd.Series, groups: pd.Series) -> pd.Series:
    """Within-group percentile with the `player_percentiles` rule: (average rank - 0.5) / n * 100,
    NaN values unranked. Pure."""
    out = pd.Series(np.nan, index=values.index, dtype=float)
    for _, idx in values.groupby(groups).groups.items():
        v = values.loc[idx]
        n = int(v.notna().sum())
        if n:
            out.loc[idx] = ((v.rank(method="average") - 0.5) / n * 100).round(1)
    return out


def axis_scores(pct: pd.DataFrame, is_gk: pd.Series) -> pd.DataFrame:
    """0-1 fit per axis from a wide table of percentiles (one column per metric). Mean of the
    available mapped percentiles; NaN when none is available or the axis is team-level only.
    Pure."""
    out = pd.DataFrame(index=pct.index)
    for axis in AXIS_COLUMNS:
        of_cols = [c for c in OUTFIELD_MAP[axis] if c in pct]
        gk_cols = [c for c in GK_MAP.get(axis, ()) if c in pct]
        of = (
            pct[of_cols].mean(axis=1, skipna=True)
            if of_cols
            else pd.Series(np.nan, index=pct.index)
        )
        gk = (
            pct[gk_cols].mean(axis=1, skipna=True)
            if gk_cols
            else pd.Series(np.nan, index=pct.index)
        )
        out[axis] = (gk.where(is_gk, of) / 100).round(4)
    return out


def compute_style_fit(
    stats: pd.DataFrame, percentiles: pd.DataFrame, values: pd.DataFrame
) -> pd.DataFrame:
    """One row per valued player: meta columns + the 12 axis scores. Pure.

    `stats`: the season table. `percentiles`: long `player_percentiles` rows (player_id, metric,
    percentile). `values`: player_id, market_value_eur (NULL values are dropped)."""
    wide = percentiles.pivot_table(index="player_id", columns="metric", values="percentile")
    s = stats.set_index("player_id")
    passes = (s["passes_attempted"] / s["minutes_total"].where(s["minutes_total"] > 0) * 90).where(
        s["position_group_fine"] != GK
    )
    wide = wide.reindex(s.index)
    wide[PASSES_P90] = group_percentile(passes, s["position_group_fine"])
    scores = axis_scores(wide, s["position_group_fine"] == GK)
    v = values.dropna(subset=["market_value_eur"]).set_index("player_id")["market_value_eur"]
    meta = s.assign(market_value_eur=v).reset_index()
    meta = meta.loc[meta["market_value_eur"].notna()]
    out = meta[[c for c in _META if c in meta]].merge(scores, left_on="player_id", right_index=True)
    out["source"] = SOURCE
    return out[_META + list(AXIS_COLUMNS)].reset_index(drop=True)


def run(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """Rebuild `player_style_fit` for a season from stored percentiles and market values."""
    engine = engine or init_db(get_engine())
    stats = pd.read_sql(text(f"SELECT * FROM {season_table_name(season)}"), engine)
    pct = pd.read_sql(
        text("SELECT player_id, metric, percentile FROM player_percentiles WHERE season = :s"),
        engine, params={"s": season},
    )  # fmt: skip
    values = pd.read_sql(
        text("SELECT player_id, market_value_eur FROM player_market_values WHERE season = :s"),
        engine, params={"s": season},
    )  # fmt: skip
    result = compute_style_fit(stats, pct, values)
    replace_season_rows(engine, STYLE_FIT_TABLE, result, season)
    logger.info("{t}: {n} rows", t=STYLE_FIT_TABLE, n=len(result))
    return result


def load_style_fit(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """Every stored row of the season (empty frame when the table has not been built)."""
    engine = engine or get_engine()
    if not inspect(engine).has_table(STYLE_FIT_TABLE):
        return pd.DataFrame(columns=_META + list(AXIS_COLUMNS))
    return pd.read_sql(
        text(f"SELECT * FROM {STYLE_FIT_TABLE} WHERE season = :s"), engine, params={"s": season}
    )


def player_style_fit(
    player_id: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> dict[str, float | None]:
    """The player's 12 axis scores (0-1; None for team-level-only axes or missing data). Empty dict
    when the player has no row (no market value, or not profiled)."""
    fit = load_style_fit(season, engine)
    hit = fit.loc[fit["player_id"] == player_id]
    if hit.empty:
        return {}
    r = hit.iloc[0]
    return {a: None if pd.isna(r[a]) else float(r[a]) for a in AXIS_COLUMNS}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Build player style-fit scores.")
    parser.add_argument("--rebuild", action="store_true", help="Recompute and store the table.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    args = parser.parse_args()
    if not args.rebuild:
        parser.print_help()
        return
    result = run(args.season)
    print(f"{len(result)} players")
    print(result[list(AXIS_COLUMNS)].describe().round(2).T.to_string())


if __name__ == "__main__":
    _main()
