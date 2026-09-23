"""Match recap from the cached StatsBomb files: timeline events, lineups with per-player events,
and team totals. Reads `data/raw/statsbomb/{events,lineups,matches}`; nothing is fetched.

Fields used (verified on the cached files):
  events   `Shot` (shot.outcome, shot.type, shot.statsbomb_xg, shot.key_pass_id), `Pass`
           (pass.goal_assist, pass.outcome absent = completed, pass.type Corner), `Foul Committed` /
           `Bad Behaviour` (.card), `Substitution` (substitution.replacement), `Own Goal Against`,
           `Starting XI` (tactics.formation). Period 5 (shoot-out) is ignored.
  lineups  player_id, player_name, player_nickname, jersey_number, positions[].start_reason.
Every piece is optional: a missing file gives an empty part, never an error.
Possession is the team's share of all passes (StatsBomb has no possession clock in this data).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import json
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from src.ingest.statsbomb.fetch import (
    events_cache_path,
    lineups_cache_path,
    load_cached_json,
    statsbomb_cache_root,
)
from src.models.match_report import (
    LineupPlayer,
    MatchEvent,
    MatchReport,
    TeamLineup,
    TeamStats,
)
from src.models.schema import get_engine

SOURCE = "statsbomb"
SHOOTOUT_PERIOD = 5
_ON_TARGET = {"Goal", "Saved", "Saved to Post"}
_CARD_KIND = {"Yellow Card": "yellow", "Second Yellow": "yellow", "Red Card": "red"}


def format_formation(code: object) -> str | None:
    """433 -> '4-3-3', 4231 -> '4-2-3-1'."""
    digits = str(code) if isinstance(code, int) and code > 0 else ""
    return "-".join(digits) if len(digits) >= 3 else None


def _clean(name: str) -> str:
    """StatsBomb names carry SQL-style doubled apostrophes (M''Vila)."""
    return name.replace("''", "'")


def _load(path: Path) -> list[dict[str, Any]] | None:
    try:
        return load_cached_json(path, list) if path.exists() else None  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001 - a bad cache file must degrade to "not available"
        return None


def parse_events(events: list[dict[str, Any]]) -> list[MatchEvent]:
    """Goals (with assister), own goals, assists, cards and substitutions, sorted by minute."""
    by_id = {e["id"]: e for e in events if e.get("period", 0) < SHOOTOUT_PERIOD}
    out: list[MatchEvent] = []
    for e in by_id.values():
        kind, team = e["type"]["name"], e.get("team", {}).get("name", "")
        player = e.get("player") or {}
        base = {
            "minute": int(e.get("minute", 0)), "team": team,
            "player_sb_id": player.get("id", 0), "player_name": _clean(player.get("name", "")),
        }  # fmt: skip
        if kind == "Shot" and e["shot"]["outcome"]["name"] == "Goal":
            passer = by_id.get(e["shot"].get("key_pass_id", ""))
            assisted = passer is not None and bool(passer["pass"].get("goal_assist"))
            out.append(MatchEvent(
                kind="goal", related=_clean(passer["player"]["name"]) if assisted and passer else None,
                detail="Penalty" if e["shot"]["type"]["name"] == "Penalty" else None, **base,
            ))  # fmt: skip
            if assisted and passer:
                out.append(MatchEvent(
                    kind="assist", minute=base["minute"], team=team,
                    player_sb_id=passer["player"]["id"], player_name=_clean(passer["player"]["name"]),
                    related=_clean(player.get("name", "")),
                ))  # fmt: skip
        elif kind == "Own Goal Against":
            out.append(MatchEvent(kind="own_goal", **base))
        elif kind in ("Foul Committed", "Bad Behaviour"):
            card = (e.get(kind.lower().replace(" ", "_")) or {}).get("card", {}).get("name")
            if card in _CARD_KIND:
                out.append(MatchEvent(
                    kind=_CARD_KIND[card], detail="Second yellow" if card == "Second Yellow" else None,
                    **base,
                ))  # fmt: skip
        elif kind == "Substitution":
            rep = e["substitution"]["replacement"]
            out.append(MatchEvent(kind="sub_off", related=_clean(rep["name"]), **base))
            out.append(MatchEvent(
                kind="sub_on", minute=base["minute"], team=team, player_sb_id=rep["id"],
                player_name=_clean(rep["name"]), related=_clean(player.get("name", "")),
            ))  # fmt: skip
    return sorted(out, key=lambda ev: ev.minute)


def parse_team_stats(events: list[dict[str, Any]]) -> dict[str, TeamStats]:
    acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for e in events:
        if e.get("period", 0) >= SHOOTOUT_PERIOD:
            continue
        s, kind = acc[e.get("team", {}).get("name", "")], e["type"]["name"]
        if kind == "Pass":
            s["passes"] += 1
            s["completed"] += "outcome" not in e["pass"]
            s["corners"] += e["pass"].get("type", {}).get("name") == "Corner"
        elif kind == "Shot":
            s["shots"] += 1
            s["on_target"] += e["shot"]["outcome"]["name"] in _ON_TARGET
            s["xg"] += e["shot"].get("statsbomb_xg", 0.0)
        elif kind == "Foul Committed":
            s["fouls"] += 1
    total = sum(s["passes"] for s in acc.values()) or 1.0
    return {
        team: TeamStats(
            possession=100 * s["passes"] / total, shots=int(s["shots"]),
            shots_on_target=int(s["on_target"]), xg=round(s["xg"], 2), passes=int(s["passes"]),
            pass_accuracy=100 * s["completed"] / s["passes"] if s["passes"] else 0.0,
            corners=int(s["corners"]), fouls=int(s["fouls"]),
        )
        for team, s in acc.items()
    }  # fmt: skip


def parse_lineups(
    lineups: list[dict[str, Any]], events: list[dict[str, Any]] | None,
    match_events: list[MatchEvent], our_ids: dict[int, str],
) -> dict[str, TeamLineup]:
    """Both teams' starting XI (goalkeeper first) and bench, with each player's events."""
    formations = {
        e["team"]["name"]: format_formation(e["tactics"].get("formation"))
        for e in events or [] if e["type"]["name"] == "Starting XI"
    }  # fmt: skip
    per_player: dict[int, list[MatchEvent]] = defaultdict(list)
    for ev in match_events:
        per_player[ev.player_sb_id].append(ev)
    out: dict[str, TeamLineup] = {}
    for t in lineups:
        starters: list[LineupPlayer] = []
        bench: list[LineupPlayer] = []
        for p in t["lineup"]:
            first = next((q for q in p["positions"] if q["start_reason"] == "Starting XI"), None)
            player = LineupPlayer(
                sb_id=p["player_id"], name=_clean(p.get("player_nickname") or p["player_name"]),
                jersey=p.get("jersey_number"), starter=first is not None,
                position=first["position"] if first else None,
                position_id=first["position_id"] if first else None,
                player_id=our_ids.get(p["player_id"]), events=per_player.get(p["player_id"], []),
            )  # fmt: skip
            (starters if first else bench).append(player)
        starters.sort(key=lambda p: p.position_id or 99)
        bench.sort(key=lambda p: p.jersey or 99)
        out[t["team_name"]] = TeamLineup(
            team=t["team_name"], formation=formations.get(t["team_name"]),
            starters=starters, bench=bench,
        )  # fmt: skip
    return out


@lru_cache(maxsize=1)
def _match_weeks() -> dict[int, int]:
    weeks: dict[int, int] = {}
    for f in (statsbomb_cache_root() / "matches").glob("*.json"):
        try:
            for m in json.loads(f.read_text(encoding="utf-8")):
                if m.get("match_week") is not None:
                    weeks[int(m["match_id"])] = int(m["match_week"])
        except (OSError, ValueError, KeyError):
            continue
    return weeks


def _statsbomb_to_our_ids(engine: Engine) -> dict[int, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT statsbomb_id, player_id FROM players WHERE statsbomb_id IS NOT NULL")
        ).fetchall()
    return {int(r[0]): str(r[1]) for r in rows}


def load_match_report(match_id: int, engine: Engine | None = None) -> MatchReport | None:
    """The recap of one match, or None when the match is not in the database."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        m = conn.execute(
            text("SELECT match_date, league, home_team, away_team, home_score, away_score "
                 "FROM matches WHERE source = :s AND match_id = :m"),
            {"s": SOURCE, "m": match_id},
        ).fetchone()  # fmt: skip
    if m is None:
        return None
    raw_events = _load(events_cache_path(match_id))
    raw_lineups = _load(lineups_cache_path(match_id))
    events = parse_events(raw_events) if raw_events else []
    lineups = (
        parse_lineups(raw_lineups, raw_events, events, _statsbomb_to_our_ids(engine))
        if raw_lineups else {}
    )  # fmt: skip
    stats = parse_team_stats(raw_events) if raw_events else {}
    home, away = str(m[2]), str(m[3])
    return MatchReport(
        match_id=match_id, match_date=date.fromisoformat(str(m[0])[:10]), league=str(m[1]),
        match_week=_match_weeks().get(match_id), home_team=home, away_team=away,
        home_score=None if m[4] is None else int(m[4]),
        away_score=None if m[5] is None else int(m[5]),
        events=events, home_lineup=lineups.get(home), away_lineup=lineups.get(away),
        home_stats=stats.get(home), away_stats=stats.get(away),
    )  # fmt: skip
