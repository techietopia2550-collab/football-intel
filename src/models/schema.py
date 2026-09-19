"""SQLAlchemy schema for the local SQLite database (`settings.db_path`).

This is the only place table definitions live. `/src/ingest` and
`/src/transform` import `get_engine`/`get_session` from here rather than
opening their own SQLite connections.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Engine, ForeignKey, Integer, String, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from src.utils.config import settings
from src.utils.logging import logger


class Base(DeclarativeBase):
    """Declarative base for all Football Intel tables."""


class Player(Base):
    """Canonical player identity, cross-referenced across sources."""

    __tablename__ = "players"

    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    fbref_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    understat_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    transfermarkt_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String, nullable=True)
    position_group: Mapped[str | None] = mapped_column(String, nullable=True)


class PlayerSeasonStats(Base):
    """One row per player, per season, per league, per source.

    `raw_stats` holds the source-specific metrics as JSON so each source
    can carry its own columns without a schema migration per stat category.
    """

    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "league", "source", name="uq_player_season_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_id: Mapped[str] = mapped_column(String, ForeignKey("players.player_id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[str] = mapped_column(String, nullable=False)
    league: Mapped[str] = mapped_column(String, nullable=False)
    team: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    position_group: Mapped[str | None] = mapped_column(String, nullable=True)
    minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matches: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class Team(Base):
    """Team identity per league/season/source."""

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("name", "league", "season", "source", name="uq_team_league_season_source"),
    )

    team_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    league: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)


class MatchResult(Base):
    """One row per played match per source. Match-level, not player-level."""

    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint(
            "source", "season", "league", "match_date", "home_team", "away_team", name="uq_match_result"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[str] = mapped_column(String, nullable=False, index=True)
    league: Mapped[str] = mapped_column(String, nullable=False, index=True)
    match_date: Mapped[date] = mapped_column(Date, nullable=False)
    home_team: Mapped[str] = mapped_column(String, nullable=False)
    away_team: Mapped[str] = mapped_column(String, nullable=False)
    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_cards_yellow: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_cards_yellow: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_cards_red: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_cards_red: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IngestLog(Base):
    """Audit trail of every ingest run, one row per source/season/league attempt."""

    __tablename__ = "ingest_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    season: Mapped[str] = mapped_column(String, nullable=False)
    league: Mapped[str] = mapped_column(String, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


def build_team_id(*, source: str, league: str, season: str, name: str) -> str:
    """Deterministic team_id so re-ingesting the same team upserts, not duplicates."""
    return f"{source}:{league}:{season}:{name}"


def get_engine() -> Engine:
    """Create the SQLAlchemy engine for `settings.db_path`, ensuring its parent dir exists."""
    settings.ensure_dirs()
    return create_engine(f"sqlite:///{settings.db_path}")


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Build a session factory bound to `engine` (or a fresh default engine)."""
    return sessionmaker(bind=engine or get_engine())


def init_db(engine: Engine | None = None) -> Engine:
    """Create all tables if they don't already exist. Safe to call repeatedly."""
    engine = engine or get_engine()
    Base.metadata.create_all(bind=engine, checkfirst=True)
    logger.info("Database initialized at {path}", path=settings.db_path)
    return engine


def _main() -> None:
    parser = argparse.ArgumentParser(description="Manage the Football Intel database schema.")
    parser.add_argument("--init", action="store_true", help="Create all tables if they don't exist.")
    args = parser.parse_args()

    if args.init:
        init_db()
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
