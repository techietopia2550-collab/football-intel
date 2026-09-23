"""Pydantic schemas for team style (`src/analytics/team_style.py`, `team_similarity.py`,
`team_profile.py`)."""

from __future__ import annotations

from pydantic import BaseModel


class SimilarTeam(BaseModel):
    rank: int
    team_name: str
    league: str
    similarity: float  # cosine similarity of the z-scored style vectors, -1 to 1
    closest_axes: list[str]  # axes where the two teams' z-scores are closest and both extreme
    biggest_gaps: list[str]  # axes where they differ most


class SimilarTeamsResult(BaseModel):
    """`find_similar_teams` output. A heuristic ranking of observable event patterns, not a
    statement about tactics."""

    team_name: str
    league: str
    season: str
    teams_in_pool: int
    similar: list[SimilarTeam]


class KeyPlayer(BaseModel):
    player_id: str
    player_name: str
    position_group_fine: str
    archetype_label: str | None
    minutes_total: float


class TeamProfile(BaseModel):
    team_name: str
    league: str
    season: str
    league_position: int | None
    points: int | None
    played: int | None
    matches_used: int  # matches whose events fed the style vector
    values: dict[str, float]  # axis -> raw value
    scores: dict[str, float]  # axis -> 0-100 rank among all teams (display orientation)
    headline_facts: list[str]
