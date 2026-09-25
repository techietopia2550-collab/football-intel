"""Team finance: the CSV contract, row rejection and the reject report, currency conversion,
club-name resolution, upsert, wage ranking and the UI's empty state. Offline only."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from src.analytics.finance import load_team_finance, wage_context
from src.app import design
from src.ingest import finance
from src.ingest.finance import FinanceCsvError, build_record, import_csv, read_csv, resolve_club
from src.models.finance import TeamWageContext
from src.models.schema import init_db

HEADER = ",".join(finance.CSV_COLUMNS)
URL = "https://example.org/accounts.pdf"


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    return init_db(create_engine(f"sqlite:///{tmp_path / 'test.db'}"))


def _row(**kw: str) -> dict[str, str]:
    """A valid official GBP row; keyword arguments override or blank a field."""
    base = {
        "team_name": "Chelsea",
        "season": "2015/16",
        "league": "",
        "wage_bill_original_amount": "215000000",
        "wage_bill_currency_original": "GBP",
        "wage_bill_annual_gbp": "",
        "confidence": "official",
        "source": "Club accounts",
        "source_url": URL,
        "notes": "",
    }
    return {**base, **kw}


def _csv(tmp_path: Path, *rows: dict[str, str], name: str = "finance.csv") -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=finance.CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def _rows(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT * FROM team_finance ORDER BY team_name"), conn)


def _import(tmp_path: Path, engine: Engine, *rows: dict[str, str], name: str = "f.csv"):  # type: ignore[no-untyped-def]
    return import_csv(_csv(tmp_path, *rows, name=name), engine, tmp_path / "rejects.csv")


# ---- schema -----------------------------------------------------------------------------------


def test_migration_is_idempotent_and_has_columns(engine: Engine) -> None:
    init_db(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("team_finance")}
    assert {
        "id",
        "team_name",
        "season",
        "league",
        "wage_bill_annual_gbp",
        "wage_bill_currency_original",
        "wage_bill_original_amount",
        "exchange_rate_used",
        "source",
        "source_url",
        "confidence",
        "notes",
        "updated_at",
    } <= cols


# ---- accepted rows and currency ---------------------------------------------------------------


def test_gbp_row_is_stored_as_published(engine: Engine, tmp_path: Path) -> None:
    n, rejects = _import(tmp_path, engine, _row(notes="year to 30 June 2016, all staff"))
    assert (n, rejects) == (1, [])
    r = _rows(engine).iloc[0]
    assert (r.team_name, r.season, r.league) == ("Chelsea", "2015", "ENG-Premier League")
    assert r.wage_bill_annual_gbp == 215_000_000 == r.wage_bill_original_amount
    assert (r.wage_bill_currency_original, r.exchange_rate_used) == ("GBP", 1.0)
    assert r.confidence == "official" and r.notes == "year to 30 June 2016, all staff"


def test_eur_is_converted_at_the_fixed_season_rate() -> None:
    rec = build_record(
        _row(
            team_name="Barcelona",
            wage_bill_original_amount="€200,000,000",
            wage_bill_currency_original="EUR",
        )
    )
    assert rec.league == "ESP-La Liga"  # derived from the club pool when blank
    assert (rec.wage_bill_original_amount, rec.exchange_rate_used) == (200_000_000, 0.75)
    assert rec.wage_bill_annual_gbp == pytest.approx(150_000_000)
    assert finance.EXCHANGE_RATES_TO_GBP["2015"]["EUR"] == 0.75


def test_supplied_gbp_must_agree_with_the_rate() -> None:
    ok = build_record(
        _row(
            team_name="Juventus",
            wage_bill_original_amount="140",
            wage_bill_currency_original="EUR",
            wage_bill_annual_gbp="105",
        )
    )
    assert ok.wage_bill_annual_gbp == pytest.approx(105)
    with pytest.raises(FinanceCsvError, match="leave it blank"):
        build_record(
            _row(
                team_name="Juventus",
                wage_bill_original_amount="140",
                wage_bill_currency_original="EUR",
                wage_bill_annual_gbp="100",
            )
        )


def test_usd_without_a_season_rate_is_rejected_not_guessed() -> None:
    with pytest.raises(FinanceCsvError, match="no USD->GBP rate"):
        build_record(_row(wage_bill_currency_original="USD"))


def test_brief_column_names_are_accepted_as_aliases(tmp_path: Path) -> None:
    path = tmp_path / "alias.csv"
    path.write_text(
        "club_name,season,wage_bill_native,native_currency,wage_bill_gbp,confidence,source,"
        "source_url,notes\nMan Utd,2015/16,232000000,GBP,,official,Accounts," + URL + ",\n",
        encoding="utf-8",
    )
    records, rejects = read_csv(path)
    assert rejects == [] and records[0].team_name == "Manchester United"


# ---- club resolution ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "canonical", "league"),
    [
        ("Man Utd", "Manchester United", "ENG-Premier League"),
        ("Atlético de Madrid", "Atlético Madrid", "ESP-La Liga"),
        ("Associazione Sportiva Roma", "AS Roma", "ITA-Serie A"),
        ("Paris Saint-Germain", "Paris Saint-Germain", "FRA-Ligue 1"),
    ],
)
def test_club_names_resolve_through_the_alias_table(name: str, canonical: str, league: str) -> None:
    assert resolve_club(name, "2015") == (canonical, league)


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("Arsenal Tula", "not in the alias table"),  # a real club, but not one of ours
        ("Bayern Munich", "not in the 2015 club pool"),  # known alias, Bundesliga not ingested
        ("Brentford", "not in the 2015 club pool"),  # known (2024/25 EPL), not a 2015/16 club
    ],
)
def test_clubs_outside_the_pool_are_rejected(name: str, message: str) -> None:
    with pytest.raises(FinanceCsvError, match=message):
        resolve_club(name, "2015")


def test_league_must_match_the_clubs_league() -> None:
    with pytest.raises(FinanceCsvError, match="played in ENG-Premier League"):
        build_record(_row(league="La Liga"))


# ---- rejection and the reject report -----------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source": ""}, "missing source"),
        ({"confidence": ""}, "missing confidence"),
        ({"team_name": ""}, "missing team_name"),
        ({"season": ""}, "missing season"),
        ({"wage_bill_original_amount": ""}, "missing wage_bill_original_amount"),
        ({"wage_bill_currency_original": ""}, "missing wage_bill_currency_original"),
        ({"confidence": "official", "source_url": ""}, "needs a source_url"),
        ({"confidence": "aggregated", "source_url": ""}, "needs a source_url"),
        ({"confidence": "guess"}, "not one of"),
        ({"season": "2015-2017"}, "consecutive"),
        ({"season": "2016/17"}, "no club pool for season 2016"),
        ({"wage_bill_original_amount": "lots"}, "not a number"),
        ({"wage_bill_original_amount": "-5"}, "greater than or equal"),
        ({"notes": "EXAMPLE ROW"}, "template example row"),
    ],
)
def test_bad_rows_are_rejected_not_guessed(override: dict[str, str], message: str) -> None:
    with pytest.raises(FinanceCsvError, match=message):
        build_record(_row(**override))


def test_estimate_and_unknown_do_not_need_a_url() -> None:
    for conf in ("estimate", "unknown"):
        assert build_record(_row(confidence=conf, source_url="")).confidence == conf


def test_rejects_go_to_the_report_and_good_rows_still_import(
    engine: Engine, tmp_path: Path
) -> None:
    n, rejects = _import(
        tmp_path,
        engine,
        _row(),
        _row(team_name="Arsenal", source=""),
        _row(team_name="Arsenal Tula"),
        _row(team_name="Chelsea", wage_bill_original_amount="1"),  # duplicate club-season
    )
    assert n == 1 and list(_rows(engine).team_name) == ["Chelsea"]
    assert [(r.line, r.team_name) for r in rejects] == [
        (3, "Arsenal"),
        (4, "Arsenal Tula"),
        (5, "Chelsea"),
    ]
    report = pd.read_csv(tmp_path / "rejects.csv")
    assert list(report.columns) == ["line", "team_name", "reason"]
    assert report["reason"].str.contains("missing source").iloc[0]
    assert report["reason"].str.contains("alias table").iloc[1]
    assert report["reason"].str.contains("duplicate").iloc[2]


def test_clean_import_writes_an_empty_report(engine: Engine, tmp_path: Path) -> None:
    _import(tmp_path, engine, _row())
    assert pd.read_csv(tmp_path / "rejects.csv").empty


def test_missing_required_column_stops_the_import(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("team_name,season\nChelsea,2015\n", encoding="utf-8")
    with pytest.raises(FinanceCsvError, match="missing columns"):
        read_csv(path)


# ---- template and empty file --------------------------------------------------------------------


def test_template_file_is_never_imported(tmp_path: Path) -> None:
    with pytest.raises(FinanceCsvError, match="template"):
        read_csv(finance.TEMPLATE_CSV)
    copy = tmp_path / "copy.csv"  # even under another name, its example rows are rejected
    copy.write_text(finance.TEMPLATE_CSV.read_text(encoding="utf-8"), encoding="utf-8")
    records, rejects = read_csv(copy)
    assert records == [] and len(rejects) == 2


def test_template_header_is_the_contract() -> None:
    with finance.TEMPLATE_CSV.open(encoding="utf-8") as fh:
        assert next(csv.reader(fh)) == list(finance.CSV_COLUMNS)


def test_header_only_file_imports_nothing(engine: Engine, tmp_path: Path) -> None:
    path = tmp_path / "manual" / "team_finance.csv"
    assert finance.write_template(path)
    assert path.read_text(encoding="utf-8").strip() == HEADER
    assert import_csv(path, engine, tmp_path / "r.csv") == (0, [])
    assert _rows(engine).empty
    assert not finance.write_template(path)  # never overwritten


def test_reimport_upserts_without_duplicates(engine: Engine, tmp_path: Path) -> None:
    _import(tmp_path, engine, _row(wage_bill_original_amount="190000000"), name="a.csv")
    _import(tmp_path, engine, _row(wage_bill_original_amount="215000000"), name="b.csv")
    df = _rows(engine)
    assert len(df) == 1 and df.iloc[0].wage_bill_annual_gbp == 215_000_000


# ---- ranking and the UI ---------------------------------------------------------------------


def test_wage_context_ranks_known_bills_only(engine: Engine, tmp_path: Path) -> None:
    _import(
        tmp_path,
        engine,
        _row(),
        _row(
            team_name="Barcelona",
            wage_bill_original_amount="300000000",
            wage_bill_currency_original="EUR",
            confidence="estimate",
            source="Press",
        ),
    )
    fin = load_team_finance("2015", engine)
    barca = wage_context("Barcelona", "ESP-La Liga", 80, fin)
    assert (barca.rank, barca.teams_ranked, barca.teams_in_season) == (1, 2, 80)
    assert barca.wage_bill_annual_gbp == pytest.approx(225_000_000)
    assert barca.confidence == "estimate"
    assert wage_context("Chelsea", "ENG-Premier League", 80, fin).rank == 2
    absent = wage_context("Leicester City", "ENG-Premier League", 80, fin)
    assert absent.wage_bill_annual_gbp is None and absent.source is None and absent.rank is None


def test_load_team_finance_without_table(tmp_path: Path) -> None:
    empty = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert load_team_finance("2015", empty).empty


def _ctx(**kw: object) -> TeamWageContext:
    base: dict[str, object] = dict(
        team_name="Chelsea",
        season="2015",
        league="ENG-Premier League",
        wage_bill_annual_gbp=None,
        rank=None,
        teams_ranked=0,
        teams_in_season=80,
        source=None,
        confidence=None,
    )
    return TeamWageContext(**{**base, **kw})  # type: ignore[arg-type]


def test_ui_empty_state_says_not_available_never_zero(tmp_path: Path) -> None:
    fin = load_team_finance("2015", init_db(create_engine(f"sqlite:///{tmp_path / 'e.db'}")))
    html = design.wage_text(wage_context("Chelsea", "ENG-Premier League", 80, fin))
    assert "Wage bill: <span" in html and "not available" in html
    assert "£" not in html and "£0" not in html
    assert "Team wage bill" in design.wage_text(_ctx(), "Team wage bill")


def test_ui_figure_carries_source_and_confidence() -> None:
    html = design.wage_text(
        _ctx(
            wage_bill_annual_gbp=187e6,
            rank=3,
            teams_ranked=12,
            source="Deloitte",
            confidence="estimate",
        )
    )
    assert "£187m/year · Deloitte, estimate" in html
    assert "rank 3 of 12 with data, 80 clubs" in html


# ---- Capology ----------------------------------------------------------------------------------


def test_capology_stops_on_cloudflare_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resp:
        status_code = 403
        headers = {"cf-mitigated": "challenge", "server": "cloudflare"}

    calls: list[str] = []
    monkeypatch.setattr(finance, "_get", lambda url: calls.append(url) or Resp())
    with pytest.raises(finance.SourceBlocked, match="403"):
        finance.fetch_capology("EPL", "2015-2016")
    assert calls == [f"{finance.CAPOLOGY_BASE}/robots.txt"]  # one request, no retry, no workaround
