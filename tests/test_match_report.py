"""Match recap parsing (events, lineups, team stats) and the cache-only team logos."""

from __future__ import annotations

from typing import Any

import pytest

from src.analytics import match_report as mr
from src.app import player_images
from src.utils.config import settings


def _ev(i: str, kind: str, team: str, player: tuple[int, str] | None, minute: int, **extra: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"id": i, "type": {"name": kind}, "team": {"name": team}, "minute": minute, "period": 1}
    if player:
        e["player"] = {"id": player[0], "name": player[1]}
    e.update(extra)
    return e


A, B, C = (1, "Ann Alpha"), (2, "Bo Beta"), (3, "Cy M''Vila")
EVENTS = [
    _ev("p1", "Pass", "X", A, 10) | {"pass": {"goal_assist": True}},
    _ev("s1", "Shot", "X", B, 11) | {"shot": {"outcome": {"name": "Goal"}, "type": {"name": "Penalty"},
                                              "key_pass_id": "p1", "statsbomb_xg": 0.76}},
    _ev("s2", "Shot", "Y", C, 20) | {"shot": {"outcome": {"name": "Off T"}, "type": {"name": "Open Play"},
                                              "statsbomb_xg": 0.1}},
    _ev("f1", "Foul Committed", "Y", C, 30, foul_committed={"card": {"name": "Second Yellow"}}),
    _ev("f2", "Foul Committed", "Y", C, 31, foul_committed={}),
    _ev("u1", "Substitution", "X", A, 60, substitution={"replacement": {"id": 9, "name": "Di Delta"}}),
    _ev("o1", "Own Goal Against", "Y", C, 70),
    _ev("sh", "Shot", "X", B, 0) | {"period": 5, "shot": {"outcome": {"name": "Goal"}, "type": {"name": "Penalty"}}},
]


def test_parse_events_goal_assist_card_sub_and_shootout_ignored() -> None:
    out = mr.parse_events(EVENTS)
    kinds = [(e.minute, e.kind, e.player_name) for e in out]
    assert (11, "goal", "Bo Beta") in kinds and (11, "assist", "Ann Alpha") in kinds
    goal = next(e for e in out if e.kind == "goal")
    assert goal.related == "Ann Alpha" and goal.detail == "Penalty"
    assert next(e for e in out if e.kind == "yellow").detail == "Second yellow"
    assert sum(e.kind == "yellow" for e in out) == 1  # the foul without a card is not an event
    assert (60, "sub_off", "Ann Alpha") in kinds and (60, "sub_on", "Di Delta") in kinds
    assert next(e for e in out if e.kind == "own_goal").player_name == "Cy M'Vila"
    assert sum(e.kind == "goal" for e in out) == 1  # period 5 goal excluded
    assert [e.minute for e in out] == sorted(e.minute for e in out)


def test_parse_team_stats() -> None:
    passes = [
        _ev(f"q{i}", "Pass", "X", A, i) | {"pass": {"outcome": {"name": "Incomplete"}} if i == 0 else {}}
        for i in range(3)
    ] + [_ev("q9", "Pass", "Y", C, 5) | {"pass": {"type": {"name": "Corner"}}}]
    stats = mr.parse_team_stats(passes + EVENTS)
    x, y = stats["X"], stats["Y"]
    assert x.passes == 4 and y.passes == 1  # EVENTS holds one more pass for X
    assert x.shots == 1 and x.xg == pytest.approx(0.76) and x.shots_on_target == 1  # shoot-out shot excluded
    assert y.shots == 1 and y.shots_on_target == 0 and y.corners == 1 and y.fouls == 2
    assert x.possession + y.possession == pytest.approx(100)
    assert x.pass_accuracy == pytest.approx(75.0)


def test_parse_lineups_starters_bench_formation_and_events() -> None:
    lineups = [{"team_name": "X", "lineup": [
        {"player_id": 2, "player_name": "Bo Beta", "player_nickname": None, "jersey_number": 9,
         "positions": [{"position": "Center Forward", "position_id": 23, "start_reason": "Starting XI"}]},
        {"player_id": 1, "player_name": "Ann Alpha", "player_nickname": "Ann", "jersey_number": 1,
         "positions": [{"position": "Goalkeeper", "position_id": 1, "start_reason": "Starting XI"}]},
        {"player_id": 9, "player_name": "Di Delta", "player_nickname": None, "jersey_number": 14,
         "positions": [{"position": "Center Forward", "position_id": 23, "start_reason": "Substitution - On (Tactical)"}]},
        {"player_id": 7, "player_name": "Unused", "player_nickname": None, "jersey_number": 20, "positions": []},
    ]}]
    starting = _ev("x", "Starting XI", "X", None, 0, tactics={"formation": 4231})
    out = mr.parse_lineups(lineups, [starting], mr.parse_events(EVENTS), {2: "statsbomb:2"})["X"]
    assert out.formation == "4-2-3-1"
    assert [p.position_abbrev for p in out.starters] == ["GK", "CF"]  # goalkeeper first
    assert [p.name for p in out.bench] == ["Di Delta", "Unused"]
    assert out.starters[1].player_id == "statsbomb:2" and out.starters[0].player_id is None
    assert {e.kind for e in out.starters[1].events} == {"goal"}
    assert {e.kind for e in out.bench[0].events} == {"sub_on"}
    assert out.bench[1].events == []


def test_format_formation() -> None:
    assert mr.format_formation(433) == "4-3-3" and mr.format_formation(None) is None


def test_team_logo_path_never_touches_the_network(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "team_logos_dir", tmp_path)

    def boom(*a: object, **k: object) -> None:
        raise AssertionError("network call during render")

    monkeypatch.setattr(player_images.requests, "get", boom)
    assert player_images.team_logo_path("Nowhere FC") is None
    assert player_images.team_logo_uri("Nowhere FC", 32).startswith("data:image/png")  # placeholder
    (tmp_path / "somewhere-fc.png").write_bytes(player_images.TEAM_PLACEHOLDER_PATH.read_bytes())
    assert player_images.team_logo_path("Somewhere FC") == tmp_path / "somewhere-fc.png"
