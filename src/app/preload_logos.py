"""Fill the team-logo cache ahead of time: `python -m src.app.preload_logos`.

Iterates over every team in `team_style`, fetches each missing logo once from Wikipedia (3 s between
teams), caches it to `data/team_logos/<slug>.png` and records attribution in that folder's
`credits.json`. Resumable: teams already cached are skipped, and so are teams already recorded as
"missing" (pass `--retry-missing` to ask Wikipedia about those again). The app never fetches by
itself; it only reads this cache.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import time

from sqlalchemy import text

from src.app import player_images
from src.models.schema import get_engine
from src.utils.logging import logger

DELAY_SECONDS = 3.0
BACKOFF_SECONDS = 30.0  # after a network error / 429, wait this long and try the team once more


def team_names() -> list[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT DISTINCT team_name FROM team_style ORDER BY team_name")).fetchall()
    return [str(r[0]) for r in rows]


def preload(
    names: list[str], retry_missing: bool = False, delay: float = DELAY_SECONDS,
    backoff: float = BACKOFF_SECONDS,
) -> dict[str, list[str]]:
    """Fetch what is missing. Returns {"cached": [...], "fetched": [...], "placeholder": [...]}."""
    credits = player_images.load_logo_credits()
    result: dict[str, list[str]] = {"cached": [], "fetched": [], "placeholder": []}
    first = True
    for name in names:
        if player_images.has_team_logo(name):
            result["cached"].append(name)
            continue
        if credits.get(name, {}).get("status") == "missing" and not retry_missing:
            result["placeholder"].append(name)
            continue
        if not first:
            time.sleep(delay)
        first = False
        got = player_images.fetch_team_logo(name)
        if got is None and player_images.load_logo_credits().get(name, {}).get("status") != "missing":
            time.sleep(backoff)  # no verdict recorded: the request failed, not the lookup
            got = player_images.fetch_team_logo(name)
        (result["fetched"] if got else result["placeholder"]).append(name)
        logger.info("{n}: {r}", n=name, r="logo cached" if got else "placeholder")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry-missing", action="store_true", help="re-ask Wikipedia for teams marked missing")
    args = parser.parse_args()
    names = team_names()
    result = preload(names, retry_missing=args.retry_missing)
    have = len(result["cached"]) + len(result["fetched"])
    print(f"{have} of {len(names)} teams have a logo ({len(result['fetched'])} fetched now).")
    print(f"Placeholder ({len(result['placeholder'])}): {', '.join(result['placeholder']) or '-'}")


if __name__ == "__main__":
    main()
