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
_PLAYER = {"type": "string", "description": "Player name as the user wrote it (accents optional)."}
_TEAM = {"type": "string", "description": "Team name as the user wrote it, e.g. 'Leicester'."}
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
        "Look up a player name in the 2015/16 data. Returns the one matching player, or an "
        "'ambiguous' list of candidates, or 'not_found' with suggestions.",
        {"query": _PLAYER},
        ["query"],
    ),
    _tool(
        "player_stats",
        "Season numbers for one player: games, goals, assists (or GK saves / clean sheets), every "
        "relevant metric with value, per-90, within-position percentile and band, archetype, "
        "market value, strengths and weaknesses. Use for factual questions.",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "profile",
        "A player's strengths (percentile score >= 80), weaknesses (<= 20) and neutral metrics, "
        "judged only on metrics relevant to his position group, plus warnings.",
        {"player_name": _PLAYER},
        ["player_name"],
    ),
    _tool(
        "find_similar",
        "Players whose 2015/16 statistical profile is most similar to a player's (cosine "
        "similarity within his archetype). Each result explains the shared and differing metrics.",
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
                "description": "False = search within the position group.",
            },
        },  # fmt: skip
        ["player_name"],
    ),
    _tool(
        "compare_players",
        "Two players side by side on the metrics both have: values, percentiles and percentile "
        "deltas (a minus b), with a note when their position groups differ.",
        {"a": _PLAYER, "b": _PLAYER},
        ["a", "b"],
    ),
    _tool(
        "find_players_by_profile",
        "Players meeting percentile conditions, e.g. wins aerials (aerials_won_p90 >= 80) but "
        "passes poorly (pass_completion_pct <= 20). Thresholds are 0-100 percentile scores "
        "(already flipped where lower is better). Metric names must be exact.",
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
                "description": "Omit to search every group that judges the metrics.",
            },
            "leagues": {"type": "array", "items": _LEAGUE},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
        },  # fmt: skip
        ["conditions"],
    ),
    _tool(
        "explain_metric",
        "Definition, formula, category and direction of a metric; with a player, his value, "
        "percentile, sample size and whether the metric is judged for his position.",
        {
            "metric": {"type": "string", "description": "Exact metric name, e.g. pressures_p90."},
            "player": _PLAYER,
        },  # fmt: skip
        ["metric"],
    ),
    _tool(
        "team_fingerprint",
        "How a team played in 2015/16: league position, points, the 12 style axes (raw value and "
        "0-100 rank among 80 teams), headline facts, key players with archetypes, squad market "
        "value and wage-bill context.",
        {"team": _TEAM},
        ["team"],
    ),
    _tool(
        "list_teams",
        "Every team in the data (80) with league and final position.",
        {"league": _LEAGUE},
        [],
    ),
    _tool(
        "list_team_axis",
        "Top or bottom teams on one style axis, e.g. most possession or hardest press.",
        {
            "axis": {"type": "string", "enum": list(AXIS_COLUMNS)},
            "direction": {"type": "string", "enum": ["top", "bottom"], "default": "top"},
            "n": {"type": "integer", "minimum": 1, "maximum": 80, "default": 10},
        },
        ["axis"],
    ),
    _tool(
        "find_similar_teams",
        "Teams whose style vector is closest to a team's, across the four leagues.",
        {"team": _TEAM, "n": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}},
        ["team"],
    ),
    _tool(
        "build_squad",
        "Tactical Composer: a starting XI whose players statistically fit a target style within a "
        "market-value budget. Give exactly one of `style` (a preset) or `copy_team`.",
        {
            "formation": {"type": "string", "enum": list(FORMATIONS)},
            "budget_eur_m": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Total budget in millions of euros.",
            },
            "style": {"type": "string", "enum": list(PRESETS)},
            "copy_team": {**_TEAM, "description": "Use this team's style as the target."},
            "leagues": {"type": "array", "items": _LEAGUE},
            "variant": {"type": "string", "enum": list(VARIANT_RULES), "default": "standard"},
            "quality_weight": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
        },  # fmt: skip
        ["formation", "budget_eur_m"],
    ),
    _tool(
        "market_value",
        "A player's Transfermarkt market value (EUR estimate, Oct 2015 - Mar 2016) and how it was "
        "matched. Not a wage, not a fee paid.",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "player_style_fit",
        "How strongly a player's own numbers lean towards each team-style axis (0-1 per axis; "
        "null for axes with no player-level counterpart).",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "archetypes",
        "The playing-style archetypes (clusters) with sizes; with an archetype label, its most "
        "typical players and defining metrics.",
        {"archetype": {"type": "string", "description": "Archetype label, e.g. 'Destroyer'."}},
        [],
    ),
    _tool(
        "player_games",
        "A player's season game counts and his longest, best and worst game (ratings compare his "
        "own matches only).",
        {"player": _PLAYER},
        ["player"],
    ),
    _tool(
        "methodology",
        "Fixed, reviewed text on how the data and metrics work and what is not measured.",
        {"topic": {"type": "string", "enum": list(METHODOLOGY)}},
        [],
    ),
]

TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOLS]

__all__ = ["LEAGUES", "TOOLS", "TOOL_NAMES"]
