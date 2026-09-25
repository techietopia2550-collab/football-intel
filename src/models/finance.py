"""Pydantic schemas for team finance (`src/ingest/finance.py`, `src/analytics/finance.py`).

Team-level wage bills only. There is deliberately no player-wage model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["official", "estimate", "aggregated", "unknown"]
FINANCE_CONFIDENCE: tuple[str, ...] = ("official", "estimate", "aggregated", "unknown")


class TeamFinanceRecord(BaseModel):
    """One club's annual wage bill for one league-season, ready to upsert into `team_finance`.

    Every money field is optional: a missing value stays None (NULL), never an estimate.
    """

    team_name: str
    season: str  # database code, e.g. "2015"
    league: str  # database code, e.g. "ENG-Premier League"
    wage_bill_annual_gbp: float | None = Field(default=None, ge=0)
    wage_bill_currency_original: str | None = None  # ISO code, e.g. "GBP", "EUR"
    wage_bill_original_amount: float | None = Field(default=None, ge=0)
    exchange_rate_used: float | None = Field(default=None, gt=0)  # GBP per unit of original
    source: str
    source_url: str | None = None
    confidence: Confidence = "unknown"


class TeamWageContext(BaseModel):
    """A club's wage bill with its rank, for display as team context (not a player's wage)."""

    team_name: str
    season: str
    league: str
    wage_bill_annual_gbp: float | None
    rank: int | None  # 1 = highest wage bill; None when the bill is unknown
    teams_ranked: int  # clubs in the season with a known wage bill
    teams_in_season: int  # clubs in the season's dataset (80 for 2015/16)
    source: str | None
    confidence: str | None
