"""Reports shared by several leagues, the failure limit, and the position pre-flight (offline)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from src.ingest.statsbomb import discovery, ingest, preflight
from src.ingest.statsbomb.ingest import SeasonResult, _write_reports
from src.ingest.statsbomb.parse import DISMISSAL_CUT, Correction
from src.models.schema import init_db
from tests.statsbomb_full_match import build_full_match


@pytest.fixture()
def processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ingest.settings, "processed_dir", tmp_path / "processed")
    return tmp_path / "processed"


def _result(**kw: object) -> SeasonResult:
    return SeasonResult(**kw)  # type: ignore[arg-type]


def test_reports_of_two_leagues_live_in_one_file_and_are_replaced_per_league(
    processed: Path,
) -> None:
    _write_reports(_result(failed={1: "boom"}), "ESP-La Liga", 2015)
    _write_reports(_result(failed={2: "bang", 3: "crash"}), "ITA-Serie A", 2015)
    df = pd.read_csv(processed / "statsbomb_failures.csv")
    assert list(df.columns) == ["league", "season", "match_id", "error"]
    assert Counter(df["league"]) == {"ESP-La Liga": 1, "ITA-Serie A": 2}

    _write_reports(_result(failed={}), "ESP-La Liga", 2015)  # La Liga re-run: fixed
    df = pd.read_csv(processed / "statsbomb_failures.csv")
    assert set(df["league"]) == {"ITA-Serie A"}  # Serie A untouched


def test_a_report_written_before_leagues_existed_is_read_as_premier_league(processed: Path) -> None:
    processed.mkdir(parents=True)
    pd.DataFrame({"match_id": [7], "error": ["old"]}).to_csv(
        processed / "statsbomb_failures.csv", index=False
    )
    _write_reports(_result(failed={9: "new"}), "ESP-La Liga", 2015)
    df = pd.read_csv(processed / "statsbomb_failures.csv")
    got = {(r.league, r.match_id) for r in df.itertuples()}
    assert got == {("ENG-Premier League", 7), ("ESP-La Liga", 9)}


def test_position_preflight_rows_survive_a_reingest(processed: Path) -> None:
    preflight._replace_report_rows(
        processed / "statsbomb_corrections.csv",
        [{"match_id": "", "kind": "inferred_mapping", "correction": "position 'X' -> ST"}],
        ["match_id", "kind", "correction"], "ESP-La Liga", 2015, kinds=("inferred_mapping",),
    )  # fmt: skip
    _write_reports(
        _result(corrections={5: [Correction(DISMISSAL_CUT, "Foo: 3.0 min cut")]}),
        "ESP-La Liga",
        2015,
    )
    _write_reports(
        _result(corrections={5: [Correction(DISMISSAL_CUT, "Foo: 3.0 min cut")]}),
        "ESP-La Liga",
        2015,
    )  # again
    df = pd.read_csv(processed / "statsbomb_corrections.csv")
    assert Counter(df["kind"]) == {"inferred_mapping": 1, "dismissal_cut": 1}


# --- the failure limit ---


@pytest.fixture()
def failing_season(engine: Engine, processed: Path, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    events, lineups, match = build_full_match()
    matches = [
        match.model_copy(update={"match_id": i, "league": "TEST-League"}) for i in range(1, 21)
    ]
    calls: list[int] = []

    def fake_events(match_id: int, **_kw: object) -> list[dict]:
        calls.append(match_id)
        raise RuntimeError("every match fails")

    monkeypatch.setattr(ingest, "init_db", lambda: engine)
    monkeypatch.setattr(ingest, "get_session_factory", lambda: sessionmaker(bind=engine))
    monkeypatch.setattr(ingest, "list_available", lambda *_a, **_kw: matches)
    monkeypatch.setattr(ingest, "fetch_match_events", fake_events)
    monkeypatch.setattr(ingest, "fetch_match_lineups", lambda *_a, **_kw: lineups)
    return calls


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    eng = create_engine(f"sqlite:///{tmp_path / 'multi.db'}")
    init_db(eng)
    return eng


def test_a_league_stops_after_more_than_the_failure_limit(failing_season: list[int]) -> None:
    result = ingest.ingest_season("EPL", 2015, max_failures=5)
    assert result.aborted
    assert len(result.failed) == 6 and failing_season == [1, 2, 3, 4, 5, 6]  # stopped at once
    failures = pd.read_csv(ingest.settings.processed_dir / "statsbomb_failures.csv")
    assert len(failures) == 6  # what did fail is still reported, nothing is hidden


def test_the_default_limit_is_15(failing_season: list[int]) -> None:
    assert ingest.MAX_FAILURES == 15
    result = ingest.ingest_season("EPL", 2015)  # 20 matches, all failing
    assert result.aborted and len(result.failed) == 16
    ok = ingest.ingest_season("EPL", 2015, max_failures=None)  # no limit: every match is tried
    assert not ok.aborted and len(ok.failed) == 20


# --- position pre-flight ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Goalkeeper", ("GK", "GK")),
        ("Right Wing Back", ("DEF", "FB")),
        ("Left Center Back", ("DEF", "CB")),
        ("Center Defensive Midfield", ("MID", "DM")),
        ("Right Attacking Midfield", ("MID", "AM")),
        ("Left Center Midfield", ("MID", "CM")),
        ("Left Wing", ("FWD", "W")),
        ("Center Forward", ("FWD", "ST")),
        ("Second Striker", ("FWD", "ST")),
        ("  center   back ", ("DEF", "CB")),
    ],
)
def test_unambiguous_position_names_are_inferred(name: str, expected: tuple[str, str]) -> None:
    assert preflight.infer_position(name) == expected


@pytest.mark.parametrize("name", ["Sweeper", "Sub", "Libero", "Playmaker", ""])
def test_ambiguous_position_names_are_not_guessed(name: str) -> None:
    assert preflight.infer_position(name) is None


def _lineup(*names: str) -> list[dict]:
    players = [
        {"player_id": i, "positions": [{"position": n}]} for i, n in enumerate(names, start=1)
    ]
    return [{"team_name": "T", "lineup": players}]


def test_scan_and_classify_positions() -> None:
    seen = preflight.scan_positions(
        [_lineup("Goalkeeper", "Right Back"), _lineup("Right Back", "Sweeper", "Third Striker")]
    )
    assert seen == {"Goalkeeper": 1, "Right Back": 2, "Sweeper": 1, "Third Striker": 1}
    known, inferred, ambiguous = preflight.classify_positions(seen)
    assert known == ["Goalkeeper", "Right Back"]
    assert inferred == {"Third Striker": ("FWD", "ST")}
    assert ambiguous == ["Sweeper"]


def test_every_position_in_the_tables_is_known_to_the_preflight() -> None:
    from src.ingest.statsbomb.parse import POSITION_GROUPS

    known, inferred, ambiguous = preflight.classify_positions(Counter(POSITION_GROUPS))
    assert len(known) == 25 and not inferred and not ambiguous


def test_preflight_stops_the_ingest_for_an_ambiguous_position(
    processed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.statsbomb_full_match import build_full_match

    _, lineups, match = build_full_match()
    lineups[0]["lineup"][2]["positions"][0]["position"] = "Sweeper"
    m = match.model_copy(update={"league": "ESP-La Liga"})
    monkeypatch.setattr(preflight, "list_available", lambda *_a, **_kw: [m])
    monkeypatch.setattr(preflight, "fetch_match_lineups", lambda *_a, **_kw: lineups)
    report = preflight.preflight("LALIGA", 2015)
    assert report.ambiguous == ["Sweeper"] and not report.ok
    assert report.lineups_read == 1
    assert discovery.resolve_league("LALIGA")[1] == "ESP-La Liga"


def test_preflight_flags_a_team_the_league_does_not_have(
    processed: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, lineups, match = build_full_match()
    monkeypatch.setattr(preflight, "list_available", lambda *_a, **_kw: [match])
    monkeypatch.setattr(preflight, "fetch_match_lineups", lambda *_a, **_kw: lineups)
    report = preflight.preflight("LALIGA", 2015)  # "Team A"/"Team B" are not La Liga clubs
    assert report.unknown_teams == ["Team A", "Team B"] and not report.ok
