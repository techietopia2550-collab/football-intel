"""Aggregate `player_match_stats` into one row per player per season, with per-90 metrics.

Output tables (SQLite, plus Parquet/CSV in `data/processed/`):
  player_season_stats_<yy>_<yy>  players with at least `min_minutes` played: identity columns,
                                 season totals of every count, and every metric from
                                 `src.analytics.metrics`.
  insufficient_sample            everyone else (all seasons in one table), with season totals
                                 and the reason they were excluded. No per-90 metrics: a rate over
                                 a few hundred minutes is not something to rank on.

Rules:
  - Minutes and counts are summed over matches; ratios and per-90 rates are computed from those
    season totals, never averaged across matches.
  - `position_group` is the group with the most minutes over the season, and `position_group_share`
    is that group's share of the player's minutes (a low share means a mixed role).
  - A player with more than one team gets the team with the most minutes, and the split is kept in
    `team_split` and written to `multi_team_players_<yy>_<yy>.csv`.
  - Nothing is dropped silently: every player below the threshold, or without a position group, is
    in `insufficient_sample` with a reason and logged.
  - The matches that failed ingest (`statsbomb_failures.csv`) are not in the input at all, so a
    player's `matches` and `minutes_total` can be a few games short. See BLOCKERS.md, section 8.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.analytics.metrics import COUNT_COLUMNS, compute_metrics
from src.models.schema import get_engine, init_db
from src.utils.config import settings
from src.utils.logging import logger

SOURCE = "statsbomb"
DEFAULT_SEASON = "2015"
DEFAULT_LEAGUE = "ENG-Premier League"
DEFAULT_MIN_MINUTES = 900.0
INSUFFICIENT_TABLE = "insufficient_sample"

_ID_COLUMNS = ["player_id", "player_name", "team", "position_group", "position_group_share"]


def season_label(season: str) -> str:
    """'2015' -> '2015_16'."""
    return f"{season}_{(int(season) + 1) % 100:02d}"


def season_table_name(season: str) -> str:
    return f"player_season_stats_{season_label(season)}"


@dataclass
class NormalizeResult:
    qualifying: pd.DataFrame
    insufficient: pd.DataFrame
    multi_team: pd.DataFrame


def load_match_stats(
    engine: Engine, season: str, league: str, source: str = SOURCE
) -> pd.DataFrame:
    """Read one league-season of `player_match_stats`."""
    query = text(
        "SELECT * FROM player_match_stats WHERE source = :source AND season = :season "
        "AND league = :league"
    )
    return pd.read_sql(query, engine, params={"source": source, "season": season, "league": league})


def _primary(df: pd.DataFrame, key: str) -> pd.DataFrame:
    """Per player: the value of `key` with the most minutes (ties broken alphabetically)."""
    minutes = df.dropna(subset=[key]).groupby(["player_id", key])["minutes"].sum().reset_index()
    minutes = minutes.sort_values(["player_id", "minutes", key], ascending=[True, False, True])
    return minutes


def aggregate_player_seasons(match_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sum `player_match_stats` per player. Returns (season totals, multi-team players).

    Pure: no database access. `match_df` holds one league-season.
    """
    df = match_df.copy()
    df["pressure_duration_total"] = df["pressure_duration_avg"].fillna(0.0) * df["pressures"]

    teams = _primary(df, "team")
    primary_team = teams.drop_duplicates("player_id").set_index("player_id")["team"]
    n_teams = teams.groupby("player_id").size()
    split = teams.assign(
        part=lambda t: t["team"] + " " + t["minutes"].round(0).astype(int).astype(str)
    )
    team_split = split.groupby("player_id")["part"].agg(" / ".join)

    groups = _primary(df, "position_group")
    primary_group = groups.drop_duplicates("player_id").set_index("player_id")
    group_minutes = groups.groupby("player_id")["minutes"].sum()

    latest = df.sort_values("match_id").drop_duplicates("player_id", keep="last")
    latest = latest.set_index("player_id")[["player_name", "season", "league", "source"]]

    sums = df.groupby("player_id")[["minutes", "pressure_duration_total", *COUNT_COLUMNS]].sum()
    out = latest.join(sums.rename(columns={"minutes": "minutes_total"}))
    out["matches"] = df.groupby("player_id").size()
    out["team"] = primary_team
    out["n_teams"] = n_teams
    out["team_split"] = team_split
    out["position_group"] = primary_group["position_group"]
    out["position_group_share"] = (primary_group["minutes"] / group_minutes).round(3)
    out = out.reset_index()

    multi = out.loc[out["n_teams"] > 1, ["player_id", "player_name", "team", "team_split"]]
    return out, multi.reset_index(drop=True)


def split_by_minutes(totals: pd.DataFrame, min_minutes: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split season totals into (qualifying, insufficient); the latter gets a `reason` column."""
    reason = pd.Series("", index=totals.index)
    short = totals["minutes_total"] < min_minutes
    reason[short] = totals.loc[short, "minutes_total"].map(
        lambda m: f"minutes_total {m:.1f} < {min_minutes:g}"
    )
    no_group = totals["position_group"].isna()
    reason[no_group & (reason == "")] = "no position_group"
    excluded = reason != ""
    return totals[~excluded].copy(), totals[excluded].assign(reason=reason[excluded]).copy()


def _column_order(df: pd.DataFrame) -> pd.DataFrame:
    front = [
        "player_id", "player_name", "team", "position_group", "position_group_share", "season",
        "league", "source", "matches", "minutes_total", "n_teams", "team_split",
    ]  # fmt: skip
    return df[front + [c for c in df.columns if c not in front]]


def build_player_season_stats(
    match_df: pd.DataFrame, min_minutes: float = DEFAULT_MIN_MINUTES
) -> NormalizeResult:
    """Aggregate, apply the minutes threshold, and compute metrics for the qualifying players."""
    totals, multi = aggregate_player_seasons(match_df)
    qualifying, insufficient = split_by_minutes(totals, min_minutes)
    qualifying = _column_order(
        compute_metrics(qualifying).drop(columns=["pressure_duration_total"])
    )
    insufficient = _column_order(insufficient.drop(columns=["pressure_duration_total"]))
    return NormalizeResult(
        qualifying.sort_values(["position_group", "minutes_total"], ascending=[True, False]),
        insufficient.sort_values("minutes_total", ascending=False),
        multi,
    )


def _replace_rows(engine: Engine, table: str, df: pd.DataFrame, season: str, league: str) -> None:
    """Replace this season/league's rows in a table shared across seasons."""
    if inspect(engine).has_table(table):
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {table} WHERE season = :s AND league = :l"),
                {"s": season, "l": league},
            )
    df.to_sql(table, engine, if_exists="append", index=False)


def run(
    season: str = DEFAULT_SEASON,
    league: str = DEFAULT_LEAGUE,
    min_minutes: float = DEFAULT_MIN_MINUTES,
    engine: Engine | None = None,
) -> NormalizeResult:
    """Build and store the season table and the insufficient-sample table."""
    engine = engine or init_db(get_engine())
    match_df = load_match_stats(engine, season, league)
    if match_df.empty:
        raise LookupError(f"No {SOURCE} player_match_stats for {league} {season}")
    result = build_player_season_stats(match_df, min_minutes)

    table, label = season_table_name(season), season_label(season)
    result.qualifying.to_sql(table, engine, if_exists="replace", index=False)
    _replace_rows(engine, INSUFFICIENT_TABLE, result.insufficient, season, league)

    out = settings.processed_dir
    out.mkdir(parents=True, exist_ok=True)
    result.qualifying.to_parquet(out / f"{table}.parquet", index=False)
    result.insufficient.to_csv(out / f"insufficient_sample_{label}.csv", index=False)
    result.multi_team.to_csv(out / f"multi_team_players_{label}.csv", index=False)

    for row in result.insufficient.itertuples():
        logger.info(
            "Excluded {name} ({team}, {pos}): {reason}",
            name=row.player_name, team=row.team, pos=row.position_group, reason=row.reason,
        )  # fmt: skip
    for row in result.multi_team.itertuples():
        logger.warning(
            "Multi-team player {name}: using {team} ({split})",
            name=row.player_name, team=row.team, split=row.team_split,
        )  # fmt: skip
    logger.info(
        "{table}: {q} qualifying players (>= {m:g} min), {i} in {ins}, {mt} multi-team",
        table=table, q=len(result.qualifying), m=min_minutes, i=len(result.insufficient),
        ins=INSUFFICIENT_TABLE, mt=len(result.multi_team),
    )  # fmt: skip
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(description="Build player season stats from match stats.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    parser.add_argument("--min-minutes", type=float, default=DEFAULT_MIN_MINUTES)
    args = parser.parse_args()
    result = run(args.season, args.league, args.min_minutes)
    print(
        f"{len(result.qualifying)} qualifying, {len(result.insufficient)} insufficient, "
        f"{len(result.multi_team)} multi-team"
    )


if __name__ == "__main__":
    _main()
