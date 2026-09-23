"""Human-readable metric names and one-line explanations for the UI."""

from __future__ import annotations

from src.analytics.metrics import METRICS_BY_NAME

_OVERRIDES: dict[str, str] = {
    "xg_p90": "xG", "xa_p90": "xA", "xg_per_shot": "xG per shot", "goals_p90": "Goals",
    "pass_completion_pct": "Pass completion", "tackle_win_pct": "Tackle win %",
    "dribble_success_pct": "Dribble success %", "aerial_win_pct": "Aerial win %",
    "shot_accuracy": "Shot accuracy", "pressure_duration_avg": "Pressure duration (s)",
    "avg_defensive_action_x": "Avg. defensive action height",
    "def_actions_in_own_box_p90": "Defensive actions in own box",
    "gk_save_pct": "Save %", "gk_goals_prevented_p90": "Goals prevented",
    "gk_goals_conceded_p90": "Goals conceded", "gk_saves_p90": "Saves",
    "gk_claims_p90": "Claims", "gk_punches_p90": "Punches",
    "gk_sweeper_actions_p90": "Sweeper actions", "gk_pass_completion_pct": "Pass completion",
    "gk_shots_on_target_faced_p90": "Shots on target faced",
    "passes_into_final_third_p90": "Passes into final third",
    "passes_received_under_pressure_p90": "Passes received under pressure",
    "progressive_passes_received_p90": "Progressive passes received",
    "cross_clearance_pct": "Cross clearance %",
}  # fmt: skip

_DESCRIPTIONS: dict[str, str] = {
    "goals_p90": "Goals scored per 90 minutes, penalties included.",
    "xg_p90": "Expected goals per 90: the quality of the shots he took.",
    "shots_p90": "Shots per 90 minutes.",
    "xa_p90": "Expected assists per 90: the xG of the shots his passes set up.",
    "key_passes_p90": "Passes that led directly to a shot, per 90.",
    "assists_p90": "Goal assists per 90.",
    "dribbles_p90": "Take-ons attempted per 90.",
    "progressive_passes_p90": "Completed passes that move the ball 10+ yards towards goal, per 90.",
    "progressive_carries_p90": "Carries that move the ball 10+ yards towards goal, per 90.",
    "tackles_p90": "Tackle duels per 90, whatever the outcome.",
    "interceptions_p90": "Interception attempts per 90.",
    "pressures_p90": "Pressure events applied to opponents per 90.",
    "clearances_p90": "Clearances per 90.",
    "blocks_p90": "Shots and passes blocked per 90.",
    "ball_recoveries_p90": "Loose balls won back per 90.",
    "aerials_won_p90": "Aerial duels won per 90 (volume as much as skill).",
    "passes_into_box_p90": "Completed passes into the penalty box per 90.",
    "carries_p90": "Carries per 90.",
}  # fmt: skip


def metric_label(name: str) -> str:
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    base = name.replace("_p90", "").replace("_pct", " %").replace("_", " ").strip()
    return base[:1].upper() + base[1:]


def metric_description(name: str) -> str:
    if name in _DESCRIPTIONS:
        return _DESCRIPTIONS[name]
    metric = METRICS_BY_NAME.get(name)
    if metric and metric.note:
        return metric.note
    return "Per 90 minutes." if name.endswith("_p90") else "Season ratio."
