"""Smoke test: every project module must import cleanly.

This is intentionally shallow — it exists to catch broken imports,
missing dependencies, and syntax errors across the whole skeleton,
not to test behavior.
"""

from __future__ import annotations

import importlib


def test_all_modules_import() -> None:
    modules = [
        "src.utils.config",
        "src.utils.logging",
        "src.utils.ratelimit",
        "src.models.player",
    ]
    for name in modules:
        importlib.import_module(name)


def test_settings_load() -> None:
    from src.utils.config import settings

    assert settings.scrape_delay_seconds >= 0
    assert str(settings.db_path).endswith("football.db")


def test_rate_limited_decorator_runs() -> None:
    from src.utils.ratelimit import rate_limited

    calls = []

    @rate_limited(delay_seconds=0)
    def _ping() -> str:
        calls.append(1)
        return "pong"

    assert _ping() == "pong"
    assert calls == [1]
