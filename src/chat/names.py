"""Player and team name resolution for the chatbot.

Thin wrappers over `src.analytics.chatbot.resolve_player_name` / `resolve_team_name`, which rank
names with `directory.search` (accent- and case-insensitive: exact, whole word, prefix,
substring). A unique best match resolves; several equally good matches are ambiguous; a fuzzy-only
match is never guessed and comes back as not found with suggestions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine

from src.analytics.chatbot import resolve_player_name, resolve_team_name
from src.transform.normalize import DEFAULT_SEASON

Resolved = tuple[str, str] | dict[str, Any]


def resolve_player(
    name: str, season: str | int = DEFAULT_SEASON, engine: Engine | None = None
) -> Resolved:
    """(player_id, canonical_name), or {"error": "not_found" | "ambiguous", ...}."""
    hit = resolve_player_name(name, season, engine)
    return hit if "error" in hit else (str(hit["player_id"]), str(hit["player_name"]))


def resolve_team(
    name: str, season: str | int = DEFAULT_SEASON, engine: Engine | None = None
) -> Resolved:
    """(team_name, canonical_name), or {"error": ...}; teams are keyed by their exact name."""
    hit = resolve_team_name(name, season, engine)
    return hit if "error" in hit else (str(hit["team_name"]), str(hit["team_name"]))


__all__ = ["resolve_player", "resolve_team"]
