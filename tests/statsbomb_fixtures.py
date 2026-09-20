"""Hand-built StatsBomb-shaped match used by the offline tests (no real StatsBomb data).

Two teams, six players, with exactly known metrics. Clock: period 1 ends at 45:30, period 2 at
90:00, so period lengths are 45.5 and 45.0 minutes (total 90.5).

  Team A (id 1)  P10 full match, Center Back
                 P11 Right Wing 0-60:00(p2), Left Wing 60:00-80:00(p2)  -> subbed off at 80:00
                 P12 subbed on at 80:00 (p2), Left Wing
                 P13 unused substitute (no positions)
  Team B (id 2)  P20 full match until sent off at 70:00 (p2)
                 P21 full match (Bad Behaviour red card is recorded but he stays in the lineup)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.models.statsbomb import StatsBombMatch

MATCH = StatsBombMatch(
    match_id=1,
    match_date=date(2016, 1, 1),
    season="2015",
    league="ENG-Premier League",
    home_team="Team A",
    away_team="Team B",
    home_score=1,
    away_score=0,
    statsbomb_competition_id=2,
    statsbomb_season_id=27,
)

_index = 0


def _event(
    period: int, minute: int, second: int, kind: str, player: int, team: int, **extra: Any
) -> dict[str, Any]:
    global _index
    _index += 1
    names = {10: "Alpha", 11: "Bravo", 12: "Charlie", 20: "Delta", 21: "Echo"}
    return {
        "id": f"evt-{_index}",
        "index": _index,
        "period": period,
        "minute": minute,
        "second": second,
        "type": {"name": kind},
        "player": {"id": player, "name": names[player]},
        "team": {"id": team, "name": "Team A" if team == 1 else "Team B"},
        **extra,
    }


def _pass(start: list[float], end: list[float], outcome: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"end_location": end}
    if outcome:
        body["outcome"] = {"name": outcome}
    return _event(1, 10, 0, "Pass", 10, 1, location=start, **{"pass": body})


def build_events() -> list[dict[str, Any]]:
    a = 10
    events = [
        # passes: gains 12 / 9.5 / exactly 10 / 15 (final third) / ~27 (into box) / incomplete
        _pass([40, 40], [52, 40]),
        _pass([60, 40], [69.5, 40]),
        _pass([60, 40], [70, 40]),
        _pass([70, 40], [85, 40]),
        _pass([90, 10], [105, 40]),
        _pass([50, 40], [60, 40], outcome="Incomplete"),
        # carries: 15 yards progressive; 5.83 yards not progressive
        _event(1, 11, 0, "Carry", a, 1, location=[30, 40], carry={"end_location": [45, 40]}),
        _event(1, 11, 5, "Carry", a, 1, location=[45, 40], carry={"end_location": [50, 43]}),
        # pressures: 1.0, 2.0, 3.0 seconds, one counterpress
        _event(1, 12, 0, "Pressure", a, 1, duration=1.0),
        _event(1, 12, 5, "Pressure", a, 1, duration=2.0, counterpress=True),
        _event(1, 12, 9, "Pressure", a, 1, duration=3.0),
        # tackles, aerial duel (not a tackle), interception, block, clearances, recoveries
        _event(
            1, 13, 0, "Duel", a, 1, duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}
        ),
        _event(
            1,
            13,
            5,
            "Duel",
            a,
            1,
            duel={"type": {"name": "Tackle"}, "outcome": {"name": "Lost In Play"}},
        ),
        _event(1, 13, 9, "Duel", a, 1, duel={"type": {"name": "Aerial Lost"}}),
        _event(1, 14, 0, "Interception", a, 1, interception={"outcome": {"name": "Lost In Play"}}),
        _event(1, 14, 5, "Block", a, 1),
        _event(1, 14, 9, "Clearance", a, 1),
        _event(1, 14, 12, "Clearance", a, 1),
        _event(1, 15, 0, "Ball Recovery", a, 1),
        _event(1, 15, 5, "Ball Recovery", a, 1, ball_recovery={"recovery_failure": True}),
        # dribbles
        _event(1, 16, 0, "Dribble", a, 1, dribble={"outcome": {"name": "Complete"}}),
        _event(1, 16, 5, "Dribble", a, 1, dribble={"outcome": {"name": "Incomplete"}}),
        # shots: a goal (0.5), a save (0.25), and a shoot-out goal that must be ignored
        _event(
            1,
            17,
            0,
            "Shot",
            a,
            1,
            id="shot-goal",
            shot={"outcome": {"name": "Goal"}, "statsbomb_xg": 0.5},
        ),  # fmt: skip
        _event(
            1,
            17,
            30,
            "Shot",
            a,
            1,
            id="shot-saved",
            shot={"outcome": {"name": "Saved"}, "statsbomb_xg": 0.25},
        ),  # fmt: skip
        _event(1, 17, 40, "Shot", a, 1, shot={"outcome": {"name": "Blocked"}, "statsbomb_xg": 0.1}),
        # Bravo: an assist (sets up the goal), a key pass (sets up the save), a plain pass
        _event(
            1,
            16,
            50,
            "Pass",
            11,
            1,
            location=[80, 40],
            **{
                "pass": {
                    "end_location": [100, 40],
                    "goal_assist": True,
                    "assisted_shot_id": "shot-goal",
                }
            },
        ),  # fmt: skip
        _event(
            1,
            17,
            20,
            "Pass",
            11,
            1,
            location=[80, 40],
            **{
                "pass": {
                    "end_location": [100, 40],
                    "shot_assist": True,
                    "assisted_shot_id": "shot-saved",
                }
            },
        ),  # fmt: skip
        _event(1, 17, 25, "Pass", 11, 1, location=[50, 40], **{"pass": {"end_location": [55, 40]}}),
        # Delta (Team B goalkeeper): saves the 0.25 shot, concedes the 0.5 one, and a few others
        _event(
            1,
            17,
            31,
            "Goal Keeper",
            20,
            2,
            related_events=["shot-saved"],
            goalkeeper={"type": {"name": "Shot Saved"}},
        ),  # fmt: skip
        _event(
            1,
            17,
            1,
            "Goal Keeper",
            20,
            2,
            related_events=["shot-goal"],
            goalkeeper={"type": {"name": "Goal Conceded"}},
        ),  # fmt: skip
        _event(1, 19, 0, "Goal Keeper", 20, 2, goalkeeper={"type": {"name": "Collected"}}),
        _event(1, 19, 10, "Goal Keeper", 20, 2, goalkeeper={"type": {"name": "Punch"}}),
        _event(1, 19, 20, "Goal Keeper", 20, 2, goalkeeper={"type": {"name": "Keeper Sweeper"}}),
        _event(
            1,
            19,
            30,
            "Goal Keeper",
            20,
            2,
            related_events=["shot-saved"],
            goalkeeper={"type": {"name": "Shot Saved Off Target"}},
        ),  # fmt: skip
        _event(5, 120, 0, "Shot", a, 1, shot={"outcome": {"name": "Goal"}, "statsbomb_xg": 0.78}),
        # fouls and cards
        _event(1, 18, 0, "Foul Committed", a, 1, foul_committed={"card": {"name": "Yellow Card"}}),
        _event(1, 18, 30, "Foul Won", a, 1),
        _event(1, 18, 40, "Foul Won", a, 1),
        # Team B: second yellow (yellow + red), bad-behaviour straight red
        _event(
            2, 70, 0, "Foul Committed", 20, 2, foul_committed={"card": {"name": "Second Yellow"}}
        ),
        _event(2, 72, 0, "Bad Behaviour", 21, 2, bad_behaviour={"card": {"name": "Red Card"}}),
        # Team A substitution at 80:00 of period 2
        _event(
            2,
            80,
            0,
            "Substitution",
            11,
            1,
            substitution={"replacement": {"id": 12, "name": "Charlie"}},
        ),
        # last events fix the period lengths: 45:30 and 90:00
        _event(1, 45, 30, "Ball Receipt*", 10, 1),
        _event(2, 90, 0, "Ball Receipt*", 10, 1),
    ]
    return events


def _pos(
    name: str, start: str, start_period: int, end: str | None, end_period: int | None
) -> dict[str, Any]:
    return {
        "position": name,
        "from": start,
        "from_period": start_period,
        "to": end,
        "to_period": end_period,
    }


def _player(pid: int, name: str, positions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "player_id": pid,
        "player_name": f"{name} Full Name",
        "player_nickname": name,
        "positions": positions,
    }


def build_lineups() -> list[dict[str, Any]]:
    return [
        {
            "team_id": 1,
            "team_name": "Team A",
            "lineup": [
                _player(10, "Alpha", [_pos("Center Back", "00:00", 1, None, None)]),
                _player(
                    11,
                    "Bravo",
                    [
                        _pos("Right Wing", "00:00", 1, "60:00", 2),
                        _pos("Left Wing", "60:00", 2, "80:00", 2),
                    ],
                ),
                _player(12, "Charlie", [_pos("Left Wing", "80:00", 2, None, None)]),
                _player(13, "Unused", []),
            ],
        },
        {
            "team_id": 2,
            "team_name": "Team B",
            "lineup": [
                _player(20, "Delta", [_pos("Goalkeeper", "00:00", 1, "70:00", 2)]),
                _player(21, "Echo", [_pos("Center Forward", "00:00", 1, None, None)]),
            ],
        },
    ]
