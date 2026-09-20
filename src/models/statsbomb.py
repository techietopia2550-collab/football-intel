"""Pydantic schemas for StatsBomb Open Data: match metadata and per-player match aggregates.

Boundary types between `/src/ingest/statsbomb` and the database.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class StatsBombMatch(BaseModel):
    """One match from StatsBomb's `matches/<competition>/<season>.json`."""

    match_id: int
    match_date: date
    season: str  # start year, matching the other tables ("2015" for 2015/16)
    league: str  # canonical league name, e.g. "ENG-Premier League"
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    match_week: int | None = None
    statsbomb_competition_id: int
    statsbomb_season_id: int
    minutes_scale_factor: float | None = None  # set when the match is ingested


class PlayerMatchRecord(BaseModel):
    """Aggregated event metrics for one player in one match.

    Coordinates are StatsBomb's 120x80 pitch, every team attacking towards x=120.
    See `src/ingest/statsbomb/parse.py` for exact metric definitions.
    """

    source: str
    match_id: int
    player_id: str  # our id, "statsbomb:<statsbomb id>"
    statsbomb_player_id: int
    player_name: str
    team: str
    opponent: str
    season: str
    league: str
    position_played: str | None
    position_group: str | None = None  # GK/DEF/MID/FWD from the most minutes; None if unmapped
    position_group_fine: str | None = None  # GK/CB/FB/DM/CM/AM/W/ST, same rule; None if unmapped
    minutes: float  # event-clock minutes times the match's minutes_scale_factor

    passes_attempted: int = 0
    passes_completed: int = 0
    pass_completion_pct: float | None = None
    progressive_passes: int = 0
    passes_into_final_third: int = 0
    passes_into_box: int = 0

    carries: int = 0
    progressive_carries: int = 0
    carry_distance: float = 0.0

    pressures: int = 0
    pressure_duration_avg: float | None = None
    counterpressures: int = 0

    tackles: int = 0
    tackles_won: int = 0
    interceptions: int = 0
    blocks: int = 0
    clearances: int = 0
    ball_recoveries: int = 0

    dribbles_attempted: int = 0
    dribbles_completed: int = 0

    shots: int = 0
    goals: int = 0
    xg: float = 0.0
    shots_on_target: int = 0

    assists: int = 0
    key_passes: int = 0
    xa: float = 0.0

    # goalkeeper actions (see parse.py); zero for outfield players
    gk_saves: int = 0
    gk_goals_conceded: int = 0
    gk_xg_on_target_faced: float = 0.0  # pre-shot xG of shots on target faced, not PSxG
    gk_claims: int = 0
    gk_punches: int = 0
    gk_sweeper_actions: int = 0

    fouls_committed: int = 0
    fouls_won: int = 0
    yellow_cards: int = 0
    red_cards: int = 0

    # advanced counts (see parse.py `aggregate_extra_events`); several are proxies, not skills
    aerials_won: int = 0
    aerials_lost: int = 0
    passes_received: int = 0
    passes_received_under_pressure: int = 0
    progressive_passes_received: int = 0
    def_actions: int = 0
    def_action_x_sum: float = 0.0
    def_actions_own_box: int = 0
    cross_clearances: int = 0
