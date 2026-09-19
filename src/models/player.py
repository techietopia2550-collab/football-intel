"""Pydantic schemas for player-related data.

These are the canonical shapes that flow between /src/ingest,
/src/transform, and /src/analytics. Extend them here as new stats
sources are added — don't redefine ad-hoc dicts elsewhere.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PlayerIdentity(BaseModel):
    """Minimal identity record every player-related table must carry."""

    player_id: str
    player_name: str
    season: str
    league: str
    source: str


class PlayerSeasonRecord(BaseModel):
    """Normalized player-season row, validated before it crosses into the DB.

    This is the boundary type `/src/ingest` produces and `/src/models.schema`
    upserts into `player_season_stats` — no bare dicts between the two.
    """

    player_id: str
    canonical_name: str
    source: str
    season: str
    league: str
    team: str | None = None
    position: str | None = None
    position_group: str | None = None
    minutes: int | None = None
    matches: int | None = None
    birth_year: int | None = None
    nationality: str | None = None
    raw_stats: dict[str, Any]
