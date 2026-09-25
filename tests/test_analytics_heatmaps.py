"""Heatmap grids: binning, event filters, per-90 normalisation and the stored table."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text

from src.analytics import heatmaps
from src.analytics.heatmaps import (
    GRID_COLS,
    GRID_ROWS,
    HEATMAP_TYPES,
    accumulate,
    build_heatmaps,
    cell,
    describe,
    heatmap_types_for,
)
from src.transform import normalize
from src.utils.config import settings
from tests.analytics_fixtures import LEAGUE, SEASON, load_into, player_rows


def ev(
    kind: str, x: float, y: float, pid: int = 1, period: int = 1, **extra: Any
) -> dict[str, Any]:
    return {
        "period": period,
        "type": {"name": kind},
        "player": {"id": pid},
        "location": [x, y],
        **extra,
    }


# --- binning -----------------------------------------------------------------------------------


def test_grid_is_12_by_8_ten_yard_cells() -> None:
    assert (GRID_COLS, GRID_ROWS) == (12, 8)
    assert cell(0, 0) == (0, 0)
    assert cell(9.99, 9.99) == (0, 0)
    assert cell(10, 10) == (1, 1)
    assert cell(60, 40) == (4, 6)  # (row from y=40, col from x=60)
    assert cell(119.9, 79.9) == (7, 11)


def test_coordinates_slightly_off_the_pitch_are_clamped_not_dropped() -> None:
    assert cell(120.9, 80.0) == (7, 11)  # StatsBomb 2015/16 has x up to 120.9
    assert cell(-0.5, -1.0) == (0, 0)


# --- which events go in which heatmap ----------------------------------------------------------


def test_types_for_each_event_kind() -> None:
    assert heatmap_types_for(ev("Pass", 50, 40)) == ["all_actions", "all_actions_1h", "passing"]
    assert heatmap_types_for(ev("Pass", 50, 40, period=2))[:2] == ["all_actions", "all_actions_2h"]
    assert "touches" in heatmap_types_for(ev("Carry", 50, 40))
    assert "touches" in heatmap_types_for(ev("Dribble", 50, 40))
    assert set(heatmap_types_for(ev("Shot", 100, 40))) >= {"touches", "shots"}
    assert "touches" in heatmap_types_for(ev("Ball Receipt*", 50, 40))


def test_an_incomplete_receipt_is_not_a_touch() -> None:
    incomplete = ev("Ball Receipt*", 50, 40, ball_receipt={"outcome": {"name": "Incomplete"}})
    assert heatmap_types_for(incomplete) == ["all_actions", "all_actions_1h"]


def test_defensive_types_match_the_metric_definitions() -> None:
    tackle = ev("Duel", 20, 40, duel={"type": {"name": "Tackle"}})
    aerial_lost = ev("Duel", 20, 40, duel={"type": {"name": "Aerial Lost"}})
    failed = ev("Ball Recovery", 20, 40, ball_recovery={"recovery_failure": True})
    assert "defensive" in heatmap_types_for(tackle)
    assert "defensive" not in heatmap_types_for(aerial_lost)
    assert "defensive" not in heatmap_types_for(failed)
    for kind in ("Interception", "Block", "Clearance", "Ball Recovery"):
        assert "defensive" in heatmap_types_for(ev(kind, 20, 40)), kind


def test_events_without_an_actor_a_location_or_in_the_shootout_are_skipped() -> None:
    no_player = {"period": 1, "type": {"name": "Pass"}, "location": [1, 1]}
    no_location = {"period": 1, "type": {"name": "Substitution"}, "player": {"id": 1}}
    assert heatmap_types_for(no_player) == []
    assert heatmap_types_for(no_location) == []
    assert heatmap_types_for(ev("Pass", 50, 40, period=5)) == []


def test_accumulate_counts_per_player_and_cell() -> None:
    events = [ev("Pass", 5, 5), ev("Pass", 6, 6), ev("Pass", 115, 75, pid=2), ev("Shot", 100, 40)]
    grids = accumulate(events)
    assert grids[(1, "passing")][0, 0] == 2
    assert grids[(2, "passing")][7, 11] == 1
    assert grids[(1, "shots")][4, 10] == 1
    assert grids[(1, "all_actions")].sum() == 3 and grids[(2, "all_actions")].sum() == 1


# --- the stored rows ---------------------------------------------------------------------------


def _stats(minutes: float = 180.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "player_id": "statsbomb:1",
                "player_name": "Test",
                "season": SEASON,
                "league": LEAGUE,
                "source": "statsbomb",
                "position_group_fine": "CM",
                "minutes_total": minutes,
            }  # fmt: skip
        ]
    )


def test_build_heatmaps_has_a_row_per_type_with_raw_and_per90_grids() -> None:
    events = [ev("Pass", 5, 5, period=1)] * 3 + [ev("Pass", 115, 75, period=2)]
    rows = build_heatmaps(_stats(180.0), accumulate(events), {1: "statsbomb:1"})
    assert list(rows["heatmap_type"]) == list(HEATMAP_TYPES)
    by_type = rows.set_index("heatmap_type")

    raw = np.array(json.loads(by_type.loc["all_actions", "grid_json"]))
    assert raw.shape == (GRID_ROWS, GRID_COLS)
    assert raw[0, 0] == 3 and raw[7, 11] == 1
    assert by_type.loc["all_actions", "event_count"] == 4

    p90 = np.array(json.loads(by_type.loc["all_actions", "grid_p90_json"]))
    assert p90[0, 0] == pytest.approx(3 / 180 * 90)  # events per 90 minutes
    assert by_type.loc["all_actions", "event_count_p90"] == pytest.approx(4 / 180 * 90)

    h1 = np.array(json.loads(by_type.loc["all_actions_1h", "grid_json"]))
    h2 = np.array(json.loads(by_type.loc["all_actions_2h", "grid_json"]))
    assert (h1 + h2 == raw).all() and h1[0, 0] == 3 and h2[7, 11] == 1


def test_a_type_with_no_events_still_gets_an_all_zero_row() -> None:
    rows = build_heatmaps(_stats(), accumulate([ev("Pass", 5, 5)]), {1: "statsbomb:1"})
    shots = rows[rows["heatmap_type"] == "shots"].iloc[0]
    assert shots["event_count"] == 0
    assert np.array(json.loads(shots["grid_json"])).sum() == 0


def test_describe_summarises_where_a_grid_sits() -> None:
    grid = np.zeros((GRID_ROWS, GRID_COLS))
    grid[3, 0] = 9  # own goal area, central
    grid[3, 11] = 1  # opposite end
    d = describe(grid)
    assert d["events"] == 10 and d["own_box_zone"] == 0.9 and d["own_half"] == 0.9
    assert d["final_third"] == 0.1 and d["central_lanes"] == 1.0
    assert describe(np.zeros((GRID_ROWS, GRID_COLS))) == {}


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    rows = pd.DataFrame(player_rows(1, "Nine", "X", "CM", matches=10, minutes=90, goals=1))
    rows = pd.concat([rows, pd.DataFrame(player_rows(2, "Fringe", "X", "CM", matches=2))])
    load_into(eng, rows)
    pd.DataFrame(
        {"player_id": ["statsbomb:1", "statsbomb:2"], "canonical_name": ["Nine", "Fringe"],
         "statsbomb_id": ["1", "2"]}
    ).to_sql("players", eng, if_exists="append", index=False)  # fmt: skip
    pd.DataFrame(
        {"source": "statsbomb", "match_id": range(1, 11), "season": SEASON, "league": LEAGUE,
         "match_date": date(2016, 1, 1), "home_team": "X", "away_team": "Y"}
    ).to_sql("matches", eng, if_exists="append", index=False)  # fmt: skip
    normalize.run(SEASON, LEAGUE, engine=eng)
    return eng


def test_run_stores_rows_for_qualifying_players_only_and_is_idempotent(engine: Engine) -> None:
    loaded: list[int] = []

    def load_events(match_id: int) -> list[dict[str, Any]]:
        loaded.append(match_id)
        return [ev("Pass", 15, 45, pid=1), ev("Block", 12, 42, pid=1)]

    for _ in range(2):
        result = heatmaps.run(SEASON, LEAGUE, engine=engine, load_events=load_events)
    assert sorted(set(loaded)) == list(range(1, 11))  # every stored match, once per run
    assert set(result["player_id"]) == {"statsbomb:1"}  # the 180-minute player is not qualifying
    assert len(result) == len(HEATMAP_TYPES)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM player_heatmaps")).scalar() == len(
            HEATMAP_TYPES
        )
        grid = conn.execute(
            text(
                "SELECT grid_json, event_count FROM player_heatmaps WHERE heatmap_type = 'passing'"
            )
        ).one()
    assert grid.event_count == 10  # one pass per match, 10 matches
    assert np.array(json.loads(grid.grid_json))[4, 1] == 10  # x=15 -> col 1, y=45 -> row 4


def test_run_without_stored_matches_fails_loudly(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM matches"))
    with pytest.raises(LookupError):
        heatmaps.run(SEASON, LEAGUE, engine=engine, load_events=lambda m: [])


def test_side_from_grid() -> None:
    from src.analytics.directory import side_from_grid

    grid = np.zeros((8, 12))
    assert side_from_grid(grid) is None
    grid[:4] = 1.0
    assert side_from_grid(grid) == "left"
    grid[4:] = 1.0
    assert side_from_grid(grid) is None  # even split
    grid[:4] = 0.2
    assert side_from_grid(grid) == "right"
