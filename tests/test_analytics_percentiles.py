"""Within-position percentile ranking."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text

from src.analytics import percentiles
from src.analytics.metrics import METRICS
from src.analytics.percentiles import MIN_GROUP_SIZE, compute_percentiles
from src.transform import normalize
from src.utils.config import settings
from tests.analytics_fixtures import LEAGUE, SEASON, league_rows, load_into


def _stats(per_group: int = 22) -> pd.DataFrame:
    return normalize.build_player_season_stats(league_rows(per_group)).qualifying


def test_percentile_is_midrank_within_the_position_group() -> None:
    result = compute_percentiles(_stats())
    fwd = result[(result["metric"] == "goals_p90") & (result["position_group_fine"] == "ST")]
    assert len(fwd) == 22 and (fwd["sample_size"] == 22).all()
    by_name = fwd.set_index("player_name")["percentile"]
    assert by_name["ST 00"] == pytest.approx(0.5 / 22 * 100, abs=0.05)
    assert by_name["ST 21"] == pytest.approx(21.5 / 22 * 100, abs=0.05)


def test_players_are_only_ranked_against_their_own_group() -> None:
    result = compute_percentiles(_stats())
    goals = result[result["metric"] == "goals_p90"]
    # every outfield group has identical goal values, so each group's best player is at the same
    # percentile: the ranking is not pooled across positions.
    top = goals.groupby("position_group_fine")["percentile"].max()
    assert top.nunique() == 1


def test_goalkeeper_and_outfield_metrics_are_kept_apart() -> None:
    result = compute_percentiles(_stats())
    gk_metrics = {m.name for m in METRICS if m.scope == "gk"}
    assert set(result.loc[result["position_group_fine"] == "GK", "metric"]) == gk_metrics
    assert not set(result.loc[result["position_group_fine"] != "GK", "metric"]) & gk_metrics


def test_ties_share_a_rank() -> None:
    result = compute_percentiles(_stats())
    red = result[(result["metric"] == "red_p90") & (result["position_group_fine"] == "CM")]
    assert red["percentile"].nunique() == 1 and red["percentile"].iloc[0] == 50.0


def test_metric_under_its_guard_has_no_percentile_and_is_not_counted() -> None:
    stats = _stats()
    stats.loc[stats["player_name"] == "ST 03", "shot_accuracy"] = float("nan")
    result = compute_percentiles(stats)
    rows = result[(result["metric"] == "shot_accuracy") & (result["position_group_fine"] == "ST")]
    nan_row = rows[rows["player_name"] == "ST 03"].iloc[0]
    assert pd.isna(nan_row["value"]) and pd.isna(nan_row["percentile"])
    assert (rows["sample_size"] == 21).all() and (rows["group_size"] == 22).all()


def test_small_groups_are_flagged() -> None:
    result = compute_percentiles(_stats(per_group=MIN_GROUP_SIZE - 1))
    assert result["small_group"].all()
    assert not compute_percentiles(_stats(per_group=MIN_GROUP_SIZE))["small_group"].any()


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    tmp_path = tmp_path_factory.mktemp("analytics")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "processed_dir", tmp_path / "processed")
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    load_into(eng, league_rows())
    normalize.run(SEASON, LEAGUE, engine=eng)
    yield eng
    patch.undo()


def test_run_stores_a_row_per_player_per_applicable_metric(engine: Engine) -> None:
    for _ in range(2):  # idempotent
        result = percentiles.run(SEASON, LEAGUE, engine=engine)
    outfield = sum(m.scope == "outfield" for m in METRICS)
    gk = sum(m.scope == "gk" for m in METRICS)
    assert len(result) == 154 * outfield + 22 * gk
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM player_percentiles")).scalar() == len(result)
        assert (
            conn.execute(text("SELECT COUNT(DISTINCT player_id) FROM player_percentiles")).scalar()
            == 176
        )
