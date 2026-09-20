"""Strengths and weaknesses of one player, from within-position percentiles (fine groups:
GK/CB/FB/DM/CM/AM/W/ST).

`profile(player_name, season)` returns a dict:
  strengths   relevant metrics at or above STRENGTH_PERCENTILE (top 20%), best first
  weaknesses  relevant metrics at or below WEAKNESS_PERCENTILE (bottom 20%), worst first
  neutral     every other relevant metric
  unranked    relevant metrics with no percentile (ratio under its sample-size guard)

Only metrics that are meaningful for the player's position (`RELEVANT_METRICS`) are classified: a
centre-back's low key-pass rate is not a weakness. Descriptive metrics (`higher_is_better` is None,
e.g. fouls committed, pressure duration, goalkeeper claims) are always neutral, and metrics where
lower is better are judged on the flipped percentile.

A player under the minutes threshold gets `qualified=False` and no strengths or weaknesses.
`warnings` lists reasons to treat the result cautiously (small position group, mixed role,
a season truncated by unrecoverable matches).
"""

from __future__ import annotations

import unicodedata
from typing import Any

import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.analytics.metrics import METRICS_BY_NAME
from src.analytics.percentiles import MIN_GROUP_SIZE, PERCENTILES_TABLE
from src.models.schema import get_engine
from src.transform.normalize import (
    DEFAULT_LEAGUE,
    INSUFFICIENT_TABLE,
    season_table_name,
)

STRENGTH_PERCENTILE = 80.0
WEAKNESS_PERCENTILE = 20.0
MIXED_ROLE_SHARE = 0.6  # below this share of minutes in one group, warn about a mixed role

_DISCIPLINE = ["fouls_committed_p90", "fouls_won_p90", "yellow_p90", "red_p90"]
_PRESSING = ["pressures_p90", "counterpressures_p90", "pressure_duration_avg"]
_AERIAL = ["aerials_won_p90", "aerial_win_pct", "aerials_attempted_p90"]
# Descriptive proxies (never judged): where defensive events happened, and how often teammates find
# the player. See the "PROXY" notes in `metrics.py`.
_POSITIONING = [
    "avg_defensive_action_x", "def_actions_in_own_box_p90", "cross_clearances_p90",
    "cross_clearance_pct",
]  # fmt: skip
_RECEIVING = [
    "passes_received_p90", "passes_received_under_pressure_p90", "progressive_passes_received_p90",
]  # fmt: skip
_BALL_PROGRESSION = [
    "progressive_passes_p90", "passes_into_final_third_p90", "carries_p90",
    "progressive_carries_p90", "carry_distance_p90",
]  # fmt: skip
_DEFENDING = ["tackles_p90", "tackle_win_pct", "interceptions_p90", "ball_recoveries_p90"]
_CREATION = ["assists_p90", "xa_p90", "key_passes_p90", "passes_into_box_p90"]
_SCORING = ["goals_p90", "xg_p90", "shots_p90"]
_DRIBBLING = ["dribbles_p90", "dribble_success_pct"]

# Fine position group -> the metrics that mean something for that role. Everything else is not
# judged. Deliberate omissions: creation and shooting metrics for CB and DM (a centre-back's low key
# passes is not a weakness), and pass_completion_pct for AM/W/ST (style, not skill).
RELEVANT_METRICS: dict[str, list[str]] = {
    "CB": [
        "pass_completion_pct", *_BALL_PROGRESSION,
        "tackles_p90", "tackle_win_pct", "interceptions_p90", "blocks_p90", "clearances_p90",
        "ball_recoveries_p90",
        *_AERIAL, *_POSITIONING, *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "FB": [
        "pass_completion_pct", *_BALL_PROGRESSION, *_CREATION, *_DRIBBLING,
        *_DEFENDING, "blocks_p90", "clearances_p90",
        *_AERIAL, *_POSITIONING, *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "DM": [
        "pass_completion_pct", *_BALL_PROGRESSION, *_DEFENDING, "blocks_p90", "clearances_p90",
        "key_passes_p90", "passes_into_box_p90", "xa_p90",
        *_AERIAL, *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "CM": [
        *_SCORING, *_CREATION, "pass_completion_pct", *_BALL_PROGRESSION, *_DRIBBLING,
        *_DEFENDING,
        *_AERIAL, *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "AM": [
        *_SCORING, "shot_accuracy", "xg_per_shot", *_CREATION,
        "progressive_passes_p90", "passes_into_final_third_p90",
        "progressive_carries_p90", "carry_distance_p90", *_DRIBBLING,
        "tackles_p90", "interceptions_p90", "ball_recoveries_p90",
        *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "W": [
        *_SCORING, "shot_accuracy", "xg_per_shot", *_CREATION,
        "progressive_carries_p90", "carry_distance_p90", *_DRIBBLING,
        "tackles_p90", "interceptions_p90", "ball_recoveries_p90",
        *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "ST": [
        *_SCORING, "shot_accuracy", "xg_per_shot", *_CREATION,
        "progressive_carries_p90", "carry_distance_p90", *_DRIBBLING,
        *_AERIAL, *_RECEIVING, *_PRESSING, *_DISCIPLINE,
    ],
    "GK": [
        "gk_save_pct", "gk_goals_prevented_p90", "gk_goals_conceded_p90",
        "gk_shots_on_target_faced_p90", "gk_saves_p90", "gk_claims_p90", "gk_punches_p90",
        "gk_sweeper_actions_p90", "gk_pass_completion_pct",
    ],
}  # fmt: skip


def _fold(text_: str) -> str:
    """Lowercase and strip accents so 'Kante' finds 'N'Golo Kanté'."""
    decomposed = unicodedata.normalize("NFKD", text_)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def find_player(names: pd.DataFrame, query: str) -> pd.Series:
    """The one row of `names` (player_id, player_name, ...) matching `query`, or raise ValueError.

    An exact (accent- and case-insensitive) match wins; otherwise the query must be a substring of
    exactly one name.
    """
    folded = names["player_name"].map(_fold)
    q = _fold(query.strip())
    exact = names[folded == q]
    hits = exact if len(exact) else names[folded.str.contains(q, regex=False)]
    if hits.empty:
        raise ValueError(f"No player matching {query!r}")
    if len(hits) > 1:
        raise ValueError(f"{query!r} is ambiguous: {sorted(hits['player_name'])}")
    return hits.iloc[0]


def classify(rows: pd.DataFrame, group: str) -> dict[str, list[dict[str, Any]]]:
    """Split one player's percentile rows into strengths, weaknesses, neutral and unranked."""
    relevant = RELEVANT_METRICS[group]
    out: dict[str, list[dict[str, Any]]] = {
        "strengths": [], "weaknesses": [], "neutral": [], "unranked": [],
    }  # fmt: skip
    by_metric = rows.set_index("metric")
    for name in relevant:
        if name not in by_metric.index:
            continue
        row = by_metric.loc[name]
        metric = METRICS_BY_NAME[name]
        item: dict[str, Any] = {
            "metric": name,
            "group": metric.group,
            "value": None if pd.isna(row["value"]) else round(float(row["value"]), 3),
            "percentile": None if pd.isna(row["percentile"]) else float(row["percentile"]),
            "sample_size": int(row["sample_size"]),
        }
        if item["percentile"] is None:
            out["unranked"].append(item)
            continue
        if metric.higher_is_better is None:
            out["neutral"].append(item)
            continue
        score = item["percentile"] if metric.higher_is_better else 100 - item["percentile"]
        item["score"] = round(score, 1)
        if score >= STRENGTH_PERCENTILE:
            out["strengths"].append(item)
        elif score <= WEAKNESS_PERCENTILE:
            out["weaknesses"].append(item)
        else:
            out["neutral"].append(item)
    out["strengths"].sort(key=lambda i: -i["score"])
    out["weaknesses"].sort(key=lambda i: i["score"])
    return out


def profile(
    player_name: str,
    season: str = "2015",
    league: str = DEFAULT_LEAGUE,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Strengths, weaknesses and neutral metrics for a player-season. Raises ValueError if the
    name matches no player or more than one."""
    engine = engine or get_engine()
    insp = inspect(engine)
    params = {"s": season, "l": league}
    qualified = pd.read_sql(
        text(f"SELECT * FROM {season_table_name(season)} WHERE league = :l AND season = :s"),
        engine, params=params,
    )  # fmt: skip
    excluded = (
        pd.read_sql(
            text(f"SELECT * FROM {INSUFFICIENT_TABLE} WHERE league = :l AND season = :s"),
            engine,
            params=params,
        )  # fmt: skip
        if insp.has_table(INSUFFICIENT_TABLE)
        else pd.DataFrame(columns=qualified.columns)
    )
    everyone = pd.concat([qualified, excluded], ignore_index=True)
    row = find_player(everyone, player_name)

    base: dict[str, Any] = {
        "player_id": row["player_id"],
        "player_name": row["player_name"],
        "team": row["team"],
        "season": season,
        "league": league,
        "position_group": row["position_group"],  # coarse GK/DEF/MID/FWD, for reference
        "position_group_fine": row["position_group_fine"],  # the group all ranking uses
        "position_group_fine_share": row["position_group_fine_share"],
        "matches": int(row["matches"]),
        "minutes_total": round(float(row["minutes_total"]), 1),
    }
    if row["player_id"] not in set(qualified["player_id"]):
        return {
            **base, "qualified": False,
            "reason": row.get("reason"),
            "strengths": [], "weaknesses": [], "neutral": [], "unranked": [],
            "warnings": ["Below the minutes threshold: no percentiles, not ranked."],
        }  # fmt: skip

    rows = pd.read_sql(
        text(
            f"SELECT * FROM {PERCENTILES_TABLE} "
            "WHERE player_id = :p AND season = :s AND league = :l"
        ),
        engine, params={**params, "p": row["player_id"]},
    )  # fmt: skip
    warnings: list[str] = []
    if rows["small_group"].any():
        warnings.append(
            f"Only {int(rows['group_size'].iloc[0])} qualifying {row['position_group_fine']} "
            f"players "
            f"(< {MIN_GROUP_SIZE}): percentiles are coarse."
        )
    if row["position_group_fine_share"] < MIXED_ROLE_SHARE:
        warnings.append(
            f"Mixed role: only {row['position_group_fine_share']:.0%} of minutes as "
            f"{row['position_group_fine']}."
        )
    if row["n_teams"] > 1:
        warnings.append(
            f"Played for several clubs ({row['team_split']}); team shown is the main one."
        )
    return {
        **base,
        "qualified": True,
        **classify(rows, row["position_group_fine"]),
        "warnings": warnings,
    }
