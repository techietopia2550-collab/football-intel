"""Per-match views of one player's season: hero counts, longest / best / worst game, age.

Ratings compare a player's OWN matches with each other (never players with players), so they are
plain, position-aware sums of counts. The exact formulas are in ANALYTICS_DECISIONS.md section 19.

`clean sheet` = the player's team conceded 0 in a match he played. StatsBomb `matches` holds the
score (there is no `match_results` row for these matches: that table is football-data.co.uk).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

from src.models.match import GameCard, PlayerGames, SeasonGameStats
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON

WORST_MIN_MINUTES = 45.0
GK_CLEAN_SHEET_BONUS = 3.0
GK_SAVE_PCT_WEIGHT = 2.0

_ATTACK = ("goals", "assists", "xg", "xa")
_MIDFIELD = ("progressive_passes", "tackles", "key_passes")
_DEFENCE = ("tackles", "interceptions", "clearances")
_LABELS = {
    "goals": "Goals", "assists": "Assists", "xg": "xG", "xa": "xA",
    "progressive_passes": "Progressive passes", "tackles": "Tackles", "key_passes": "Key passes",
    "interceptions": "Interceptions", "clearances": "Clearances", "gk_saves": "Saves",
    "clean_sheet": "Clean sheet", "save_pct": "Save %", "gk_goals_conceded": "Goals conceded",
}  # fmt: skip
# The columns shown as "what made the game" (for GK the rating itself uses saves, clean sheet, save %).
RATING_FEATURES: dict[str, tuple[str, ...]] = {
    "ST": _ATTACK, "W": _ATTACK, "AM": _ATTACK,
    "CM": _MIDFIELD, "DM": _MIDFIELD,
    "CB": _DEFENCE, "FB": _DEFENCE,
    "GK": ("gk_saves", "clean_sheet", "save_pct", "gk_goals_conceded"),
}  # fmt: skip

_SQL = """
SELECT s.*, m.match_date, m.home_team, m.home_score, m.away_score
FROM player_match_stats s
JOIN matches m ON m.source = s.source AND m.match_id = s.match_id
WHERE s.player_id = :p AND s.season = :s
"""


def load_player_matches(
    player_id: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> pd.DataFrame:
    """One row per match the player appeared in, with the score from his team's point of view."""
    df = pd.read_sql(text(_SQL), engine or get_engine(), params={"p": player_id, "s": season})
    df["match_date"] = pd.to_datetime(df["match_date"]).dt.date
    df["home"] = df["home_team"] == df["team"]
    df["team_goals"] = df["home_score"].where(df["home"], df["away_score"])
    df["opponent_goals"] = df["away_score"].where(df["home"], df["home_score"])
    df["clean_sheet"] = (df["opponent_goals"] == 0).astype(float).where(df["opponent_goals"].notna())
    faced = df["gk_saves"] + df["gk_goals_conceded"]
    df["save_pct"] = (df["gk_saves"] / faced.where(faced > 0)).astype(float)
    return df.sort_values(["match_date", "match_id"]).reset_index(drop=True)


def rating_components(match: pd.Series, group: str) -> list[tuple[str, float]]:
    """The (label, value) pairs that make up a match's rating, missing values dropped."""
    out: list[tuple[str, float]] = []
    for feat in RATING_FEATURES[group]:
        v = match[feat]
        if not pd.isna(v):
            out.append((_LABELS[feat], float(v)))
    return out


def rate_match(match: pd.Series, group: str) -> float:
    """Position-aware match rating (section 19). Higher is better."""
    if group == "GK":
        cs = match["clean_sheet"]
        sp = match["save_pct"]
        return float(
            match["gk_saves"]
            + GK_CLEAN_SHEET_BONUS * (0.0 if pd.isna(cs) else cs)
            + GK_SAVE_PCT_WEIGHT * (0.0 if pd.isna(sp) else sp)
        )
    return float(sum(match[f] for f in RATING_FEATURES[group]))


def _card(match: pd.Series, group: str, rated: bool) -> GameCard:
    return GameCard(
        match_id=int(match["match_id"]), match_date=match["match_date"],
        team=match["team"], opponent=match["opponent"], home=bool(match["home"]),
        team_goals=None if pd.isna(match["team_goals"]) else int(match["team_goals"]),
        opponent_goals=None if pd.isna(match["opponent_goals"]) else int(match["opponent_goals"]),
        minutes=float(match["minutes"]), position_played=match["position_played"],
        rating=rate_match(match, group) if rated else None,
        components=rating_components(match, group) if rated else [],
    )  # fmt: skip


def season_game_stats(matches: pd.DataFrame, group: str) -> SeasonGameStats:
    """Games played and, by position, goals / assists or clean sheets / saves / save % / conceded."""
    games = int(matches["match_id"].nunique())
    if group == "GK":
        saves, conceded = int(matches["gk_saves"].sum()), int(matches["gk_goals_conceded"].sum())
        cs = matches["clean_sheet"].dropna()
        return SeasonGameStats(
            games=games,
            clean_sheets=int(cs.sum()) if len(cs) else None,
            saves=saves, goals_conceded=conceded,
            save_pct=saves / (saves + conceded) * 100 if saves + conceded else None,
        )  # fmt: skip
    return SeasonGameStats(
        games=games, goals=int(matches["goals"].sum()), assists=int(matches["assists"].sum())
    )


def player_games(matches: pd.DataFrame, group: str) -> PlayerGames:
    """Longest, best and worst (45+ minutes) game. Ties: more minutes, then earlier date."""
    if matches.empty:
        return PlayerGames()
    m = matches.copy()
    m["rating"] = [rate_match(r, group) for _, r in m.iterrows()]
    longest = m.sort_values(["minutes", "match_date"], ascending=[False, True]).iloc[0]
    best = m.sort_values(["rating", "minutes", "match_date"], ascending=[False, False, True]).iloc[0]
    eligible = m[m["minutes"] >= WORST_MIN_MINUTES]
    worst = (
        eligible.sort_values(["rating", "minutes", "match_date"], ascending=[True, False, True]).iloc[0]
        if len(eligible) else None
    )
    return PlayerGames(
        longest=_card(longest, group, rated=False),
        best=_card(best, group, rated=True),
        worst=None if worst is None else _card(worst, group, rated=True),
    )


def game_card(matches: pd.DataFrame, match_id: int, group: str) -> GameCard | None:
    hit = matches[matches["match_id"] == match_id]
    return None if hit.empty else _card(hit.iloc[0], group, rated=True)


def age_on(birth_date: date | None, on: date) -> int | None:
    """Whole years of age on a date (None when the birth date is unknown or after `on`)."""
    if birth_date is None or birth_date > on:
        return None
    return on.year - birth_date.year - ((on.month, on.day) < (birth_date.month, birth_date.day))


def statsbomb_player_id(player_id: str, engine: Engine | None = None) -> int | None:
    """StatsBomb's own id for one of our player ids (needed to find his events in a match file)."""
    with (engine or get_engine()).connect() as conn:
        row = conn.execute(
            text("SELECT statsbomb_id FROM players WHERE player_id = :p"), {"p": player_id}
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _ratio(done: float, tried: float) -> str:
    return f"{int(done)}/{int(tried)}"


def match_stat_line(match: pd.Series, group: str) -> list[tuple[str, str]]:
    """The player's stats in one match as (label, text) pairs for the match page."""
    s = match
    lines: list[tuple[str, str]] = [
        ("Minutes", f"{s['minutes']:.0f}"),
        ("Position played", str(s["position_played"] or "-")),
    ]
    if group == "GK":
        lines += [
            ("Saves", f"{int(s['gk_saves'])}"), ("Goals conceded", f"{int(s['gk_goals_conceded'])}"),
            ("xG on target faced", f"{s['gk_xg_on_target_faced']:.2f}"),
            ("Claims / punches", f"{int(s['gk_claims'])} / {int(s['gk_punches'])}"),
            ("Sweeper actions", f"{int(s['gk_sweeper_actions'])}"),
        ]  # fmt: skip
    else:
        lines += [
            ("Goals", f"{int(s['goals'])}"), ("Assists", f"{int(s['assists'])}"),
            ("Shots", f"{int(s['shots'])} ({int(s['shots_on_target'])} on target)"),
            ("xG / xA", f"{s['xg']:.2f} / {s['xa']:.2f}"), ("Key passes", f"{int(s['key_passes'])}"),
            ("Dribbles", _ratio(s["dribbles_completed"], s["dribbles_attempted"])),
            ("Tackles", f"{int(s['tackles'])} ({int(s['tackles_won'])} won)"),
            ("Interceptions", f"{int(s['interceptions'])}"),
            ("Clearances", f"{int(s['clearances'])}"), ("Blocks", f"{int(s['blocks'])}"),
            ("Ball recoveries", f"{int(s['ball_recoveries'])}"),
            ("Progressive carries", f"{int(s['progressive_carries'])}"),
        ]  # fmt: skip
    lines.insert(2, ("Passes", _ratio(s["passes_completed"], s["passes_attempted"])))
    lines.insert(3, ("Progressive passes", f"{int(s['progressive_passes'])}"))
    return lines
