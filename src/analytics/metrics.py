"""Metric definitions for StatsBomb 2015/16 and the pure function that computes them.

One registry (`METRICS`) drives normalization, percentiles and profiles: a metric is defined once.
Every metric is one of:
  - `kind="rate"`:  a season count per 90 minutes: count / minutes_total * 90.
  - `kind="ratio"`: numerator / denominator * scale, from season totals (never an average of
    per-match ratios). A ratio is NaN when its denominator is below `min_denominator`, so a small
    sample is never ranked or described as if it were reliable.

Status of each metric, as required (the count behind every rate is taken straight from
StatsBomb events in `src/ingest/statsbomb/parse.py`; the metric itself is what is labelled here):
  - "direct":      an event-level measure aggregated with no normalization (pressure_duration_avg).
  - "derived":     per-90 normalization or a ratio of event counts.
  - "unavailable": cannot be computed from the 2015/16 open data. Listed in `UNAVAILABLE` only.

Metrics whose note starts with "PROXY" describe an event count honestly; they are not measures of a
trait. Nothing here measures leadership, trust or positioning intelligence. They are descriptive
(`higher_is_better=None`): ranked and used for similarity, never called a strength or weakness.

`higher_is_better` is True/False when a strength is a high/low value, and None for descriptive
metrics (style or context, not quality): they are ranked but never called a strength or weakness.

Outfield metrics (`scope="outfield"`) and goalkeeper metrics (`scope="gk"`) are never mixed:
outfield metrics are NaN for goalkeepers and goalkeeper metrics are NaN for outfield players.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Kind = Literal["rate", "ratio"]
Scope = Literal["outfield", "gk"]
Status = Literal["direct", "derived", "unavailable"]

GK = "GK"
# Fine position groups (`position_group_fine`), the only grouping used for ranking and similarity.
OUTFIELD_GROUPS = ("CB", "FB", "DM", "CM", "AM", "W", "ST")
POSITION_GROUPS = (GK, *OUTFIELD_GROUPS)

# Season totals of `player_match_stats` counts (summed over matches).
COUNT_COLUMNS: tuple[str, ...] = (
    "passes_attempted", "passes_completed", "progressive_passes", "passes_into_final_third",
    "passes_into_box", "carries", "progressive_carries", "carry_distance", "pressures",
    "counterpressures", "tackles", "tackles_won", "interceptions", "blocks", "clearances",
    "ball_recoveries", "dribbles_attempted", "dribbles_completed", "shots", "shots_on_target",
    "goals", "xg", "assists", "key_passes", "xa", "fouls_committed", "fouls_won", "yellow_cards",
    "red_cards", "gk_saves", "gk_goals_conceded", "gk_xg_on_target_faced", "gk_claims",
    "gk_punches", "gk_sweeper_actions", "aerials_won", "aerials_lost", "passes_received",
    "passes_received_under_pressure", "progressive_passes_received", "def_actions",
    "def_action_x_sum", "def_actions_own_box", "cross_clearances",
)  # fmt: skip


@dataclass(frozen=True)
class Metric:
    name: str
    group: str
    scope: Scope
    status: Status
    kind: Kind
    numerator: str  # a column of season totals
    denominator: str | None = None  # ratios only
    scale: float = 1.0  # ratios only (100 for percentages)
    min_denominator: float = 0.0  # ratios only: NaN below this many denominator events
    higher_is_better: bool | None = True
    note: str = ""


def _rate(name: str, group: str, col: str, *, hib: bool | None = True, note: str = "") -> Metric:
    return Metric(name, group, "outfield", "derived", "rate", col, higher_is_better=hib, note=note)


def _ratio(
    name: str, group: str, num: str, den: str, *, scale: float = 1.0, min_den: float,
    note: str = "",
    status: Status = "derived", scope: Scope = "outfield", hib: bool | None = True,
) -> Metric:  # fmt: skip
    return Metric(name, group, scope, status, "ratio", num, den, scale, min_den, hib, note)


METRICS: tuple[Metric, ...] = (
    # ---- ATTACKING -------------------------------------------------------------------------
    _rate("goals_p90", "ATTACKING", "goals", note="Goal shots, penalties included."),
    _rate("xg_p90", "ATTACKING", "xg", note="Sum of StatsBomb pre-shot xG, penalties included."),
    _rate("shots_p90", "ATTACKING", "shots"),
    _ratio("shot_accuracy", "ATTACKING", "shots_on_target", "shots", scale=100, min_den=15,
           note="Shots on target (Goal, Saved, Saved to Post) / shots, %."),
    _ratio("xg_per_shot", "ATTACKING", "xg", "shots", min_den=15,
           note="Average shot quality; penalties included."),
    # ---- CREATION --------------------------------------------------------------------------
    _rate("assists_p90", "CREATION", "assists", note="Passes flagged goal_assist."),
    _rate("xa_p90", "CREATION", "xa", note="xG of the shot each key pass set up."),
    _rate("key_passes_p90", "CREATION", "key_passes", note="Passes that led directly to a shot."),
    _rate("passes_into_box_p90", "CREATION", "passes_into_box",
          note="Completed passes ending in the box that started outside it."),
    # ---- PASSING ---------------------------------------------------------------------------
    _ratio("pass_completion_pct", "PASSING", "passes_completed", "passes_attempted", scale=100,
           min_den=200, note="Style-dependent: long-ball and risky passers score lower."),
    _rate("progressive_passes_p90", "PASSING", "progressive_passes",
          note="Completed passes cutting distance to goal by 10+ yards (our own threshold)."),
    _rate("passes_into_final_third_p90", "PASSING", "passes_into_final_third"),
    # ---- POSSESSION ------------------------------------------------------------------------
    _rate("carries_p90", "POSSESSION", "carries"),
    _rate("progressive_carries_p90", "POSSESSION", "progressive_carries",
          note="Carries cutting distance to goal by 10+ yards (our own threshold)."),
    _rate("carry_distance_p90", "POSSESSION", "carry_distance", note="Yards carried."),
    # Not in the requested list; added because dribbling is needed to describe wide players.
    _rate("dribbles_p90", "POSSESSION", "dribbles_attempted", note="Take-ons attempted."),
    _ratio("dribble_success_pct", "POSSESSION", "dribbles_completed", "dribbles_attempted",
           scale=100, min_den=20),
    # ---- DEFENDING -------------------------------------------------------------------------
    _rate("tackles_p90", "DEFENDING", "tackles", note="All Tackle duels, whatever the outcome."),
    _ratio("tackle_win_pct", "DEFENDING", "tackles_won", "tackles", scale=100, min_den=20),
    _rate("interceptions_p90", "DEFENDING", "interceptions", note="Interception attempts."),
    _rate("blocks_p90", "DEFENDING", "blocks"),
    _rate("clearances_p90", "DEFENDING", "clearances"),
    _rate("ball_recoveries_p90", "DEFENDING", "ball_recoveries", note="Successful recoveries."),
    # ---- PRESSING --------------------------------------------------------------------------
    _rate("pressures_p90", "PRESSING", "pressures"),
    _rate("counterpressures_p90", "PRESSING", "counterpressures"),
    _ratio("pressure_duration_avg", "PRESSING", "pressure_duration_total", "pressures",
           min_den=100, status="direct", hib=None,
           note="Mean pressure duration in seconds, pressure-weighted; a style measure."),
    # ---- DISCIPLINE (descriptive, except drawing fouls) ------------------------------------
    _rate("fouls_committed_p90", "DISCIPLINE", "fouls_committed", hib=None),
    _rate("fouls_won_p90", "DISCIPLINE", "fouls_won"),
    _rate("yellow_p90", "DISCIPLINE", "yellow_cards", hib=None),
    _rate("red_p90", "DISCIPLINE", "red_cards", hib=None,
          note="Almost every player has 0, so it carries little ranking signal."),
    # ---- AERIAL (FACT: won = `aerial_won` flag, lost = `Aerial Lost` duel; see parse.py) -----
    _rate("aerials_attempted_p90", "AERIAL", "aerials_attempted", hib=None,
          note="Won + lost aerial duels. Role and team style decide how many arise."),
    _rate("aerials_won_p90", "AERIAL", "aerials_won",
          note="Volume as much as skill: more contests means more wins."),
    _ratio("aerial_win_pct", "AERIAL", "aerials_won", "aerials_attempted", scale=100, min_den=20,
           note="Won / (won + lost) aerial duels, %. Says nothing about the opponents' aerial "
                "ability or the quality of the ball."),
    # ---- POSITIONING PROXIES (descriptive: where the defensive events happened) ------------
    _ratio("avg_defensive_action_x", "POSITIONING", "def_action_x_sum", "def_actions", min_den=50,
           hib=None,
           note="PROXY: mean x (yards from own goal line, pitch 0-120) of tackles, interceptions, "
                "clearances and blocks. Reflects team defensive line and possession as much as "
                "the player. NOT positioning intelligence."),
    _rate("def_actions_in_own_box_p90", "POSITIONING", "def_actions_own_box", hib=None,
          note="PROXY: tackles/interceptions/clearances/blocks inside the own penalty box. Teams "
               "that defend deep or face more crosses record more. NOT a quality measure."),
    _rate("cross_clearances_p90", "POSITIONING", "cross_clearances", hib=None,
          note="PROXY: clearances whose related event is a cross. A lower bound: about 17% of "
               "clearances have no related event."),
    _ratio("cross_clearance_pct", "POSITIONING", "cross_clearances", "clearances", scale=100,
           min_den=30, hib=None,
           note="PROXY: share of the player's clearances that dealt with a cross. Describes what "
                "he clears, not how well; a lower bound."),
    # ---- RECEIVING (the brief's 'trust' proxies; descriptive) -------------------------------
    _rate("passes_received_p90", "RECEIVING", "passes_received", hib=None,
          note="PROXY: completed passes to the player. Driven by role, team possession and "
               "style. NOT a measure of trust or leadership."),
    _rate("passes_received_under_pressure_p90", "RECEIVING", "passes_received_under_pressure",
          hib=None, note="PROXY: completed receptions flagged under pressure. Same caveats."),
    _rate("progressive_passes_received_p90", "RECEIVING", "progressive_passes_received",
          hib=None, note="PROXY: completed progressive passes (10+ yards toward goal) received."),
    # ---- GOALKEEPER (scope gk) -------------------------------------------------------------
    Metric("gk_shots_on_target_faced_p90", "GK", "gk", "derived", "rate",
           "gk_shots_on_target_faced",
           higher_is_better=None, note="Saves + goals conceded; team-context, not quality."),
    Metric("gk_saves_p90", "GK", "gk", "derived", "rate", "gk_saves", higher_is_better=None,
           note="Volume depends on how many shots the team allows."),
    _ratio("gk_save_pct", "GK", "gk_saves", "gk_shots_on_target_faced", scale=100, min_den=40,
           scope="gk", note="Saves / (saves + goals conceded), %."),
    Metric("gk_goals_conceded_p90", "GK", "gk", "derived", "rate", "gk_goals_conceded",
           higher_is_better=False, note="Team defence contributes heavily; not pure GK quality."),
    Metric("gk_goals_prevented_p90", "GK", "gk", "derived", "rate", "gk_goals_prevented",
           note="PROXY: pre-shot xG of shots on target faced minus goals conceded. Not PSxG."),
    Metric("gk_claims_p90", "GK", "gk", "derived", "rate", "gk_claims", higher_is_better=None,
           note="`Collected` events; a style measure."),
    Metric("gk_punches_p90", "GK", "gk", "derived", "rate", "gk_punches", higher_is_better=None),
    Metric("gk_sweeper_actions_p90", "GK", "gk", "derived", "rate", "gk_sweeper_actions",
           higher_is_better=None, note="`Keeper Sweeper` events; a style measure."),
    _ratio("gk_pass_completion_pct", "GK", "passes_completed", "passes_attempted", scale=100,
           min_den=200, scope="gk", note="Distribution accuracy; style-dependent."),
)  # fmt: skip

METRICS_BY_NAME: dict[str, Metric] = {m.name: m for m in METRICS}

# Cannot be computed from the 2015/16 StatsBomb open data (name -> why).
UNAVAILABLE: dict[str, str] = {
    "psxg_minus_goals (GK)": "The open data has pre-shot xG only, no post-shot xG (PSxG).",
    "crosses_stopped_pct (GK)": "Crosses faced are not recorded, only Collected/Punch outcomes.",
    "last_line_events_p90": "Needs teammate positions at each defensive action; only Shot events "
                            "carry a freeze frame in the open data.",
    "is_captain": "The lineup and event files carry no captain field (0 matches for 'captain' in "
                  "all 380 lineup and event files).",
    "long_pass_pct / launch_pct (GK)": "Computable from pass length; not extracted yet.",
    "npxg": "Computable from shot type; not extracted yet (xG includes penalties).",
}  # fmt: skip


def metrics_for_scope(scope: Scope) -> list[Metric]:
    return [m for m in METRICS if m.scope == scope]


def add_derived_totals(totals: pd.DataFrame) -> pd.DataFrame:
    """Add the season-total helper columns some metrics need. Returns a copy."""
    out = totals.copy()
    out["gk_shots_on_target_faced"] = out["gk_saves"] + out["gk_goals_conceded"]
    out["gk_goals_prevented"] = out["gk_xg_on_target_faced"] - out["gk_goals_conceded"]
    out["aerials_attempted"] = out["aerials_won"] + out["aerials_lost"]
    return out


def compute_metrics(totals: pd.DataFrame) -> pd.DataFrame:
    """Add one column per metric to a table of season totals.

    `totals` needs `minutes_total`, `position_group_fine`, the `COUNT_COLUMNS`, and
    `pressure_duration_total` (sum over matches of mean duration x pressures). Outfield metrics are
    set to NaN for goalkeepers and goalkeeper metrics to NaN for everyone else.
    """
    out = add_derived_totals(totals)
    minutes = out["minutes_total"].where(out["minutes_total"] > 0)
    is_gk = out["position_group_fine"] == GK
    for m in METRICS:
        if m.kind == "rate":
            values = out[m.numerator] / minutes * 90
        else:
            assert m.denominator is not None
            den = out[m.denominator]
            values = (out[m.numerator] / den.where(den >= m.min_denominator)) * m.scale
        applies = is_gk if m.scope == "gk" else ~is_gk
        out[m.name] = values.where(applies, np.nan)
    return out
