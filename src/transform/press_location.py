"""Press location: where on the pitch a player makes his pressing actions (`player_press_location`).

Offline re-parse of the cached StatsBomb events (`data/raw/statsbomb/events/`), for the matches in
the `matches` table only; nothing is downloaded (a missing cache file is an error). Per player per
season:

  avg_pressure_x      mean x of `Pressure` events
  avg_tackle_x        mean x of `Duel` events of type Tackle
  avg_interception_x  mean x of `Interception` events
  avg_press_action_x  mean x over all three pooled (a count-weighted mean of the three, so
                      pressures, the most numerous, dominate)

Also, from the same pass over the events (so Direct speed needs no second re-parse):

  avg_pass_dx         mean forward x-gain per pass, end x - start x, over every `Pass` with an
                      end location (set pieces and incomplete passes included) - the team-level
                      Direct speed definition (`team_style.style_from_counts`) applied to one player

StatsBomb coordinates are oriented per acting team: x=0 is the player's own goal line, x=120 the
opponent's. Penalty shoot-out events (period 5) are skipped, as in `parse.aggregate_events`.

Not the same as `avg_defensive_action_x` in the season table (tackles, interceptions, clearances
and blocks; no pressures), which feeds the Defensive-line-height axis. ANALYTICS_DECISIONS.md
section 30.

    python -m src.transform.press_location
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from src.ingest.statsbomb.fetch import events_cache_path, load_cached_json
from src.ingest.statsbomb.parse import SHOOTOUT_PERIOD
from src.models.schema import get_engine, init_db
from src.transform.normalize import DEFAULT_SEASON, replace_season_rows
from src.utils.logging import logger

PRESS_LOCATION_TABLE = "player_press_location"
SOURCE = "statsbomb"
KINDS = ("pressure", "tackle", "interception")


def press_kind(event: dict[str, Any]) -> str | None:
    """'pressure', 'tackle', 'interception' or None for any other event. Pure."""
    kind = event["type"]["name"]
    if kind == "Pressure":
        return "pressure"
    if kind == "Interception":
        return "interception"
    if kind == "Duel" and event.get("duel", {}).get("type", {}).get("name") == "Tackle":
        return "tackle"
    return None


Sums = defaultdict[str, float]  # n_<kind>, x_<kind>, n_pass, dx_pass for one player


def _sums() -> Sums:
    return defaultdict(float)


def accumulate_press_x(
    events: Iterable[dict[str, Any]], acc: dict[int, Sums] | None = None
) -> dict[int, Sums]:
    """Add each located pressing action's count and x to `acc` ({statsbomb id: sums}), keys
    `n_<kind>` and `x_<kind>`, and passes' count and summed x-gain (`n_pass`, `dx_pass`). Pure
    apart from mutating `acc`."""
    acc = acc if acc is not None else defaultdict(_sums)
    for e in events:
        if e.get("period", 0) >= SHOOTOUT_PERIOD or "player" not in e or not e.get("location"):
            continue
        if e["type"]["name"] == "Pass" and "end_location" in e.get("pass", {}):
            c = acc[e["player"]["id"]]
            c["n_pass"] += 1
            c["dx_pass"] += float(e["pass"]["end_location"][0]) - float(e["location"][0])
            continue
        kind = press_kind(e)
        if kind is None:
            continue
        c = acc[e["player"]["id"]]
        c[f"n_{kind}"] += 1
        c[f"x_{kind}"] += float(e["location"][0])
    return acc


def summarise(acc: dict[int, Sums]) -> pd.DataFrame:
    """One row per StatsBomb player: counts and mean x per kind, plus the pooled mean. Pure."""
    rows = []
    for sb_id, c in acc.items():
        row: dict[str, Any] = {"statsbomb_id": sb_id}
        for k in KINDS:
            n = int(c[f"n_{k}"])
            row[f"{k}s"] = n
            row[f"avg_{k}_x"] = round(c[f"x_{k}"] / n, 2) if n else None
        total = int(sum(c[f"n_{k}"] for k in KINDS))
        row["press_actions"] = total
        row["avg_press_action_x"] = (
            round(sum(c[f"x_{k}"] for k in KINDS) / total, 2) if total else None
        )
        row["passes"] = int(c["n_pass"])
        row["avg_pass_dx"] = round(c["dx_pass"] / c["n_pass"], 2) if c["n_pass"] else None
        rows.append(row)
    return pd.DataFrame(rows)


def run(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """Re-parse every cached event file of the season's StatsBomb matches and store the table."""
    engine = engine or init_db(get_engine())
    matches = pd.read_sql(
        text("SELECT match_id, league FROM matches WHERE source = :src AND season = :s"),
        engine, params={"src": SOURCE, "s": season},
    )  # fmt: skip
    acc: dict[int, Sums] = defaultdict(_sums)
    for i, match_id in enumerate(matches["match_id"], 1):
        path = events_cache_path(int(match_id))
        if not path.exists():
            raise FileNotFoundError(f"No cached events for match {match_id}: {path}")
        accumulate_press_x(load_cached_json(path, list), acc)
        if i % 250 == 0:
            logger.info("press location: {i}/{n} matches", i=i, n=len(matches))
    out = summarise(acc)
    # league: the one the player logged most minutes in (as the season table does, via its row)
    league = pd.read_sql(
        text(
            "SELECT player_id, league, SUM(minutes) AS m FROM player_match_stats "
            "WHERE source = :src AND season = :s GROUP BY player_id, league"
        ),
        engine, params={"src": SOURCE, "s": season},
    ).sort_values("m").drop_duplicates("player_id", keep="last")  # fmt: skip
    ids = pd.read_sql(
        text("SELECT player_id, statsbomb_id FROM players WHERE statsbomb_id IS NOT NULL"), engine
    )
    ids["statsbomb_id"] = ids["statsbomb_id"].astype(int)
    out = out.merge(ids, on="statsbomb_id", how="inner").merge(
        league[["player_id", "league"]], on="player_id", how="inner"
    )
    out["season"] = season
    out["source"] = SOURCE
    cols = ["player_id", "season", "league", "source", "press_actions", "avg_press_action_x"]
    cols += [c for k in KINDS for c in (f"{k}s", f"avg_{k}_x")] + ["passes", "avg_pass_dx"]
    out = out[cols].sort_values("player_id").reset_index(drop=True)
    replace_season_rows(engine, PRESS_LOCATION_TABLE, out, season)
    logger.info("{t}: {n} rows, {m} matches", t=PRESS_LOCATION_TABLE, n=len(out), m=len(matches))
    return out


def load_press_location(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """Stored rows of the season."""
    engine = engine or get_engine()
    return pd.read_sql(
        text(f"SELECT * FROM {PRESS_LOCATION_TABLE} WHERE season = :s"),
        engine, params={"s": season},
    )  # fmt: skip


if __name__ == "__main__":
    result = run()
    print(f"{len(result)} players")
