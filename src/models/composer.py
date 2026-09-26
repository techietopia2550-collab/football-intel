"""Schemas for the Tactical Composer (`src/analytics/composer.py`).

A suggested XI is a set of players whose own numbers statistically lean the way a target team
style does, within a market-value budget. It is not a tactic, a prediction or a line-up a coach
would pick: form, chemistry, age, injuries and wages are not modelled.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Variant = Literal["standard", "best_fit", "budget_lean", "most_balanced"]


class AxisContribution(BaseModel):
    """How much one style axis pushed a fit up (positive) or down (negative)."""

    axis: str
    label: str
    contribution: float


class SquadSlot(BaseModel):
    slot_label: str
    group: str
    x: float
    y: float
    player_id: str
    player_name: str
    team: str
    league: str
    market_value_eur: float = Field(ge=0)
    fit: float = Field(ge=0, le=1)  # 0-1; 0.5 = neither towards nor against the target
    quality: float = Field(ge=0, le=1)  # sqrt-scaled market value, 0-1
    score: float  # (1 - w) x fit + w x quality: what the greedy build ranked on
    axis_scores: dict[str, float | None]  # the player's 0-1 score per axis
    top_axes: list[AxisContribution]  # his three strongest contributions to the fit


class AxisBreakdown(BaseModel):
    axis: str
    label: str
    target: float  # 0-100
    squad_mean: float | None  # 0-100 mean of the XI's scores; None for team-level-only axes
    used: bool  # False for axes with no player-level counterpart


class Squad(BaseModel):
    variant: Variant
    title: str
    formation: str
    budget_eur: float
    total_value_eur: float
    avg_fit: float  # 0-1
    avg_quality: float  # 0-1
    quality_weight: float  # w the variant used (after its shift)
    slots: list[SquadSlot]  # in formation order
    breakdown: list[AxisBreakdown]
    top_axes: list[AxisContribution]  # the three axes that drove the picks most, squad-wide
    distinct_clubs: int
    notes: list[str] = []
