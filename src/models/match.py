"""Pydantic schema for match-level results (football-data.co.uk and future sources).

Boundary type between `/src/ingest` and the `match_results` table. Match
level only — this is not a player record.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class MatchRecord(BaseModel):
    """One played match with full-time score and basic team stats."""

    source: str
    season: str
    league: str
    match_date: date
    home_team: str
    away_team: str
    home_goals: int | None = None
    away_goals: int | None = None
    home_shots: int | None = None
    away_shots: int | None = None
    home_shots_on_target: int | None = None
    away_shots_on_target: int | None = None
    home_corners: int | None = None
    away_corners: int | None = None
    home_cards_yellow: int | None = None
    away_cards_yellow: int | None = None
    home_cards_red: int | None = None
    away_cards_red: int | None = None
