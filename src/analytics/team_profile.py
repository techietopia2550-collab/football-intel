"""Read side of the Team Fingerprint page: one team's axis scores, headline facts and key players.

Everything the UI shows about a team is computed here; the app only renders it.
Scores are 0-100 ranks among all teams of the season, oriented so that outward on the radar always
means "more of the labelled thing" (see ANALYTICS_DECISIONS.md section 17).
"""

from __future__ import annotations

from collections import Counter

import pandas as pd
from sqlalchemy import Engine, text

from src.analytics.team_similarity import load_team_style
from src.analytics.team_style import AXES, AXIS_COLUMNS, AXIS_LABELS
from src.models.schema import get_engine
from src.models.team import KeyPlayer, TeamProfile
from src.transform.normalize import DEFAULT_SEASON, season_table_name

# Axes where fewer is "more of the thing shown": PPDA (fewer opponent passes per action = harder
# press) and seconds to a shot (fewer = faster). Their scores are flipped for display only.
FLIPPED_AXES = frozenset({"ppda", "transition_secs"})
RADAR_LABELS: dict[str, str] = {
    **AXIS_LABELS,
    "ppda": "Pressing (low PPDA)",
    "transition_secs": "Transition speed",
}
HEADLINE_THRESHOLD = 25.0  # a fact needs a score at least this far from 50
HEADLINE_COUNT = 3

# axis -> (phrase when the score is high, phrase when it is low); wording describes event patterns
_PHRASES: dict[str, tuple[str, str]] = {
    "possession_pct": ("Possession-heavy", "Low possession"),
    "ppda": ("High press (low PPDA)", "Low press (high PPDA)"),
    "direct_speed": ("Direct passing", "Short, patient passing"),
    "buildup_length": ("Long possessions", "Short possessions"),
    "def_line_height": ("Defends high up the pitch", "Deep defensive block"),
    "width": ("Wide passing", "Central passing"),
    "crosses_p90": ("Heavy crossing", "Rarely crosses"),
    "progressive_pass_rate": ("Progressive passing", "Few progressive passes"),
    "xg_per_shot": ("High-quality shots", "Low-quality shots"),
    "shots_pm": ("High shot volume", "Low shot volume"),
    "transition_secs": ("Fast to shoot after winning the ball", "Slow to reach a shot"),
    "set_piece_xg_pct": ("Set-piece reliant", "Little set-piece reliance"),
}
KEY_PLAYER_COUNT = 5


def axis_scores(style: pd.DataFrame) -> pd.DataFrame:
    """0-100 rank of every team on every axis (0 = lowest of the pool, 100 = highest), flipped
    for `FLIPPED_AXES`. Ties share the average rank."""
    ranks = style[list(AXIS_COLUMNS)].astype(float).rank(method="average")
    scores = (ranks - 1) / max(len(style) - 1, 1) * 100
    for axis in FLIPPED_AXES:
        scores[axis] = 100 - scores[axis]
    return scores


def headline_facts(scores: dict[str, float]) -> list[str]:
    """Up to three plain-language facts from the axes furthest from the pool median."""
    ranked = sorted(scores.items(), key=lambda kv: -abs(kv[1] - 50))
    facts = [
        _PHRASES[axis][0] if score > 50 else _PHRASES[axis][1]
        for axis, score in ranked
        if abs(score - 50) >= HEADLINE_THRESHOLD
    ][:HEADLINE_COUNT]
    return facts or ["Close to the average of the 80 teams on every axis"]


def list_teams(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """team_name, league, league_position for every team, in table order within league."""
    style = load_team_style(season, engine)
    return style[["team_name", "league", "league_position"]].sort_values("team_name").reset_index(drop=True)


def team_profile(
    team_name: str, season: str = DEFAULT_SEASON, engine: Engine | None = None,
    style: pd.DataFrame | None = None,
) -> TeamProfile:
    style = style if style is not None else load_team_style(season, engine)
    hit = style.index[style["team_name"] == team_name]
    if len(hit) == 0:
        raise ValueError(f"Unknown team '{team_name}' for {season}")
    i = hit[0]
    row = style.loc[i]
    scores = {k: float(v) for k, v in axis_scores(style).loc[i].items()}

    def _int(col: str) -> int | None:
        return None if pd.isna(row[col]) else int(row[col])

    return TeamProfile(
        team_name=team_name, league=str(row["league"]), season=str(season),
        league_position=_int("league_position"), points=_int("points"), played=_int("played"),
        matches_used=int(row["matches_used"]),
        values={c: float(row[c]) for c in AXIS_COLUMNS}, scores=scores,
        headline_facts=headline_facts(scores),
    )  # fmt: skip


def key_players(
    team_name: str, league: str, season: str = DEFAULT_SEASON, engine: Engine | None = None,
    n: int = KEY_PLAYER_COUNT,
) -> tuple[list[KeyPlayer], str]:
    """The `n` players with most minutes for the team (ranked players only), and their archetype mix
    as text, e.g. '2 Destroyers, 1 Poacher'. Goalkeepers have no archetype and are labelled by pool."""
    engine = engine or get_engine()
    df = pd.read_sql(
        text(
            "SELECT a.player_id, a.player_name, a.position_group_fine, a.archetype_label, s.minutes_total "
            f"FROM player_archetypes a JOIN {season_table_name(season)} s "
            "ON s.player_id = a.player_id AND s.season = a.season "
            "WHERE a.team = :t AND a.league = :l AND a.season = :s "
            "ORDER BY s.minutes_total DESC, a.player_name LIMIT :n"
        ),
        engine, params={"t": team_name, "l": league, "s": season, "n": n},
    )  # fmt: skip
    players = [
        KeyPlayer(
            player_id=r.player_id, player_name=r.player_name, position_group_fine=r.position_group_fine,
            archetype_label=r.archetype_label if isinstance(r.archetype_label, str) else None,
            minutes_total=float(r.minutes_total),
        )
        for r in df.itertuples()
    ]  # fmt: skip
    mix = Counter(p.archetype_label or "Goalkeeper" for p in players)
    text_mix = ", ".join(f"{k} x{v}" if v > 1 else k for k, v in mix.most_common())
    return players, text_mix


__all__ = ["AXES", "axis_scores", "headline_facts", "key_players", "list_teams", "team_profile"]
