"""Pydantic schema for one match recap: events, lineups and team stats (from StatsBomb open data)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

EventKind = Literal["goal", "own_goal", "assist", "yellow", "red", "sub_off", "sub_on"]


class MatchEvent(BaseModel):
    """One thing a player did in the match. `related` is the assister (goal), the scorer
    (assist), the replacement (sub_off) or the player who came off (sub_on)."""

    minute: int
    kind: EventKind
    team: str
    player_sb_id: int
    player_name: str
    related: str | None = None
    detail: str | None = None  # "Penalty", "Second yellow"


class LineupPlayer(BaseModel):
    sb_id: int
    name: str
    jersey: int | None = None
    position: str | None = None  # position at kick-off (starters only)
    position_id: int | None = None
    starter: bool
    player_id: str | None = None  # our id, when the player is in the database
    events: list[MatchEvent] = []

    @property
    def position_abbrev(self) -> str:
        if not self.position:
            return ""
        return "GK" if self.position == "Goalkeeper" else "".join(w[0] for w in self.position.split())


class TeamLineup(BaseModel):
    team: str
    formation: str | None = None  # "4-3-3"
    starters: list[LineupPlayer]
    bench: list[LineupPlayer]


class TeamStats(BaseModel):
    """One team's match totals; possession is the team's share of all passes, in percent."""

    possession: float
    shots: int
    shots_on_target: int
    xg: float
    passes: int
    pass_accuracy: float
    corners: int
    fouls: int


class MatchReport(BaseModel):
    match_id: int
    match_date: date
    league: str
    match_week: int | None = None
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    events: list[MatchEvent] = []  # sorted by minute
    home_lineup: TeamLineup | None = None
    away_lineup: TeamLineup | None = None
    home_stats: TeamStats | None = None
    away_stats: TeamStats | None = None
