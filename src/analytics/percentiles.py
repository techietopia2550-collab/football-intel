"""Percentile-rank every metric within position group, across a whole league-season.

Rules:
  - Only qualifying players (those in `player_season_stats_<yy>_<yy>`, i.e. above the minutes
    threshold) are ranked, and only against players of the same `position_group`, never across.
  - Outfield metrics are ranked among DEF/MID/FWD and goalkeeper metrics among GK only.
  - Percentile is the mid-rank share of the group below the value: (rank - 0.5) / n * 100, with tied
    values sharing the average rank. 100 is the highest *value*; for a metric where lower is better
    the profile layer flips it, so the stored percentile is always "how high is the number".
  - A metric with no value (a ratio under its minimum-denominator guard) is stored with a NULL value
    and percentile and is not counted in `sample_size`.
  - `sample_size` is the number of players ranked for that metric in that group; `group_size` is
    every qualifying player in the group. `small_group` is True when `group_size` < MIN_GROUP_SIZE,
    which means percentiles there are coarse and must not be quoted as precise.

Output table `player_percentiles`: one row per qualifying player per applicable metric.
"""

from __future__ import annotations

import argparse

import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.analytics.metrics import GK, METRICS, POSITION_GROUPS, Metric
from src.models.schema import get_engine, init_db
from src.transform.normalize import DEFAULT_LEAGUE, DEFAULT_SEASON, season_table_name
from src.utils.logging import logger

PERCENTILES_TABLE = "player_percentiles"
MIN_GROUP_SIZE = 20

_COLUMNS = [
    "player_id", "player_name", "team", "season", "league", "source", "position_group", "metric",
    "metric_group", "value", "percentile", "sample_size", "group_size", "small_group",
]  # fmt: skip


def _applicable(metric: Metric, group: str) -> bool:
    return (group == GK) == (metric.scope == "gk")


def compute_percentiles(stats: pd.DataFrame) -> pd.DataFrame:
    """Long table of percentiles from a season-stats table (columns as built by `normalize`)."""
    frames: list[pd.DataFrame] = []
    for group in POSITION_GROUPS:
        members = stats[stats["position_group"] == group]
        if members.empty:
            continue
        for metric in METRICS:
            if not _applicable(metric, group):
                continue
            values = members[metric.name]
            n = int(values.notna().sum())
            rank = values.rank(method="average")
            percentile = ((rank - 0.5) / n * 100).round(1) if n else values * float("nan")
            frames.append(
                members[["player_id", "player_name", "team", "season", "league", "source"]].assign(
                    position_group=group,
                    metric=metric.name,
                    metric_group=metric.group,
                    value=values,
                    percentile=percentile,
                    sample_size=n,
                    group_size=len(members),
                    small_group=len(members) < MIN_GROUP_SIZE,
                )
            )
    if not frames:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.concat(frames, ignore_index=True)[_COLUMNS]


def run(
    season: str = DEFAULT_SEASON, league: str = DEFAULT_LEAGUE, engine: Engine | None = None
) -> pd.DataFrame:
    """Compute and store `player_percentiles` for a league-season (replacing its earlier rows)."""
    engine = engine or init_db(get_engine())
    stats = pd.read_sql(text(f"SELECT * FROM {season_table_name(season)}"), engine)
    stats = stats[stats["league"] == league]
    result = compute_percentiles(stats)

    if inspect(engine).has_table(PERCENTILES_TABLE):
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {PERCENTILES_TABLE} WHERE season = :s AND league = :l"),
                {"s": season, "l": league},
            )
    result.to_sql(PERCENTILES_TABLE, engine, if_exists="append", index=False)

    sizes = result.drop_duplicates(["position_group"]).set_index("position_group")["group_size"]
    for group, size in sizes.items():
        if size < MIN_GROUP_SIZE:
            logger.warning(
                "Position group {g} has only {n} qualifying players (< {m}): coarse percentiles",
                g=group, n=size, m=MIN_GROUP_SIZE,
            )  # fmt: skip
    logger.info(
        "{t}: {rows} rows for {p} players; group sizes {sizes}",
        t=PERCENTILES_TABLE, rows=len(result), p=result["player_id"].nunique(),
        sizes=sizes.to_dict(),
    )  # fmt: skip
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(description="Compute within-position percentiles.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    args = parser.parse_args()
    result = run(args.season, args.league)
    print(f"{len(result)} rows, {result['player_id'].nunique()} players")


if __name__ == "__main__":
    _main()
