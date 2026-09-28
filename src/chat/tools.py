"""The 17 chatbot tools as OpenAI-style function schemas (docs/RAG_DESIGN.md section 9.5).

These are what the LLM sees. `src.chat.dispatch.call_tool` executes them. Season is fixed to
2015/16 and never exposed; names go in, ids are resolved in code.
"""

from __future__ import annotations

from typing import Any

from src.analytics.chatbot import METHODOLOGY
from src.analytics.composer import PRESETS, VARIANT_RULES
from src.analytics.formations import FORMATIONS
from src.analytics.metrics import POSITION_GROUPS
from src.analytics.team_style import AXIS_COLUMNS

LEAGUES: list[str] = ["ENG-Premier League", "ESP-La Liga", "FRA-Ligue 1", "ITA-Serie A"]
_GROUPS = list(POSITION_GROUPS)
_PLAYER = {"type": "string", "description": "Player name as written."}
_TEAM = {"type": "string", "description": "Team name as written."}
_LEAGUE = {"type": "string", "enum": LEAGUES}


def _tool(
    name: str, description: str, props: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "resolve_player",
        "Resolve a player name: the match, an 'ambiguous' candidate list, or 'not_found'.",
        {"query": _PLAYER},
        ["query"],
    ),
    _tool(
        "player_stats",
        "A player's season counts, per-90 metrics with position-group percentiles, archetype and "
        "market value.",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "profile",
        "A player's strengths (score >= 80), weaknesses (<= 20) and neutral metrics, with "
        "warnings.",
        {"player_name": _PLAYER},
        ["player_name"],
    ),
    _tool(
        "find_similar",
        "Players most similar to a player (cosine within his archetype), with shared and "
        "differing metrics.",
        {
            "player_name": _PLAYER,
            "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            "filters": {
                "type": "object",
                "properties": {
                    "exclude_same_team": {"type": "boolean"},
                    "same_team_only": {"type": "boolean"},
                    "min_minutes": {"type": "number", "minimum": 0},
                },
            },
            "league": {**_LEAGUE, "description": "Restrict the whole pool to one league."},
            "use_archetypes": {
                "type": "boolean",
                "default": True,
                "description": "False: position-group pool.",
            },
        },  # fmt: skip
        ["player_name"],
    ),
    _tool(
        "compare_players",
        "Two players side by side: values, percentiles and percentile deltas (a minus b).",
        {"a": _PLAYER, "b": _PLAYER},
        ["a", "b"],
    ),
    _tool(
        "find_players_by_profile",
        "Players meeting every percentile condition (exact metric names, 0-100 scores, flipped "
        "where lower is better).",
        {
            "conditions": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string"},
                        "op": {"type": "string", "enum": [">=", "<=", ">", "<"]},
                        "threshold": {"type": "number", "minimum": 0, "maximum": 100},
                    },
                    "required": ["metric", "op", "threshold"],
                },
            },
            "position_group": {
                "type": "string",
                "enum": _GROUPS,
                "description": "Omit for all groups.",
            },
            "leagues": {"type": "array", "items": _LEAGUE},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
        },  # fmt: skip
        ["conditions"],
    ),
    _tool(
        "explain_metric",
        "A metric's definition, formula and direction; with a player, his value and percentile.",
        {
            "metric": {"type": "string", "description": "Exact metric name."},
            "player": _PLAYER,
        },  # fmt: skip
        ["metric"],
    ),
    _tool(
        "team_fingerprint",
        "A team's league position, points, 12 style axes (0-100 ranks), key players, squad value "
        "and wages.",
        {"team": _TEAM},
        ["team"],
    ),
    _tool(
        "list_teams",
        "All 80 teams with league and final position.",
        {"league": _LEAGUE},
        [],
    ),
    _tool(
        "list_team_axis",
        "Top or bottom teams on one style axis.",
        {
            "axis": {"type": "string", "enum": list(AXIS_COLUMNS)},
            "direction": {"type": "string", "enum": ["top", "bottom"], "default": "top"},
            "n": {"type": "integer", "minimum": 1, "maximum": 80, "default": 10},
        },
        ["axis"],
    ),
    _tool(
        "find_similar_teams",
        "Teams whose style is closest to a team's.",
        {"team": _TEAM, "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}},
        ["team"],
    ),
    _tool(
        "build_squad",
        "Tactical Composer: an XI fitting a style within a budget. Give exactly one of style or "
        "copy_team.",
        {
            "formation": {"type": "string", "enum": list(FORMATIONS)},
            "budget_eur_m": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "EUR millions.",
            },
            "style": {"type": "string", "enum": list(PRESETS)},
            "copy_team": {**_TEAM, "description": "Target this team's style."},
            "leagues": {"type": "array", "items": _LEAGUE},
            "variant": {"type": "string", "enum": list(VARIANT_RULES), "default": "standard"},
            "quality_weight": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
        },  # fmt: skip
        ["formation", "budget_eur_m"],
    ),
    _tool(
        "market_value",
        "A player's Transfermarkt market value (EUR estimate, 2015/16), not a wage or fee.",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "player_style_fit",
        "How strongly a player's numbers lean towards each team-style axis (0-1, null if "
        "unmapped).",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "archetypes",
        "Archetypes with sizes; with a label, its typical players and defining metrics.",
        {"archetype": {"type": "string", "description": "Archetype label."}},
        [],
    ),
    _tool(
        "player_games",
        "A player's game counts and his longest, best and worst game.",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "methodology",
        "Reviewed text on how the data and metrics work and what is not measured.",
        {"topic": {"type": "string", "enum": list(METHODOLOGY)}},
        [],
    ),
]

TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOLS]

__all__ = ["LEAGUES", "TOOLS", "TOOL_NAMES"]
