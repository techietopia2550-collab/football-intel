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


class GameCard(BaseModel):
    """One of a player's matches, as shown on a profile card or the match page."""

    match_id: int
    match_date: date
    team: str
    opponent: str
    home: bool
    team_goals: int | None = None
    opponent_goals: int | None = None
    minutes: float
    position_played: str | None = None
    rating: float | None = None
    # (label, value) pairs that make up the rating, in display order
    components: list[tuple[str, float]] = []

    @property
    def score(self) -> str:
        if self.team_goals is None or self.opponent_goals is None:
            return "-"
        return f"{self.team_goals}-{self.opponent_goals}"


class SeasonGameStats(BaseModel):
    """Match-count based numbers for the hero card. None means unavailable (the tile is hidden)."""

    games: int
    goals: int | None = None
    assists: int | None = None
    clean_sheets: int | None = None
    saves: int | None = None
    save_pct: float | None = None
    goals_conceded: int | None = None


class PlayerGames(BaseModel):
    longest: GameCard | None = None
    best: GameCard | None = None
    worst: GameCard | None = None
