"""Team finance: CSV import, currency conversion, NULLs, upsert, wage ranking. Offline only."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from src.analytics.finance import load_team_finance, wage_context
from src.ingest import finance
from src.ingest.finance import FinanceCsvError, build_record, import_csv, read_csv
from src.models.schema import init_db

HEADER = ",".join(finance.CSV_COLUMNS)


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    return init_db(create_engine(f"sqlite:///{tmp_path / 'test.db'}"))


def _csv(tmp_path: Path, *rows: str, name: str = "finance.csv") -> Path:
    path = tmp_path / name
    path.write_text("\n".join([HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def _rows(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM team_finance ORDER BY team_name"), conn)


def test_migration_is_idempotent_and_has_columns(engine: Engine) -> None:
    init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("team_finance")}
    assert {
        "id", "team_name", "season", "league", "wage_bill_annual_gbp",
        "wage_bill_currency_original",
        "wage_bill_original_amount", "exchange_rate_used", "source", "source_url", "confidence",
        "updated_at",
    } <= cols  # fmt: skip


def test_gbp_row_is_stored_as_given(engine: Engine, tmp_path: Path) -> None:
    path = _csv(
        tmp_path,
        "Chelsea,2015-2016,EPL,215000000,GBP,,Club accounts,https://example.org/a,official",
    )
    assert import_csv(path, engine) == 1
    r = _rows(engine).iloc[0]
    assert (r.team_name, r.season, r.league) == ("Chelsea", "2015", "ENG-Premier League")
    assert r.wage_bill_annual_gbp == 215_000_000
    assert r.wage_bill_currency_original == "GBP"
    assert r.wage_bill_original_amount == 215_000_000
    assert r.exchange_rate_used == 1.0
    assert r.confidence == "official"


def test_eur_row_is_converted_at_the_season_rate(engine: Engine, tmp_path: Path) -> None:
    path = _csv(tmp_path, "Barcelona,2015/16,La Liga,,EUR,200000000,Club accounts,,official")
    import_csv(path, engine)
    r = _rows(engine).iloc[0]
    assert r.league == "ESP-La Liga"
    assert r.wage_bill_currency_original == "EUR"
    assert r.wage_bill_original_amount == 200_000_000
    assert r.exchange_rate_used == 0.75
    assert r.wage_bill_annual_gbp == pytest.approx(150_000_000)


def test_supplied_gbp_wins_over_conversion() -> None:
    rec = build_record(
        {"team_name": "Juventus", "season": "2015", "league": "Serie A",
         "wage_bill_annual_gbp": "100",
         "wage_bill_currency_original": "EUR", "wage_bill_original_amount": "140", "source": "X"}
    )  # fmt: skip
    assert rec.wage_bill_annual_gbp == 100
    assert rec.exchange_rate_used is None  # we did not convert, so we did not use a rate


def test_missing_values_stay_null(engine: Engine, tmp_path: Path) -> None:
    path = _csv(tmp_path, "Watford,2015-2016,EPL,,,,Not found in any source,,unknown")
    import_csv(path, engine)
    r = _rows(engine).iloc[0]
    for col in ("wage_bill_annual_gbp", "wage_bill_original_amount", "exchange_rate_used",
                "wage_bill_currency_original", "source_url"):  # fmt: skip
        assert r[col] is None or pd.isna(r[col]), col
    assert r.confidence == "unknown"


def test_blank_confidence_defaults_to_unknown() -> None:
    rec = build_record({"team_name": "Arsenal", "season": "2015", "league": "EPL", "source": "X"})
    assert rec.confidence == "unknown"
    assert rec.wage_bill_annual_gbp is None


def test_reimport_upserts_without_duplicates(engine: Engine, tmp_path: Path) -> None:
    import_csv(_csv(tmp_path, "Arsenal,2015-2016,EPL,,,,Pending,,unknown", name="a.csv"), engine)
    import_csv(
        _csv(
            tmp_path, "Arsenal,2015-2016,EPL,190000000,GBP,,Club accounts,,official", name="b.csv"
        ),
        engine,
    )
    row = "Arsenal,2015/16,ENG-Premier League,190000000,GBP,,Club accounts,,official"
    import_csv(_csv(tmp_path, row, name="c.csv"), engine)
    df = _rows(engine)
    assert len(df) == 1
    assert df.iloc[0].wage_bill_annual_gbp == 190_000_000
    assert df.iloc[0].confidence == "official"


def test_team_names_are_normalized(tmp_path: Path) -> None:
    [rec] = read_csv(_csv(tmp_path, "Man Utd,2015-2016,EPL,,,,X,,unknown"))
    assert rec.team_name == "Manchester United"


@pytest.mark.parametrize(
    "row, message",
    [
        ("Arsenal,2015-2017,EPL,,,,X,,unknown", "consecutive"),
        ("Arsenal,2015-2016,MLS,,,,X,,unknown", "league"),
        ("Arsenal,2015-2016,EPL,lots,,,X,,unknown", "not a number"),
        ("Arsenal,2015-2016,EPL,,USD,100,X,,unknown", "rate"),
        ("Arsenal,2015-2016,EPL,,,100,X,,unknown", "without a currency"),
        ("Arsenal,2015-2016,EPL,,,,X,,guess", "confidence"),
        ("Arsenal,2015-2016,EPL,,,,,,unknown", "source"),
        ("Arsenal,2015-2016,EPL,-5,GBP,,X,,unknown", "greater than or equal"),
    ],
)
def test_bad_rows_are_rejected_not_guessed(tmp_path: Path, row: str, message: str) -> None:
    with pytest.raises(FinanceCsvError, match=message):
        read_csv(_csv(tmp_path, row))


def test_template_is_header_only_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "manual" / "team_finance.csv"
    assert finance.write_template(path)
    assert path.read_text(encoding="utf-8").strip() == HEADER
    assert read_csv(path) == []
    path.write_text(HEADER + "\nArsenal,2015,EPL,,,,X,,unknown\n", encoding="utf-8")
    assert not finance.write_template(path)
    assert len(read_csv(path)) == 1


def test_wage_context_ranks_known_bills_only(engine: Engine, tmp_path: Path) -> None:
    import_csv(
        _csv(
            tmp_path,
            "Chelsea,2015-2016,EPL,215000000,GBP,,A,,official",
            "Barcelona,2015-2016,La Liga,,EUR,300000000,B,,estimate",
            "Watford,2015-2016,EPL,,,,C,,unknown",
        ),
        engine,
    )
    fin = load_team_finance("2015", engine)
    barca = wage_context("Barcelona", "ESP-La Liga", 80, fin)
    assert (barca.rank, barca.teams_ranked, barca.teams_in_season) == (1, 2, 80)
    assert barca.wage_bill_annual_gbp == pytest.approx(225_000_000)
    assert barca.confidence == "estimate"
    assert wage_context("Chelsea", "ENG-Premier League", 80, fin).rank == 2
    watford = wage_context("Watford", "ENG-Premier League", 80, fin)
    assert watford.wage_bill_annual_gbp is None and watford.rank is None
    absent = wage_context("Leicester City", "ENG-Premier League", 80, fin)
    assert absent.wage_bill_annual_gbp is None and absent.source is None


def test_load_team_finance_without_table(tmp_path: Path) -> None:
    empty = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert load_team_finance("2015", empty).empty


def test_capology_stops_on_cloudflare_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 403
        headers = {"cf-mitigated": "challenge", "server": "cloudflare"}

    calls: list[str] = []
    monkeypatch.setattr(finance, "_get", lambda url: calls.append(url) or Resp())
    with pytest.raises(finance.SourceBlocked, match="403"):
        finance.fetch_capology("EPL", "2015-2016")
    assert calls == [f"{finance.CAPOLOGY_BASE}/robots.txt"]  # one request, no retry, no workaround
