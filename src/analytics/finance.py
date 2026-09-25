"""Team wage-bill context: a club's annual wage bill and its rank among the season's clubs.

Team level only: this says what the club spent on wages, not what any player earned. Clubs with no
known wage bill are left out of the ranking (never estimated), so `teams_ranked` can be below the
number of clubs in the season.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.models.finance import TeamWageContext
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON


def load_team_finance(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """Every `team_finance` row of the season (empty frame when the table does not exist yet)."""
    engine = engine or get_engine()
    cols = ["team_name", "league", "wage_bill_annual_gbp", "source", "confidence"]
    if not inspect(engine).has_table("team_finance"):
        return pd.DataFrame(columns=cols)
    with engine.connect() as conn:
        return pd.read_sql(
            text(f"SELECT {', '.join(cols)} FROM team_finance WHERE season = :season"),
            conn, params={"season": season},
        )  # fmt: skip


def wage_ranks(finance: pd.DataFrame) -> pd.Series:
    """Rank by wage bill, 1 = highest, across all leagues. Unknown bills get NA. Pure."""
    return (
        pd.to_numeric(finance["wage_bill_annual_gbp"])
        .rank(ascending=False, method="min")
        .astype("Int64")
    )


def wage_context(
    team_name: str,
    league: str,
    teams_in_season: int,
    finance: pd.DataFrame,
    season: str = DEFAULT_SEASON,
) -> TeamWageContext:
    """The club's wage context from a season's finance rows. Pure. Unknown -> None fields."""
    ranks = wage_ranks(finance)
    hit = finance.index[(finance["team_name"] == team_name) & (finance["league"] == league)]
    row = finance.loc[hit[0]] if len(hit) else None
    bill = (
        None
        if row is None or pd.isna(row["wage_bill_annual_gbp"])
        else float(row["wage_bill_annual_gbp"])
    )
    return TeamWageContext(
        team_name=team_name, season=season, league=league, wage_bill_annual_gbp=bill,
        rank=None if bill is None else int(ranks.loc[hit[0]]),
        teams_ranked=int(finance["wage_bill_annual_gbp"].notna().sum()),
        teams_in_season=teams_in_season,
        source=None if row is None else str(row["source"]),
        confidence=None if row is None else str(row["confidence"]),
    )  # fmt: skip


def team_wage_context(
    team_name: str, league: str, season: str = DEFAULT_SEASON, engine: Engine | None = None
) -> TeamWageContext:
    """`wage_context` read from the database; `teams_in_season` counts the clubs in
    `player_match_stats` for the season."""
    engine = engine or get_engine()
    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT COUNT(DISTINCT team) FROM player_match_stats WHERE season = :s"),
            {"s": season},
        ).scalar_one()
    return wage_context(team_name, league, int(n), load_team_finance(season, engine), season)
