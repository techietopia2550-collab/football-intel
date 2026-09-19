"""Parse one StatsBomb match (events + lineups) into per-player match aggregates.

Pure functions, no network or database access. Coordinates are StatsBomb's
120x80 pitch in yards, and every team attacks towards x=120, so the goal we
attack is centred at (120, 40) for whichever team performed the event.

Minutes and position come from the lineup file, where each player has
position spells (`positions[].from/to` game-clock times and periods). Play time
is measured on the event clock: the game clock restarts at 45:00 in the second
half, so a clock time is converted to elapsed minutes using each period's real
length (the last event of that period). Substitutions are reflected in the spells and
`check_against_substitutions` cross-checks them. Red cards are NOT reliably reflected:
StatsBomb can leave a sent-off player's last spell open until the final whistle (Courtois in
Chelsea v Swansea 2015/16), so spells are cut at the dismissal event (`dismissal_times`) and each
cut is reported by `find_corrections`. Temporary `Player Off`/`Player On` breaks are already
gaps in the spells.

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

Position groups: StatsBomb positions are full names ("Right Center Back", id 3), 25 in
total, and each spell in the lineup carries the position actually played. There is no
"Sub" position: a substitute's spell holds the position he took, so every minute on the
pitch has a position. `position_group` is the group (GK/DEF/MID/FWD) with the most minutes
in the match; a tie goes to the group of the player's earliest spell. See POSITION_GROUPS.

Minutes: StatsBomb minutes are event-clock play time, about 97 for a full match once
stoppage time is included. `parse_match(..., minutes_scale=s)` multiplies every player's
minutes by `s`; `match_minutes_scale` returns 90 / match length so a full match is 90.0.

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

FULL_MATCH_MINUTES = 90.0

# StatsBomb position name -> position group. Keyed on the name as it appears in lineups/events.
# Ids are StatsBomb's position_id. Observed in real data so far: 1, 2, 3, 5, 6, 12, 13, 15, 16,
# 22, 24 (one match). The others come from StatsBomb's documented list of 25 positions and are
# unverified until the season data shows them; an unmapped name is logged and left NULL.
# Choices: wing-backs -> DEF (they hold the back line, as Understat's DL/DR do); defensive and
# all central/wide midfielders -> MID (so 4-4-2 wide midfielders such as Mahrez are MID, unlike
# Understat's wide attacking mids, which are FWD); attacking midfielders on the flanks and
# wingers -> FWD; central attacking midfielders -> MID.
POSITION_GROUPS: dict[str, str] = {
    "Goalkeeper": "GK",  # 1
    "Right Back": "DEF",  # 2
    "Right Center Back": "DEF",  # 3
    "Center Back": "DEF",  # 4
    "Left Center Back": "DEF",  # 5
    "Left Back": "DEF",  # 6
    "Right Wing Back": "DEF",  # 7
    "Left Wing Back": "DEF",  # 8
    "Right Defensive Midfield": "MID",  # 9
    "Center Defensive Midfield": "MID",  # 10
    "Left Defensive Midfield": "MID",  # 11
    "Right Midfield": "MID",  # 12
    "Right Center Midfield": "MID",  # 13
    "Center Midfield": "MID",  # 14
    "Left Center Midfield": "MID",  # 15
    "Left Midfield": "MID",  # 16
    "Right Wing": "FWD",  # 17
    "Right Attacking Midfield": "FWD",  # 18
    "Center Attacking Midfield": "MID",  # 19
    "Left Attacking Midfield": "FWD",  # 20
    "Left Wing": "FWD",  # 21
    "Right Center Forward": "FWD",  # 22
    "Center Forward": "FWD",  # 23
    "Left Center Forward": "FWD",  # 24
    "Secondary Striker": "FWD",  # 25
}

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


def dismissal_times(events: list[dict[str, Any]], lengths: dict[int, float]) -> dict[int, float]:
    """Player id -> elapsed minute of his dismissal (`Red Card` or `Second Yellow`)."""
    out: dict[int, float] = {}
    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD or "player" not in e:
            continue
        if e["type"]["name"] in {"Foul Committed", "Bad Behaviour"}:
            card = (e.get("foul_committed") or e.get("bad_behaviour") or {}).get("card")
            if card and card["name"] in _RED:
                out.setdefault(e["player"]["id"], elapsed_minutes(e["period"], _clock(e), lengths))
    return out


def player_spells(
    lineups: list[dict[str, Any]],
    lengths: dict[int, float],
    dismissals: dict[int, float] | None = None,
) -> dict[int, list[tuple[str, float, float]]]:
    """Map player id -> [(position, start_elapsed, end_elapsed)] from the lineup files.

    If `dismissals` is given, a player's spells are cut at his dismissal time: a sent-off
    player cannot play on, and StatsBomb's lineup can leave his last spell open until the
    final whistle (seen in Chelsea v Swansea 2015/16, where Courtois is sent off).
    """
    total = sum(lengths.values())
    spells: dict[int, list[tuple[str, float, float]]] = {}
    for team in lineups:
        for player in team["lineup"]:
            player_spells_: list[tuple[str, float, float]] = []
            cutoff = (dismissals or {}).get(player["player_id"])
            for pos in player.get("positions") or []:
                start = elapsed_minutes(pos["from_period"], _parse_clock(pos["from"]), lengths)
                if pos.get("to") is None:
                    end = total
                else:
                    end = elapsed_minutes(pos["to_period"], _parse_clock(pos["to"]), lengths)
                end = min(end, total)
                if cutoff is not None:
                    if start >= cutoff:
                        continue
                    end = min(end, cutoff)
                player_spells_.append((pos["position"], start, end))
            spells[player["player_id"]] = player_spells_
    return spells


def find_corrections(events: list[dict[str, Any]], lineups: list[dict[str, Any]]) -> list[str]:
    """Describe every player whose lineup minutes were cut back to his dismissal time.

    Only reports cuts larger than 0.1 minutes; a lineup that already ends the spell at the
    dismissal needs no correction and produces no message.
    """
    lengths = period_lengths(events)
    dismissals = dismissal_times(events, lengths)
    raw = player_spells(lineups, lengths)
    cut = player_spells(lineups, lengths, dismissals)
    names = {
        p["player_id"]: p.get("player_nickname") or p["player_name"]
        for t in lineups
        for p in t["lineup"]
    }
    messages = []
    for pid, when in dismissals.items():
        removed = sum(e - s for _, s, e in raw.get(pid, [])) - sum(
            e - s for _, s, e in cut.get(pid, [])
        )
        if removed > 0.1:
            messages.append(
                f"{names.get(pid, pid)}: {removed:.1f} min of lineup spells after his "
                f"dismissal at {when:.1f} min removed"
            )
    return messages


def position_group(position: str) -> str | None:
    """GK/DEF/MID/FWD for a StatsBomb position name; None (and a warning) if it is unmapped."""
    group = POSITION_GROUPS.get(position)
    if group is None:
        logger.warning(
            "Unmapped StatsBomb position {name!r}; position_group left NULL", name=position
        )
    return group


def match_minutes_scale(events: list[dict[str, Any]], *, normalize: bool) -> float:
    """Factor that turns event-clock minutes into minutes where a full match is 90.0.

    1.0 when `normalize` is False. This is an approximation: it spreads stoppage time evenly.
    """
    if not normalize:
        return 1.0
    total = sum(period_lengths(events).values())
    return FULL_MATCH_MINUTES / total if total > 0 else 1.0


def _minutes_and_position(
    spells: list[tuple[str, float, float]],
) -> tuple[float, str | None, str | None]:
    """Total minutes, the position with the most minutes, and the group with the most minutes.

    Ties go to the earliest spell. The group is None if no spell's position is mapped.
    """
    by_position: dict[str, float] = defaultdict(float)
    by_group: dict[str, float] = defaultdict(float)
    for position, start, end in spells:
        length = max(end - start, 0.0)
        by_position[position] += length
        group = position_group(position)
        if group is not None:
            by_group[group] += length
    if not by_position:
        return 0.0, None, None
    best = max(by_position.values())
    top = next(pos for pos, _, _ in spells if by_position[pos] == best)

    top_group: str | None = None
    if by_group:
        best_group = max(by_group.values())
        top_group = next(
            g
            for pos, _, _ in spells
            if (g := POSITION_GROUPS.get(pos)) and by_group[g] == best_group
        )
    return sum(by_position.values()), top, top_group


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
    events: list[dict[str, Any]],
    lineups: list[dict[str, Any]],
    match: StatsBombMatch,
    *,
    minutes_scale: float = 1.0,
) -> list[PlayerMatchRecord]:
    """Build one `PlayerMatchRecord` per player who was on the pitch.

    Every player's minutes are multiplied by `minutes_scale` (see `match_minutes_scale`).
    """
    lengths = period_lengths(events)
    spells = player_spells(lineups, lengths, dismissal_times(events, lengths))
    stats, pressure_durations = aggregate_events(events)

    team_names = {t["team_id"]: normalize_team_name(t["team_name"]) for t in lineups}
    records: list[PlayerMatchRecord] = []
    for team in lineups:
        team_name = team_names[team["team_id"]]
        opponent = next(name for tid, name in team_names.items() if tid != team["team_id"])
        for player in team["lineup"]:
            pid = player["player_id"]
            minutes, position, group = _minutes_and_position(spells.get(pid, []))
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
                    position_group=group,
                    minutes=round(minutes * minutes_scale, 2),
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
