"""Per-match invariant checks for a parsed StatsBomb match.

Pure functions. Each returns a list of human-readable violation messages (empty = all hold).
Violations are reported, never hidden: the season ingest stores the match anyway and lists
every violation in its report and in `data/processed/statsbomb_violations.csv`.

Invariants:
  1. at least MIN_PLAYERS_PER_MATCH players have minutes (two teams of 11).
  2. each team's minutes sum to 11 x match length, less the minutes it played short after
     a dismissal (`Red Card` or `Second Yellow`) or a temporary `Player Off`, within
     TEAM_MINUTES_TOLERANCE.
  3. each team's goals (shots scored + `Own Goal For` events) equal its official score.
  4. every player who played has a position_group and a position_group_fine (unmapped
     positions are violations).
  5. every team name normalizes to a club in the expected season list (when one is given).
"""

from __future__ import annotations

from typing import Any

from src.ingest.statsbomb.parse import (
    SHOOTOUT_PERIOD,
    dismissal_times,
    elapsed_minutes,
    period_lengths,
    player_spells,
)
from src.ingest.team_identity import normalize_team_name
from src.models.statsbomb import PlayerMatchRecord, StatsBombMatch

MIN_PLAYERS_PER_MATCH = 22
TEAM_MINUTES_TOLERANCE = 0.1  # minutes


def temporary_absences(events: list[dict[str, Any]], lengths: dict[int, float]) -> dict[int, float]:
    """Team id -> minutes it played short because a player was temporarily off the pitch.

    A `Player Off` event followed by a `Player On` for the same player is an injury or
    treatment break (the lineup file leaves a matching gap in his spells).
    """
    off: dict[int, float] = {}
    gaps: dict[int, float] = {}
    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD or "player" not in e:
            continue
        kind = e["type"]["name"]
        if kind not in {"Player Off", "Player On"}:
            continue
        when = elapsed_minutes(e["period"], e["minute"] + e["second"] / 60, lengths)
        pid = e["player"]["id"]
        if kind == "Player Off":
            off[pid] = when
        elif pid in off:
            team = e["team"]["id"]
            gaps[team] = gaps.get(team, 0.0) + max(when - off.pop(pid), 0.0)
    return gaps


def team_minutes(
    events: list[dict[str, Any]], lineups: list[dict[str, Any]]
) -> dict[int, tuple[float, float]]:
    """Per team id: (minutes from lineup spells, expected minutes), both unscaled and unrounded.

    Actual uses spells cut at dismissals (as `parse_match` does). Expected is 11 x match length,
    minus the time left after each dismissal of a player who was on the pitch, minus the time
    lost to temporary `Player Off`/`Player On` breaks.
    """
    lengths = period_lengths(events)
    total = sum(lengths.values())
    dismissals = dismissal_times(events, lengths)
    raw_spells = player_spells(lineups, lengths)
    spells = player_spells(lineups, lengths, dismissals)
    team_of = {p["player_id"]: t["team_id"] for t in lineups for p in t["lineup"]}

    expected = {t["team_id"]: 11 * total for t in lineups}
    for pid, when in dismissals.items():
        on_pitch = any(start < when <= end for _, start, end in raw_spells.get(pid, []))
        if on_pitch and pid in team_of:  # a card shown to an unused substitute costs no minutes
            expected[team_of[pid]] -= max(total - when, 0.0)
    for team_id, gap in temporary_absences(events, lengths).items():
        if team_id in expected:
            expected[team_id] -= gap

    actual = {
        t["team_id"]: sum(
            end - start for p in t["lineup"] for _, start, end in spells.get(p["player_id"], [])
        )
        for t in lineups
    }
    return {tid: (actual[tid], expected[tid]) for tid in actual}


def team_goals(events: list[dict[str, Any]]) -> dict[int, int]:
    """Goals per team id: scored shots plus own goals conceded by the opponent."""
    goals: dict[int, int] = {}
    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD:
            continue
        kind = e["type"]["name"]
        if kind == "Shot" and e["shot"]["outcome"]["name"] == "Goal":
            goals[e["team"]["id"]] = goals.get(e["team"]["id"], 0) + 1
        elif kind == "Own Goal For":
            goals[e["team"]["id"]] = goals.get(e["team"]["id"], 0) + 1
    return goals


def check_match(
    events: list[dict[str, Any]],
    lineups: list[dict[str, Any]],
    match: StatsBombMatch,
    records: list[PlayerMatchRecord],
    *,
    known_teams: frozenset[str] | None = None,
) -> list[str]:
    """Run every invariant on one parsed match and return the violations."""
    problems: list[str] = []

    if len(records) < MIN_PLAYERS_PER_MATCH:
        problems.append(f"only {len(records)} players with minutes (need {MIN_PLAYERS_PER_MATCH}+)")

    names = {t["team_id"]: normalize_team_name(t["team_name"]) for t in lineups}
    for team_id, (actual, expected) in team_minutes(events, lineups).items():
        if abs(actual - expected) > TEAM_MINUTES_TOLERANCE:
            problems.append(
                f"{names[team_id]} minutes {actual:.2f} differ from expected {expected:.2f} "
                f"by {actual - expected:+.2f}"
            )

    scores = {match.home_team: match.home_score, match.away_team: match.away_score}
    goals = team_goals(events)
    for team_id, name in names.items():
        official = scores.get(name)
        if official is None:
            problems.append(f"{name} is not one of the match's teams {sorted(scores)}")
        elif goals.get(team_id, 0) != official:
            problems.append(f"{name} goals {goals.get(team_id, 0)} differ from score {official}")

    for r in records:
        if r.position_group is None:
            problems.append(
                f"{r.player_name} ({r.team}) has no position_group ({r.position_played})"
            )
        if r.position_group_fine is None:
            problems.append(
                f"{r.player_name} ({r.team}) has no position_group_fine ({r.position_played})"
            )

    if known_teams is not None:
        for name in names.values():
            if name not in known_teams:
                problems.append(f"team {name!r} is not in the expected club list")
    return problems
