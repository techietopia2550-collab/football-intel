"""Shared loguru logger configuration for the project.

Import `logger` from this module everywhere instead of the standard
`logging` module or bare `print` calls.
"""

from __future__ import annotations

import sys

from loguru import logger

from src.utils.config import PROJECT_ROOT

_LOG_DIR = PROJECT_ROOT / "data" / "logs"

logger.remove()
logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
logger.add(
    _LOG_DIR / "football-intel_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="14 days",
    level="DEBUG",
)

__all__ = ["logger"]
