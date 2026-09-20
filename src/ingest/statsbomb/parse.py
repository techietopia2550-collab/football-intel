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
  key_passes              `Pass` events that led directly to a shot (they carry `assisted_shot_id`;
                          StatsBomb flags them `shot_assist`, or `goal_assist` if the shot scored).
  assists                 `Pass` events flagged `goal_assist` (a subset of key_passes).
  xa                      sum of `shot.statsbomb_xg` of the shot each key pass set up.
  shots_on_target         `Shot` events with outcome Goal, Saved or Saved to Post (not Blocked,
                          Off T, Wayward, Post or Saved Off Target).
Goalkeeper metrics (from `Goal Keeper` events; shots are linked through `related_events`):
  gk_saves                `Shot Saved`, `Shot Saved to Post`, `Penalty Saved`
                          (`Shot Saved Off Target`
                          and bare `Save` events are not counted).
  gk_goals_conceded       `Goal Conceded`, `Penalty Conceded`.
  gk_xg_on_target_faced   sum of pre-shot `statsbomb_xg` over the shots behind saves and goals
                          conceded. This is NOT post-shot xG: the open data has no PSxG.
  gk_claims, gk_punches   `Collected` and `Punch` events; gk_sweeper_actions: `Keeper Sweeper`.

Position groups: StatsBomb positions are full names ("Right Center Back", id 3), 25 in
total, and each spell in the lineup carries the position actually played. There is no
"Sub" position: a substitute's spell holds the position he took, so every minute on the
pitch has a position. `position_group` is the group (GK/DEF/MID/FWD) with the most minutes
in the match; a tie goes to the group of the player's earliest spell. See POSITION_GROUPS.

Minutes: StatsBomb minutes are event-clock play time, about 97 for a full match once
stoppage time is included. `parse_match(..., minutes_scale=s)` multiplies every player's
minutes by `s`; `match_minutes_scale` returns 90 / match length so a full match is 90.0.

Fine position groups (`position_group_fine`: GK, CB, FB, DM, CM, AM, W, ST): a finer split of the
same lineup positions, chosen the same way (the group with the most minutes in the match, a tie
going to the earliest spell). See FINE_POSITION_GROUPS. Coarse `position_group` is unchanged.

Advanced counts (`aggregate_extra_events`). Checked against the 2015/16 raw events, not assumed:
  aerials_won      events carrying `aerial_won` (only Pass, Clearance, Shot and Miscontrol have it):
                   the winner's follow-up event. 16,845 flags across the 380 cached matches.
  aerials_lost     `Duel` events of type `Aerial Lost`. 16,845 across the same matches, exactly one
                   loser per winner, so the pair is complete.
  passes_received  completed passes (no `pass.outcome`) whose `pass.recipient` is the player.
  progressive_passes_received  the same passes, restricted to progressive ones (see above).
  passes_received_under_pressure  `Ball Receipt*` events with no `ball_receipt.outcome` (a completed
                   receipt) flagged `under_pressure`. Counted on the receipt because the pressure
                   flag on the pass belongs to the passer.
  def_actions      Tackle duels, Interceptions, Clearances and Blocks that carry a location.
  def_action_x_sum sum of their x coordinates (own goal line x=0, opponent goal x=120), so the
                   mean is `def_action_x_sum / def_actions`.
  def_actions_own_box  those events inside the player's own penalty box (x <= 18, 18 <= y <= 62).
  cross_clearances Clearances whose `related_events` include a cross Pass. Clearances with no
                   related event (about 17%) cannot be classified: a lower bound.
Not extracted: touches, freeze-frame based measures (only `Shot` events carry a freeze frame, so
teammate positions at a defensive action are unknown). Penalties are included in shots, goals and
xG. Temporary `Player Off`/`Player On` (injury) events are ignored.
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

# StatsBomb position name -> fine position group (GK, CB, FB, DM, CM, AM, W, ST). The lineup
# position string is never used directly downstream: everything goes through this table. Names
# are StatsBomb's 25 positions. Choices: wing-backs -> FB with the full-backs; every "Midfield"
# that is neither defensive nor attacking (including the wide Left/Right Midfield) -> CM, so 4-4-2
# wide midfielders are CM; "Right/Left Attacking Midfield" -> AM (not W); only "Left/Right Wing"
# -> W; the three forward slots and Secondary Striker -> ST (StatsBomb has no plain "Striker").
FINE_POSITION_GROUPS: dict[str, str] = {
    "Goalkeeper": "GK",
    "Right Center Back": "CB",
    "Center Back": "CB",
    "Left Center Back": "CB",
    "Right Back": "FB",
    "Left Back": "FB",
    "Right Wing Back": "FB",
    "Left Wing Back": "FB",
    "Right Defensive Midfield": "DM",
    "Center Defensive Midfield": "DM",
    "Left Defensive Midfield": "DM",
    "Right Midfield": "CM",
    "Right Center Midfield": "CM",
    "Center Midfield": "CM",
    "Left Center Midfield": "CM",
    "Left Midfield": "CM",
    "Right Attacking Midfield": "AM",
    "Center Attacking Midfield": "AM",
    "Left Attacking Midfield": "AM",
    "Right Wing": "W",
    "Left Wing": "W",
    "Right Center Forward": "ST",
    "Center Forward": "ST",
    "Left Center Forward": "ST",
    "Secondary Striker": "ST",
}
FINE_GROUPS: tuple[str, ...] = ("GK", "CB", "FB", "DM", "CM", "AM", "W", "ST")

OWN_BOX_X_MAX = 18.0  # the player's own penalty box: x <= 18 and 18 <= y <= 62

_TACKLE_WON = {"Won", "Success", "Success In Play", "Success Out"}
_ON_TARGET = {"Goal", "Saved", "Saved to Post"}
_GK_SAVES = {"Shot Saved", "Shot Saved to Post", "Penalty Saved"}
_GK_CONCEDED = {"Goal Conceded", "Penalty Conceded"}
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


def fine_position_group(position: str) -> str | None:
    """Fine group for a StatsBomb position name; None if unmapped (`position_group` logs that)."""
    return FINE_POSITION_GROUPS.get(position)


def _top_fine_group(spells: list[tuple[str, float, float]]) -> str | None:
    """The fine group with the most minutes; a tie goes to the earliest spell. None if unmapped."""
    by_group: dict[str, float] = defaultdict(float)
    for position, start, end in spells:
        group = fine_position_group(position)
        if group is not None:
            by_group[group] += max(end - start, 0.0)
    if not by_group:
        return None
    best = max(by_group.values())
    return next(
        g for pos, _, _ in spells if (g := fine_position_group(pos)) and by_group[g] == best
    )


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
    shot_xg = {  # event id -> xG, to link key passes and goalkeeper events to their shot
        e["id"]: e["shot"].get("statsbomb_xg", 0.0)
        for e in events
        if e["type"]["name"] == "Shot" and "id" in e
    }

    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD or "player" not in e:
            continue
        pid = e["player"]["id"]
        s = stats[pid]
        kind = e["type"]["name"]

        if kind == "Pass":
            p = e["pass"]
            s["passes_attempted"] += 1
            if "assisted_shot_id" in p:
                s["key_passes"] += 1
                s["xa"] += shot_xg.get(p["assisted_shot_id"], 0.0)
                if p.get("goal_assist"):
                    s["assists"] += 1
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
            if e["shot"]["outcome"]["name"] in _ON_TARGET:
                s["shots_on_target"] += 1
        elif kind == "Goal Keeper":
            gk_type = e["goalkeeper"]["type"]["name"]
            linked_xg = sum(shot_xg.get(r, 0.0) for r in e.get("related_events", []))
            if gk_type in _GK_SAVES:
                s["gk_saves"] += 1
                s["gk_xg_on_target_faced"] += linked_xg
            elif gk_type in _GK_CONCEDED:
                s["gk_goals_conceded"] += 1
                s["gk_xg_on_target_faced"] += linked_xg
            elif gk_type == "Collected":
                s["gk_claims"] += 1
            elif gk_type == "Punch":
                s["gk_punches"] += 1
            elif gk_type == "Keeper Sweeper":
                s["gk_sweeper_actions"] += 1
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


def _own_box(x: float, y: float) -> bool:
    return x <= OWN_BOX_X_MAX and BOX_Y_MIN <= y <= BOX_Y_MAX


def aggregate_extra_events(events: list[dict[str, Any]]) -> dict[int, Counter[str]]:
    """Advanced counts per player id (definitions in the module docstring).

    Kept apart from `aggregate_events` because a pass receiver is credited on someone else's
    event, and a receiver with no actions of his own must not look like a player with events but
    no minutes.
    """
    out: dict[int, Counter[str]] = defaultdict(_new_accumulator)
    cross_ids = {
        e["id"]
        for e in events
        if e["type"]["name"] == "Pass" and e["pass"].get("cross") and "id" in e
    }
    for e in events:
        if e["period"] >= SHOOTOUT_PERIOD or "player" not in e:
            continue
        kind = e["type"]["name"]
        pid = e["player"]["id"]
        loc = e.get("location")

        if kind == "Duel" and e["duel"]["type"]["name"] == "Aerial Lost":
            out[pid]["aerials_lost"] += 1
        for key in ("pass", "clearance", "shot", "miscontrol"):
            if e.get(key, {}).get("aerial_won") and kind.lower() == key:
                out[pid]["aerials_won"] += 1

        if kind == "Pass":
            p = e["pass"]
            if "outcome" not in p and "recipient" in p:
                receiver = out[p["recipient"]["id"]]
                receiver["passes_received"] += 1
                if _gain(e["location"], p["end_location"]) >= PROGRESSIVE_MIN_GAIN:
                    receiver["progressive_passes_received"] += 1
        elif kind == "Ball Receipt*":
            if "outcome" not in e.get("ball_receipt", {}) and e.get("under_pressure"):
                out[pid]["passes_received_under_pressure"] += 1

        is_tackle = kind == "Duel" and e["duel"]["type"]["name"] == "Tackle"
        if (is_tackle or kind in {"Interception", "Clearance", "Block"}) and loc:
            out[pid]["def_actions"] += 1
            out[pid]["def_action_x_sum"] += loc[0]
            if _own_box(loc[0], loc[1]):
                out[pid]["def_actions_own_box"] += 1
        if kind == "Clearance" and cross_ids.intersection(e.get("related_events", [])):
            out[pid]["cross_clearances"] += 1
    return out


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
    extra = aggregate_extra_events(events)

    team_names = {t["team_id"]: normalize_team_name(t["team_name"]) for t in lineups}
    records: list[PlayerMatchRecord] = []
    for team in lineups:
        team_name = team_names[team["team_id"]]
        opponent = next(name for tid, name in team_names.items() if tid != team["team_id"])
        for player in team["lineup"]:
            pid = player["player_id"]
            minutes, position, group = _minutes_and_position(spells.get(pid, []))
            fine_group = _top_fine_group(spells.get(pid, []))
            if minutes <= 0:
                if pid in stats:
                    raise LineupError(
                        f"Player {pid} ({player['player_name']}) has events but no lineup minutes"
                    )
                continue  # unused substitute

            s = Counter(stats.get(pid, Counter()))
            s.update(extra.get(pid, {}))
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
                    position_group_fine=fine_group,
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
                    shots_on_target=s["shots_on_target"],
                    assists=s["assists"],
                    key_passes=s["key_passes"],
                    xa=round(s["xa"], 4),
                    gk_saves=s["gk_saves"],
                    gk_goals_conceded=s["gk_goals_conceded"],
                    gk_xg_on_target_faced=round(s["gk_xg_on_target_faced"], 4),
                    gk_claims=s["gk_claims"],
                    gk_punches=s["gk_punches"],
                    gk_sweeper_actions=s["gk_sweeper_actions"],
                    fouls_committed=s["fouls_committed"],
                    fouls_won=s["fouls_won"],
                    yellow_cards=s["yellow_cards"],
                    red_cards=s["red_cards"],
                    aerials_won=s["aerials_won"],
                    aerials_lost=s["aerials_lost"],
                    passes_received=s["passes_received"],
                    passes_received_under_pressure=s["passes_received_under_pressure"],
                    progressive_passes_received=s["progressive_passes_received"],
                    def_actions=s["def_actions"],
                    def_action_x_sum=round(s["def_action_x_sum"], 2),
                    def_actions_own_box=s["def_actions_own_box"],
                    cross_clearances=s["cross_clearances"],
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
