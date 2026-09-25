"""Read-side helpers the UI needs: the player directory, search, radar data, stats table, heatmap
grids, archetype rosters and the archetype scatter. No rendering, no Streamlit.

Everything here is derived from tables the pipeline already wrote (season table, percentiles,
archetypes, heatmaps) or from the existing engines (`profile.classify`, `similarity.find_similar`,
`archetypes.fit_all`). Nothing is re-tuned: this module only assembles and orders results.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import difflib
import json
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.analytics import archetypes as arch_mod
from src.analytics.metrics import GK, METRICS_BY_NAME, add_derived_totals
from src.analytics.percentiles import PERCENTILES_TABLE
from src.analytics.profile import RELEVANT_METRICS, _fold, classify
from src.analytics.similarity import ARCHETYPES_TABLE, LOW_CONFIDENCE
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON, season_table_name
from src.utils.config import settings

HEATMAP_TABLE = "player_heatmaps"
TOP_BADGES = 3

# Metrics drawn on the radar for each fine position group (all are ranked within that group).
RADAR_METRICS: dict[str, list[str]] = {
    "GK": ["gk_save_pct", "gk_goals_prevented_p90", "gk_goals_conceded_p90", "gk_saves_p90",
           "gk_claims_p90", "gk_sweeper_actions_p90", "gk_pass_completion_pct"],
    "CB": ["tackles_p90", "tackle_win_pct", "interceptions_p90", "clearances_p90", "blocks_p90",
           "aerial_win_pct", "progressive_passes_p90", "pass_completion_pct",
           "ball_recoveries_p90", "progressive_carries_p90"],
    "FB": ["tackles_p90", "interceptions_p90", "progressive_passes_p90", "progressive_carries_p90",
           "key_passes_p90", "xa_p90", "dribbles_p90", "pass_completion_pct", "aerials_won_p90",
           "ball_recoveries_p90"],
    "DM": ["tackles_p90", "interceptions_p90", "ball_recoveries_p90", "pressures_p90",
           "progressive_passes_p90", "pass_completion_pct", "passes_into_final_third_p90",
           "blocks_p90", "aerial_win_pct", "carries_p90"],
    "CM": ["progressive_passes_p90", "key_passes_p90", "xa_p90", "tackles_p90",
           "interceptions_p90", "pressures_p90", "progressive_carries_p90", "dribbles_p90",
           "goals_p90", "passes_into_final_third_p90"],
    "AM": ["goals_p90", "xg_p90", "shots_p90", "key_passes_p90", "xa_p90", "assists_p90",
           "progressive_carries_p90", "dribbles_p90", "passes_into_box_p90", "pressures_p90"],
    "W": ["goals_p90", "xg_p90", "shots_p90", "xa_p90", "key_passes_p90", "assists_p90",
          "dribbles_p90", "dribble_success_pct", "progressive_carries_p90", "pressures_p90"],
    "ST": ["goals_p90", "xg_p90", "shots_p90", "xg_per_shot", "shot_accuracy", "xa_p90",
           "dribbles_p90", "aerials_won_p90", "pressures_p90", "progressive_carries_p90"],
}  # fmt: skip

# The headline metric shown big in a profile's hero card, per group.
PRIMARY_METRIC: dict[str, str] = {
    "GK": "gk_save_pct", "CB": "clearances_p90", "FB": "progressive_carries_p90",
    "DM": "tackles_p90", "CM": "progressive_passes_p90", "AM": "key_passes_p90",
    "W": "dribbles_p90", "ST": "goals_p90",
}  # fmt: skip

# Metrics that are the "xG or equivalent" third hero stat.
SECONDARY_METRIC: dict[str, str] = {
    "GK": "gk_goals_prevented_p90", "CB": "interceptions_p90", "FB": "xa_p90",
    "DM": "interceptions_p90", "CM": "xa_p90", "AM": "xa_p90", "W": "xg_p90", "ST": "xg_p90",
}  # fmt: skip

HEATMAP_TYPES: dict[str, str] = {
    "All actions": "all_actions", "Touches": "touches", "Defensive": "defensive",
    "Passing": "passing", "Shots": "shots", "1st Half": "all_actions_1h",
    "2nd Half": "all_actions_2h",
}  # fmt: skip


def _engine(engine: Engine | None) -> Engine:
    return engine or get_engine()


def _badges(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"metric": i["metric"], "percentile": i["percentile"], "score": i["score"]}
        for i in items[:TOP_BADGES]
    ]


def load_directory(engine: Engine | None = None, season: str = DEFAULT_SEASON) -> pd.DataFrame:
    """One row per qualifying player: identity, minutes, archetype and the top strengths and
    weaknesses (lists of {metric, percentile, score}) from `profile.classify`."""
    engine = _engine(engine)
    stats = pd.read_sql(
        text(
            f"SELECT player_id, player_name, team, league, season, position_group_fine, "
            f"position_group_fine_share, minutes_total, matches, n_teams, team_split "
            f"FROM {season_table_name(season)}"
        ),
        engine,
    )
    arch = pd.read_sql(
        text(
            "SELECT player_id, archetype_id, archetype_label, cluster_confidence "
            f"FROM {ARCHETYPES_TABLE} WHERE season = :s"
        ),
        engine,
        params={"s": season},
    )
    pct = pd.read_sql(
        text(f"SELECT * FROM {PERCENTILES_TABLE} WHERE season = :s"), engine, params={"s": season}
    )
    strengths: dict[str, list[dict[str, Any]]] = {}
    weaknesses: dict[str, list[dict[str, Any]]] = {}
    groups = stats.set_index("player_id")["position_group_fine"]
    for pid, rows in pct.groupby("player_id"):
        cls = classify(rows, groups[pid])
        strengths[str(pid)] = _badges(cls["strengths"])
        weaknesses[str(pid)] = _badges(cls["weaknesses"])
    out = stats.merge(arch, on="player_id", how="left")
    out["strengths"] = out["player_id"].map(lambda p: strengths.get(p, []))
    out["weaknesses"] = out["player_id"].map(lambda p: weaknesses.get(p, []))
    out["name_key"] = out["player_name"].map(_fold)
    return out.sort_values("minutes_total", ascending=False).reset_index(drop=True)


def filter_directory(
    directory: pd.DataFrame,
    leagues: list[str] | None = None,
    groups: list[str] | None = None,
    min_minutes: float = 0.0,
) -> pd.DataFrame:
    out = directory[directory["minutes_total"] >= min_minutes]
    if leagues:
        out = out[out["league"].isin(leagues)]
    if groups:
        out = out[out["position_group_fine"].isin(groups)]
    return out


FUZZY_CUTOFF = 0.72


def search(directory: pd.DataFrame, query: str) -> pd.DataFrame:
    """Players whose name matches `query`, best first. Accent- and case-insensitive: exact name,
    then a name word starting with the query, then a substring, then a fuzzy (typo) match. An
    empty query returns the input unchanged (already ordered by minutes)."""
    q = _fold(query.strip())
    if not q:
        return directory
    scores: list[float] = []
    for key in directory["name_key"]:
        words = key.replace("'", " ").replace("-", " ").split()
        if key == q:
            s = 4.0
        elif any(w == q for w in words):
            s = 3.5
        elif key.startswith(q) or any(w.startswith(q) for w in words):
            s = 3.0
        elif q in key:
            s = 2.0
        else:
            best = max(
                (difflib.SequenceMatcher(None, q, w).ratio() for w in [*words, key]), default=0.0
            )
            s = best if best >= FUZZY_CUTOFF else 0.0
        scores.append(s)
    scored = directory.assign(_score=scores)
    scored = scored[scored["_score"] > 0]
    return scored.sort_values(
        ["_score", "minutes_total"], ascending=[False, False], kind="stable"
    ).drop(columns="_score")


def lineup_correction_counts(season: str = DEFAULT_SEASON) -> dict[str, int]:
    """Number of lineup corrections (dismissal / substitution cuts, see BLOCKERS.md) per player
    name, from `statsbomb_corrections.csv`. Matched on the name written in the report, so a
    player whose lineup spelling differs from his canonical name is undercounted."""
    path = settings.processed_dir / "statsbomb_corrections.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    df = df[(df["season"] == season) & (df["kind"] != "inferred_mapping")]
    names = df["correction"].str.split(": ", n=1).str[0]
    return {str(k): int(v) for k, v in names.value_counts().items()}


def player_row(directory: pd.DataFrame, player_id: str) -> pd.Series | None:
    hit = directory[directory["player_id"] == player_id]
    return None if hit.empty else hit.iloc[0]


def confidence_is_soft(confidence: float | None) -> bool:
    return confidence is not None and not pd.isna(confidence) and confidence < 0.3


def load_player_percentiles(
    player_id: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> pd.DataFrame:
    return pd.read_sql(
        text(f"SELECT * FROM {PERCENTILES_TABLE} WHERE player_id = :p AND season = :s"),
        _engine(engine),
        params={"p": player_id, "s": season},
    )


def load_player_totals(
    player_id: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> pd.Series:
    row = pd.read_sql(
        text(f"SELECT * FROM {season_table_name(season)} WHERE player_id = :p"),
        _engine(engine),
        params={"p": player_id},
    )
    return add_derived_totals(row).iloc[0]


def score_of(metric: str, percentile: float | None) -> float | None:
    """The percentile where a high number is good, flipped where a low number is good. None for a
    missing percentile or a descriptive metric (never judged)."""
    if percentile is None or pd.isna(percentile):
        return None
    hib = METRICS_BY_NAME[metric].higher_is_better
    if hib is None:
        return None
    return float(percentile if hib else 100 - percentile)


def radar_items(rows: pd.DataFrame, group: str) -> list[dict[str, Any]]:
    """Radar axes for one player: metric, raw value, stored percentile and `score` (percentile
    flipped where lower is better, so outward always means better). Axes with no percentile (a
    ratio under its sample guard) are left out."""
    by_metric = rows.set_index("metric")
    items = []
    for name in RADAR_METRICS[group]:
        if name not in by_metric.index:
            continue
        pct, val = by_metric.loc[name, "percentile"], by_metric.loc[name, "value"]
        if pd.isna(pct):
            continue
        hib = METRICS_BY_NAME[name].higher_is_better
        items.append(
            {
                "metric": name, "value": float(val), "percentile": float(pct),
                "score": float(pct if hib is not False else 100 - pct),
                "flipped": hib is False,
            }
        )  # fmt: skip
    return items


def group_reference(
    group: str, metrics: list[str], season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> dict[str, dict[str, float]]:
    """Group maximum and median of each metric's raw value, for scaling a raw-value radar."""
    marks = ",".join(f":m{i}" for i in range(len(metrics)))
    params: dict[str, Any] = {"g": group, "s": season, **{f"m{i}": m for i, m in enumerate(metrics)}}
    df = pd.read_sql(
        text(
            f"SELECT metric, value FROM {PERCENTILES_TABLE} WHERE season = :s "
            f"AND position_group_fine = :g AND metric IN ({marks})"
        ),
        _engine(engine),
        params=params,
    )
    return {
        m: {"max": float(v["value"].max()), "median": float(v["value"].median())}
        for m, v in df.groupby("metric")
    }


def stats_table(rows: pd.DataFrame, totals: pd.Series, group: str) -> pd.DataFrame:
    """Every metric relevant to the player's group: category, metric, value, per-90, percentile,
    score (judged metrics only) and a text band. Rates show the season count as the value and the
    per-90 rate; ratios show the ratio as the value and no per-90."""
    by_metric = rows.set_index("metric")
    out = []
    for name in RELEVANT_METRICS[group]:
        if name not in by_metric.index:
            continue
        m = METRICS_BY_NAME[name]
        v = by_metric.loc[name, "value"]
        pct = by_metric.loc[name, "percentile"]
        if m.kind == "rate":
            total = totals.get(m.numerator)
            value, per90 = (None if pd.isna(total) else float(total)), (
                None if pd.isna(v) else float(v)
            )
        else:
            value, per90 = (None if pd.isna(v) else float(v)), None
        score = score_of(name, pct)
        if pd.isna(pct):
            band = "Not ranked"
        elif score is None:
            band = "Descriptive"
        elif score >= 70:
            band = "Strong"
        elif score < 30:
            band = "Weak"
        else:
            band = "Average"
        out.append(
            {
                "category": m.group.title(), "metric": name, "value": value, "per90": per90,
                "percentile": None if pd.isna(pct) else float(pct), "score": score, "band": band,
                "unit": "%" if m.scale == 100 else "",
            }
        )  # fmt: skip
    return pd.DataFrame(out)


def load_heatmap(
    player_id: str, heatmap_type: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> tuple[np.ndarray, int] | None:
    """(8 x 12 grid of event counts, event count) or None when no heatmap is stored."""
    engine = _engine(engine)
    if not inspect(engine).has_table(HEATMAP_TABLE):
        return None
    df = pd.read_sql(
        text(
            f"SELECT grid_json, event_count FROM {HEATMAP_TABLE} "
            "WHERE player_id = :p AND season = :s AND heatmap_type = :t"
        ),
        engine,
        params={"p": player_id, "s": season, "t": heatmap_type},
    )
    if df.empty:
        return None
    grid = np.array(json.loads(df["grid_json"].iloc[0]), dtype=float)
    return grid, int(df["event_count"].iloc[0])


SIDE_SHARE = 0.55  # share of a wide player's events on one half of the pitch to call it his side


def side_from_grid(grid: np.ndarray, share: float = SIDE_SHARE) -> str | None:
    """'left' or 'right' from where a heatmap's events fall (grid rows run from the left touchline
    to the right), or None for an empty grid or a near-even split."""
    total = grid.sum()
    if total <= 0:
        return None
    left = grid[: grid.shape[0] // 2].sum() / total
    return "left" if left >= share else "right" if left <= 1 - share else None


def side_detection(
    player_id: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> str | None:
    """A player's preferred flank from his all-actions heatmap, or None with no heatmap."""
    hm = load_heatmap(player_id, "all_actions", season, engine)
    if hm is None or hm[1] == 0:
        return None
    return side_from_grid(hm[0])


def has_heatmaps(
    player_ids: list[str], season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> set[str]:
    engine = _engine(engine)
    if not inspect(engine).has_table(HEATMAP_TABLE):
        return set()
    df = pd.read_sql(
        text(f"SELECT DISTINCT player_id FROM {HEATMAP_TABLE} WHERE season = :s"),
        engine,
        params={"s": season},
    )
    return set(df["player_id"]) & set(player_ids)


def archetype_table(directory: pd.DataFrame) -> pd.DataFrame:
    """One row per archetype: id, label, pool, size, mean confidence, ordered outfield first."""
    valid = directory.dropna(subset=["archetype_id"])
    g = valid.groupby(["archetype_id", "archetype_label"])
    out = g.agg(size=("player_id", "size"), confidence=("cluster_confidence", "mean")).reset_index()
    out["archetype_id"] = out["archetype_id"].astype(int)
    out["pool"] = np.where(out["archetype_id"] >= arch_mod.GK_ID_OFFSET, "gk", "outfield")
    return out.sort_values("archetype_id").reset_index(drop=True)


def archetype_exemplars(
    engine: Engine | None = None, season: str = DEFAULT_SEASON, n: int = 5
) -> pd.DataFrame:
    """The `n` players closest to each archetype's centroid (its most typical members)."""
    df = pd.read_sql(
        text(
            f"SELECT player_id, player_name, team, archetype_id, distance_to_centroid "
            f"FROM {ARCHETYPES_TABLE} WHERE season = :s"
        ),
        _engine(engine),
        params={"s": season},
    )
    return (
        df.sort_values("distance_to_centroid")
        .groupby("archetype_id", group_keys=False)
        .head(n)
        .sort_values(["archetype_id", "distance_to_centroid"])
        .reset_index(drop=True)
    )


def archetype_defining_metrics(
    archetype_id: int, engine: Engine | None = None, season: str = DEFAULT_SEASON, n: int = 5
) -> list[tuple[str, float]]:
    """The metrics that define an archetype with the archetype's mean z-score (outfield only;
    goalkeeper archetypes use their fixed nine metrics and return an empty list)."""
    if archetype_id >= arch_mod.GK_ID_OFFSET:
        return []
    engine = _engine(engine)
    stats = pd.read_sql(text(f"SELECT * FROM {season_table_name(season)}"), engine)
    arch = pd.read_sql(
        text(f"SELECT player_id, archetype_id FROM {ARCHETYPES_TABLE} WHERE season = :s"),
        engine,
        params={"s": season},
    )
    outfield = stats.merge(arch, on="player_id")
    outfield = outfield[outfield["position_group_fine"] != GK]
    table = arch_mod.distinctive_table(outfield, archetype_id).head(n)
    return [(r.feature, float(r.archetype_z)) for r in table.itertuples()]


def archetype_scatter(engine: Engine | None = None, season: str = DEFAULT_SEASON) -> pd.DataFrame:
    """Outfield players on the first two principal components of the archetype clustering space
    (the same PCA the archetypes were fitted in): player_id, player_name, x, y, archetype_label."""
    engine = _engine(engine)
    stats = arch_mod.load_pool(engine, season)
    outfield, _ = arch_mod.fit_all(stats, arch_mod.CHOSEN_K or 10, arch_mod.CHOSEN_GK_K or 3)
    table = arch_mod.assignments(outfield, arch_mod.all_labels())
    return pd.DataFrame(
        {
            "player_id": table["player_id"].to_numpy(),
            "player_name": table["player_name"].to_numpy(),
            "team": table["team"].to_numpy(),
            "x": outfield.scores[:, 0], "y": outfield.scores[:, 1],
            "archetype_id": table["archetype_id"].to_numpy(),
            "archetype_label": table["archetype_label"].to_numpy(),
            "distance_to_centroid": table["distance_to_centroid"].to_numpy(),
        }
    )  # fmt: skip


__all__ = [
    "HEATMAP_TYPES", "LOW_CONFIDENCE", "PRIMARY_METRIC", "RADAR_METRICS", "SECONDARY_METRIC",
    "archetype_exemplars", "archetype_scatter", "archetype_table", "filter_directory",
    "load_directory", "radar_items", "search", "stats_table",
]  # fmt: skip
