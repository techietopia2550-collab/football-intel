"""Centralized application settings, loaded from environment / .env.

Never hardcode paths or secrets elsewhere in the codebase — import
`settings` from this module instead.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application configuration sourced from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_football_key: str = Field(default="", alias="API_FOOTBALL_KEY")
    db_path: Path = Field(default=PROJECT_ROOT / "data" / "football.db", alias="DB_PATH")
    cache_dir: Path = Field(default=PROJECT_ROOT / "data" / "raw", alias="CACHE_DIR")
    processed_dir: Path = Field(default=PROJECT_ROOT / "data" / "processed", alias="PROCESSED_DIR")
    player_images_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "player_images", alias="PLAYER_IMAGES_DIR"
    )
    # StatsBomb minutes are event-clock (about 97 for a full match). When True they are scaled so
    # a full match is 90.0; the factor is stored per match in matches.minutes_scale_factor.
    minutes_normalize: bool = Field(default=True, alias="MINUTES_NORMALIZE")
    scrape_delay_seconds: float = Field(default=3.0, alias="SCRAPE_DELAY_SECONDS")

    def ensure_dirs(self) -> None:
        """Create the cache/db parent directories if they don't exist yet."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()

# Leagues supported by FBref ingest, in the format soccerdata expects
# (`soccerdata.FBref.available_leagues()`). Edit this list to add/drop
# league coverage.
FBREF_LEAGUES: list[str] = [
    "ENG-Premier League",
    "ESP-La Liga",
    "ITA-Serie A",
    "GER-Bundesliga",
    "FRA-Ligue 1",
]

# Leagues Understat covers via soccerdata (`soccerdata.Understat.available_leagues()`).
# Understat only publishes the top-5 European leagues; requesting anything
# else must be logged and skipped, not silently dropped.
UNDERSTAT_LEAGUES: list[str] = [
    "ENG-Premier League",
    "ESP-La Liga",
    "ITA-Serie A",
    "GER-Bundesliga",
    "FRA-Ligue 1",
]

# Default season range for first-run ingest. Narrower than the full
# 2020-2024 window supported so a first run stays fast; pass --season
# explicitly to fetch older seasons.
DEFAULT_SEASONS: list[int] = [2022, 2023, 2024]
