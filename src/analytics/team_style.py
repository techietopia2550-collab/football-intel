"""Team style vectors: 12 observable event-pattern axes per (team, season, league).

Definitions, limits and the direct-versus-proxy call for every axis are in ANALYTICS_DECISIONS.md
section 17. These describe what a team's events look like, NOT its tactical intent.

Structure: `accumulate_match` turns one match's events into raw season-summable counts per team;
`style_from_counts` turns summed counts into the 12 axes (sums first, ratios after, as everywhere
else in this project); `run` does both for every stored StatsBomb match and writes `team_style`.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any

import pandas as pd
from loguru import logger
from sqlalchemy import Engine, text

from src.analytics.heatmaps import SHOOTOUT_PERIOD, heatmap_types_for
from src.ingest.statsbomb.fetch import fetch_match_events, load_cached_json, statsbomb_cache_root
from src.ingest.team_identity import normalize_team_name
from src.models.schema import get_engine, init_db
from src.transform.normalize import DEFAULT_SEASON, replace_season_rows

TEAM_STYLE_TABLE = "team_style"
SOURCE = "statsbomb"
GOAL = (120.0, 40.0)
PROGRESSIVE_YARDS = 10.0  # same threshold as the player metric (ANALYTICS_DECISIONS.md section 2)
PPDA_ZONE_X = 72.0  # opponent passes starting at x <= 72 (their own 60%) count towards PPDA
SET_PIECE_PATTERNS = {"From Corner", "From Free Kick"}
NON_OPEN_PLAY_PATTERNS = {"From Corner", "From Free Kick", "From Kick Off"}
SET_PIECE_SHOT_TYPES = {"Corner", "Free Kick", "Penalty"}

# (column, display label). The order is the radar order.
AXES: tuple[tuple[str, str], ...] = (
    ("possession_pct", "Possession %"),
    ("ppda", "PPDA"),
    ("direct_speed", "Direct speed"),
    ("buildup_length", "Build-up length"),
    ("def_line_height", "Defensive line height"),
    ("width", "Width"),
    ("crosses_p90", "Crossing volume"),
    ("progressive_pass_rate", "Progressive pass rate"),
    ("xg_per_shot", "Shot quality"),
    ("shots_pm", "Shot volume"),
    ("transition_secs", "Transition speed"),
    ("set_piece_xg_pct", "Set-piece reliance"),
)
AXIS_COLUMNS: tuple[str, ...] = tuple(c for c, _ in AXES)
AXIS_LABELS: dict[str, str] = dict(AXES)
META_COLUMNS = [
    "team_name", "season", "league", "source", "matches_used", "played", "points",
    "goal_diff", "league_position",
]  # fmt: skip


@dataclass
class TeamCounts:
    """Raw counts for one team; adding two of them gives the counts of both matches."""

    matches: int = 0
    passes: int = 0
    opp_passes: int = 0
    opp_passes_in_zone: int = 0  # opponent passes started in their own 60%
    pressing_actions: int = 0  # own tackles, interceptions, fouls at x >= 120 - PPDA_ZONE_X
    possessions: int = 0  # possessions with at least one own pass
    dx_sum: float = 0.0
    y_sum: float = 0.0
    y_sq_sum: float = 0.0
    progressive_passes: int = 0
    crosses: int = 0
    shots: int = 0
    xg: float = 0.0
    set_piece_xg: float = 0.0
    def_actions: int = 0
    def_x_sum: float = 0.0
    transition_n: int = 0
    transition_secs: float = 0.0

    def add(self, other: TeamCounts) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))


def _dist_to_goal(x: float, y: float) -> float:
    return math.hypot(GOAL[0] - x, GOAL[1] - y)


def _clock(e: dict[str, Any]) -> int:
    return int(e["minute"]) * 60 + int(e["second"])


def accumulate_match(events: list[dict[str, Any]]) -> dict[str, TeamCounts]:
    """Raw counts per (normalised) team name for one match. Shoot-out events are ignored."""
    counts: dict[str, TeamCounts] = defaultdict(TeamCounts)
    pass_possessions: dict[str, set[int]] = defaultdict(set)
    # possession id -> [team, first clock, first pattern, first shot clock or None]
    possessions: dict[int, list[Any]] = {}
    teams: list[str] = []

    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD or "team" not in e:
            continue
        team = normalize_team_name(e["team"]["name"])
        if team not in teams:
            teams.append(team)
        c = counts[team]
        kind = e["type"]["name"]
        loc = e.get("location")

        pid = e.get("possession")
        if pid is not None and pid not in possessions and "possession_team" in e:
            possessions[pid] = [
                normalize_team_name(e["possession_team"]["name"]), _clock(e),
                e["play_pattern"]["name"], None,
            ]  # fmt: skip

        if kind == "Pass" and loc is not None and "end_location" in e["pass"]:
            p = e["pass"]
            end = p["end_location"]
            c.passes += 1
            c.dx_sum += end[0] - loc[0]
            c.y_sum += loc[1]
            c.y_sq_sum += loc[1] ** 2
            if pid is not None:
                pass_possessions[team].add(pid)
            if "outcome" not in p and (
                _dist_to_goal(loc[0], loc[1]) - _dist_to_goal(end[0], end[1]) >= PROGRESSIVE_YARDS
            ):
                c.progressive_passes += 1
            if p.get("cross") and p.get("type", {}).get("name") != "Corner":
                c.crosses += 1
        elif kind == "Shot":
            xg = float(e["shot"].get("statsbomb_xg", 0.0))
            c.shots += 1
            c.xg += xg
            if (
                e["shot"].get("type", {}).get("name") in SET_PIECE_SHOT_TYPES
                or e["play_pattern"]["name"] in SET_PIECE_PATTERNS
            ):
                c.set_piece_xg += xg
            if (
                pid in possessions and possessions[pid][3] is None
                and e["shot"].get("type", {}).get("name") != "Penalty"
            ):  # a penalty is not a transition, whatever pattern StatsBomb gives its possession
                possessions[pid][3] = _clock(e)

        if loc is not None and (
            (kind == "Duel" and e.get("duel", {}).get("type", {}).get("name") == "Tackle")
            or kind in {"Interception", "Foul Committed"}
        ) and loc[0] >= 120.0 - PPDA_ZONE_X:
            c.pressing_actions += 1
        if "defensive" in heatmap_types_for(e):
            c.def_actions += 1
            c.def_x_sum += e["location"][0]

    for team in teams:
        c = counts[team]
        c.matches = 1
        c.possessions = len(pass_possessions[team])
        for other in teams:
            if other != team:
                c.opp_passes += counts[other].passes
    # opponent passes inside their own 60%: a second pass over the events, kept separate for clarity
    for e in events:
        if (
            e["period"] < SHOOTOUT_PERIOD and e["type"]["name"] == "Pass" and "team" in e
            and e.get("location") is not None and e["location"][0] <= PPDA_ZONE_X
        ):
            team = normalize_team_name(e["team"]["name"])
            for other in teams:
                if other != team:
                    counts[other].opp_passes_in_zone += 1
    for _pid, (team, start, pattern, shot_clock) in possessions.items():
        if shot_clock is not None and pattern not in NON_OPEN_PLAY_PATTERNS and team in counts:
            counts[team].transition_n += 1
            counts[team].transition_secs += max(shot_clock - start, 0)
    return dict(counts)


def _ratio(num: float, den: float) -> float:
    return num / den if den > 0 else float("nan")


def style_from_counts(c: TeamCounts) -> dict[str, float]:
    """The 12 axes from season-summed counts (NaN where the denominator is zero)."""
    mean_y = _ratio(c.y_sum, c.passes)
    width = math.sqrt(max(_ratio(c.y_sq_sum, c.passes) - mean_y**2, 0.0)) if c.passes else float("nan")
    return {
        "possession_pct": 100 * _ratio(c.passes, c.passes + c.opp_passes),
        "ppda": _ratio(c.opp_passes_in_zone, c.pressing_actions),
        "direct_speed": _ratio(c.dx_sum, c.passes),
        "buildup_length": _ratio(c.passes, c.possessions),
        "def_line_height": _ratio(c.def_x_sum, c.def_actions),
        "width": width,
        "crosses_p90": _ratio(c.crosses, c.matches),
        "progressive_pass_rate": _ratio(c.progressive_passes, c.passes),
        "xg_per_shot": _ratio(c.xg, c.shots),
        "shots_pm": _ratio(c.shots, c.matches),
        "transition_secs": _ratio(c.transition_secs, c.transition_n),
        "set_piece_xg_pct": 100 * _ratio(c.set_piece_xg, c.xg),
    }


def load_standings(engine: Engine, season: str) -> pd.DataFrame:
    """League table per team from the cached StatsBomb match lists (all fixtures StatsBomb lists,
    including the few whose events were not ingested). Ranked by points, goal difference, goals
    for, then name: real leagues' head-to-head tie-breaks are not applied."""
    comps = pd.read_sql(
        text(
            "SELECT DISTINCT league, statsbomb_competition_id AS comp, statsbomb_season_id AS sid "
            "FROM matches WHERE source = :src AND season = :s"
        ),
        engine, params={"src": SOURCE, "s": season},
    )  # fmt: skip
    rows: list[dict[str, Any]] = []
    for r in comps.itertuples():
        path = statsbomb_cache_root() / "matches" / f"{r.comp}_{r.sid}.json"
        for m in load_cached_json(path, list):
            if m.get("home_score") is None or m.get("away_score") is None:
                continue
            h = normalize_team_name(m["home_team"]["home_team_name"])
            a = normalize_team_name(m["away_team"]["away_team_name"])
            hs, as_ = int(m["home_score"]), int(m["away_score"])
            for team, gf, ga in ((h, hs, as_), (a, as_, hs)):
                rows.append(
                    {
                        "league": r.league, "team_name": team, "gf": gf, "ga": ga,
                        "points": 3 if gf > ga else 1 if gf == ga else 0,
                    }
                )  # fmt: skip
    if not rows:
        return pd.DataFrame(columns=["league", "team_name", "played", "points", "goal_diff", "league_position"])
    t = (
        pd.DataFrame(rows).groupby(["league", "team_name"], as_index=False)
        .agg(played=("gf", "size"), points=("points", "sum"), gf=("gf", "sum"), ga=("ga", "sum"))
    )  # fmt: skip
    t["goal_diff"] = t["gf"] - t["ga"]
    t = t.sort_values(["league", "points", "goal_diff", "gf", "team_name"],
                      ascending=[True, False, False, False, True])  # fmt: skip
    t["league_position"] = t.groupby("league").cumcount() + 1
    return t[["league", "team_name", "played", "points", "goal_diff", "league_position"]]


def build_team_style(
    totals: dict[tuple[str, str], TeamCounts], season: str, standings: pd.DataFrame
) -> pd.DataFrame:
    """One row per (league, team) from summed counts, joined to the league table."""
    rows = []
    for (league, team), c in sorted(totals.items()):
        rows.append(
            {
                "team_name": team, "season": season, "league": league, "source": SOURCE,
                "matches_used": c.matches, **style_from_counts(c),
            }
        )  # fmt: skip
    df = pd.DataFrame(rows)
    df = df.merge(standings, on=["league", "team_name"], how="left")
    return df[[*META_COLUMNS, *AXIS_COLUMNS]]


def run(
    season: str = DEFAULT_SEASON,
    engine: Engine | None = None,
    load_events: Callable[[int], list[dict[str, Any]]] = fetch_match_events,
) -> pd.DataFrame:
    """Compute and store `team_style` for a season (replacing its earlier rows), reading the cached
    event files of every stored StatsBomb match. Never touches the network unless a file is missing."""
    engine = engine or init_db(get_engine())
    matches = pd.read_sql(
        text("SELECT match_id, league FROM matches WHERE source = :src AND season = :s ORDER BY league, match_id"),
        engine, params={"src": SOURCE, "s": season},
    )  # fmt: skip
    if matches.empty:
        raise LookupError(f"No stored {SOURCE} matches for {season}")
    totals: dict[tuple[str, str], TeamCounts] = defaultdict(TeamCounts)
    for league, group in matches.groupby("league"):
        for i, match_id in enumerate(group["match_id"], start=1):
            for team, counts in accumulate_match(load_events(int(match_id))).items():
                totals[(str(league), team)].add(counts)
            if i % 100 == 0 or i == len(group):
                logger.info("Team style {lg}: {i}/{n} matches", lg=league, i=i, n=len(group))
    result = build_team_style(totals, season, load_standings(engine, season))
    replace_season_rows(engine, TEAM_STYLE_TABLE, result, season)
    logger.info("{t}: {n} rows", t=TEAM_STYLE_TABLE, n=len(result))
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(description="Compute team style vectors.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    args = parser.parse_args()
    result = run(args.season)
    print(f"{len(result)} teams")


if __name__ == "__main__":
    _main()
