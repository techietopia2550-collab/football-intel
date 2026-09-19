"""A complete, valid 22-player StatsBomb-shaped match for invariant and season-loop tests."""

from __future__ import annotations

from typing import Any

from src.models.statsbomb import StatsBombMatch
from tests.statsbomb_fixtures import MATCH, _player, _pos

_index = 0


def _event(
    period: int,
    minute: int,
    second: int,
    kind: str,
    team: int,
    player: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    global _index
    _index += 1
    event: dict[str, Any] = {
        "index": _index,
        "period": period,
        "minute": minute,
        "second": second,
        "type": {"name": kind},
        "team": {"id": team, "name": "Team A" if team == 1 else "Team B"},
        **extra,
    }
    if player is not None:
        event["player"] = {"id": player, "name": f"P{player}"}
    return event


def build_full_match() -> tuple[list[dict[str, Any]], list[dict[str, Any]], StatsBombMatch]:
    """22 players, period lengths 45.5 + 45.0, a scored shot, an own goal and a red card.

    Team A (id 1): players 101-111, everyone plays the whole match. Home team, wins 2-0: one shot
    scored by 105 and one `Own Goal For` (conceded by Team B).
    Team B (id 2): players 201-211, but 211 is sent off (Second Yellow) at 70:00 of period 2 and
    leaves the lineup then, so Team B plays 20.0 minutes a man down.
    """
    events = [
        _event(1, 20, 0, "Shot", 1, 105, shot={"outcome": {"name": "Goal"}, "statsbomb_xg": 0.3}),
        _event(1, 30, 0, "Own Goal For", 1),  # no player key, as in the real data
        _event(
            2, 70, 0, "Foul Committed", 2, 211, foul_committed={"card": {"name": "Second Yellow"}}
        ),
        _event(1, 45, 30, "Ball Receipt*", 1, 101),
        _event(2, 90, 0, "Ball Receipt*", 1, 101),
    ]

    def team(team_id: int, name: str, first: int, sent_off: int | None = None) -> dict[str, Any]:
        players = []
        for pid in range(first, first + 11):
            position = "Goalkeeper" if pid == first else "Center Midfield"
            end, end_period = ("70:00", 2) if pid == sent_off else (None, None)
            players.append(_player(pid, f"P{pid}", [_pos(position, "00:00", 1, end, end_period)]))
        return {"team_id": team_id, "team_name": name, "lineup": players}

    lineups = [team(1, "Team A", 101), team(2, "Team B", 201, sent_off=211)]
    match = MATCH.model_copy(update={"home_score": 2, "away_score": 0})
    return events, lineups, match
