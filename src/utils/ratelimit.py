"""Rate limiting and retry decorator for outbound requests to external sources.

Every scraper/API call in /src/ingest should be wrapped with `@rate_limited`
so we stay polite to free/public data sources and recover from transient
failures instead of hammering them.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from src.utils.config import settings
from src.utils.logging import logger

P = ParamSpec("P")
T = TypeVar("T")


def rate_limited(
    delay_seconds: float | None = None,
    *,
    max_retries: int = 3,
    backoff_factor: float = 2.0,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that enforces a delay before each call and retries with backoff.

    Args:
        delay_seconds: Seconds to wait before invoking the wrapped function.
            Defaults to `settings.scrape_delay_seconds` when not given.
        max_retries: Number of attempts before the final exception is raised.
        backoff_factor: Multiplier applied to the delay after each failed
            attempt (exponential backoff).
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            wait = delay_seconds if delay_seconds is not None else settings.scrape_delay_seconds
            last_exc: Exception | None = None
            for attempt in range(1, max_retries + 1):
                time.sleep(wait)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - deliberately broad, we retry any failure
                    last_exc = exc
                    logger.warning(
                        "{func} attempt {attempt}/{max_retries} failed: {exc}",
                        func=func.__name__,
                        attempt=attempt,
                        max_retries=max_retries,
                        exc=exc,
                    )
                    wait *= backoff_factor
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
