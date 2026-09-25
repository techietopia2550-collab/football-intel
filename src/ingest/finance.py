"""Team finance ingest: annual club wage bills into `team_finance`.

Two ways in:

- **Manual CSV** (`--import-csv`, the working path): a hand-filled file such as
  `data/manual/team_finance.csv`, one row per club per league-season, every value traceable to a
  `source` and `source_url`. EUR (or other) amounts are converted to GBP at a fixed per-season
  rate (`EXCHANGE_RATES_TO_GBP`); both the original and the converted amount are stored.
- **Capology** (`--source capology`): `fetch_capology` is a scaffold only. On 2026-09-25
  capology.com answered `robots.txt` with a Cloudflare challenge (403, `cf-mitigated: challenge`),
  so it stops with `SourceBlocked` and never tries to get round the challenge. See BLOCKERS.md
  section 19.

(FBref's wages pages sit behind the same kind of challenge: BLOCKERS.md sections 1 and 19.)

Rules: team-level wage bills only, never player wages. A missing value stays NULL and is logged;
nothing is estimated or filled in.

    python -m src.ingest.finance --import-csv data/manual/team_finance.csv
    python -m src.ingest.finance --source capology --league EPL --season 2015-2016
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import csv
import re
from datetime import UTC, datetime
from pathlib import Path

import requests
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.ingest.team_identity import is_known_team, normalize_team_name
from src.models.finance import FINANCE_CONFIDENCE, TeamFinanceRecord
from src.models.schema import TeamFinance, get_engine, init_db
from src.utils.config import settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

MANUAL_CSV: Path = settings.manual_dir / "team_finance.csv"
CSV_COLUMNS: tuple[str, ...] = (
    "team_name", "season", "league", "wage_bill_annual_gbp", "wage_bill_currency_original",
    "wage_bill_original_amount", "source", "source_url", "confidence",
)  # fmt: skip

# GBP per unit of the original currency, by database season code. 2015/16 uses 1 EUR = 0.75 GBP,
# a round season average (ANALYTICS_DECISIONS.md section 24). Add a season before importing it.
EXCHANGE_RATES_TO_GBP: dict[str, dict[str, float]] = {"2015": {"GBP": 1.0, "EUR": 0.75}}

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


def build_record(row: dict[str, str]) -> TeamFinanceRecord:
    """One CSV row -> a record. Pure: no database, no network.

    GBP is taken as given when `wage_bill_annual_gbp` is filled. Otherwise it is the original amount
    times the season's rate for its currency. When neither is present every money field stays None.
    """
    get = lambda k: (row.get(k) or "").strip()  # noqa: E731
    season = normalize_season(get("season"))
    league = normalize_league(get("league"))
    raw_team = get("team_name")
    if not raw_team:
        raise FinanceCsvError("team_name is blank")
    team = normalize_team_name(raw_team)
    if not is_known_team(raw_team):
        logger.warning(
            "Finance: team {team!r} is not in team_identity; stored as given", team=raw_team
        )

    confidence = get("confidence").lower() or "unknown"
    if confidence not in FINANCE_CONFIDENCE:
        raise FinanceCsvError(f"confidence {confidence!r} is not one of {FINANCE_CONFIDENCE}")
    source = get("source")
    if not source:
        raise FinanceCsvError(f"{team}: source is blank (every value must be traceable)")

    gbp = _number(get("wage_bill_annual_gbp"), "wage_bill_annual_gbp")
    original = _number(get("wage_bill_original_amount"), "wage_bill_original_amount")
    currency = get("wage_bill_currency_original").upper() or None
    if original is not None and currency is None:
        raise FinanceCsvError(f"{team}: wage_bill_original_amount given without a currency")

    rate: float | None = None
    if gbp is None and original is not None:
        rates = EXCHANGE_RATES_TO_GBP.get(season, {})
        if currency not in rates:
            raise FinanceCsvError(f"{team}: no {currency}->GBP rate for season {season}")
        rate = rates[currency]
        gbp = original * rate
    elif gbp is not None and original is None:
        # A GBP-only row is its own original amount, so the three columns are filled together.
        currency = currency or "GBP"
        if currency == "GBP":
            original, rate = gbp, 1.0
    elif gbp is not None and currency == "GBP":
        rate = 1.0
    elif gbp is not None and original:
        # Both amounts supplied in a foreign currency: keep the source's own GBP figure, but flag
        # it if it strays from our season rate (a typo, or the source used another rate).
        expected = EXCHANGE_RATES_TO_GBP.get(season, {}).get(currency or "")
        if expected is not None and abs(gbp / original - expected) > 0.02 * expected:
            logger.warning(
                "Finance: {team} GBP/{cur} ratio {r:.3f} differs from season rate {e}",
                team=team, cur=currency, r=gbp / original, e=expected,
            )  # fmt: skip

    try:
        return TeamFinanceRecord(
            team_name=team, season=season, league=league, wage_bill_annual_gbp=gbp,
            wage_bill_currency_original=currency, wage_bill_original_amount=original,
            exchange_rate_used=rate, source=source, source_url=get("source_url") or None,
            confidence=confidence,  # type: ignore[arg-type]  # checked against FINANCE_CONFIDENCE above
        )  # fmt: skip
    except ValidationError as exc:
        raise FinanceCsvError(f"{team}: {exc.errors()[0]['msg']}") from exc


def read_csv(path: Path) -> list[TeamFinanceRecord]:
    """Parse a finance CSV. Stops on the first bad row (with its line number), never skips it."""
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = {"team_name", "season", "league", "source"} - set(reader.fieldnames or [])
        if missing:
            raise FinanceCsvError(f"{path.name}: missing columns {sorted(missing)}")
        records = []
        for line, row in enumerate(reader, start=2):
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                records.append(build_record(row))
            except FinanceCsvError as exc:
                raise FinanceCsvError(f"{path.name} line {line}: {exc}") from exc
    return records


# ---- database ---------------------------------------------------------------------------------


def upsert_records(engine: Engine, records: list[TeamFinanceRecord]) -> int:
    """Insert or replace by (team_name, season, league). Logs every row without a wage bill."""
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


def import_csv(path: Path, engine: Engine | None = None) -> int:
    """Read `path` and upsert it into `team_finance`. Returns the number of rows written."""
    engine = init_db(engine or get_engine())
    records = read_csv(path)
    n = upsert_records(engine, records)
    logger.info("Finance: imported {n} rows from {path}", n=n, path=path)
    return n


def write_template(path: Path = MANUAL_CSV) -> bool:
    """Create an empty CSV (header only) if `path` does not exist. Never overwrites."""
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
        import_csv(args.import_csv)
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
