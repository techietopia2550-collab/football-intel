"""football-data.co.uk CSV ingest: match-level results and basic team stats.

Plain HTTP download of one static CSV per league/season. No soccerdata, no
tls_requests, no browser. This is the no-scraping fallback source.

Match level only (goals, shots, corners, cards) -- it carries no player
data and must not be used to fake player stats. It is the foundation for
team analysis.

Raw CSV bytes are cached to `/data/raw/football_data/<SSYY>/<code>.csv`.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import io
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.ingest.team_identity import normalize_team_name
from src.models.match import MatchRecord
from src.models.schema import (
    IngestLog,
    MatchResult,
    Team,
    build_team_id,
    get_session_factory,
    init_db,
)
from src.utils.config import settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

SOURCE = "football_data_csv"
BASE_URL = "https://www.football-data.co.uk/mmz4281"

# CLI short name -> (football-data code, canonical league name). The canonical
# name matches `UNDERSTAT_LEAGUES` so tables can be joined on `league`.
LEAGUES: dict[str, tuple[str, str]] = {
    "EPL": ("E0", "ENG-Premier League"),
    "LALIGA": ("SP1", "ESP-La Liga"),
    "SERIEA": ("I1", "ITA-Serie A"),
    "BUNDESLIGA": ("D1", "GER-Bundesliga"),
    "LIGUE1": ("F1", "FRA-Ligue 1"),
}

# CSV column -> MatchRecord field, for the optional numeric columns.
_NUMERIC_COLUMNS: dict[str, str] = {
    "FTHG": "home_goals",
    "FTAG": "away_goals",
    "HS": "home_shots",
    "AS": "away_shots",
    "HST": "home_shots_on_target",
    "AST": "away_shots_on_target",
    "HC": "home_corners",
    "AC": "away_corners",
    "HY": "home_cards_yellow",
    "AY": "away_cards_yellow",
    "HR": "home_cards_red",
    "AR": "away_cards_red",
}


def season_code(season: int) -> str:
    """2024 -> '2425' (football-data.co.uk's URL season segment)."""
    return f"{season % 100:02d}{(season + 1) % 100:02d}"


def _resolve_league(league: str) -> tuple[str, str]:
    """Accept 'EPL', 'E0', or the canonical name; return (code, canonical name)."""
    key = league.strip()
    for short, (code, canonical) in LEAGUES.items():
        if key.upper() in {short, code} or key == canonical:
            return code, canonical
    raise ValueError(f"Unsupported league '{league}'. Supported: {sorted(LEAGUES)}")


def _raw_cache_path(season: int, code: str) -> Path:
    return settings.cache_dir / "football_data" / season_code(season) / f"{code}.csv"


@rate_limited()
def _download_csv(url: str) -> bytes:
    """Single rate-limited GET. Raises on HTTP errors so the decorator retries."""
    response = requests.get(url, timeout=30, headers={"User-Agent": "football-intel/0.1"})
    response.raise_for_status()
    return response.content


def fetch_csv(season: int, league: str, *, force: bool = False) -> bytes:
    """Return raw CSV bytes for one league/season, from cache unless `force`."""
    code, _ = _resolve_league(league)
    cache_path = _raw_cache_path(season, code)
    if cache_path.exists() and not force:
        logger.info("football-data cache hit, skipping network: {path}", path=cache_path)
        return cache_path.read_bytes()

    url = f"{BASE_URL}/{season_code(season)}/{code}.csv"
    logger.info("Downloading {url}", url=url)
    content = _download_csv(url)
    # A missing season can come back as an HTML page with a 200; never cache that.
    if b"HomeTeam" not in content[:2000]:
        raise ValueError(f"Response from {url} does not look like a football-data CSV")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)
    logger.info(
        "Wrote football-data raw cache: {path} ({n} bytes)", path=cache_path, n=len(content)
    )
    return content


def parse_csv(raw: bytes) -> pd.DataFrame:
    """Decode raw CSV bytes into a DataFrame (handles BOM and legacy latin-1 files)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return pd.read_csv(io.StringIO(text), skip_blank_lines=True)


def _to_int(value: object) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(float(str(value)))


def _to_records(df: pd.DataFrame, *, season: int, league: str) -> list[MatchRecord]:
    """Normalize a raw football-data DataFrame into validated MatchRecord rows.

    Rows with no teams or an unparseable date are dropped and logged.
    """
    _, canonical = _resolve_league(league)
    records: list[MatchRecord] = []
    dropped = 0
    for row in df.to_dict(orient="records"):
        home, away = row.get("HomeTeam"), row.get("AwayTeam")
        match_date = pd.to_datetime(row.get("Date"), dayfirst=True, errors="coerce", format="mixed")
        if not isinstance(home, str) or not isinstance(away, str) or pd.isna(match_date):
            dropped += 1
            continue

        stats = {field: _to_int(row.get(col)) for col, field in _NUMERIC_COLUMNS.items()}
        records.append(
            MatchRecord(
                source=SOURCE,
                season=str(season),
                league=canonical,
                match_date=match_date.date(),
                home_team=normalize_team_name(home),
                away_team=normalize_team_name(away),
                **stats,
            )
        )

    if dropped:
        logger.warning(
            "Dropped {n} football-data rows (no teams/date) for {league} {season}",
            n=dropped,
            league=canonical,
            season=season,
        )
    return records


def _upsert_records(session: Session, records: list[MatchRecord]) -> int:
    """Upsert match_results rows (and the teams they mention). Idempotent on rerun."""
    for rec in records:
        for team in (rec.home_team, rec.away_team):
            session.execute(
                sqlite_insert(Team)
                .values(
                    team_id=build_team_id(league=rec.league, season=rec.season, name=team),
                    name=team,
                    league=rec.league,
                    season=rec.season,
                    source=rec.source,
                )
                .on_conflict_do_nothing(index_elements=["team_id"])
            )
        values = rec.model_dump()
        session.execute(
            sqlite_insert(MatchResult)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[
                    "source",
                    "season",
                    "league",
                    "match_date",
                    "home_team",
                    "away_team",
                ],
                set_={
                    k: v
                    for k, v in values.items()
                    if k
                    not in {"source", "season", "league", "match_date", "home_team", "away_team"}
                },
            )
        )
    session.commit()
    return len(records)


def ingest_football_data(season: int, league: str, *, force: bool = False) -> int:
    """Fetch, normalize, and upsert football-data match results. Returns row count."""
    init_db()
    session_factory = get_session_factory()
    started_at = datetime.now(UTC)
    status = "success"
    error: str | None = None
    rows = 0
    league_name = league

    try:
        _, league_name = _resolve_league(league)
        raw = fetch_csv(season, league, force=force)
        records = _to_records(parse_csv(raw), season=season, league=league)
        with session_factory() as session:
            rows = _upsert_records(session, records)
    except Exception as exc:  # noqa: BLE001 - always log to ingest_log before re-raising
        status = "failed"
        error = str(exc)
        logger.error(
            "football-data ingest failed for {league} {season}: {exc}",
            league=league_name,
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
                    league=league_name,
                    rows=rows,
                    status=status,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    error=error,
                )
            )
            session.commit()

    logger.info(
        "football-data ingest complete: {rows} rows for {league} {season}",
        rows=rows,
        league=league_name,
        season=season,
    )
    return rows


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest football-data.co.uk match results.")
    parser.add_argument("--season", type=int, required=True, help="Season start year, e.g. 2024.")
    parser.add_argument(
        "--league", type=str, required=True, help=f"One of {sorted(LEAGUES)} (or E0/SP1/I1/D1/F1)."
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if a raw cache exists."
    )
    args = parser.parse_args()

    rows = ingest_football_data(args.season, args.league, force=args.force)
    print(f"Ingested {rows} matches for {args.league} {args.season}")


if __name__ == "__main__":
    _main()
