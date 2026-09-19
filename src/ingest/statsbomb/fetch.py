"""Fetch and cache StatsBomb Open Data JSON files.

Plain HTTP GETs of static files from `raw.githubusercontent.com`. Every file is
cached under `<cache_dir>/statsbomb/` (on disk D with the rest of the project).

Downloads are never trusted blindly: a response must parse as JSON of the
expected type before it is written, files are written atomically (via a `.part`
file), and a cache file that is not valid JSON raises `CorruptCacheError`
instead of being used or silently replaced.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

from src.utils.config import settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
REQUEST_DELAY_SECONDS = 2.0


class CorruptCacheError(RuntimeError):
    """A cached StatsBomb file is not valid JSON of the expected shape."""


def statsbomb_cache_root() -> Path:
    return settings.cache_dir / "statsbomb"


def events_cache_path(match_id: int) -> Path:
    return statsbomb_cache_root() / "events" / f"{match_id}.json"


def lineups_cache_path(match_id: int) -> Path:
    return statsbomb_cache_root() / "lineups" / f"{match_id}.json"


def _check_json(raw: bytes, expected: type, source: str) -> Any:
    """Parse `raw` and check it is a non-empty JSON `expected`; raise ValueError if not."""
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(data, expected):
        raise ValueError(f"{source} is JSON {type(data).__name__}, expected {expected.__name__}")
    if not data:
        raise ValueError(f"{source} is empty")
    return data


@rate_limited(REQUEST_DELAY_SECONDS)
def _download(url: str, expected: type) -> bytes:
    """Rate-limited GET, retried with backoff. Invalid or partial bodies are retried too."""
    response = requests.get(url, timeout=60, headers={"User-Agent": "football-intel/0.1"})
    response.raise_for_status()
    _check_json(response.content, expected, url)
    return response.content


def load_cached_json(path: Path, expected: type) -> Any:
    """Read a cache file, failing loudly if it is not valid JSON."""
    try:
        return _check_json(path.read_bytes(), expected, str(path))
    except ValueError as exc:
        raise CorruptCacheError(
            f"Corrupt StatsBomb cache file: {exc}. Delete it or re-run with --force."
        ) from exc


def fetch_json(relative_url: str, cache_path: Path, expected: type, *, force: bool = False) -> Any:
    """Return parsed JSON for `<BASE_URL>/<relative_url>`, using the cache unless `force`."""
    if cache_path.exists() and not force:
        logger.info("StatsBomb cache hit: {path}", path=cache_path)
        return load_cached_json(cache_path, expected)

    url = f"{BASE_URL}/{relative_url}"
    logger.info("Downloading {url}", url=url)
    raw = _download(url, expected)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    part = cache_path.with_suffix(cache_path.suffix + ".part")
    part.write_bytes(raw)
    os.replace(part, cache_path)  # atomic: the cache path never holds a partial file
    logger.info("Wrote {path} ({n} bytes)", path=cache_path, n=len(raw))
    return json.loads(raw)


def fetch_match_events(match_id: int, *, force: bool = False) -> list[dict[str, Any]]:
    """Fetch (or load cached) the event list for one match."""
    return fetch_json(f"events/{match_id}.json", events_cache_path(match_id), list, force=force)


def fetch_match_lineups(match_id: int, *, force: bool = False) -> list[dict[str, Any]]:
    """Fetch (or load cached) the two team lineups for one match."""
    return fetch_json(f"lineups/{match_id}.json", lineups_cache_path(match_id), list, force=force)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and cache one StatsBomb match.")
    parser.add_argument("--match-id", type=int, required=True, help="StatsBomb match id.")
    parser.add_argument("--force", action="store_true", help="Re-download even if cached.")
    args = parser.parse_args()

    events = fetch_match_events(args.match_id, force=args.force)
    lineups = fetch_match_lineups(args.match_id, force=args.force)
    print(f"match {args.match_id}: {len(events)} events -> {events_cache_path(args.match_id)}")
    print(f"match {args.match_id}: {len(lineups)} lineups -> {lineups_cache_path(args.match_id)}")


if __name__ == "__main__":
    _main()
