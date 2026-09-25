"""Team finance ingest: annual club wage bills into `team_finance`.

Two ways in:

- **Manual CSV** (`--import-csv`, the working path): the hand-filled `data/manual/team_finance.csv`,
  one row per club per season (contract: `CSV_COLUMNS`, ANALYTICS_DECISIONS.md section 26). The
  figure is entered as published, in its own currency, and converted to GBP at the fixed season
  rate (`EXCHANGE_RATES_TO_GBP`); both are stored. Rows missing a required field, naming a club
  outside the 80-club pool, or claiming official/aggregated confidence without a `source_url` are
  rejected into `data/processed/team_finance_rejects.csv`, never skipped silently or repaired.
  `data/manual/team_finance.template.csv` shows the format with example rows and is never imported.
- **Capology** (`--source capology`): `fetch_capology` is a scaffold only. On 2026-09-25
  capology.com answered `robots.txt` with a Cloudflare challenge (403, `cf-mitigated: challenge`),
  so it stops with `SourceBlocked` and never tries to get round the challenge. See BLOCKERS.md
  section 19.

(FBref's wages pages sit behind the same kind of challenge: BLOCKERS.md sections 1 and 19.)

Rules: team-level wage bills only, never player wages. No source, no row. A club with no row has no
wage bill (NULL): nothing is estimated or filled in.

    python -m src.ingest.finance --import-csv data/manual/team_finance.csv
    python -m src.ingest.finance --source capology --league EPL --season 2015-2016
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import requests
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.ingest.team_identity import CLUBS_2015_16, is_known_team, normalize_team_name
from src.models.finance import FINANCE_CONFIDENCE, TeamFinanceRecord
from src.models.schema import TeamFinance, get_engine, init_db
from src.utils.config import settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

MANUAL_CSV: Path = settings.manual_dir / "team_finance.csv"
TEMPLATE_CSV: Path = settings.manual_dir / "team_finance.template.csv"
REJECTS_CSV: Path = settings.processed_dir / "team_finance_rejects.csv"
# The CSV contract (ANALYTICS_DECISIONS.md section 26). Names follow the `team_finance` columns.
CSV_COLUMNS: tuple[str, ...] = (
    "team_name", "season", "league", "wage_bill_original_amount", "wage_bill_currency_original",
    "wage_bill_annual_gbp", "confidence", "source", "source_url", "notes",
)  # fmt: skip
REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"team_name", "season", "wage_bill_original_amount", "wage_bill_currency_original",
     "confidence", "source"}
)  # fmt: skip
# Accepted header synonyms -> contract column (the names used in the 2026-09-26 brief).
COLUMN_ALIASES: dict[str, str] = {
    "club_name": "team_name", "wage_bill_native": "wage_bill_original_amount",
    "native_currency": "wage_bill_currency_original", "wage_bill_gbp": "wage_bill_annual_gbp",
}  # fmt: skip
URL_REQUIRED_FOR: frozenset[str] = frozenset({"official", "aggregated"})
GBP_TOLERANCE = 0.01  # a supplied GBP figure may differ from native x rate by 1% (rounding) at most
EXAMPLE_MARKER = "EXAMPLE"  # template rows start their notes with this and are never imported

# GBP per unit of the original currency, by database season code. 2015/16 uses 1 EUR = 0.75 GBP,
# a round season average (ANALYTICS_DECISIONS.md section 24). Add a season before importing it.
EXCHANGE_RATES_TO_GBP: dict[str, dict[str, float]] = {"2015": {"GBP": 1.0, "EUR": 0.75}}

# The clubs a finance row may name, by database season code: the 80 clubs of the four leagues in
# the dataset (the Bundesliga is not ingested, so its clubs are not in the pool).
CLUB_POOLS: dict[str, dict[str, frozenset[str]]] = {
    "2015": {lg: clubs for lg, clubs in CLUBS_2015_16.items() if lg != "GER-Bundesliga"},
}

# Short names accepted on the CLI and in the CSV -> database league code.
LEAGUES: dict[str, str] = {
    "EPL": "ENG-Premier League",
    "LALIGA": "ESP-La Liga",
    "SERIEA": "ITA-Serie A",
    "LIGUE1": "FRA-Ligue 1",
}

CAPOLOGY_BASE = "https://www.capology.com"
USER_AGENT = "FootballIntel/0.1 (football research project; plain requests, rate-limited)"


class SourceBlocked(RuntimeError):
    """The source answered with a bot challenge or refusal. Never worked around: report it."""


class FinanceCsvError(ValueError):
    """A CSV row cannot be imported as given (bad league, season, currency, number, confidence)."""


# ---- normalization (pure) ---------------------------------------------------------------------


def normalize_season(season: str) -> str:
    """'2015-2016', '2015/16', '2015/2016', '2015-16' or '2015' -> the database code '2015'."""
    m = re.fullmatch(r"\s*(\d{4})(?:\s*[-/]\s*(\d{2}|\d{4}))?\s*", season)
    if not m:
        raise FinanceCsvError(f"Unrecognised season {season!r}")
    start, end = int(m.group(1)), m.group(2)
    if end is not None and int(end[-2:]) != (start + 1) % 100:
        raise FinanceCsvError(f"Season {season!r} does not span consecutive years")
    return str(start)


def normalize_league(league: str) -> str:
    """A short name ('EPL', 'La Liga', 'Serie A', 'Ligue 1') or a database code -> the code."""
    if league.strip() in LEAGUES.values():
        return league.strip()
    key = re.sub(r"[\s_-]", "", league).upper()
    if key not in LEAGUES:
        raise FinanceCsvError(f"Unrecognised league {league!r}")
    return LEAGUES[key]


def _number(value: str | None, field: str) -> float | None:
    """Blank -> None. Accepts thousands separators and a leading currency symbol."""
    if value is None or not value.strip():
        return None
    cleaned = re.sub(r"[£€$,\s]", "", value)
    try:
        return float(cleaned)
    except ValueError as exc:
        raise FinanceCsvError(f"{field}: {value!r} is not a number") from exc


def resolve_club(name: str, season: str, league: str | None = None) -> tuple[str, str]:
    """A CSV club name -> (canonical name, league code) in the season's club pool (the 80 clubs of
    the four leagues in 2015/16), through the existing alias table. Raises FinanceCsvError for a
    name that is not in the pool, and for a league that disagrees with the club's. Never guesses."""
    pool = CLUB_POOLS.get(season, {})
    if not pool:
        raise FinanceCsvError(f"no club pool for season {season} (only {sorted(CLUB_POOLS)})")
    canonical = normalize_team_name(name)
    if not is_known_team(name):
        raise FinanceCsvError(f"club {name!r} is not in the alias table")
    leagues = [lg for lg, clubs in pool.items() if canonical in clubs]
    if not leagues:
        raise FinanceCsvError(f"club {canonical!r} is not in the {season} club pool")
    if league is not None and league != leagues[0]:
        raise FinanceCsvError(f"{canonical} played in {leagues[0]} in {season}, not {league}")
    return canonical, leagues[0]


def build_record(row: dict[str, str]) -> TeamFinanceRecord:
    """One CSV row (contract column names) -> a record. Pure: no database, no network.

    Rejects (FinanceCsvError) any row missing a required field, with an unknown club, with
    confidence official/aggregated but no source_url, with a currency that has no season rate, or
    with a supplied GBP figure that disagrees with native x rate. GBP is always native x rate."""
    get = lambda k: (row.get(k) or "").strip()  # noqa: E731
    missing = sorted(c for c in REQUIRED_COLUMNS if not get(c))
    if missing:
        raise FinanceCsvError(f"missing {', '.join(missing)}")
    if get("notes").upper().startswith(EXAMPLE_MARKER):
        raise FinanceCsvError("template example row (notes start with EXAMPLE); not imported")
    season = normalize_season(get("season"))
    team, league = resolve_club(
        get("team_name"), season, normalize_league(get("league")) if get("league") else None
    )

    confidence = get("confidence").lower()
    if confidence not in FINANCE_CONFIDENCE:
        raise FinanceCsvError(f"confidence {confidence!r} is not one of {FINANCE_CONFIDENCE}")
    source_url = get("source_url") or None
    if confidence in URL_REQUIRED_FOR and source_url is None:
        raise FinanceCsvError(f"{team}: confidence {confidence!r} needs a source_url")

    native = _number(get("wage_bill_original_amount"), "wage_bill_original_amount")
    assert native is not None  # required above
    currency = get("wage_bill_currency_original").upper()
    rates = EXCHANGE_RATES_TO_GBP.get(season, {})
    if currency not in rates:
        raise FinanceCsvError(
            f"{team}: no {currency}->GBP rate for season {season} (have {sorted(rates)}); "
            "add a cited rate to EXCHANGE_RATES_TO_GBP first"
        )
    rate = rates[currency]
    gbp = native * rate
    supplied = _number(get("wage_bill_annual_gbp"), "wage_bill_annual_gbp")
    if supplied is not None and abs(supplied - gbp) > GBP_TOLERANCE * max(gbp, 1.0):
        raise FinanceCsvError(
            f"{team}: wage_bill_annual_gbp {supplied:,.0f} is not "
            f"{native:,.0f} {currency} x {rate} = {gbp:,.0f}; leave it blank and it is computed"
        )

    try:
        return TeamFinanceRecord(
            team_name=team, season=season, league=league, wage_bill_annual_gbp=gbp,
            wage_bill_currency_original=currency, wage_bill_original_amount=native,
            exchange_rate_used=rate, source=get("source"), source_url=source_url,
            confidence=confidence,  # type: ignore[arg-type]  # checked above
            notes=get("notes") or None,
        )  # fmt: skip
    except ValidationError as exc:
        raise FinanceCsvError(f"{team}: {exc.errors()[0]['msg']}") from exc


@dataclass(frozen=True)
class Reject:
    """A CSV row that was not imported, with the reason. Written to the reject report."""

    line: int
    team_name: str
    reason: str


def read_csv(path: Path) -> tuple[list[TeamFinanceRecord], list[Reject]]:
    """Parse a finance CSV into (records, rejects). A bad row is rejected with its line number and
    reason, never skipped silently and never repaired. A second row for the same club and season is
    rejected too. Raises FinanceCsvError for the template file or a missing required column."""
    if path.name.endswith(".template.csv"):
        raise FinanceCsvError(f"{path.name} is the template; import {MANUAL_CSV.name} instead")
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        names = [(h or "").strip() for h in reader.fieldnames or []]
        header = [COLUMN_ALIASES.get(h, h) for h in names]
        missing = REQUIRED_COLUMNS - set(header)
        if missing:
            raise FinanceCsvError(f"{path.name}: missing columns {sorted(missing)}")
        records: list[TeamFinanceRecord] = []
        rejects: list[Reject] = []
        seen: set[tuple[str, str]] = set()
        for line, raw in enumerate(csv.reader(fh), start=2):
            if not any(v.strip() for v in raw):
                continue
            row = dict(zip(header, raw, strict=False))
            try:
                rec = build_record(row)
                if (rec.team_name, rec.season) in seen:
                    raise FinanceCsvError(f"{rec.team_name} {rec.season}: duplicate row")
                seen.add((rec.team_name, rec.season))
                records.append(rec)
            except FinanceCsvError as exc:
                rejects.append(Reject(line, (row.get("team_name") or "").strip(), str(exc)))
                logger.warning("Finance: {f} line {n} rejected: {e}", f=path.name, n=line, e=exc)
    return records, rejects


def write_rejects(rejects: list[Reject], path: Path = REJECTS_CSV) -> Path:
    """Write the reject report (header only when nothing was rejected), replacing the last one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["line", "team_name", "reason"])
        w.writerows([r.line, r.team_name, r.reason] for r in rejects)
    return path


# ---- database ---------------------------------------------------------------------------------


def upsert_records(engine: Engine, records: list[TeamFinanceRecord]) -> int:
    """Insert or replace by (team_name, season, league). Logs any row without a wage bill (none
    come from the CSV now, which rejects them)."""
    if not records:
        return 0
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [{**r.model_dump(), "updated_at": now} for r in records]
    for r in records:
        if r.wage_bill_annual_gbp is None:
            logger.warning(
                "Finance: no wage bill for {team} ({league} {season}); stored as NULL",
                team=r.team_name, league=r.league, season=r.season,
            )  # fmt: skip
    stmt = sqlite_insert(TeamFinance).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["team_name", "season", "league"],
        set_={c: stmt.excluded[c] for c in rows[0] if c not in ("team_name", "season", "league")},
    )
    with engine.begin() as conn:
        conn.execute(stmt)
    return len(rows)


def import_csv(
    path: Path, engine: Engine | None = None, rejects_path: Path = REJECTS_CSV
) -> tuple[int, list[Reject]]:
    """Read `path`, upsert its valid rows into `team_finance` and write every rejected row to the
    reject report. Returns (rows written, rejects). A header-only file writes nothing: a club with
    no row is a valid state."""
    engine = init_db(engine or get_engine())
    records, rejects = read_csv(path)
    n = upsert_records(engine, records)
    write_rejects(rejects, rejects_path)
    logger.info("Finance: imported {n} rows from {path}; {r} rejected -> {rp}",
                n=n, path=path, r=len(rejects), rp=rejects_path)  # fmt: skip
    return n, rejects


def write_template(path: Path = MANUAL_CSV) -> bool:
    """Create the empty manual CSV (header only) if `path` does not exist. Never overwrites. The
    annotated example lives in TEMPLATE_CSV, which is never imported."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(CSV_COLUMNS)
    return True


# ---- Capology (scaffold) ----------------------------------------------------------------------


def _is_challenge(resp: requests.Response) -> bool:
    return resp.status_code in (403, 429, 503) and (
        "cf-mitigated" in resp.headers or resp.headers.get("server", "").lower() == "cloudflare"
    )


@rate_limited(max_retries=1)
def _get(url: str) -> requests.Response:
    return requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)


def fetch_capology(league: str, season: str) -> list[TeamFinanceRecord]:
    """Club wage bills for one league-season from capology.com. Scaffold only.

    Checks `robots.txt` first and raises `SourceBlocked` on a Cloudflare challenge or any non-200
    answer, without retrying round it. The payroll page parser is not written: no page has ever been
    readable, so its structure is unknown, and a parser written blind would be a guess.
    """
    code, start = normalize_league(league), normalize_season(season)
    robots = _get(f"{CAPOLOGY_BASE}/robots.txt")
    if _is_challenge(robots) or robots.status_code != 200:
        raise SourceBlocked(
            f"capology.com robots.txt answered {robots.status_code} "
            f"(cf-mitigated={robots.headers.get('cf-mitigated')}). Not worked around: "
            "use the manual CSV (BLOCKERS.md section 19)."
        )
    raise NotImplementedError(
        f"Capology is reachable; write the payroll parser for {code} {start} from a real page "
        "and note it in BLOCKERS.md section 19 before running a scrape."
    )


# ---- CLI --------------------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingest team wage bills into team_finance.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--import-csv", type=Path, help="Hand-filled finance CSV to upsert.")
    group.add_argument("--source", choices=["capology"], help="Fetch from a web source.")
    group.add_argument("--write-template", action="store_true", help="Create the empty manual CSV.")
    parser.add_argument("--league", help="EPL, LALIGA, SERIEA or LIGUE1 (with --source).")
    parser.add_argument("--season", default="2015-2016", help="e.g. 2015-2016 (with --source).")
    args = parser.parse_args()

    if args.write_template:
        created = write_template()
        logger.info(
            "Template {state}: {path}", state="created" if created else "exists", path=MANUAL_CSV
        )
    elif args.import_csv:
        try:
            _, rejects = import_csv(args.import_csv)
        except FinanceCsvError as exc:  # the template, or a missing required column
            logger.error("{exc}", exc=exc)
            raise SystemExit(2) from exc
        if rejects:
            raise SystemExit(1)  # rows were rejected: see the reject report
    else:
        if not args.league:
            parser.error("--league is required with --source")
        try:
            records = fetch_capology(args.league, args.season)
        except SourceBlocked as exc:
            logger.error("{exc}", exc=exc)
            raise SystemExit(2) from exc
        upsert_records(init_db(), records)


if __name__ == "__main__":
    _main()
