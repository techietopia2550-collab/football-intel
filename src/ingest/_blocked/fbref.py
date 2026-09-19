"""FBref ingest: season-level player stats via `soccerdata.FBref`.

Data collection only — this module talks to soccerdata and writes raw
output to `/data/raw`, then normalizes it into `player_season_stats`.
No analytics logic lives here (see CLAUDE.md).

soccerdata 1.9.1's `FBref.read_player_season_stats` only exposes these
stat_type values: standard, keeper, shooting, playing_time, misc.
Categories originally scoped for this ingest (passing, passing_types,
defense, possession, keeper_adv) do not exist at the season-stats level
in this soccerdata version -- only at match level (`summary`/`keepers`
per game), which would mean scraping every match and is out of scope
for this session. See the session notes handed back with this module.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import soccerdata as sd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.models.player import PlayerSeasonRecord
from src.models.schema import (
    IngestLog,
    Player,
    PlayerSeasonStats,
    Team,
    build_team_id,
    get_session_factory,
    init_db,
)
from src.utils.config import FBREF_LEAGUES, settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

SOURCE = "fbref"

OUTFIELD_STAT_TYPES: list[str] = ["standard", "shooting", "playing_time", "misc"]
KEEPER_STAT_TYPES: list[str] = ["standard", "keeper"]
ALL_STAT_TYPES: list[str] = ["standard", "shooting", "playing_time", "misc", "keeper"]

_POSITION_GROUPS: dict[str, str] = {
    "GK": "Goalkeeper",
    "DF": "Defender",
    "MF": "Midfielder",
    "FW": "Forward",
}


def _raw_cache_path(season: int, league: str) -> Path:
    return settings.cache_dir / "fbref" / str(season) / f"{league}.parquet"


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse FBref's two-level (category, stat) columns into single names."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    df = df.copy()
    flat_names = []
    for col in df.columns:
        parts = [str(level) for level in col if level and not str(level).startswith("Unnamed")]
        flat_names.append("_".join(parts).strip("_").lower().replace(" ", "_").replace("%", "pct"))
    df.columns = flat_names
    return df


@rate_limited()
def _read_stat_type(fb: sd.FBref, stat_type: str) -> pd.DataFrame:
    """Single rate-limited call into soccerdata for one stat category."""
    return fb.read_player_season_stats(stat_type=stat_type)


def _position_group(position: str | None) -> str | None:
    if not position:
        return None
    first = position.split(",")[0].strip().upper()
    return _POSITION_GROUPS.get(first)


def fetch_fbref(season: int, league: str, *, force: bool = False) -> pd.DataFrame:
    """Fetch (or load cached) FBref player-season stats for one league/season.

    Merges every stat category soccerdata exposes at season level into one
    wide DataFrame indexed by (league, season, team, player). Writes the
    merged raw result to `/data/raw/fbref/<season>/<league>.parquet`.
    Skips the network entirely if that cache already exists, unless
    `force=True`.
    """
    cache_path = _raw_cache_path(season, league)
    if cache_path.exists() and not force:
        logger.info("FBref cache hit, skipping network: {path}", path=cache_path)
        return pd.read_parquet(cache_path)

    if league not in FBREF_LEAGUES:
        logger.warning(
            "League {league} is not in the configured FBref league list; fetching anyway.",
            league=league,
        )

    fb = sd.FBref(leagues=league, seasons=season)

    merged: pd.DataFrame | None = None
    for stat_type in ALL_STAT_TYPES:
        logger.info(
            "Fetching FBref '{stat_type}' for {league} {season}",
            stat_type=stat_type,
            league=league,
            season=season,
        )
        try:
            df = _read_stat_type(fb, stat_type)
        except Exception as exc:  # noqa: BLE001 - log and continue with other stat types
            logger.error(
                "Failed to fetch FBref '{stat_type}' for {league} {season}: {exc}",
                stat_type=stat_type,
                league=league,
                season=season,
                exc=exc,
            )
            continue
        df = _flatten_columns(df)
        if stat_type != "standard":
            df = df.add_prefix(f"{stat_type}_")
        merged = df if merged is None else merged.join(df, how="outer", rsuffix=f"_{stat_type}_dup")

    if merged is None:
        raise RuntimeError(f"No FBref stat category could be fetched for {league} {season}")

    merged = merged.reset_index()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(cache_path, index=False)
    logger.info("Wrote FBref raw cache: {path} ({rows} rows)", path=cache_path, rows=len(merged))
    return merged


def _to_records(df: pd.DataFrame, *, season: int, league: str) -> list[PlayerSeasonRecord]:
    """Normalize a raw FBref DataFrame into validated PlayerSeasonRecord rows.

    Rows missing a player name are dropped and logged (never silently).
    """
    records: list[PlayerSeasonRecord] = []
    dropped = 0
    for row in df.to_dict(orient="records"):
        name = row.get("player")
        if not name or (isinstance(name, float) and pd.isna(name)):
            dropped += 1
            continue

        team = row.get("team")
        position = row.get("pos")
        fbref_id = row.get("player_id") or row.get("url")

        minutes_raw = row.get("playing_time_min") or row.get("min")
        matches_raw = row.get("playing_time_mp") or row.get("mp")
        born_raw = row.get("born")

        raw_stats = {
            k: v
            for k, v in row.items()
            if k not in {"league", "season", "team", "player"}
            and not (isinstance(v, float) and pd.isna(v))
        }

        player_id = f"fbref:{fbref_id}" if fbref_id else f"fbref:{name}:{team}"

        records.append(
            PlayerSeasonRecord(
                player_id=player_id,
                canonical_name=str(name),
                source=SOURCE,
                season=str(season),
                league=league,
                team=str(team) if team is not None and not pd.isna(team) else None,
                position=str(position) if position is not None and not pd.isna(position) else None,
                position_group=_position_group(str(position) if position else None),
                minutes=int(minutes_raw)
                if minutes_raw is not None and not pd.isna(minutes_raw)
                else None,
                matches=int(matches_raw)
                if matches_raw is not None and not pd.isna(matches_raw)
                else None,
                birth_year=int(born_raw)
                if born_raw is not None and not pd.isna(born_raw)
                else None,
                nationality=str(row.get("nation"))
                if row.get("nation") is not None and not pd.isna(row.get("nation"))
                else None,
                raw_stats=raw_stats,
            )
        )

    if dropped:
        logger.warning(
            "Dropped {n} FBref rows with no player name for {league} {season}",
            n=dropped,
            league=league,
            season=season,
        )
    return records


def _upsert_records(session: Session, records: list[PlayerSeasonRecord]) -> int:
    """Upsert players + player_season_stats rows. Idempotent on rerun."""
    rows_written = 0
    for rec in records:
        session.execute(
            sqlite_insert(Player)
            .values(
                player_id=rec.player_id,
                canonical_name=rec.canonical_name,
                fbref_id=rec.player_id.removeprefix("fbref:") if rec.source == SOURCE else None,
                birth_year=rec.birth_year,
                nationality=rec.nationality,
                position_group=rec.position_group,
            )
            .on_conflict_do_update(
                index_elements=["player_id"],
                set_={
                    "canonical_name": rec.canonical_name,
                    "fbref_id": rec.player_id.removeprefix("fbref:")
                    if rec.source == SOURCE
                    else None,
                    "birth_year": rec.birth_year,
                    "nationality": rec.nationality,
                    "position_group": rec.position_group,
                },
            )
        )

        if rec.team:
            team_id = build_team_id(
                source=rec.source, league=rec.league, season=rec.season, name=rec.team
            )
            session.execute(
                sqlite_insert(Team)
                .values(
                    team_id=team_id,
                    name=rec.team,
                    league=rec.league,
                    season=rec.season,
                    source=rec.source,
                )
                .on_conflict_do_nothing(index_elements=["team_id"])
            )

        session.execute(
            sqlite_insert(PlayerSeasonStats)
            .values(
                player_id=rec.player_id,
                source=rec.source,
                season=rec.season,
                league=rec.league,
                team=rec.team,
                position=rec.position,
                position_group=rec.position_group,
                minutes=rec.minutes,
                matches=rec.matches,
                raw_stats=rec.raw_stats,
            )
            .on_conflict_do_update(
                index_elements=["player_id", "season", "league", "source"],
                set_={
                    "team": rec.team,
                    "position": rec.position,
                    "position_group": rec.position_group,
                    "minutes": rec.minutes,
                    "matches": rec.matches,
                    "raw_stats": rec.raw_stats,
                },
            )
        )
        rows_written += 1
    session.commit()
    return rows_written


def ingest_fbref(season: int, league: str, *, force: bool = False) -> int:
    """Fetch, normalize, and upsert FBref player-season stats. Returns row count."""
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    status = "success"
    error: str | None = None
    rows = 0

    try:
        raw_df = fetch_fbref(season, league, force=force)
        records = _to_records(raw_df, season=season, league=league)
        with session_factory() as session:
            rows = _upsert_records(session, records)
    except Exception as exc:  # noqa: BLE001 - always log to ingest_log before re-raising
        status = "failed"
        error = str(exc)
        logger.error(
            "FBref ingest failed for {league} {season}: {exc}",
            league=league,
            season=season,
            exc=exc,
        )
        raise
    finally:
        with session_factory() as session:
            session.add(
                IngestLog(
                    source=SOURCE,
                    season=str(season),
                    league=league,
                    rows=rows,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    error=error,
                )
            )
            session.commit()

    logger.info(
        "FBref ingest complete: {rows} rows for {league} {season}",
        rows=rows,
        league=league,
        season=season,
    )
    return rows


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest FBref player-season stats.")
    parser.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2024.")
    parser.add_argument(
        "--league", type=str, required=True, help="League code, e.g. 'ENG-Premier League'."
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch even if a raw cache exists.")
    args = parser.parse_args()

    rows = ingest_fbref(args.season, args.league, force=args.force)
    print(f"Ingested {rows} rows for {args.league} {args.season}")


if __name__ == "__main__":
    _main()
