"""Read side of `player_market_values`: a player's Transfermarkt snapshot and the summed values of a
club's profiled players. Written by `src/transform/market_values.py`; ANALYTICS_DECISIONS.md
section 25.

A missing value is None, never 0: an unmatched or unvalued player adds nothing to a club's sum and
is counted as not valued, so the sum always comes with "N of M players".
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.models.market_value import MarketValueCoverage, PlayerMarketValue, SquadMarketValue
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON

TABLE = "player_market_values"
COVERAGE_OK = 0.8  # below this share of valued players a club's sum is flagged as thin
_COLS = ["player_id", "team", "league", "match_method", "transfermarkt_name", "valuation_date",
         "market_value_eur"]  # fmt: skip


def load_market_values(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """Every row of the season (an empty frame when the table does not exist yet)."""
    engine = engine or get_engine()
    if not inspect(engine).has_table(TABLE):
        return pd.DataFrame(columns=_COLS)
    df = pd.read_sql(
        text(f"SELECT {', '.join(_COLS)} FROM {TABLE} WHERE season = :s"), engine,
        params={"s": season},
    )  # fmt: skip
    df["valuation_date"] = pd.to_datetime(df["valuation_date"]).dt.date
    return df


def player_value(player_id: str, values: pd.DataFrame) -> PlayerMarketValue | None:
    """The player's snapshot, or None when he has no row at all. Pure."""
    hit = values.loc[values["player_id"] == player_id]
    if hit.empty:
        return None
    r = hit.iloc[0]
    eur = r["market_value_eur"]
    return PlayerMarketValue(
        player_id=player_id, match_method=r["match_method"],
        transfermarkt_name=r["transfermarkt_name"] if isinstance(r["transfermarkt_name"], str) else None,
        valuation_date=None if pd.isna(eur) else r["valuation_date"],
        market_value_eur=None if pd.isna(eur) else float(eur),
    )  # fmt: skip


def values_by_player(values: pd.DataFrame) -> dict[str, float | None]:
    """player_id -> value in EUR (None when unmatched or unvalued). Pure."""
    return {
        pid: None if pd.isna(v) else float(v)
        for pid, v in zip(values["player_id"], values["market_value_eur"], strict=True)
    }


def squad_value(team: str, league: str, values: pd.DataFrame) -> SquadMarketValue:
    """Sum of the known values of the club's profiled players (each player counted at his main
    club only). `total_eur` is None when none is valued. Pure."""
    club = values.loc[(values["team"] == team) & (values["league"] == league)]
    known = club["market_value_eur"].dropna()
    return SquadMarketValue(
        team=team, league=league, total_eur=float(known.sum()) if len(known) else None,
        n_valued=len(known), n_profiled=len(club),
    )  # fmt: skip


def coverage_is_thin(sv: SquadMarketValue) -> bool:
    return sv.n_profiled == 0 or sv.n_valued / sv.n_profiled < COVERAGE_OK


def coverage(values: pd.DataFrame) -> MarketValueCoverage:
    """How many profiled players are matched and valued, overall and per league. Pure."""
    by_league = {
        league: (len(g), int((g["match_method"] != "unmatched").sum()),
                 int(g["market_value_eur"].notna().sum()))
        for league, g in values.groupby("league")
    }  # fmt: skip
    return MarketValueCoverage(
        n_profiled=len(values),
        n_matched=int((values["match_method"] != "unmatched").sum()),
        n_valued=int(values["market_value_eur"].notna().sum()),
        by_league=by_league,
    )
