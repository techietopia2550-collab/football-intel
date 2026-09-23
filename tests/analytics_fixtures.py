"""Synthetic `player_match_stats` rows for the metrics, normalize, percentile and profile tests.

`league_rows()` builds a small league where every player's stats are a known function of his index,
so percentiles and classifications can be asserted exactly. No real StatsBomb data.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine

from src.analytics.metrics import COUNT_COLUMNS, OUTFIELD_GROUPS
from src.models.schema import init_db

SEASON, LEAGUE = "2015", "ENG-Premier League"

# fine group -> the coarse group the ingest would also record for the same position
COARSE_GROUP = {
    "GK": "GK", "CB": "DEF", "FB": "DEF", "DM": "MID", "CM": "MID", "AM": "MID", "W": "FWD",
    "ST": "FWD",
}  # fmt: skip


def player_rows(
    pid: int, name: str, team: str, group: str, *, matches: int = 10, minutes: float = 90.0,
    first_match: int = 1, pressure_avg: float | None = 1.5, league: str = LEAGUE, **counts: float,
) -> list[dict[str, object]]:  # fmt: skip
    """`matches` identical match rows for one player; `group` is a FINE position group and
    `counts` are per-match values."""
    rows = []
    for k in range(matches):
        row: dict[str, object] = {
            "source": "statsbomb", "match_id": first_match + k, "player_id": f"statsbomb:{pid}",
            "player_name": name, "team": team, "opponent": "Opp", "season": SEASON,
            "league": league, "position_played": group, "position_group": COARSE_GROUP[group],
            "position_group_fine": group, "minutes": minutes, "pass_completion_pct": None,
            "pressure_duration_avg": pressure_avg,
        }  # fmt: skip
        row.update({c: 0 for c in COUNT_COLUMNS})
        row.update(counts)
        rows.append(row)
    return rows


def league_rows(per_group: int = 22) -> pd.DataFrame:
    """`per_group` players in each of the seven outfield fine groups (stats rise with the index),
    plus as many goalkeepers."""
    rows: list[dict[str, object]] = []
    pid = 0
    for group in OUTFIELD_GROUPS:
        for i in range(per_group):
            pid += 1
            rows += player_rows(
                pid, f"{group} {i:02d}", f"Team {i % 5}", group,
                goals=i / 10, shots=2 + i / 10, shots_on_target=1 + i / 20, xg=i / 20,
                tackles=1 + i / 10, tackles_won=(1 + i / 10) * 0.5,
                passes_attempted=50, passes_completed=30 + i, fouls_committed=i / 10,
                key_passes=i / 10,
            )  # fmt: skip
    for i in range(per_group):
        pid += 1
        rows += player_rows(
            pid, f"Keeper {i:02d}", f"Team {i % 5}", "GK", gk_saves=2 + i / 10, gk_goals_conceded=1,
            gk_xg_on_target_faced=2.5, passes_attempted=30, passes_completed=15,
        )  # fmt: skip
    return pd.DataFrame(rows)


def load_into(engine: Engine, df: pd.DataFrame) -> None:
    """Create the schema and insert `df` as `player_match_stats` (no foreign keys are enforced)."""
    init_db(engine)
    df.to_sql("player_match_stats", engine, if_exists="append", index=False)
