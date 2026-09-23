"""Pre-flight for a StatsBomb league-season: fetch every lineup, check what the ingest will meet.

Run BEFORE the events are fetched. It downloads (or reads from cache) the lineup file of every
match and reports:
  - every distinct position name, how often it appears, and whether it is in `POSITION_GROUPS` and
    `FINE_POSITION_GROUPS` (the tables the ingest uses);
  - any position the tables do not know, with an inferred mapping when the name is unambiguous
    (`infer_position`); each inference is written to `statsbomb_corrections.csv` with kind
    `inferred_mapping`. An unknown name with no unambiguous reading is AMBIGUOUS: the CLI exits
    non-zero and the ingest must not start;
  - any team name the identity table cannot resolve to one of the league's expected clubs.

An inferred mapping is only reported here. To use it, add it to `POSITION_GROUPS` and
`FINE_POSITION_GROUPS` in `parse.py` (so it is code-reviewed, not applied silently at run time).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from src.ingest.statsbomb.discovery import list_available, resolve_league
from src.ingest.statsbomb.fetch import fetch_match_lineups
from src.ingest.statsbomb.ingest import _replace_report_rows
from src.ingest.statsbomb.parse import FINE_POSITION_GROUPS, POSITION_GROUPS
from src.ingest.team_identity import CLUBS_2015_16, normalize_team_name
from src.utils.config import settings
from src.utils.logging import logger

# Unambiguous readings of a position name that is not in the tables: (regex, coarse, fine).
# Checked in order; the first match wins. Anything that matches none is ambiguous.
_INFERENCE: tuple[tuple[str, str, str], ...] = (
    (r"goal\s*keeper", "GK", "GK"),
    (r"(centre|center)\s+back", "DEF", "CB"),
    (r"wing\s*back|full\s*back|\bback$", "DEF", "FB"),
    (r"defensive\s+midfield", "MID", "DM"),
    (r"attacking\s+midfield", "MID", "AM"),
    (r"midfield", "MID", "CM"),
    (r"\bwing$", "FWD", "W"),
    (r"forward|striker", "FWD", "ST"),
)


def infer_position(name: str) -> tuple[str, str] | None:
    """(coarse, fine) group for an unknown position if its wording is unambiguous, else None."""
    text = re.sub(r"\s+", " ", name.strip().lower())
    for pattern, coarse, fine in _INFERENCE:
        if re.search(pattern, text):
            return coarse, fine
    return None


@dataclass
class PreflightReport:
    league: str
    season: int
    matches: int
    lineups_read: int
    positions: Counter[str] = field(default_factory=Counter)
    known: list[str] = field(default_factory=list)
    inferred: dict[str, tuple[str, str]] = field(default_factory=dict)
    ambiguous: list[str] = field(default_factory=list)
    unknown_teams: list[str] = field(default_factory=list)
    missing_lineups: dict[int, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True if the ingest may start: every position is already in the tables (an inferred
        mapping must first be added to `parse.py`), and every team resolves."""
        return not self.ambiguous and not self.inferred and not self.unknown_teams


def scan_positions(lineups: list[list[dict[str, Any]]]) -> Counter[str]:
    """Count every position spell name across a list of lineup files."""
    seen: Counter[str] = Counter()
    for match in lineups:
        for team in match:
            for player in team["lineup"]:
                for spell in player.get("positions") or []:
                    seen[spell["position"]] += 1
    return seen


def classify_positions(
    positions: Counter[str],
) -> tuple[list[str], dict[str, tuple[str, str]], list[str]]:
    """Split position names into (known, inferred mappings, ambiguous)."""
    known: list[str] = []
    inferred: dict[str, tuple[str, str]] = {}
    ambiguous: list[str] = []
    for name in sorted(positions):
        if name in POSITION_GROUPS and name in FINE_POSITION_GROUPS:
            known.append(name)
        elif (guess := infer_position(name)) is not None:
            inferred[name] = guess
        else:
            ambiguous.append(name)
    return known, inferred, ambiguous


def preflight(league: str, season: int = 2015, *, force: bool = False) -> PreflightReport:
    """Fetch every lineup of a league-season (cached, rate-limited) and check it (module doc)."""
    _, canonical, _, _ = resolve_league(league)
    matches = list_available(league, season)
    lineups: list[list[dict[str, Any]]] = []
    report = PreflightReport(canonical, season, len(matches), 0)
    for i, match in enumerate(matches, start=1):
        try:
            lineups.append(fetch_match_lineups(match.match_id, force=force))
        except Exception as exc:  # noqa: BLE001 - a missing lineup is reported, the scan goes on
            report.missing_lineups[match.match_id] = f"{type(exc).__name__}: {exc}"
            logger.error("Lineup {m} failed: {e}", m=match.match_id, e=exc)
        if i % 50 == 0 or i == len(matches):
            logger.info("Pre-flight {lg}: {i}/{n} lineups", lg=canonical, i=i, n=len(matches))
    report.lineups_read = len(lineups)
    report.positions = scan_positions(lineups)
    report.known, report.inferred, report.ambiguous = classify_positions(report.positions)

    expected = CLUBS_2015_16.get(canonical)
    raw_teams = {t["team_name"] for match in lineups for t in match}
    report.unknown_teams = sorted(
        n for n in raw_teams if expected is not None and normalize_team_name(n) not in expected
    )

    rows = [
        {"match_id": "", "kind": "inferred_mapping",
         "correction": f"position {name!r} -> group {coarse}/{fine} (inferred from its wording)"}
        for name, (coarse, fine) in sorted(report.inferred.items())
    ]  # fmt: skip
    out = settings.processed_dir
    out.mkdir(parents=True, exist_ok=True)
    _replace_report_rows(
        out / "statsbomb_corrections.csv", rows, ["match_id", "kind", "correction"], canonical,
        season, kinds=("inferred_mapping",),
    )  # fmt: skip
    return report


def _main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a league's lineups and check positions.")
    parser.add_argument("--league", required=True, help="EPL, LALIGA, SERIEA, BUNDESLIGA, LIGUE1.")
    parser.add_argument("--season", type=int, default=2015)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    r = preflight(args.league, args.season, force=args.force)
    print(f"{r.league} {r.season}: {r.lineups_read}/{r.matches} lineups read")
    print(f"positions seen ({len(r.positions)}):")
    for name, n in sorted(r.positions.items()):
        status = "known" if name in r.known else "INFERRED" if name in r.inferred else "AMBIGUOUS"
        print(f"  {n:6d}  {name:28s} {status}")
    for name, (coarse, fine) in r.inferred.items():
        print(f"inferred mapping: {name!r} -> {coarse}/{fine} (add to parse.py; logged)")
    if r.ambiguous:
        print(f"AMBIGUOUS positions, ingest must not start: {r.ambiguous}")
    if r.unknown_teams:
        print(f"team names not in the expected club list: {r.unknown_teams}")
    if r.missing_lineups:
        print(
            f"lineups that failed to load ({len(r.missing_lineups)}): {sorted(r.missing_lineups)}"
        )
    if not r.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
