"""Season aggregation, the minutes threshold and the stored tables."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text

from src.transform import normalize
from src.transform.normalize import (
    INSUFFICIENT_TABLE,
    aggregate_player_seasons,
    build_player_season_stats,
    season_label,
    season_table_name,
)
from src.utils.config import settings
from tests.analytics_fixtures import LEAGUE, SEASON, league_rows, load_into, player_rows


def test_season_names() -> None:
    assert season_label("2015") == "2015_16"
    assert season_table_name("2015") == "player_season_stats_2015_16"
    assert season_label("1999") == "1999_00"


def test_totals_ratios_and_position_come_from_the_whole_season() -> None:
    rows = player_rows(1, "A", "X", "MID", matches=8, minutes=90, passes_attempted=50,
                       passes_completed=40, pressures=10, pressure_avg=1.0)  # fmt: skip
    rows += player_rows(1, "A", "X", "DEF", matches=2, minutes=90, first_match=100,
                        passes_attempted=150, passes_completed=60, pressures=30,
                        pressure_avg=2.0)  # fmt: skip
    totals, _ = aggregate_player_seasons(pd.DataFrame(rows))
    row = totals.iloc[0]
    assert (row["matches"], row["minutes_total"]) == (10, 900.0)
    assert (row["position_group"], row["position_group_share"]) == ("MID", 0.8)
    assert row["passes_attempted"] == 700
    assert row["pressure_duration_total"] == pytest.approx(8 * 10 * 1.0 + 2 * 30 * 2.0)


def test_multi_team_player_gets_the_team_with_most_minutes() -> None:
    rows = player_rows(1, "Mover", "Old FC", "FWD", matches=3, minutes=90)
    rows += player_rows(1, "Mover", "New FC", "FWD", matches=7, minutes=90, first_match=50)
    totals, multi = aggregate_player_seasons(pd.DataFrame(rows))
    assert totals.iloc[0]["team"] == "New FC" and totals.iloc[0]["n_teams"] == 2
    assert list(multi["player_name"]) == ["Mover"]
    assert (
        "Old FC 270" in multi.iloc[0]["team_split"] and "New FC 630" in multi.iloc[0]["team_split"]
    )


def test_minutes_threshold_splits_and_gives_a_reason() -> None:
    rows = player_rows(1, "Regular", "X", "MID", matches=10, minutes=90)  # 900: qualifies exactly
    rows += player_rows(2, "Fringe", "X", "MID", matches=4, minutes=90)  # 360
    result = build_player_season_stats(pd.DataFrame(rows), min_minutes=900)
    assert list(result.qualifying["player_name"]) == ["Regular"]
    assert list(result.insufficient["player_name"]) == ["Fringe"]
    assert "360.0 < 900" in result.insufficient.iloc[0]["reason"]
    assert "goals_p90" in result.qualifying and "goals_p90" not in result.insufficient
    assert len(result.qualifying) + len(result.insufficient) == 2  # nobody vanishes


def test_player_without_a_position_group_is_excluded_with_a_reason() -> None:
    rows = pd.DataFrame(player_rows(1, "Nobody", "X", "MID", matches=12, minutes=90))
    rows["position_group"] = None
    result = build_player_season_stats(rows, min_minutes=900)
    assert result.qualifying.empty
    assert result.insufficient.iloc[0]["reason"] == "no position_group"


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    load_into(eng, league_rows())
    return eng


def test_run_stores_both_tables_and_is_idempotent(engine: Engine) -> None:
    for _ in range(2):
        normalize.run(SEASON, LEAGUE, engine=engine)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM player_season_stats_2015_16")).scalar()
        assert n == 88  # 22 each of DEF, MID, FWD, GK, all with 900 minutes
        assert conn.execute(text(f"SELECT COUNT(*) FROM {INSUFFICIENT_TABLE}")).scalar() == 0
    assert (settings.processed_dir / "player_season_stats_2015_16.parquet").exists()


def test_run_rejects_a_season_with_no_data(engine: Engine) -> None:
    with pytest.raises(LookupError):
        normalize.run("1999", LEAGUE, engine=engine)
