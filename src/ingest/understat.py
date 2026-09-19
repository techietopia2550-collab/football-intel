"""Understat ingest: player-season xG/xA enrichment via `soccerdata.Understat`.

Data collection only (see CLAUDE.md) -- talks to soccerdata, writes raw
output to `/data/raw`, then normalizes it into `player_season_stats`
with `source="understat"`.

`soccerdata.Understat` sits on `BaseRequestsReader` (plain HTTP, no
Selenium) in the installed version, unlike FBref in this same
soccerdata release -- see the session notes handed back with this
module for the FBref/Selenium finding.

Understat only publishes the top-5 European leagues (soccerdata's
`Understat.available_leagues()` confirms this -- RFPL is not actually
available in this soccerdata version despite being mentioned in the
original brief). Requesting any other league is logged and skipped,
never silently dropped.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import soccerdata as sd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.ingest.team_identity import normalize_team_name
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
from src.utils.config import UNDERSTAT_LEAGUES, settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

SOURCE = "understat"


def _raw_cache_path(season: int, league: str) -> Path:
    return settings.cache_dir / "understat" / str(season) / f"{league}.parquet"


def _team_xg_cache_path(season: int, league: str) -> Path:
    return settings.cache_dir / "understat" / str(season) / f"{league}_team_xg.parquet"


@rate_limited()
def _read_player_season_stats(us: sd.Understat) -> pd.DataFrame:
    return us.read_player_season_stats()


@rate_limited()
def _read_team_match_stats(us: sd.Understat) -> pd.DataFrame:
    return us.read_team_match_stats()


def _team_season_xg(team_match_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-match home/away xG into one row per team per season.

    Best-effort context data: there is no team-level stats table in the
    current schema (`teams` only carries identity columns), so this is
    cached to raw only, not upserted into any table.
    """
    if team_match_df.empty:
        return pd.DataFrame(
            columns=["league", "season", "team", "xg_for_sum", "xg_against_sum", "matches"]
        )

    df = team_match_df.reset_index()
    home = df.rename(columns={"home_team": "team", "home_xg": "xg_for", "away_xg": "xg_against"})[
        ["league", "season", "team", "xg_for", "xg_against"]
    ]
    away = df.rename(columns={"away_team": "team", "away_xg": "xg_for", "home_xg": "xg_against"})[
        ["league", "season", "team", "xg_for", "xg_against"]
    ]
    combined = pd.concat([home, away], ignore_index=True)
    return combined.groupby(["league", "season", "team"], as_index=False).agg(
        xg_for_sum=("xg_for", "sum"),
        xg_against_sum=("xg_against", "sum"),
        matches=("xg_for", "count"),
    )


def fetch_understat(season: int, league: str, *, force: bool = False) -> pd.DataFrame:
    """Fetch (or load cached) Understat player-season stats for one league/season.

    Also fetches team match stats and caches a team-season xG aggregate
    alongside it (raw context only, not part of the return value).
    Returns an empty DataFrame and logs a warning if `league` is not one
    of the leagues Understat actually publishes.
    """
    if league not in UNDERSTAT_LEAGUES:
        logger.warning(
            "Understat does not publish '{league}' (supported: {supported}); skipping.",
            league=league,
            supported=UNDERSTAT_LEAGUES,
        )
        return pd.DataFrame()

    cache_path = _raw_cache_path(season, league)
    if cache_path.exists() and not force:
        logger.info("Understat cache hit, skipping network: {path}", path=cache_path)
        return pd.read_parquet(cache_path)

    us = sd.Understat(leagues=league, seasons=season)

    logger.info(
        "Fetching Understat player season stats for {league} {season}", league=league, season=season
    )
    df = _read_player_season_stats(us)
    df = df.reset_index()

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
    logger.info("Wrote Understat raw cache: {path} ({rows} rows)", path=cache_path, rows=len(df))

    try:
        logger.info(
            "Fetching Understat team match stats for {league} {season}",
            league=league,
            season=season,
        )
        team_match_df = _read_team_match_stats(us)
        team_xg_df = _team_season_xg(team_match_df)
        team_xg_path = _team_xg_cache_path(season, league)
        team_xg_path.parent.mkdir(parents=True, exist_ok=True)
        team_xg_df.to_parquet(team_xg_path, index=False)
        logger.info(
            "Wrote Understat team xG raw cache: {path} ({rows} rows)",
            path=team_xg_path,
            rows=len(team_xg_df),
        )
    except Exception as exc:  # noqa: BLE001 - team xG is context data, must not fail the ingest
        logger.error(
            "Failed to fetch Understat team match stats for {league} {season}: {exc}",
            league=league,
            season=season,
            exc=exc,
        )

    return df


def _to_records(df: pd.DataFrame, *, season: int, league: str) -> list[PlayerSeasonRecord]:
    """Normalize a raw Understat DataFrame into validated PlayerSeasonRecord rows."""
    records: list[PlayerSeasonRecord] = []
    dropped = 0
    for row in df.to_dict(orient="records"):
        name = row.get("player")
        if not name or (isinstance(name, float) and pd.isna(name)):
            dropped += 1
            continue

        team = row.get("team")
        position = row.get("position")
        understat_id = row.get("player_id")

        raw_stats = {
            k: v
            for k, v in row.items()
            if k
            not in {
                "league",
                "season",
                "team",
                "player",
                "player_id",
                "team_id",
                "league_id",
                "season_id",
                "position",
                "matches",
                "minutes",
            }
            and not (isinstance(v, float) and pd.isna(v))
        }

        player_id = (
            f"understat:{understat_id}" if understat_id is not None else f"understat:{name}:{team}"
        )

        matches_raw = row.get("matches")
        minutes_raw = row.get("minutes")

        records.append(
            PlayerSeasonRecord(
                player_id=player_id,
                canonical_name=str(name),
                source=SOURCE,
                season=str(season),
                league=league,
                team=normalize_team_name(str(team))
                if team is not None and not pd.isna(team)
                else None,
                position=str(position) if position is not None and not pd.isna(position) else None,
                # position_group is derived from per-match data by understat_positions.py;
                # Understat's season `position` letters are alphabetical, so they can't set it.
                minutes=int(minutes_raw)
                if minutes_raw is not None and not pd.isna(minutes_raw)
                else None,
                matches=int(matches_raw)
                if matches_raw is not None and not pd.isna(matches_raw)
                else None,
                raw_stats=raw_stats,
            )
        )

    if dropped:
        logger.warning(
            "Dropped {n} Understat rows with no player name for {league} {season}",
            n=dropped,
            league=league,
            season=season,
        )
    return records


def _upsert_records(session: Session, records: list[PlayerSeasonRecord]) -> int:
    """Upsert players + player_season_stats rows. Idempotent on rerun."""
    rows_written = 0
    for rec in records:
        understat_id = rec.player_id.removeprefix("understat:") if rec.source == SOURCE else None
        session.execute(
            sqlite_insert(Player)
            .values(
                player_id=rec.player_id,
                canonical_name=rec.canonical_name,
                understat_id=understat_id,
                position_group=rec.position_group,
            )
            .on_conflict_do_update(
                index_elements=["player_id"],
                set_={
                    "canonical_name": rec.canonical_name,
                    "understat_id": understat_id,
                },
            )
        )

        if rec.team:
            team_id = build_team_id(league=rec.league, season=rec.season, name=rec.team)
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
                    "minutes": rec.minutes,
                    "matches": rec.matches,
                    "raw_stats": rec.raw_stats,
                },
            )
        )
        rows_written += 1
    session.commit()
    return rows_written


def ingest_understat(season: int, league: str, *, force: bool = False) -> int:
    """Fetch, normalize, and upsert Understat player-season stats. Returns row count."""
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    status = "success"
    error: str | None = None
    rows = 0

    if league not in UNDERSTAT_LEAGUES:
        status = "skipped"
        error = f"League '{league}' is not published by Understat"
        with session_factory() as session:
            session.add(
                IngestLog(
                    source=SOURCE,
                    season=str(season),
                    league=league,
                    rows=0,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    error=error,
                )
            )
            session.commit()
        logger.warning(
            "Understat ingest skipped for {league} {season}: {error}",
            league=league,
            season=season,
            error=error,
        )
        return 0

    try:
        raw_df = fetch_understat(season, league, force=force)
        records = _to_records(raw_df, season=season, league=league)
        with session_factory() as session:
            rows = _upsert_records(session, records)
    except Exception as exc:  # noqa: BLE001 - always log to ingest_log before re-raising
        status = "failed"
        error = str(exc)
        logger.error(
            "Understat ingest failed for {league} {season}: {exc}",
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
        "Understat ingest complete: {rows} rows for {league} {season}",
        rows=rows,
        league=league,
        season=season,
    )
    return rows


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Understat player-season stats.")
    parser.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2024.")
    parser.add_argument(
        "--league", type=str, required=True, help="League code, e.g. 'ENG-Premier League'."
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch even if a raw cache exists.")
    args = parser.parse_args()

    rows = ingest_understat(args.season, args.league, force=args.force)
    print(f"Ingested {rows} rows for {args.league} {args.season}")


if __name__ == "__main__":
    _main()
