"""Parse one StatsBomb match (events + lineups) into per-player match aggregates.

Pure functions, no network or database access. Coordinates are StatsBomb's
120x80 pitch in yards, and every team attacks towards x=120, so the goal we
attack is centred at (120, 40) for whichever team performed the event.

Minutes and position come from the lineup file, where each player has
position spells (`positions[].from/to` game-clock times and periods). Play time
is measured on the event clock: the game clock restarts at 45:00 in the second
half, so a clock time is converted to elapsed minutes using each period's real
length (the last event of that period). Substitutions and red cards are already
reflected in the spells; `check_against_substitutions` cross-checks them.

Metric definitions (all count every match event of that type for the player,
excluding the penalty shoot-out, period 5):
  passes_attempted        every `Pass` event, set pieces included.
  passes_completed        `Pass` events with no `pass.outcome` (StatsBomb omits it when complete).
  pass_completion_pct     completed / attempted * 100; None when there are no passes.
  progressive_passes      completed pass that reduces the straight-line distance to the
                          opponent's goal centre by at least PROGRESSIVE_MIN_GAIN yards.
  passes_into_final_third completed pass starting at x < 80 and ending at x >= 80.
  passes_into_box         completed pass ending in the opponent's penalty box
                          (x >= 102, 18 <= y <= 62) that started outside it.
  carries                 `Carry` events.
  progressive_carries     carry that reduces distance to goal by at least PROGRESSIVE_MIN_GAIN.
  carry_distance          total straight-line length of all carries, in yards.
  pressures               `Pressure` events; pressure_duration_avg is their mean duration (s).
  counterpressures        `Pressure` events flagged `counterpress`.
  tackles                 `Duel` events of type Tackle; tackles_won are those with outcome
                          Won / Success / Success In Play / Success Out.
  interceptions           `Interception` events (attempts, whatever the outcome).
  blocks, clearances      `Block` and `Clearance` events.
  ball_recoveries         `Ball Recovery` events not flagged `recovery_failure`.
  dribbles_*              `Dribble` events (take-ons); completed = outcome Complete.
  shots, goals, xg        `Shot` events; goal = outcome Goal; xg = sum of `shot.statsbomb_xg`.
                          Own goals are not shots and are not counted.
  fouls_committed/won     `Foul Committed` / `Foul Won` events.
  yellow_cards            Yellow Card + Second Yellow. red_cards: Red Card + Second Yellow.

Not computed: touches, aerial duels, and passes received are available in the events but not
requested yet. Temporary `Player Off`/`Player On` (injury) events are ignored.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

from src.ingest.team_identity import normalize_team_name
from src.models.statsbomb import PlayerMatchRecord, StatsBombMatch
from src.utils.logging import logger

SOURCE = "statsbomb"

GOAL_X, GOAL_Y = 120.0, 40.0
PROGRESSIVE_MIN_GAIN = 10.0  # yards closer to the opponent's goal centre
FINAL_THIRD_X = 80.0
BOX_X_MIN = 102.0
BOX_Y_MIN, BOX_Y_MAX = 18.0, 62.0
PERIOD_START = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}  # game clock at the start of each period
SHOOTOUT_PERIOD = 5

_TACKLE_WON = {"Won", "Success", "Success In Play", "Success Out"}
_YELLOW = {"Yellow Card", "Second Yellow"}
_RED = {"Red Card", "Second Yellow"}


class LineupError(ValueError):
    """The lineup data cannot be turned into minutes (e.g. a played-in player has no positions)."""


def _clock(event: dict[str, Any]) -> float:
    return event["minute"] + event["second"] / 60


def period_lengths(events: list[dict[str, Any]]) -> dict[int, float]:
    """Real length (minutes) of each played period: last event clock minus the period start."""
    ends: dict[int, float] = {}
    for e in events:
        period = e["period"]
        if period < SHOOTOUT_PERIOD:
            ends[period] = max(ends.get(period, 0.0), _clock(e))
    return {p: end - PERIOD_START[p] for p, end in sorted(ends.items())}


def elapsed_minutes(period: int, clock: float, lengths: dict[int, float]) -> float:
    """Convert a (period, game clock) pair to elapsed play time in minutes."""
    before = sum(length for p, length in lengths.items() if p < period)
    return before + clock - PERIOD_START[period]


def _parse_clock(text: str) -> float:
    minutes, seconds = text.split(":")
    return int(minutes) + int(seconds) / 60


def _distance_to_goal(x: float, y: float) -> float:
    return math.hypot(GOAL_X - x, GOAL_Y - y)


def _in_box(x: float, y: float) -> bool:
    return x >= BOX_X_MIN and BOX_Y_MIN <= y <= BOX_Y_MAX


def _gain(start: list[float], end: list[float]) -> float:
    """Yards closer to goal centre gained between two points (negative if moving away)."""
    return _distance_to_goal(*start) - _distance_to_goal(*end)


def player_spells(
    lineups: list[dict[str, Any]], lengths: dict[int, float]
) -> dict[int, list[tuple[str, float, float]]]:
    """Map player id -> [(position, start_elapsed, end_elapsed)] from the lineup files."""
    total = sum(lengths.values())
    spells: dict[int, list[tuple[str, float, float]]] = {}
    for team in lineups:
        for player in team["lineup"]:
            player_spells_: list[tuple[str, float, float]] = []
            for pos in player.get("positions") or []:
                start = elapsed_minutes(pos["from_period"], _parse_clock(pos["from"]), lengths)
                if pos.get("to") is None:
                    end = total
                else:
                    end = elapsed_minutes(pos["to_period"], _parse_clock(pos["to"]), lengths)
                player_spells_.append((pos["position"], start, min(end, total)))
            spells[player["player_id"]] = player_spells_
    return spells


def _minutes_and_position(spells: list[tuple[str, float, float]]) -> tuple[float, str | None]:
    """Total minutes and the position with the most minutes (earliest spell wins a tie)."""
    by_position: dict[str, float] = defaultdict(float)
    for position, start, end in spells:
        by_position[position] += max(end - start, 0.0)
    if not by_position:
        return 0.0, None
    best = max(by_position.values())
    top = next(pos for pos, _, _ in spells if by_position[pos] == best)
    return sum(by_position.values()), top


def _new_accumulator() -> Counter[str]:
    return Counter()


def aggregate_events(
    events: list[dict[str, Any]],
) -> tuple[dict[int, Counter[str]], dict[int, list[float]]]:
    """Count event metrics per player id. Returns (counters, pressure durations)."""
    stats: dict[int, Counter[str]] = defaultdict(_new_accumulator)
    pressure_durations: dict[int, list[float]] = defaultdict(list)

    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD or "player" not in e:
            continue
        pid = e["player"]["id"]
        s = stats[pid]
        kind = e["type"]["name"]

        if kind == "Pass":
            p = e["pass"]
            s["passes_attempted"] += 1
            if "outcome" not in p:
                s["passes_completed"] += 1
                start, end = e["location"], p["end_location"]
                if _gain(start, end) >= PROGRESSIVE_MIN_GAIN:
                    s["progressive_passes"] += 1
                if start[0] < FINAL_THIRD_X <= end[0]:
                    s["passes_into_final_third"] += 1
                if _in_box(*end) and not _in_box(*start):
                    s["passes_into_box"] += 1
        elif kind == "Carry":
            start, end = e["location"], e["carry"]["end_location"]
            s["carries"] += 1
            s["carry_distance"] += math.hypot(end[0] - start[0], end[1] - start[1])
            if _gain(start, end) >= PROGRESSIVE_MIN_GAIN:
                s["progressive_carries"] += 1
        elif kind == "Pressure":
            s["pressures"] += 1
            if e.get("counterpress"):
                s["counterpressures"] += 1
            if e.get("duration") is not None:
                pressure_durations[pid].append(e["duration"])
        elif kind == "Duel":
            if e["duel"]["type"]["name"] == "Tackle":
                s["tackles"] += 1
                if e["duel"].get("outcome", {}).get("name") in _TACKLE_WON:
                    s["tackles_won"] += 1
        elif kind == "Interception":
            s["interceptions"] += 1
        elif kind == "Block":
            s["blocks"] += 1
        elif kind == "Clearance":
            s["clearances"] += 1
        elif kind == "Ball Recovery":
            if not e.get("ball_recovery", {}).get("recovery_failure"):
                s["ball_recoveries"] += 1
        elif kind == "Dribble":
            s["dribbles_attempted"] += 1
            if e["dribble"]["outcome"]["name"] == "Complete":
                s["dribbles_completed"] += 1
        elif kind == "Shot":
            s["shots"] += 1
            s["xg"] += e["shot"].get("statsbomb_xg", 0.0)
            if e["shot"]["outcome"]["name"] == "Goal":
                s["goals"] += 1
        elif kind == "Foul Won":
            s["fouls_won"] += 1

        if kind == "Foul Committed":
            s["fouls_committed"] += 1
        if kind in {"Foul Committed", "Bad Behaviour"}:
            card = (e.get("foul_committed") or e.get("bad_behaviour") or {}).get("card")
            if card and card["name"] in _YELLOW:
                s["yellow_cards"] += 1
            if card and card["name"] in _RED:
                s["red_cards"] += 1

    return stats, pressure_durations


def parse_match(
    events: list[dict[str, Any]], lineups: list[dict[str, Any]], match: StatsBombMatch
) -> list[PlayerMatchRecord]:
    """Build one `PlayerMatchRecord` per player who was on the pitch."""
    lengths = period_lengths(events)
    spells = player_spells(lineups, lengths)
    stats, pressure_durations = aggregate_events(events)

    team_names = {t["team_id"]: normalize_team_name(t["team_name"]) for t in lineups}
    records: list[PlayerMatchRecord] = []
    for team in lineups:
        team_name = team_names[team["team_id"]]
        opponent = next(name for tid, name in team_names.items() if tid != team["team_id"])
        for player in team["lineup"]:
            pid = player["player_id"]
            minutes, position = _minutes_and_position(spells.get(pid, []))
            if minutes <= 0:
                if pid in stats:
                    raise LineupError(
                        f"Player {pid} ({player['player_name']}) has events but no lineup minutes"
                    )
                continue  # unused substitute

            s = stats.get(pid, Counter())
            attempted = s["passes_attempted"]
            durations = pressure_durations.get(pid, [])
            records.append(
                PlayerMatchRecord(
                    source=SOURCE,
                    match_id=match.match_id,
                    player_id=f"{SOURCE}:{pid}",
                    statsbomb_player_id=pid,
                    player_name=player.get("player_nickname") or player["player_name"],
                    team=team_name,
                    opponent=opponent,
                    season=match.season,
                    league=match.league,
                    position_played=position,
                    minutes=round(minutes, 2),
                    passes_attempted=attempted,
                    passes_completed=s["passes_completed"],
                    pass_completion_pct=(
                        round(100 * s["passes_completed"] / attempted, 1) if attempted else None
                    ),
                    progressive_passes=s["progressive_passes"],
                    passes_into_final_third=s["passes_into_final_third"],
                    passes_into_box=s["passes_into_box"],
                    carries=s["carries"],
                    progressive_carries=s["progressive_carries"],
                    carry_distance=round(s["carry_distance"], 1),
                    pressures=s["pressures"],
                    pressure_duration_avg=(
                        round(sum(durations) / len(durations), 3) if durations else None
                    ),
                    counterpressures=s["counterpressures"],
                    tackles=s["tackles"],
                    tackles_won=s["tackles_won"],
                    interceptions=s["interceptions"],
                    blocks=s["blocks"],
                    clearances=s["clearances"],
                    ball_recoveries=s["ball_recoveries"],
                    dribbles_attempted=s["dribbles_attempted"],
                    dribbles_completed=s["dribbles_completed"],
                    shots=s["shots"],
                    goals=s["goals"],
                    xg=round(s["xg"], 4),
                    fouls_committed=s["fouls_committed"],
                    fouls_won=s["fouls_won"],
                    yellow_cards=s["yellow_cards"],
                    red_cards=s["red_cards"],
                )
            )
    return records


def check_against_substitutions(
    events: list[dict[str, Any]], lineups: list[dict[str, Any]]
) -> list[str]:
    """Cross-check lineup minutes against `Substitution` events; return discrepancy messages.

    For each substitution the player coming off must end (and the replacement start) within
    a minute of the event time in the lineup spells. An empty list means they agree.
    """
    lengths = period_lengths(events)
    spells = player_spells(lineups, lengths)
    problems: list[str] = []
    for e in events:
        if e["type"]["name"] != "Substitution":
            continue
        when = elapsed_minutes(e["period"], _clock(e), lengths)
        off = e["player"]["id"]
        on = e["substitution"]["replacement"]["id"]
        off_end = max((end for _, _, end in spells.get(off, [])), default=None)
        on_start = min((start for _, start, _ in spells.get(on, [])), default=None)
        if off_end is None or abs(off_end - when) > 1.0:
            problems.append(f"player {off} off at {off_end}, substitution event at {when:.2f}")
        if on_start is None or abs(on_start - when) > 1.0:
            problems.append(f"player {on} on at {on_start}, substitution event at {when:.2f}")
    if problems:
        logger.warning("Lineup/substitution mismatches: {p}", p=problems)
    return problems
