"""Canonical team names, so the same club is one row across every source.

Covers the Premier League clubs of 2015/16 and 2024/25, and the 2015/16 clubs of La Liga, Serie A,
the Bundesliga and Ligue 1 (the spellings are taken from StatsBomb's cached match files and every
one is a test case). Names not in the table are returned stripped but otherwise unchanged (never
dropped or guessed at). Lookup ignores case, accents and curly apostrophes.
"""

from __future__ import annotations

import re
import unicodedata

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
    # ---- La Liga 2015/16 ------------------------------------------------------------------
    "Athletic Club": ("Athletic Bilbao", "Athletic Club Bilbao"),
    "Atlético Madrid": ("Atletico Madrid", "Atlético de Madrid", "Atl. Madrid"),
    "Barcelona": ("FC Barcelona", "Barca"),
    "Celta Vigo": ("Celta de Vigo", "RC Celta", "RC Celta de Vigo"),
    "Deportivo La Coruña": ("RC Deportivo La Coruña", "Deportivo", "Deportivo La Coruna"),
    "Eibar": ("SD Eibar",),
    "Espanyol": ("RCD Espanyol", "Espanyol Barcelona"),
    "Getafe": ("Getafe CF",),
    "Granada": ("Granada CF",),
    "Las Palmas": ("UD Las Palmas",),
    "Levante": ("Levante UD",),
    "Málaga": ("Malaga", "Málaga CF", "Malaga CF"),
    "Rayo Vallecano": ("Rayo",),
    "Real Betis": ("Betis", "Real Betis Balompié"),
    "Real Madrid": ("Real Madrid CF",),
    "Real Sociedad": ("La Real",),
    "Sevilla": ("Sevilla FC",),
    "Sporting Gijón": ("Sporting Gijon", "Real Sporting", "Real Sporting de Gijón"),
    "Valencia": ("Valencia CF",),
    "Villarreal": ("Villarreal CF",),
    # ---- Serie A 2015/16 ------------------------------------------------------------------
    "AC Milan": ("Milan", "A.C. Milan"),
    "AS Roma": ("Roma", "A.S. Roma"),
    "Atalanta": ("Atalanta BC",),
    "Bologna": ("Bologna FC",),
    "Carpi": ("Carpi FC",),
    "Chievo": ("Chievo Verona", "ChievoVerona", "AC ChievoVerona"),
    "Empoli": ("Empoli FC",),
    "Fiorentina": ("ACF Fiorentina",),
    "Frosinone": ("Frosinone Calcio",),
    "Genoa": ("Genoa CFC",),
    "Hellas Verona": ("Verona", "Hellas Verona FC"),
    "Inter Milan": ("Inter", "Internazionale", "FC Internazionale Milano", "FC Inter"),
    "Juventus": ("Juve", "Juventus FC"),
    "Lazio": ("SS Lazio",),
    "Napoli": ("SSC Napoli",),
    "Palermo": ("US Palermo",),
    "Sampdoria": ("UC Sampdoria",),
    "Sassuolo": ("US Sassuolo", "US Sassuolo Calcio"),
    "Torino": ("Torino FC",),
    "Udinese": ("Udinese Calcio",),
    # ---- Bundesliga 2015/16 -------------------------------------------------------------
    "Bayer Leverkusen": ("Leverkusen", "Bayer 04 Leverkusen"),
    "Bayern Munich": ("Bayern München", "FC Bayern München", "FC Bayern Munich", "Bayern"),
    "Borussia Dortmund": ("Dortmund", "BVB"),
    "Borussia Mönchengladbach": ("Gladbach", "M'gladbach", "Mönchengladbach"),
    "Darmstadt 98": ("SV Darmstadt 98", "Darmstadt"),
    "Eintracht Frankfurt": ("Frankfurt",),
    "FC Augsburg": ("Augsburg",),
    "FC Ingolstadt": ("Ingolstadt", "FC Ingolstadt 04"),
    "1. FC Köln": ("FC Köln", "Köln", "Cologne", "1. FC Koln"),
    "Hamburger SV": ("Hamburg", "HSV"),
    "Hannover 96": ("Hannover",),
    "Hertha BSC": ("Hertha Berlin", "Hertha"),
    "Hoffenheim": ("TSG Hoffenheim", "TSG 1899 Hoffenheim"),
    "Mainz 05": ("FSV Mainz 05", "Mainz"),
    "Schalke 04": ("Schalke", "FC Schalke 04"),
    "VfB Stuttgart": ("Stuttgart",),
    "VfL Wolfsburg": ("Wolfsburg",),
    "Werder Bremen": ("Bremen", "SV Werder Bremen"),
    # ---- Ligue 1 2015/16 ------------------------------------------------------------------
    "Angers": ("Angers SCO",),
    "Bastia": ("SC Bastia",),
    "Bordeaux": ("Girondins de Bordeaux", "FC Girondins de Bordeaux"),
    "Caen": ("Stade Malherbe Caen", "SM Caen"),
    "Gazélec Ajaccio": ("Gazelec Ajaccio", "GFC Ajaccio", "GFCO Ajaccio"),
    "Guingamp": ("EA Guingamp",),
    "Lille": ("LOSC Lille", "Lille OSC"),
    "Lorient": ("FC Lorient",),
    "Lyon": ("Olympique Lyonnais", "Olympique Lyon", "OL"),
    "Marseille": ("Olympique de Marseille", "Olympique Marseille", "OM"),
    "Monaco": ("AS Monaco", "AS Monaco FC"),
    "Montpellier": ("Montpellier HSC",),
    "Nantes": ("FC Nantes",),
    "Nice": ("OGC Nice",),
    "Paris Saint-Germain": ("PSG", "Paris SG", "Paris Saint Germain"),
    "Reims": ("Stade de Reims",),
    "Rennes": ("Stade Rennais", "Stade Rennais FC"),
    "Saint-Étienne": ("AS Saint-Étienne", "St Etienne", "Saint Etienne", "St-Etienne"),
    "Toulouse": ("Toulouse FC",),
    "Troyes": ("ESTAC Troyes", "ESTAC"),
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

# The 2015/16 clubs of each other league, by canonical name (what StatsBomb calls them is in the
# aliases above; `tests/test_team_identity.py` checks every name in the cached match files).
LALIGA_2015_16: frozenset[str] = frozenset(
    {
        "Athletic Club", "Atlético Madrid", "Barcelona", "Celta Vigo", "Deportivo La Coruña",
        "Eibar", "Espanyol", "Getafe", "Granada", "Las Palmas", "Levante", "Málaga",
        "Rayo Vallecano", "Real Betis", "Real Madrid", "Real Sociedad", "Sevilla",
        "Sporting Gijón", "Valencia", "Villarreal",
    }
)  # fmt: skip
SERIEA_2015_16: frozenset[str] = frozenset(
    {
        "AC Milan", "AS Roma", "Atalanta", "Bologna", "Carpi", "Chievo", "Empoli", "Fiorentina",
        "Frosinone", "Genoa", "Hellas Verona", "Inter Milan", "Juventus", "Lazio", "Napoli",
        "Palermo", "Sampdoria", "Sassuolo", "Torino", "Udinese",
    }
)  # fmt: skip
BUNDESLIGA_2015_16: frozenset[str] = frozenset(
    {
        "Bayer Leverkusen", "Bayern Munich", "Borussia Dortmund", "Borussia Mönchengladbach",
        "Darmstadt 98", "Eintracht Frankfurt", "FC Augsburg", "FC Ingolstadt", "1. FC Köln",
        "Hamburger SV", "Hannover 96", "Hertha BSC", "Hoffenheim", "Mainz 05", "Schalke 04",
        "VfB Stuttgart", "VfL Wolfsburg", "Werder Bremen",
    }
)  # fmt: skip
LIGUE1_2015_16: frozenset[str] = frozenset(
    {
        "Angers", "Bastia", "Bordeaux", "Caen", "Gazélec Ajaccio", "Guingamp", "Lille", "Lorient",
        "Lyon", "Marseille", "Monaco", "Montpellier", "Nantes", "Nice", "Paris Saint-Germain",
        "Reims", "Rennes", "Saint-Étienne", "Toulouse", "Troyes",
    }
)  # fmt: skip

# canonical league name -> that league's 2015/16 clubs (the ingest's team-name invariant)
CLUBS_2015_16: dict[str, frozenset[str]] = {
    "ENG-Premier League": EPL_2015_16,
    "ESP-La Liga": LALIGA_2015_16,
    "ITA-Serie A": SERIEA_2015_16,
    "GER-Bundesliga": BUNDESLIGA_2015_16,
    "FRA-Ligue 1": LIGUE1_2015_16,
}

# Every club we can name: every club in the alias table.
CANONICAL_TEAMS: frozenset[str] = frozenset(_ALIASES)


def _key(name: str) -> str:
    """Case-, accent-, whitespace- and apostrophe-insensitive lookup key."""
    text = re.sub(r"\s+", " ", name.replace("’", "'").strip()).casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in _ALIASES.items():
    for _alias in (_canonical, *_aliases):
        _existing = _LOOKUP.setdefault(_key(_alias), _canonical)
        if _existing != _canonical:  # would silently merge two clubs
            raise ValueError(f"Team alias {_alias!r} maps to both {_existing!r} and {_canonical!r}")


def normalize_team_name(name: str) -> str:
    """Return the canonical club name, or the stripped input if it is unknown."""
    return _LOOKUP.get(_key(name), name.strip())


def is_known_team(name: str) -> bool:
    """True if `name` resolves to one of the canonical clubs."""
    return _key(name) in _LOOKUP
