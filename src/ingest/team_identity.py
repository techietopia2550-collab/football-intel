"""Canonical team names, so the same club is one row across every source.

Covers the 20 Premier League clubs of 2024/25. Names not in the table are
returned stripped but otherwise unchanged (never dropped or guessed at), so
other leagues pass through until their clubs are added here.
"""

from __future__ import annotations

import re

# canonical name -> known aliases (source spellings and common short forms).
# The canonical name itself is always accepted.
_ALIASES: dict[str, tuple[str, ...]] = {
    "Arsenal": (),
    "Aston Villa": (),
    "Bournemouth": ("AFC Bournemouth",),
    "Brentford": (),
    "Brighton & Hove Albion": ("Brighton", "Brighton and Hove Albion"),
    "Chelsea": (),
    "Crystal Palace": (),
    "Everton": (),
    "Fulham": (),
    "Ipswich Town": ("Ipswich",),
    "Leicester City": ("Leicester",),
    "Liverpool": (),
    "Manchester City": ("Man City",),
    "Manchester United": ("Man United", "Man Utd"),
    "Newcastle United": ("Newcastle", "Newcastle Utd"),
    "Nottingham Forest": ("Nott'm Forest", "Nottm Forest", "Nott Forest"),
    "Southampton": (),
    "Tottenham Hotspur": ("Tottenham", "Spurs"),
    "West Ham United": ("West Ham",),
    "Wolverhampton Wanderers": ("Wolves", "Wolverhampton"),
}

CANONICAL_TEAMS: frozenset[str] = frozenset(_ALIASES)


def _key(name: str) -> str:
    """Case-, whitespace- and apostrophe-insensitive lookup key."""
    return re.sub(r"\s+", " ", name.replace("’", "'").strip()).casefold()


_LOOKUP: dict[str, str] = {
    _key(alias): canonical
    for canonical, aliases in _ALIASES.items()
    for alias in (canonical, *aliases)
}


def normalize_team_name(name: str) -> str:
    """Return the canonical club name, or the stripped input if it is unknown."""
    return _LOOKUP.get(_key(name), name.strip())


def is_known_team(name: str) -> bool:
    """True if `name` resolves to one of the canonical clubs."""
    return _key(name) in _LOOKUP
