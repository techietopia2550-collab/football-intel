"""Ingest StatsBomb matches: fetch -> parse -> check -> write to the database.

`ingest_match` handles one match; `ingest_season` loops over a league-season (optionally the
first N matches, for a smoke test). The season loop is resumable, because raw files are cached
and a re-run only re-downloads what is missing, and loud: a match that fails is logged with its
id and error, collected, written to `data/processed/statsbomb_failures.csv` and left out of the
database. It is never skipped silently. Invariant violations are reported the same way, in
`data/processed/statsbomb_violations.csv`; the match is still stored so nothing is hidden.

Each run is logged to `ingest_log` (source `statsbomb`) and every write is an idempotent upsert.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.ingest.statsbomb.checks import check_match
from src.ingest.statsbomb.discovery import list_available
from src.ingest.statsbomb.fetch import fetch_match_events, fetch_match_lineups
from src.ingest.statsbomb.identity import resolve_players
from src.ingest.statsbomb.parse import (
    SOURCE,
    check_against_substitutions,
    find_corrections,
    match_minutes_scale,
    parse_match,
)
from src.ingest.team_identity import EPL_2015_16
from src.models.schema import IngestLog, Match, PlayerMatchStats, get_session_factory, init_db
from src.models.statsbomb import PlayerMatchRecord, StatsBombMatch
from src.utils.config import settings
from src.utils.logging import logger

_MATCH_KEYS = {"source", "match_id"}
_STATS_KEYS = {"source", "match_id", "player_id"}

# (canonical league, season) -> the clubs expected in it, for the team-name invariant.
_EXPECTED_TEAMS: dict[tuple[str, str], frozenset[str]] = {
    ("ENG-Premier League", "2015"): EPL_2015_16,
}


@dataclass
class ProcessedMatch:
    """A match parsed and checked, ready to store."""

    match: StatsBombMatch  # with minutes_scale_factor set
    records: list[PlayerMatchRecord]
    violations: list[str]
    corrections: list[str]  # lineup data flaws we corrected (e.g. minutes after a red card)


@dataclass
class SeasonResult:
    """Outcome of a season (or partial season) run."""

    attempted: int = 0
    stored: list[int] = field(default_factory=list)
    failed: dict[int, str] = field(default_factory=dict)  # match_id -> error
    violations: dict[int, list[str]] = field(default_factory=dict)  # match_id -> messages
    corrections: dict[int, list[str]] = field(default_factory=dict)  # match_id -> messages
    player_rows: int = 0
    seconds: float = 0.0


def process_match(
    events: list[dict[str, Any]],
    lineups: list[dict[str, Any]],
    match: StatsBombMatch,
    *,
    normalize: bool,
) -> ProcessedMatch:
    """Parse one match and run every invariant. No network, no database.

    Raises ValueError if the lineups disagree with the substitution events, and LineupError
    if a player with events has no lineup minutes; the caller treats both as a failed match.
    """
    if problems := check_against_substitutions(events, lineups):
        raise ValueError(f"Lineup minutes disagree with substitution events: {problems}")

    corrections = find_corrections(events, lineups)
    scale = match_minutes_scale(events, normalize=normalize)
    records = parse_match(events, lineups, match, minutes_scale=scale)
    violations = check_match(
        events,
        lineups,
        match,
        records,
        known_teams=_EXPECTED_TEAMS.get((match.league, match.season)),
    )
    return ProcessedMatch(
        match.model_copy(update={"minutes_scale_factor": scale}), records, violations, corrections
    )


def upsert_match(session: Session, match: StatsBombMatch) -> None:
    """Upsert one `matches` row (including its minutes scale factor)."""
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
        "minutes_scale_factor": match.minutes_scale_factor,
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


def store_match(session: Session, processed: ProcessedMatch) -> int:
    """Write a processed match and its player rows. The caller commits."""
    upsert_match(session, processed.match)
    return upsert_player_match_stats(session, processed.records)


def _log_run(
    session_factory: Any, *, season: int, league: str, rows: int, status: str, started_at: datetime,
    error: str | None,
) -> None:  # fmt: skip
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


def ingest_match(league: str, match_id: int, season: int = 2015, *, force: bool = False) -> int:
    """Fetch, parse and store one match. Returns the number of `player_match_stats` rows."""
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    status, error, rows, league_name = "success", None, 0, league

    try:
        matches = {m.match_id: m for m in list_available(league, season)}
        if match_id not in matches:
            raise LookupError(
                f"Match {match_id} is not in StatsBomb {league} {season}/{season + 1}"
            )
        league_name = matches[match_id].league
        events = fetch_match_events(match_id, force=force)
        lineups = fetch_match_lineups(match_id, force=force)
        processed = process_match(
            events, lineups, matches[match_id], normalize=settings.minutes_normalize
        )
        for c in processed.corrections:
            logger.warning("Match {m} lineup correction: {c}", m=match_id, c=c)
        for v in processed.violations:
            logger.warning("Match {m} invariant violation: {v}", m=match_id, v=v)
        with session_factory() as session:
            rows = store_match(session, processed)
            session.commit()
    except Exception as exc:  # noqa: BLE001 - always log to ingest_log before re-raising
        status, error = "failed", str(exc)
        logger.error("StatsBomb ingest failed for match {m}: {exc}", m=match_id, exc=exc)
        raise
    finally:
        _log_run(
            session_factory, season=season, league=league_name, rows=rows, status=status,
            started_at=started_at, error=error,
        )  # fmt: skip

    logger.info("StatsBomb match {m} ingested: {rows} player rows", m=match_id, rows=rows)
    return rows


def _write_reports(result: SeasonResult) -> tuple[Path, Path, Path]:
    """Write failures, violations and corrections to `data/processed/` so they cannot be missed."""
    out = settings.processed_dir
    out.mkdir(parents=True, exist_ok=True)
    failures = out / "statsbomb_failures.csv"
    violations = out / "statsbomb_violations.csv"
    corrections = out / "statsbomb_corrections.csv"
    pd.DataFrame(
        [{"match_id": m, "error": e} for m, e in sorted(result.failed.items())],
        columns=["match_id", "error"],
    ).to_csv(failures, index=False)
    pd.DataFrame(
        [
            {"match_id": m, "violation": v}
            for m, vs in sorted(result.violations.items())
            for v in vs
        ],
        columns=["match_id", "violation"],
    ).to_csv(violations, index=False)
    pd.DataFrame(
        [
            {"match_id": m, "correction": c}
            for m, cs in sorted(result.corrections.items())
            for c in cs
        ],
        columns=["match_id", "correction"],
    ).to_csv(corrections, index=False)
    return failures, violations, corrections


def ingest_season(
    league: str, season: int = 2015, *, limit: int | None = None, force: bool = False
) -> SeasonResult:
    """Ingest a league-season, or its first `limit` matches by date. Resumable and loud.

    Never raises for a single bad match: the failure is logged with its match id, recorded in
    the result and in `statsbomb_failures.csv`, and the loop continues. The run is logged to
    `ingest_log` as `success` (no failures) or `partial`.
    """
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    matches = list_available(league, season)
    league_name = matches[0].league if matches else league
    if limit is not None:
        matches = matches[:limit]

    result = SeasonResult(attempted=len(matches))
    t0 = time.monotonic()
    for i, match in enumerate(matches, start=1):
        try:
            events = fetch_match_events(match.match_id, force=force)
            lineups = fetch_match_lineups(match.match_id, force=force)
            processed = process_match(events, lineups, match, normalize=settings.minutes_normalize)
            with session_factory() as session:
                result.player_rows += store_match(session, processed)
                session.commit()
        except Exception as exc:  # noqa: BLE001 - every failure is recorded, never swallowed
            result.failed[match.match_id] = f"{type(exc).__name__}: {exc}"
            logger.error(
                "FAILED match {m} ({home} v {away}): {exc}",
                m=match.match_id, home=match.home_team, away=match.away_team, exc=exc,
            )  # fmt: skip
            continue
        result.stored.append(match.match_id)
        if processed.corrections:
            result.corrections[match.match_id] = processed.corrections
            for c in processed.corrections:
                logger.warning("Match {m} lineup correction: {c}", m=match.match_id, c=c)
        if processed.violations:
            result.violations[match.match_id] = processed.violations
            for v in processed.violations:
                logger.warning("Match {m} invariant violation: {v}", m=match.match_id, v=v)
        if i % 10 == 0 or i == len(matches):
            logger.info(
                "Progress {i}/{n} matches ({secs:.0f}s, {f} failed)",
                i=i, n=len(matches), secs=time.monotonic() - t0, f=len(result.failed),
            )  # fmt: skip

    result.seconds = time.monotonic() - t0
    failures_path, violations_path, corrections_path = _write_reports(result)
    status = "partial" if result.failed else "success"
    error = None
    if result.failed:
        error = f"{len(result.failed)} match(es) failed: {sorted(result.failed)[:20]}"
    _log_run(
        session_factory, season=season, league=league_name, rows=result.player_rows, status=status,
        started_at=started_at, error=error,
    )  # fmt: skip
    logger.info(
        "Season run done: {ok}/{n} stored, {f} failed, {v} matches with violations, "
        "{c} with lineup corrections. Reports: {fp}, {vp}, {cp}",
        ok=len(result.stored), n=result.attempted, f=len(result.failed),
        v=len(result.violations), c=len(result.corrections),
        fp=failures_path, vp=violations_path, cp=corrections_path,
    )  # fmt: skip
    return result


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest StatsBomb match(es).")
    parser.add_argument("--league", required=True, help="EPL, LALIGA, SERIEA or LIGUE1.")
    parser.add_argument(
        "--season", type=int, default=2015, help="Season start year (default 2015)."
    )
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--match-id", type=int, help="Ingest this one match.")
    what.add_argument("--limit", type=int, help="Ingest the first N matches by date (smoke test).")
    what.add_argument("--all", action="store_true", help="Ingest the whole league-season.")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached.")
    args = parser.parse_args()

    if args.match_id is not None:
        rows = ingest_match(args.league, args.match_id, args.season, force=args.force)
        print(f"Ingested {rows} player rows for match {args.match_id}")
        return

    result = ingest_season(
        args.league, args.season, limit=args.limit if not args.all else None, force=args.force
    )
    print(
        f"stored {len(result.stored)}/{result.attempted} matches, "
        f"{result.player_rows} player rows, {len(result.failed)} failed, "
        f"{len(result.violations)} with violations, {len(result.corrections)} with corrections, "
        f"{result.seconds:.0f}s"
    )
    if result.failed:
        raise SystemExit(f"FAILED matches: {result.failed}")


if __name__ == "__main__":
    _main()
