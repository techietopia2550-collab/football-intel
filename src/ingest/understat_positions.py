"""Understat position ingest: per-match roster positions -> `position_group`.

Understat's season-level `position` string is an alphabetical list of letters
("D F M S"), not priority-ordered, so it cannot say where a player actually
plays. The per-match roster data does: every appearance carries a position
code (GK, DC, DMC, AMR, FW, ... or "Sub" for a bench appearance) and minutes.

Method: for each player, sum *starter* minutes per position group across the
season and take the group with the most minutes. No priority heuristic and no
guessing: a player with no starter minutes ("Sub" appearances carry no role) or
an exact tie gets a NULL group and the reason is written to
`data/processed/position_nulls.csv`.

Plain HTTP via `soccerdata.Understat` (no Selenium). One match page per request,
each cached to `/data/raw/understat/<season>/<league>_match_players/<game_id>.parquet`
so an interrupted run resumes without re-fetching.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import soccerdata as sd
from sqlalchemy import update
from sqlalchemy.orm import Session

from src.models.player import PositionAssignment
from src.models.schema import IngestLog, Player, PlayerSeasonStats, get_session_factory, init_db
from src.utils.config import UNDERSTAT_LEAGUES, settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

SOURCE = "understat_positions"
UNDERSTAT_SOURCE = "understat"
SUB_CODE = "Sub"

# Understat per-match roster position code -> position group.
# Wide attacking mids (AML/AMR) are wingers and count as FWD; central attacking
# mids (AMC) and all other midfield codes count as MID. Full-backs and
# wing-backs coded DL/DR count as DEF.
POSITION_CODE_TO_GROUP: dict[str, str] = {
    "GK": "GK",
    "DC": "DEF",
    "DL": "DEF",
    "DR": "DEF",
    "DMC": "MID",
    "DML": "MID",
    "DMR": "MID",
    "MC": "MID",
    "ML": "MID",
    "MR": "MID",
    "AMC": "MID",
    "AML": "FWD",
    "AMR": "FWD",
    "FW": "FWD",
    "FWL": "FWD",
    "FWR": "FWD",
}

NULLS_COLUMNS = ["player_id", "player_name", "team", "season", "league", "source", "reason"]


def _match_cache_dir(season: int, league: str) -> Path:
    return settings.cache_dir / "understat" / str(season) / f"{league}_match_players"


def nulls_csv_path() -> Path:
    return settings.processed_dir / "position_nulls.csv"


@rate_limited()
def _read_schedule(us: sd.Understat) -> pd.DataFrame:
    return us.read_schedule(include_matches_without_data=False)


@rate_limited()
def _read_match_players(us: sd.Understat, game_id: int) -> pd.DataFrame:
    return us.read_player_match_stats(match_id=game_id)


def fetch_match_players(season: int, league: str, *, force: bool = False) -> pd.DataFrame:
    """Fetch (or load cached) per-match roster rows for every match in a league/season.

    Raises RuntimeError if any match could not be fetched, because positions
    derived from a partial season would be wrong. Re-running resumes from cache.
    """
    if league not in UNDERSTAT_LEAGUES:
        raise ValueError(f"Understat does not publish '{league}' (supported: {UNDERSTAT_LEAGUES})")

    cache_dir = _match_cache_dir(season, league)
    cache_dir.mkdir(parents=True, exist_ok=True)
    us = sd.Understat(leagues=league, seasons=season)

    schedule = _read_schedule(us)
    game_ids = sorted(int(g) for g in schedule["game_id"])
    logger.info(
        "Understat positions: {n} matches for {league} {season}",
        n=len(game_ids),
        league=league,
        season=season,
    )

    frames: list[pd.DataFrame] = []
    failed: list[int] = []
    for i, game_id in enumerate(game_ids, start=1):
        path = cache_dir / f"{game_id}.parquet"
        if path.exists() and not force:
            frames.append(pd.read_parquet(path))
            continue
        try:
            df = _read_match_players(us, game_id).reset_index()
        except Exception as exc:  # noqa: BLE001 - collect, then fail once with the full list
            logger.error(
                "Failed to fetch Understat match {game_id}: {exc}", game_id=game_id, exc=exc
            )
            failed.append(game_id)
            continue
        df.to_parquet(path, index=False)
        frames.append(df)
        if i % 20 == 0:
            logger.info("Fetched {i}/{n} matches", i=i, n=len(game_ids))

    if failed:
        raise RuntimeError(
            f"{len(failed)} match(es) could not be fetched: {failed}. Re-run to resume from cache."
        )
    return pd.concat(frames, ignore_index=True)


def derive_position_groups(match_players: pd.DataFrame) -> list[PositionAssignment]:
    """Assign each player the position group with the most starter minutes.

    Pure function of the per-match rows (columns: player_id, player, position,
    minutes). Bench appearances ("Sub") carry no role and are ignored; a player
    with no starter minutes, or an exact tie, gets `position_group=None` and a reason.
    """
    starters = match_players[match_players["position"] != SUB_CODE].copy()

    unknown = sorted(set(starters["position"].dropna()) - set(POSITION_CODE_TO_GROUP))
    if unknown:
        logger.warning("Ignoring appearances with unmapped position codes: {codes}", codes=unknown)
    starters["group"] = starters["position"].map(POSITION_CODE_TO_GROUP)
    starters = starters.dropna(subset=["group"])

    by_group = starters.groupby(["player_id", "group"])["minutes"].sum()
    minutes: dict[int, dict[str, int]] = {}
    for (player_id, group), mins in by_group.items():
        minutes.setdefault(int(player_id), {})[str(group)] = int(mins)

    names = match_players.drop_duplicates("player_id").set_index("player_id")["player"]
    assignments: list[PositionAssignment] = []
    for player_id in sorted(int(p) for p in names.index):
        per_group = {g: m for g, m in minutes.get(player_id, {}).items() if m > 0}
        total = sum(per_group.values())
        group: str | None = None
        reason: str | None = None
        if not per_group:
            reason = "no starter minutes (substitute appearances only carry no position)"
        else:
            best = max(per_group.values())
            leaders = sorted(g for g, m in per_group.items() if m == best)
            if len(leaders) > 1:
                reason = f"tie on starter minutes between {'/'.join(leaders)} ({best} min each)"
            else:
                group = leaders[0]
        assignments.append(
            PositionAssignment(
                player_id=player_id,
                player_name=str(names.loc[player_id]),
                position_group=group,
                starter_minutes=total,
                minutes_by_group=per_group,
                null_reason=reason,
            )
        )
    return assignments


def apply_position_groups(
    session: Session, assignments: list[PositionAssignment], *, season: int, league: str
) -> pd.DataFrame:
    """Write derived groups onto Understat rows for one league/season.

    Every Understat row for the league/season is set: to its derived group, or to
    NULL (clearing any older value) with a reason. Returns the NULL rows as a
    DataFrame shaped like `NULLS_COLUMNS`.
    """
    by_id = {f"{UNDERSTAT_SOURCE}:{a.player_id}": a for a in assignments}
    rows = (
        session.execute(
            PlayerSeasonStats.__table__.select().where(
                PlayerSeasonStats.source == UNDERSTAT_SOURCE,
                PlayerSeasonStats.season == str(season),
                PlayerSeasonStats.league == league,
            )
        )
        .mappings()
        .all()
    )

    nulls: list[dict[str, object]] = []
    for row in rows:
        assignment = by_id.get(row["player_id"])
        group = assignment.position_group if assignment else None
        session.execute(
            update(PlayerSeasonStats)
            .where(PlayerSeasonStats.id == row["id"])
            .values(position_group=group)
        )
        session.execute(
            update(Player).where(Player.player_id == row["player_id"]).values(position_group=group)
        )
        if group is None:
            reason = (
                assignment.null_reason
                if assignment
                else "player not present in Understat per-match data"
            )
            name = assignment.player_name if assignment else row["player_id"]
            nulls.append(
                {
                    "player_id": row["player_id"],
                    "player_name": name,
                    "team": row["team"],
                    "season": str(season),
                    "league": league,
                    "source": UNDERSTAT_SOURCE,
                    "reason": reason,
                }
            )
    session.commit()
    return pd.DataFrame(nulls, columns=NULLS_COLUMNS)


def write_nulls_csv(nulls: pd.DataFrame, *, season: int, league: str) -> Path:
    """Replace this league/season's rows in `position_nulls.csv`, keeping other runs."""
    path = nulls_csv_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_csv(path, dtype=str)
        keep = ~(
            (existing["season"] == str(season))
            & (existing["league"] == league)
            & (existing["source"] == UNDERSTAT_SOURCE)
        )
        nulls = pd.concat([existing[keep], nulls.astype(str)], ignore_index=True)
    nulls.to_csv(path, index=False)
    return path


def ingest_understat_positions(season: int, league: str, *, force: bool = False) -> int:
    """Derive and apply position groups. Returns the number of players given a group."""
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    status = "success"
    error: str | None = None
    assigned = 0

    try:
        match_players = fetch_match_players(season, league, force=force)
        assignments = derive_position_groups(match_players)
        with session_factory() as session:
            nulls = apply_position_groups(session, assignments, season=season, league=league)
            assigned = int(
                session.query(PlayerSeasonStats)
                .filter_by(source=UNDERSTAT_SOURCE, season=str(season), league=league)
                .filter(PlayerSeasonStats.position_group.is_not(None))
                .count()
            )
        path = write_nulls_csv(nulls, season=season, league=league)
        logger.info("Wrote {n} NULL-position rows with reasons to {path}", n=len(nulls), path=path)
    except Exception as exc:  # noqa: BLE001 - always log to ingest_log before re-raising
        status = "failed"
        error = str(exc)
        logger.error(
            "Understat positions failed for {league} {season}: {exc}",
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
                    rows=assigned,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    error=error,
                )
            )
            session.commit()

    logger.info(
        "Understat positions complete: {n} players grouped for {league} {season}",
        n=assigned,
        league=league,
        season=season,
    )
    return assigned


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Derive Understat position groups from per-match rosters."
    )
    parser.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2024.")
    parser.add_argument(
        "--league", type=str, required=True, help="League code, e.g. 'ENG-Premier League'."
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch matches even if cached.")
    args = parser.parse_args()

    n = ingest_understat_positions(args.season, args.league, force=args.force)
    print(f"Assigned a position group to {n} players for {args.league} {args.season}")


if __name__ == "__main__":
    _main()
