"""Route a tool call from the LLM to `src/analytics` and return a JSON-safe dict.

`call_tool(name, arguments)` never raises: unknown tools, bad arguments, unknown names and engine
errors all come back as {"error": <code>, ...}. Every call is logged with its arguments, outcome
and duration. The functions below only resolve names and assemble existing analytics results;
they compute nothing (CLAUDE.md hard rule 1).
"""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Callable
from typing import Any, cast

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import text

from src.analytics import chatbot, directory, finance, market_values, team_profile
from src.analytics.composer import PRESETS, VARIANT_RULES, InfeasibleBudget, build_squad
from src.analytics.games import load_player_matches, player_games, season_game_stats
from src.analytics.profile import profile
from src.analytics.similarity import ARCHETYPES_TABLE, AgeDataUnavailable, find_similar
from src.analytics.style_fit import load_style_fit, player_style_fit
from src.analytics.team_similarity import find_similar_teams
from src.chat.tools import TOOL_NAMES
from src.models.composer import Variant
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON
from src.utils.logging import logger

SEASON = DEFAULT_SEASON
Result = dict[str, Any]


class ToolError(Exception):
    """An expected failure carrying the error dict to return."""

    def __init__(self, payload: Result) -> None:
        super().__init__(payload.get("error"))
        self.payload = payload


def _player(name: str) -> Result:
    hit = chatbot.resolve_player_name(name, SEASON)
    if "error" in hit:
        raise ToolError(hit)
    return hit


def _qualified_player(name: str) -> Result:
    hit = _player(name)
    if not hit["qualified"]:
        raise ToolError(
            {
                "error": "below_threshold", "player_name": hit["player_name"],
                "minutes_total": hit["minutes_total"], "threshold": 900,
            }
        )  # fmt: skip
    return hit


def _team(name: str) -> Result:
    hit = chatbot.resolve_team_name(name, SEASON)
    if "error" in hit:
        raise ToolError(hit)
    return hit


# ---- tool implementations ----------------------------------------------------------------------


def _resolve_player(query: str) -> Result:
    return chatbot.resolve_player_name(query, SEASON)


def _player_stats(player: str) -> Result:
    return chatbot.player_stats(player, SEASON)


def _profile(player_name: str) -> Result:
    return profile(_player(player_name)["player_name"], SEASON)


def _find_similar(
    player_name: str, n: int = 5, filters: dict[str, Any] | None = None,
    league: str | None = None, use_archetypes: bool = True,
) -> Result:  # fmt: skip
    who = _qualified_player(player_name)
    res = find_similar(
        who["player_name"], SEASON, n=max(1, min(int(n), 10)), filters=filters, league=league,
        use_archetypes=use_archetypes,
    )  # fmt: skip
    return res.model_dump()


def _compare_players(a: str, b: str) -> Result:
    return chatbot.compare_players(a, b, SEASON)


def _find_players_by_profile(
    conditions: list[Any], position_group: str | None = None, leagues: list[str] | None = None,
    limit: int = 10,
) -> Result:  # fmt: skip
    return chatbot.find_players_by_profile(position_group, conditions, SEASON, leagues, limit)


def _explain_metric(metric: str, player: str | None = None) -> Result:
    return chatbot.explain_metric(metric, player, SEASON)


def _team_fingerprint(team: str) -> Result:
    hit = _team(team)
    name, league = hit["team_name"], hit["league"]
    prof = team_profile.team_profile(name, SEASON)
    players, mix = team_profile.key_players(name, league, SEASON)
    sv = market_values.squad_value(name, league, market_values.load_market_values(SEASON))
    wage = finance.team_wage_context(name, league, SEASON)
    return {
        "profile": prof.model_dump(), "key_players": [p.model_dump() for p in players],
        "archetype_mix": mix, "squad_value": sv.model_dump(),
        "squad_value_thin": market_values.coverage_is_thin(sv), "wage": wage.model_dump(),
    }  # fmt: skip


def _list_teams(league: str | None = None) -> Result:
    teams = team_profile.list_teams(SEASON)
    if league:
        teams = teams[teams["league"] == league]
    return {"season": SEASON, "n": len(teams), "teams": teams.to_dict("records")}


def _list_team_axis(axis: str, direction: str = "top", n: int = 10) -> Result:
    return chatbot.list_team_axis(axis, direction, n, SEASON)


def _find_similar_teams(team: str, n: int = 5) -> Result:
    res = find_similar_teams(_team(team)["team_name"], SEASON, n=max(1, min(int(n), 10)))
    return res.model_dump()


def _build_squad(
    formation: str, budget_eur_m: float, style: str | None = None, copy_team: str | None = None,
    leagues: list[str] | None = None, variant: str = "standard", quality_weight: float = 0.5,
) -> Result:  # fmt: skip
    if variant not in VARIANT_RULES:
        raise ToolError(
            {"error": "bad_arguments", "detail": f"variant must be {list(VARIANT_RULES)}"}
        )
    if (style is None) == (copy_team is None):
        raise ToolError(
            {"error": "bad_arguments", "detail": "give exactly one of style, copy_team"}
        )
    if style is not None:
        if style not in PRESETS:
            raise ToolError({"error": "bad_arguments", "detail": f"style must be {list(PRESETS)}"})
        target, target_name = PRESETS[style], style
    else:
        team = _team(str(copy_team))["team_name"]
        target, target_name = team_profile.team_profile(team, SEASON).scores, team
    squad = build_squad(
        target, float(budget_eur_m) * 1e6, formation, load_style_fit(SEASON),
        league_filter=leagues, variant=cast(Variant, variant),
        quality_weight=float(quality_weight),
    )  # fmt: skip
    return {"target": target_name, "squad": squad.model_dump()}


def _market_value(player: str) -> Result:
    who = _player(player)
    mv = market_values.player_value(who["player_id"], market_values.load_market_values(SEASON))
    if mv is None:
        raise ToolError({"error": "no_data", "detail": f"no market value row for "
                                                       f"{who['player_name']}"})  # fmt: skip
    return {"player": who, "market_value": mv.model_dump()}


def _player_style_fit(player: str) -> Result:
    who = _player(player)
    fit = player_style_fit(who["player_id"], SEASON)
    if not fit:
        raise ToolError({"error": "no_data", "detail": f"no style-fit row for "
                                                       f"{who['player_name']}"})  # fmt: skip
    return {"player": who, "axes": fit}


def _archetypes(archetype: str | None = None) -> Result:
    members = pd.read_sql(
        text(
            "SELECT player_id, archetype_id, archetype_label, cluster_confidence "
            f"FROM {ARCHETYPES_TABLE} WHERE season = :s"
        ),
        get_engine(),
        params={"s": SEASON},
    )
    table = directory.archetype_table(members)
    out: Result = {"season": SEASON, "archetypes": table.to_dict("records")}
    if archetype is None:
        return out
    hit = table[table["archetype_label"].str.casefold() == archetype.strip().casefold()]
    if hit.empty:
        raise ToolError({"error": "not_found", "query": archetype,
                         "valid": table["archetype_label"].tolist()})  # fmt: skip
    aid = int(hit.iloc[0]["archetype_id"])
    ex = directory.archetype_exemplars(season=SEASON)
    out["archetype"] = {
        **hit.iloc[0].to_dict(),
        "exemplars": ex[ex["archetype_id"] == aid].to_dict("records"),
        "defining_metrics": [
            {"metric": m, "archetype_mean_z": round(z, 3)}
            for m, z in directory.archetype_defining_metrics(aid, season=SEASON)
        ],
    }
    return out


def _player_games(player: str) -> Result:
    who = _player(player)
    matches = load_player_matches(who["player_id"], SEASON)
    group = who["position_group_fine"]
    if matches.empty:
        raise ToolError({"error": "no_data", "detail": "no matches stored for this player"})
    return {
        "player": who, "season_counts": season_game_stats(matches, group).model_dump(),
        "games": player_games(matches, group).model_dump(),
        "note": "Ratings compare the player's own matches only, never players with players.",
    }  # fmt: skip


def _methodology(topic: str | None = None) -> Result:
    text_ = chatbot.methodology()
    if topic is None:
        return text_
    if topic not in text_:
        raise ToolError({"error": "bad_arguments", "valid_topics": list(text_)})
    return {topic: text_[topic]}


REGISTRY: dict[str, Callable[..., Result]] = {
    "resolve_player": _resolve_player, "player_stats": _player_stats, "profile": _profile,
    "find_similar": _find_similar, "compare_players": _compare_players,
    "find_players_by_profile": _find_players_by_profile, "explain_metric": _explain_metric,
    "team_fingerprint": _team_fingerprint, "list_teams": _list_teams,
    "list_team_axis": _list_team_axis, "find_similar_teams": _find_similar_teams,
    "build_squad": _build_squad, "market_value": _market_value,
    "player_style_fit": _player_style_fit, "archetypes": _archetypes,
    "player_games": _player_games, "methodology": _methodology,
}  # fmt: skip
assert list(REGISTRY) == TOOL_NAMES, "tools.py and dispatch.py are out of step"


# ---- dispatch ----------------------------------------------------------------------------------


def _map_value_error(exc: ValueError) -> Result:
    msg = str(exc)
    if msg.startswith("No player matching"):
        return {"error": "not_found", "detail": msg}
    if "ambiguous" in msg:
        return {"error": "ambiguous", "detail": msg}
    if "below the minutes threshold" in msg:
        return {"error": "below_threshold", "detail": msg}
    if msg.startswith("Unknown team"):
        return {"error": "not_found", "detail": msg}
    return {"error": "bad_arguments", "detail": msg}


def call_tool(name: str, arguments: dict[str, Any] | str | None) -> Result:
    """Run one tool by name with the LLM's arguments (a dict or a JSON string)."""
    start = time.perf_counter()
    result: Result
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        result = {"error": "bad_arguments", "detail": "arguments are not a JSON object"}
        args = {}
    else:
        fn = REGISTRY.get(name)
        if fn is None:
            result = {"error": "unknown_tool", "tool": name, "valid_tools": TOOL_NAMES}
        else:
            try:
                inspect.signature(fn).bind(**args)
            except TypeError as exc:
                result = {"error": "bad_arguments", "detail": str(exc)}
            else:
                result = _run(fn, args)
    result = chatbot.clean(result)
    status = result.get("error", "ok") if isinstance(result, dict) else "ok"
    logger.info(
        "tool {name} args={args} -> {status} ({ms:.0f} ms)",
        name=name, args=args, status=status, ms=(time.perf_counter() - start) * 1000,
    )  # fmt: skip
    return result


def _run(fn: Callable[..., Result], args: dict[str, Any]) -> Result:
    try:
        return fn(**args)
    except ToolError as exc:
        return exc.payload
    except InfeasibleBudget as exc:
        return {"error": "infeasible_budget", "detail": str(exc)}
    except AgeDataUnavailable as exc:
        return {"error": "no_data", "detail": str(exc)}
    except ValidationError as exc:
        return {"error": "bad_arguments", "detail": exc.errors(include_url=False)}
    except KeyError as exc:  # a programming error, not missing data
        logger.exception("tool {fn} failed", fn=fn.__name__)
        return {"error": "internal", "detail": f"KeyError: {exc}"}
    except LookupError as exc:  # a table that is not built yet
        return {"error": "no_data", "detail": str(exc)}
    except ValueError as exc:
        return _map_value_error(exc)
    except Exception as exc:  # noqa: BLE001 - the chatbot must get an answer, never a traceback
        logger.exception("tool {fn} failed", fn=fn.__name__)
        return {"error": "internal", "detail": f"{type(exc).__name__}: {exc}"}


__all__ = ["REGISTRY", "call_tool"]
