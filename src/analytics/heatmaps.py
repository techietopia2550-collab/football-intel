"""Per-player event-location heatmaps for a league-season. DATA ONLY: no rendering.

Each qualifying player-season gets one row per heatmap type in `player_heatmaps`, holding a 2D
histogram of where his events happened:

  grid            12 columns (x, pitch length) by 8 rows (y, pitch width) = 96 cells of 10 x 10
                  yards on StatsBomb's 120 x 80 pitch. Stored as JSON `grid[row][col]` (8 lists of
                  12 numbers). Column 0 is the strip next to the player's OWN goal line and
                  column 11 the strip next to the opponent's goal, because StatsBomb always
                  records the acting team attacking towards x = 120. Ten-yard cells were chosen
                  because they divide the pitch exactly, they are coarse enough that a 900-minute
                  player's few hundred events still show a shape, and a UI can smooth them.
  y direction     row 0 is y in [0, 10), row 7 is y in [70, 80]. In this data y increases towards
                  the acting player's RIGHT-hand touchline (verified on the data: right-backs' mean
                  y is high and left-backs' is low; see docs/HEATMAPS.md).
  coordinates     StatsBomb x runs 0.1 to 120.9 and y 0.1 to 80.0 in the 2015/16 data, so a few
                  events lie marginally outside 0-120 / 0-80; they are clamped into the edge cell.

Heatmap types (each event counts once per type it belongs to):
  all_actions       every event with a player and a location (period 1-4; no shoot-out).
  touches           completed pass receipts (`Ball Receipt*` without an outcome), carries (start
                    location), dribbles and shots. As specified for this project; passes played
                    are not "touches" here.
  defensive         tackle duels, interceptions, blocks, clearances and ball recoveries that did
                    not fail (the same event definitions as the metrics).
  passing           passes played, by their START location. Key passes and passes into the box
                    are subsets of passes, so they are not added again.
  shots             shots, by shot location.
  all_actions_1h / _2h   `all_actions` restricted to period 1 / period 2 (a league season has no
                    period 3+; extra time would count in `all_actions` only).

Normalisation: `grid` holds raw event counts; `grid_p90` is the same grid divided by the
player's season minutes and multiplied by 90 (cells are events per 90 minutes, so players with
different playing time are comparable). `event_count` and `event_count_p90` are the grid sums.
Minutes are the season total from the season table (`minutes_total`); events come from the same
matches (the stored ones), so counts and minutes cover the same games.

Not a measure of quality: a heatmap is where a player was involved, which reflects role, team style
and possession as much as the player.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from src.ingest.statsbomb.fetch import fetch_match_events
from src.models.schema import get_engine, init_db
from src.transform.normalize import (
    DEFAULT_LEAGUE,
    DEFAULT_SEASON,
    SOURCE,
    replace_season_rows,
    season_table_name,
)
from src.utils.logging import logger

HEATMAPS_TABLE = "player_heatmaps"
GRID_COLS, GRID_ROWS = 12, 8
PITCH_LENGTH, PITCH_WIDTH = 120.0, 80.0
SHOOTOUT_PERIOD = 5

HEATMAP_TYPES: tuple[str, ...] = (
    "all_actions",
    "touches",
    "defensive",
    "passing",
    "shots",
    "all_actions_1h",
    "all_actions_2h",
)
_TACKLE_LIKE = {"Interception", "Block", "Clearance"}

_COLUMNS = [
    "player_id", "player_name", "season", "league", "source", "position_group_fine",
    "heatmap_type", "minutes_total", "event_count", "event_count_p90", "grid_json",
    "grid_p90_json",
]  # fmt: skip

Grids = dict[tuple[int, str], np.ndarray]  # (statsbomb player id, heatmap type) -> 8 x 12 counts


def cell(x: float, y: float) -> tuple[int, int]:
    """(row, col) of a coordinate; positions outside the pitch are clamped to the edge cell."""
    col = min(max(int(x // (PITCH_LENGTH / GRID_COLS)), 0), GRID_COLS - 1)
    row = min(max(int(y // (PITCH_WIDTH / GRID_ROWS)), 0), GRID_ROWS - 1)
    return row, col


def heatmap_types_for(event: dict[str, Any]) -> list[str]:
    """The heatmap types one event contributes to (empty if it has no actor, location or is a
    shoot-out event)."""
    if "player" not in event or "location" not in event or event["period"] >= SHOOTOUT_PERIOD:
        return []
    kind = event["type"]["name"]
    types = ["all_actions"]
    if event["period"] == 1:
        types.append("all_actions_1h")
    elif event["period"] == 2:
        types.append("all_actions_2h")

    if kind == "Ball Receipt*":
        if "outcome" not in event.get("ball_receipt", {}):
            types.append("touches")
    elif kind in {"Carry", "Dribble"}:
        types.append("touches")
    elif kind == "Shot":
        types += ["touches", "shots"]
    elif kind == "Pass":
        types.append("passing")
    elif kind == "Ball Recovery":
        if not event.get("ball_recovery", {}).get("recovery_failure"):
            types.append("defensive")
    elif kind == "Duel":
        if event["duel"]["type"]["name"] == "Tackle":
            types.append("defensive")
    elif kind in _TACKLE_LIKE:
        types.append("defensive")
    return types


def accumulate(events: Iterable[dict[str, Any]], grids: Grids | None = None) -> Grids:
    """Add every event's location to the grids of the player who performed it."""
    grids = grids if grids is not None else defaultdict(lambda: np.zeros((GRID_ROWS, GRID_COLS)))
    for e in events:
        types = heatmap_types_for(e)
        if not types:
            continue
        row, col = cell(*e["location"][:2])
        pid = e["player"]["id"]
        for t in types:
            grids[(pid, t)][row, col] += 1
    return grids


def _grid_json(grid: np.ndarray, decimals: int | None = None) -> str:
    values = grid if decimals is None else grid.round(decimals)
    return json.dumps(values.tolist() if decimals else values.astype(int).tolist())


def build_heatmaps(
    season_stats: pd.DataFrame, grids: Grids, statsbomb_ids: dict[int, str]
) -> pd.DataFrame:
    """One row per qualifying player per heatmap type (all-zero grids included).

    `statsbomb_ids` maps a StatsBomb player id to our `player_id`.
    """
    by_our_id = {our: sb for sb, our in statsbomb_ids.items()}
    rows: list[dict[str, Any]] = []
    for p in season_stats.itertuples():
        sb_id = by_our_id.get(p.player_id)
        minutes = float(p.minutes_total)
        for kind in HEATMAP_TYPES:
            empty = np.zeros((GRID_ROWS, GRID_COLS))
            grid = empty if sb_id is None else grids.get((sb_id, kind), empty)
            per90 = grid / minutes * 90 if minutes > 0 else grid * 0
            rows.append(
                {
                    "player_id": p.player_id,
                    "player_name": p.player_name,
                    "season": p.season,
                    "league": p.league,
                    "source": p.source,
                    "position_group_fine": p.position_group_fine,
                    "heatmap_type": kind,
                    "minutes_total": round(minutes, 2),
                    "event_count": int(grid.sum()),
                    "event_count_p90": round(float(per90.sum()), 4),
                    "grid_json": _grid_json(grid),
                    "grid_p90_json": _grid_json(per90, decimals=4),
                }
            )
    return pd.DataFrame(rows, columns=_COLUMNS)


def describe(grid: np.ndarray) -> dict[str, float]:
    """Where a grid's events sit, as shares of its total (for sanity checks and documentation).

    `own_box_zone` is columns 0-1 (x < 20) by rows 2-5 (y 20-60): a box-sized 20 x 40 yard zone,
    a little larger than the real 18-yard box because cells are 10 yards wide.
    """
    total = float(grid.sum())
    if total == 0:
        return {}
    col_share = grid.sum(axis=0) / total
    row_share = grid.sum(axis=1) / total
    xs = (np.arange(GRID_COLS) + 0.5) * (PITCH_LENGTH / GRID_COLS)
    ys = (np.arange(GRID_ROWS) + 0.5) * (PITCH_WIDTH / GRID_ROWS)
    return {
        "events": total,
        "mean_x": float((col_share * xs).sum()),
        "mean_y": float((row_share * ys).sum()),
        "own_box_zone": float(grid[2:6, 0:2].sum() / total),
        "own_half": float(col_share[:6].sum()),
        "middle_third_x": float(col_share[4:8].sum()),
        "final_third": float(col_share[8:].sum()),
        "central_lanes": float(row_share[2:6].sum()),
        "right_wide": float(row_share[6:].sum()),
        "left_wide": float(row_share[:2].sum()),
    }


def load_statsbomb_ids(engine: Engine) -> dict[int, str]:
    """StatsBomb player id -> our player_id, from `players`."""
    df = pd.read_sql(
        text("SELECT statsbomb_id, player_id FROM players WHERE statsbomb_id IS NOT NULL"), engine
    )
    return {int(r.statsbomb_id): r.player_id for r in df.itertuples()}


def run(
    season: str = DEFAULT_SEASON,
    league: str = DEFAULT_LEAGUE,
    engine: Engine | None = None,
    load_events: Callable[[int], list[dict[str, Any]]] = fetch_match_events,
) -> pd.DataFrame:
    """Build and store `player_heatmaps` for a league-season (replacing its earlier rows).

    Reads the cached StatsBomb event files for the matches that were ingested (so the events
    cover the same games as the minutes). Only qualifying players get rows.
    """
    engine = engine or init_db(get_engine())
    stats = pd.read_sql(
        text(f"SELECT * FROM {season_table_name(season)} WHERE league = :l AND season = :s"),
        engine,
        params={"l": league, "s": season},
    )
    match_ids = pd.read_sql(
        text(
            "SELECT match_id FROM matches WHERE source = :src AND season = :s AND league = :l "
            "ORDER BY match_id"
        ),
        engine,
        params={"src": SOURCE, "s": season, "l": league},
    )["match_id"].tolist()
    if not match_ids:
        raise LookupError(f"No stored {SOURCE} matches for {league} {season}")

    grids: Grids = defaultdict(lambda: np.zeros((GRID_ROWS, GRID_COLS)))
    for i, match_id in enumerate(match_ids, start=1):
        accumulate(load_events(int(match_id)), grids)
        if i % 100 == 0 or i == len(match_ids):
            logger.info("Heatmaps: {i}/{n} matches read", i=i, n=len(match_ids))

    result = build_heatmaps(stats, grids, load_statsbomb_ids(engine))
    replace_season_rows(engine, HEATMAPS_TABLE, result, season, league)
    logger.info(
        "{t}: {rows} rows for {p} players", t=HEATMAPS_TABLE, rows=len(result),
        p=result["player_id"].nunique(),
    )  # fmt: skip
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(description="Compute per-player heatmap grids.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--league", default=DEFAULT_LEAGUE)
    args = parser.parse_args()
    result = run(args.season, args.league)
    print(f"{len(result)} rows, {result['player_id'].nunique()} players")


if __name__ == "__main__":
    _main()
