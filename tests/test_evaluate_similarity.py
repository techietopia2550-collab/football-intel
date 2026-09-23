"""The v2 evaluation helpers: the verdict rule, reading the stored v1 file, and group summaries."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import create_engine

from src.analytics import evaluate_similarity as ev
from src.models.similarity import (
    MetricContribution,
    SimilarityFilters,
    SimilarityResult,
    SimilarPlayer,
)
from src.transform.normalize import season_table_name
from tests.analytics_fixtures import LEAGUE, SEASON
from tests.test_analytics_similarity import _league


def _result(name: str, scores: list[float]) -> SimilarityResult:
    contribution = MetricContribution(
        metric="tackles_p90", target_value=1.0, candidate_value=1.0, target_z=1.0,
        candidate_z=1.0, contribution=0.5,
    )  # fmt: skip
    similar = [
        SimilarPlayer(
            rank=i, player_id=f"p{i}", player_name=f"Other {i}", team="T",
            position_group_fine="CM", minutes_total=1000.0, similarity=s,
            shared_metrics=[contribution], differing_metrics=[contribution],
        )
        for i, s in enumerate(scores, start=1)
    ]  # fmt: skip
    return SimilarityResult(
        target_id="t", target_name=name, target_team="T", season=SEASON, league=LEAGUE,
        pool_leagues=[LEAGUE], position_group_fine="CM", position_group_fine_share=1.0,
        group_size=30, candidates_considered=29, features_used=["tackles_p90"],
        features_dropped={}, n_components=1, explained_variance=0.9, filters=SimilarityFilters(),
        warnings=[], similar=similar,
    )  # fmt: skip


def test_verdict_needs_top1_and_top10_mean_to_move_together() -> None:
    base = _result("x", [0.60, 0.50, 0.40])
    assert ev.verdict(base, _result("x", [0.70, 0.60, 0.50])) == "improved"
    assert ev.verdict(base, _result("x", [0.50, 0.40, 0.30])) == "worse"
    assert ev.verdict(base, _result("x", [0.62, 0.52, 0.42])) == "same"  # within the +-0.05 band
    assert (
        ev.verdict(base, _result("x", [0.80, 0.30, 0.20])) == "same"
    )  # top-1 up, top-10 mean down
    assert ev.verdict(base, _result("x", [0.60, 0.50, 0.40])) == "same"


def test_top10_mean() -> None:
    assert ev.top10_mean(_result("x", [0.2, 0.4, 0.6])) == pytest.approx(0.4)


def test_stored_v1_scores_are_read_from_the_markdown(tmp_path: Path) -> None:
    md = tmp_path / "v1.md"
    md.write_text(
        "# Title\n\n## Petr Čech (Arsenal), GK\n\n| # | Player | Team | Similarity | S | D |\n"
        "|---|---|---|---|---|---|\n| 1 | Hugo Lloris | Spurs | 0.874 | a | b |\n"
        "| 2 | Adrián | West Ham | 0.824 | a | b |\n\n"
        "## Wes Morgan (Leicester City), CB\n\n| 1 | Jonas Olsson | WBA | -0.120 | a | b |\n",
        encoding="utf-8",
    )
    assert ev.stored_v1_top_scores(md) == {"Petr Čech": 0.874, "Wes Morgan": -0.12}


def test_check_v1_reproduces_reports_differences(tmp_path: Path) -> None:
    md = tmp_path / "v1.md"
    md.write_text("## Anna (T), CM\n\n| 1 | X | T | 0.500 | a | b |\n", encoding="utf-8")
    assert ev.check_v1_reproduces({"Anna": _result("Anna", [0.5004])}, md) == []
    diffs = ev.check_v1_reproduces({"Anna": _result("Anna", [0.61])}, md)
    assert len(diffs) == 1 and "Anna" in diffs[0]
    assert ev.check_v1_reproduces({"Anna": _result("Anna", [0.5])}, tmp_path / "none.md")


def test_group_summaries_report_size_components_and_variance(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    _league().to_sql(season_table_name(SEASON), engine, index=False)
    got = ev.group_summaries(engine, SEASON, None)
    assert set(got) == {"CM", "ST"}  # groups with fewer than 3 players are skipped
    assert got["CM"].size == 24 and got["ST"].size == 10
    assert got["CM"].variance >= ev.VARIANCE_TARGET and got["CM"].components >= 1
    assert isinstance(got["CM"].features, int) and not np.isnan(got["CM"].variance)
    other = ev.group_summaries(engine, SEASON, "ESP-La Liga")  # a league with no rows
    assert other == {}
