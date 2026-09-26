"""Pydantic schemas for Transfermarkt market values (`src/transform/market_values.py`,
`src/analytics/market_values.py`).

A market value is Transfermarkt's community estimate of a transfer fee, not a wage and not a fee
that was paid. Every value field is optional: unmatched or unvalued stays None, never an estimate.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

MatchMethod = Literal["exact", "normalized", "fuzzy", "unmatched"]
# How far the match can be trusted, derived from the method (ANALYTICS_DECISIONS section 27):
# exact_name = the name alone identifies him among the 2015/16 league players; name_club = the name
# is shared, the club decided; fuzzy_name = a fuzzy stage; manual = a reviewed override (none yet).
MatchConfidence = Literal["exact_name", "fuzzy_name", "name_club", "manual"]


class MarketValueRecord(BaseModel):
    """One profiled player's snapshot, ready to upsert into `player_market_values`."""

    player_id: str
    player_name: str
    season: str
    league: str
    team: str
    transfermarkt_id: int | None = None
    transfermarkt_name: str | None = None
    match_method: MatchMethod = "unmatched"
    match_confidence: MatchConfidence | None = None  # None when unmatched
    match_score: float | None = None
    match_note: str | None = None
    valuation_date: date | None = None
    valuation_club: str | None = None
    market_value_eur: float | None = Field(default=None, ge=0)
    market_value_gbp: float | None = Field(default=None, ge=0)
    exchange_rate_used: float | None = Field(default=None, gt=0)
    source: str


class PlayerMarketValue(BaseModel):
    """What the profile shows: the value and its date, or None fields when there is none."""

    player_id: str
    match_method: MatchMethod
    transfermarkt_name: str | None
    valuation_date: date | None
    market_value_eur: float | None


class SquadMarketValue(BaseModel):
    """Sum of the known values of a club's profiled players. A partial sum, not a squad value:
    only players with 900+ minutes are profiled, and unmatched or unvalued players add nothing."""

    team: str
    league: str
    total_eur: float | None  # None when no profiled player has a value
    n_valued: int
    n_profiled: int


class MarketValueCoverage(BaseModel):
    """Match and value counts over the profiled players; by_league maps league ->
    (profiled, matched, valued)."""

    n_profiled: int
    n_matched: int
    n_valued: int
    by_league: dict[str, tuple[int, int, int]]
