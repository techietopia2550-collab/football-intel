"""Canonical team names, so the same club is one row across every source.

Covers the Premier League clubs of 2015/16 and 2024/25. Names not in the table
are returned stripped but otherwise unchanged (never dropped or guessed at), so
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
    "Norwich City": ("Norwich",),
    "Nottingham Forest": ("Nott'm Forest", "Nottm Forest", "Nott Forest"),
    "Southampton": (),
    "Stoke City": ("Stoke",),
    "Sunderland": (),
    "Swansea City": ("Swansea",),
    "Tottenham Hotspur": ("Tottenham", "Spurs"),
    "Watford": (),
    "West Bromwich Albion": ("West Brom",),
    "West Ham United": ("West Ham",),
    "Wolverhampton Wanderers": ("Wolves", "Wolverhampton"),
}

# The clubs of each Premier League season, by canonical name (season = start year).
EPL_2015_16: frozenset[str] = frozenset(
    {
        "Arsenal",
        "Aston Villa",
        "Bournemouth",
        "Chelsea",
        "Crystal Palace",
        "Everton",
        "Leicester City",
        "Liverpool",
        "Manchester City",
        "Manchester United",
        "Newcastle United",
        "Norwich City",
        "Southampton",
        "Stoke City",
        "Sunderland",
        "Swansea City",
        "Tottenham Hotspur",
        "Watford",
        "West Bromwich Albion",
        "West Ham United",
    }
)
EPL_2024_25: frozenset[str] = frozenset(
    {
        "Arsenal",
        "Aston Villa",
        "Bournemouth",
        "Brentford",
        "Brighton & Hove Albion",
        "Chelsea",
        "Crystal Palace",
        "Everton",
        "Fulham",
        "Ipswich Town",
        "Leicester City",
        "Liverpool",
        "Manchester City",
        "Manchester United",
        "Newcastle United",
        "Nottingham Forest",
        "Southampton",
        "Tottenham Hotspur",
        "West Ham United",
        "Wolverhampton Wanderers",
    }
)

# Every club we can name: the union of the seasons above.
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
