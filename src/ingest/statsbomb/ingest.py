"""Ingest ONE StatsBomb match end to end: fetch -> parse -> write to the database.

Deliberately single-match: a full-season loop is a later step. Every run is logged
to `ingest_log` (source `statsbomb`) and is idempotent (upserts).
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.ingest.statsbomb.discovery import list_available
from src.ingest.statsbomb.fetch import fetch_match_events, fetch_match_lineups
from src.ingest.statsbomb.identity import resolve_players
from src.ingest.statsbomb.parse import SOURCE, check_against_substitutions, parse_match
from src.models.schema import IngestLog, Match, PlayerMatchStats, get_session_factory, init_db
from src.models.statsbomb import PlayerMatchRecord, StatsBombMatch
from src.utils.logging import logger

_MATCH_KEYS = {"source", "match_id"}
_STATS_KEYS = {"source", "match_id", "player_id"}


def upsert_match(session: Session, match: StatsBombMatch) -> None:
    """Upsert one `matches` row."""
    values = {
        "source": SOURCE,
        "match_id": match.match_id,
        "season": match.season,
        "league": match.league,
        "match_date": match.match_date,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "statsbomb_competition_id": match.statsbomb_competition_id,
        "statsbomb_season_id": match.statsbomb_season_id,
    }
    session.execute(
        sqlite_insert(Match)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["source", "match_id"],
            set_={k: v for k, v in values.items() if k not in _MATCH_KEYS},
        )
    )


def upsert_player_match_stats(session: Session, records: list[PlayerMatchRecord]) -> int:
    """Resolve players, then upsert `player_match_stats` rows. Returns the row count."""
    ids = resolve_players(session, {r.statsbomb_player_id: r.player_name for r in records})
    for rec in records:
        values = rec.model_dump(exclude={"statsbomb_player_id"})
        values["player_id"] = ids[rec.statsbomb_player_id]
        session.execute(
            sqlite_insert(PlayerMatchStats)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["source", "match_id", "player_id"],
                set_={k: v for k, v in values.items() if k not in _STATS_KEYS},
            )
        )
    return len(records)


def ingest_match(league: str, match_id: int, season: int = 2015, *, force: bool = False) -> int:
    """Fetch, parse and store one match. Returns the number of `player_match_stats` rows."""
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    status, error, rows = "success", None, 0
    league_name = league

    try:
        matches = {m.match_id: m for m in list_available(league, season)}
        if match_id not in matches:
            raise LookupError(
                f"Match {match_id} is not in StatsBomb {league} {season}/{season + 1}"
            )
        match = matches[match_id]
        league_name = match.league

        events = fetch_match_events(match_id, force=force)
        lineups = fetch_match_lineups(match_id, force=force)
        if problems := check_against_substitutions(events, lineups):
            raise ValueError(f"Lineup minutes disagree with substitution events: {problems}")
        records = parse_match(events, lineups, match)

        with session_factory() as session:
            upsert_match(session, match)
            rows = upsert_player_match_stats(session, records)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - always log to ingest_log before re-raising
        status, error = "failed", str(exc)
        logger.error("StatsBomb ingest failed for match {m}: {exc}", m=match_id, exc=exc)
        raise
    finally:
        with session_factory() as session:
            session.add(
                IngestLog(
                    source=SOURCE,
                    season=str(season),
                    league=league_name,
                    rows=rows,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    error=error,
                )
            )
            session.commit()

    logger.info("StatsBomb match {m} ingested: {rows} player rows", m=match_id, rows=rows)
    return rows


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest ONE StatsBomb match.")
    parser.add_argument("--league", required=True, help="EPL, LALIGA, SERIEA or LIGUE1.")
    parser.add_argument("--match-id", type=int, required=True, help="StatsBomb match id.")
    parser.add_argument(
        "--season", type=int, default=2015, help="Season start year (default 2015)."
    )
    parser.add_argument("--force", action="store_true", help="Re-download even if cached.")
    args = parser.parse_args()

    rows = ingest_match(args.league, args.match_id, args.season, force=args.force)
    print(f"Ingested {rows} player rows for match {args.match_id}")


if __name__ == "__main__":
    _main()
