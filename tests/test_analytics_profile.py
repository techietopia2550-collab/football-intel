"""Strength / weakness profiles."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine

from src.analytics import percentiles
from src.analytics.metrics import METRICS_BY_NAME
from src.analytics.profile import RELEVANT_METRICS, classify, profile
from src.transform import normalize
from src.utils.config import settings
from tests.analytics_fixtures import LEAGUE, SEASON, league_rows, load_into, player_rows


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    tmp_path = tmp_path_factory.mktemp("analytics")
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "processed_dir", tmp_path / "processed")
    rows = league_rows()
    # one accented name, and one player under the threshold
    rows.loc[rows["player_name"] == "ST 21", "player_name"] = "Zoë Kanté"
    extra = pd.DataFrame(player_rows(900, "Bench Warmer", "Team 0", "ST", matches=3, goals=1))
    eng = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    load_into(eng, pd.concat([rows, extra], ignore_index=True))
    normalize.run(SEASON, LEAGUE, engine=eng)
    percentiles.run(SEASON, LEAGUE, engine=eng)
    yield eng
    patch.undo()


def _names(items: list[dict[str, object]]) -> list[str]:
    return [str(i["metric"]) for i in items]


def test_relevant_metrics_exist_and_respect_scope() -> None:
    for group, names in RELEVANT_METRICS.items():
        for name in names:
            assert (METRICS_BY_NAME[name].scope == "gk") == (group == "GK"), (group, name)


def test_best_forward_has_attacking_strengths(engine: Engine) -> None:
    p = profile("kante", SEASON, LEAGUE, engine)  # accent- and case-insensitive
    assert (p["player_name"], p["position_group_fine"], p["qualified"]) == ("Zoë Kanté", "ST", True)
    assert {"goals_p90", "xg_p90", "shots_p90"} <= set(_names(p["strengths"]))
    assert p["weaknesses"] == []
    scores = [i["score"] for i in p["strengths"]]
    assert scores == sorted(scores, reverse=True)


def test_worst_forward_has_weaknesses(engine: Engine) -> None:
    p = profile("ST 00", SEASON, LEAGUE, engine)
    assert "goals_p90" in _names(p["weaknesses"]) and p["strengths"] == []


def test_irrelevant_metrics_are_never_judged(engine: Engine) -> None:
    p = profile("CB 00", SEASON, LEAGUE, engine)  # the lowest key passes among centre-backs
    judged = _names(p["strengths"] + p["weaknesses"] + p["neutral"] + p["unranked"])
    assert "key_passes_p90" not in judged and "goals_p90" not in judged
    assert not any(m.startswith("gk_") for m in judged)


def test_descriptive_metrics_are_always_neutral(engine: Engine) -> None:
    p = profile("kante", SEASON, LEAGUE, engine)
    judged = _names(p["strengths"] + p["weaknesses"])
    assert "fouls_committed_p90" not in judged and "pressure_duration_avg" not in judged
    assert "fouls_committed_p90" in _names(p["neutral"])


def test_lower_is_better_metrics_are_flipped() -> None:
    rows = pd.DataFrame(
        {
            "metric": ["gk_goals_conceded_p90"] * 2, "value": [0.5, 2.0],
            "percentile": [5.0, 95.0], "sample_size": [20, 20],
        }
    )  # fmt: skip
    for pct, bucket in ((5.0, "strengths"), (95.0, "weaknesses")):
        out = classify(rows.assign(percentile=pct).iloc[:1], "GK")
        assert _names(out[bucket]) == ["gk_goals_conceded_p90"]


def test_unranked_metric_is_reported_not_guessed() -> None:
    rows = pd.DataFrame(
        {"metric": ["shot_accuracy"], "value": [float("nan")], "percentile": [float("nan")],
         "sample_size": [20]}
    )  # fmt: skip
    assert _names(classify(rows, "ST")["unranked"]) == ["shot_accuracy"]


def test_player_under_the_minutes_threshold_is_not_profiled(engine: Engine) -> None:
    p = profile("Bench Warmer", SEASON, LEAGUE, engine)
    assert p["qualified"] is False and p["strengths"] == [] and p["weaknesses"] == []
    assert "270.0 < 900" in p["reason"]


def test_unknown_and_ambiguous_names_raise(engine: Engine) -> None:
    with pytest.raises(ValueError, match="No player"):
        profile("Nobody Here", SEASON, LEAGUE, engine)
    with pytest.raises(ValueError, match="ambiguous"):
        profile("Keeper", SEASON, LEAGUE, engine)


def test_goalkeeper_profile_uses_goalkeeper_metrics_only(engine: Engine) -> None:
    p = profile("Keeper 21", SEASON, LEAGUE, engine)
    judged = _names(p["strengths"] + p["weaknesses"] + p["neutral"])
    assert judged and all(m.startswith("gk_") for m in judged)
