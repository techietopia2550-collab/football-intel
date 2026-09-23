"""The similarity engine: z-scoring, PCA, cosine ranking, filters and explanations.

The league is synthetic. Central midfielders come in two well-separated styles (ball-winners and
creators), so the right neighbours are known in advance and no real player is being tuned for.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine

from src.analytics.similarity import (
    EXCLUDED_FEATURES,
    MAX_MISSING_SHARE,
    VARIANCE_TARGET,
    AgeDataUnavailable,
    cosine,
    fit_group,
    group_features,
    pair_contributions,
)
from src.analytics.similarity import (
    find_similar as _find_similar,
)
from src.models.similarity import SimilarityFilters
from src.transform.normalize import INSUFFICIENT_TABLE, season_table_name
from tests.analytics_fixtures import LEAGUE, SEASON

# These tests cover the position-group behaviour; archetype search is in test_archetypes.py.
find_similar = partial(_find_similar, use_archetypes=False)

WINNER_METRICS = {"tackles_p90", "interceptions_p90", "ball_recoveries_p90", "pressures_p90"}
CREATOR_METRICS = {"key_passes_p90", "xa_p90", "assists_p90", "passes_into_box_p90"}


def _group_frame(
    group: str, names: list[str], styles: list[str], seed: int, teams: list[str] | None = None
) -> pd.DataFrame:
    """Players of one fine group. `styles` is 'win' or 'create' per player."""
    rng = np.random.default_rng(seed)
    rows = []
    for i, (name, style) in enumerate(zip(names, styles, strict=True)):
        row: dict[str, object] = {
            "player_id": f"statsbomb:{group}{i}", "player_name": name,
            "team": (teams or ["Team A", "Team B", "Team C"])[i % 3],
            "position_group_fine": group, "position_group_fine_share": 1.0,
            "position_group": "MID", "position_group_share": 1.0,
            "minutes_total": 1000.0 + 100 * i, "season": SEASON, "league": LEAGUE,
            "source": "statsbomb",
        }  # fmt: skip
        for metric in group_features(group):
            base = rng.normal(0.0, 0.4)
            if style == "win" and metric in WINNER_METRICS:
                base += 3.0
            if style == "create" and metric in CREATOR_METRICS:
                base += 3.0
            row[metric] = 10.0 + 2.0 * base  # a positive per-90-like value
        rows.append(row)
    return pd.DataFrame(rows)


def _league() -> pd.DataFrame:
    names = [f"Winner {i}" for i in range(12)] + [f"Creator {i}" for i in range(12)]
    styles = ["win"] * 12 + ["create"] * 12
    cm = _group_frame("CM", names, styles, seed=1)
    st = _group_frame("ST", [f"Striker {i}" for i in range(10)], ["win"] * 10, seed=2)
    return pd.concat([cm, st], ignore_index=True)


@pytest.fixture()
def engine(tmp_path: Path) -> Engine:
    eng = create_engine(f"sqlite:///{tmp_path / 'sim.db'}")
    _league().to_sql(season_table_name(SEASON), eng, index=False)
    return eng


# --- the method --------------------------------------------------------------------------------


def test_features_come_from_the_position_group_and_exclude_rare_event_counts() -> None:
    cm = group_features("CM")
    assert "tackles_p90" in cm and not set(cm) & EXCLUDED_FEATURES
    assert all(m.startswith("gk_") for m in group_features("GK"))  # goalkeepers: own metrics


def test_z_scores_are_within_the_group_and_pca_keeps_the_target_variance() -> None:
    members = _league().query("position_group_fine == 'CM'").reset_index(drop=True)
    model = fit_group(members, "CM")
    assert np.allclose(model.z.mean(axis=0), 0, atol=1e-9)
    assert np.allclose(model.z.std(axis=0), 1, atol=1e-9)  # population standard deviation
    assert model.explained_variance >= VARIANCE_TARGET
    # one fewer component would not have been enough
    ratios = model.pca.explained_variance_ratio_
    assert ratios[:-1].sum() < VARIANCE_TARGET or len(ratios) == 1


def test_a_feature_empty_for_much_of_the_group_is_dropped_and_a_few_gaps_are_imputed() -> None:
    members = _league().query("position_group_fine == 'CM'").reset_index(drop=True)
    many = int(len(members) * MAX_MISSING_SHARE) + 1
    members.loc[: many - 1, "dribbles_p90"] = np.nan  # over the limit: dropped
    members.loc[0, "carries_p90"] = np.nan  # one gap: kept, imputed at the group mean
    model = fit_group(members, "CM")
    assert "dribbles_p90" in model.dropped and "dribbles_p90" not in model.features
    j = model.features.index("carries_p90")
    assert "carries_p90" not in model.dropped and model.z[0, j] == 0.0


def test_a_feature_with_no_variance_is_dropped() -> None:
    members = _league().query("position_group_fine == 'CM'").reset_index(drop=True)
    members["pass_completion_pct"] = 80.0
    dropped = fit_group(members, "CM").dropped
    assert dropped["pass_completion_pct"] == "no variance in the group"


def test_cosine_of_a_zero_vector_is_zero_not_nan() -> None:
    out = cosine(np.zeros(3), np.array([[1.0, 0, 0], [0, 1.0, 0]]))
    assert list(out) == [0.0, 0.0]


def test_the_shared_contributions_add_up_to_the_similarity() -> None:
    members = _league().query("position_group_fine == 'CM'").reset_index(drop=True)
    model = fit_group(members, "CM")
    sims = cosine(model.scores[0], model.scores)
    for other in (1, 5, 13, 20):
        shared, _ = pair_contributions(model, 0, other)
        assert shared.sum() == pytest.approx(sims[other], abs=1e-9)


def test_differences_are_feature_deltas_through_the_loadings() -> None:
    members = _league().query("position_group_fine == 'CM'").reset_index(drop=True)
    model = fit_group(members, "CM")
    _, differing = pair_contributions(model, 0, 15)  # a winner vs a creator
    style = [j for j, f in enumerate(model.features) if f in WINNER_METRICS | CREATOR_METRICS]
    other = [j for j in range(len(model.features)) if j not in style]
    # the styles differ on their own metrics, so those carry more of the difference on average
    # (the other metrics still differ a little between any two individuals: noise)
    assert np.abs(differing[style]).mean() > np.abs(differing[other]).mean()
    assert np.sign(differing[[model.features.index("tackles_p90")]])[0] < 0  # creator has fewer


# --- find_similar ------------------------------------------------------------------------------


def test_the_nearest_players_share_the_targets_style(engine: Engine) -> None:
    result = find_similar("Winner 0", SEASON, n=8, league=LEAGUE, engine=engine)
    assert [p.rank for p in result.similar] == list(range(1, 9))
    assert all(p.player_name.startswith("Winner") for p in result.similar[:8])
    sims = [p.similarity for p in result.similar]
    assert sims == sorted(sims, reverse=True) and all(-1 <= s <= 1 for s in sims)
    creator = find_similar("Creator 0", SEASON, n=8, league=LEAGUE, engine=engine)
    assert all(p.player_name.startswith("Creator") for p in creator.similar)


def test_target_is_never_returned_and_only_the_same_position_group_is_compared(
    engine: Engine,
) -> None:
    result = find_similar("Winner 0", SEASON, n=50, league=LEAGUE, engine=engine)
    assert "Winner 0" not in {p.player_name for p in result.similar}
    assert {p.position_group_fine for p in result.similar} == {"CM"}
    assert not any(p.player_name.startswith("Striker") for p in result.similar)
    assert result.group_size == 24 and result.candidates_considered == 23
    assert len(result.similar) == 23  # n larger than the group just returns everyone


def test_explanations_name_the_styles_metrics(engine: Engine) -> None:
    top = find_similar("Winner 0", SEASON, n=1, league=LEAGUE, engine=engine).similar[0]
    assert len(top.shared_metrics) == 3 and len(top.differing_metrics) == 3
    contributions = [m.contribution for m in top.shared_metrics]
    assert contributions == sorted(contributions, reverse=True)
    deltas = [abs(m.contribution) for m in top.differing_metrics]
    assert deltas == sorted(deltas, reverse=True)
    # raw values are reported alongside the z-scores
    assert all(m.target_value is not None for m in top.shared_metrics)


def test_result_is_deterministic(engine: Engine) -> None:
    a = find_similar("Winner 3", SEASON, league=LEAGUE, engine=engine)
    b = find_similar("Winner 3", SEASON, league=LEAGUE, engine=engine)
    assert a.model_dump() == b.model_dump()


def test_result_records_how_it_was_built(engine: Engine) -> None:
    r = find_similar("Winner 0", SEASON, league=LEAGUE, engine=engine)
    assert r.features_used and not set(r.features_used) & EXCLUDED_FEATURES
    assert r.explained_variance >= VARIANCE_TARGET and r.n_components >= 1
    assert r.position_group_fine == "CM" and r.season == SEASON


# --- filters -----------------------------------------------------------------------------------


def test_same_team_only_and_exclude_same_team(engine: Engine) -> None:
    same = find_similar("Winner 0", SEASON, n=20, filters={"same_team_only": True},
                        league=LEAGUE, engine=engine)  # fmt: skip
    assert same.similar and {p.team for p in same.similar} == {same.target_team}
    other = find_similar("Winner 0", SEASON, n=20, filters={"exclude_same_team": True},
                         league=LEAGUE, engine=engine)  # fmt: skip
    assert other.similar and same.target_team not in {p.team for p in other.similar}


def test_contradictory_team_filters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SimilarityFilters(same_team_only=True, exclude_same_team=True)


def test_min_minutes_filter(engine: Engine) -> None:
    result = find_similar("Winner 0", SEASON, n=50, filters=SimilarityFilters(min_minutes=2000),
                          league=LEAGUE, engine=engine)  # fmt: skip
    assert result.similar and all(p.minutes_total >= 2000 for p in result.similar)


def test_filters_narrow_the_ranking_but_not_the_model(engine: Engine) -> None:
    plain = find_similar("Winner 0", SEASON, n=50, league=LEAGUE, engine=engine)
    filtered = find_similar("Winner 0", SEASON, n=50, filters={"min_minutes": 1500},
                            league=LEAGUE, engine=engine)  # fmt: skip
    plain_scores = {p.player_id: p.similarity for p in plain.similar}
    assert plain.n_components == filtered.n_components
    for p in filtered.similar:
        assert p.similarity == plain_scores[p.player_id]  # same score with or without the filter


def test_max_age_without_age_data_fails_loudly_instead_of_being_ignored(engine: Engine) -> None:
    pd.DataFrame(columns=["player_id", "canonical_name", "birth_year"]).to_sql(
        "players", engine, index=False
    )
    with pytest.raises(AgeDataUnavailable, match="birth"):
        find_similar("Winner 0", SEASON, filters={"max_age": 25}, league=LEAGUE, engine=engine)


def test_max_age_uses_birth_years_when_they_exist(engine: Engine) -> None:
    ids = _league().query("position_group_fine == 'CM'")["player_id"]
    born = {pid: (1995 if i % 2 == 0 else 1980) for i, pid in enumerate(ids)}  # 20 or 35 in 2015
    pd.DataFrame(
        {"player_id": list(born), "canonical_name": list(born), "birth_year": list(born.values())}
    ).to_sql("players", engine, index=False)
    result = find_similar("Winner 0", SEASON, n=50, filters={"max_age": 25}, league=LEAGUE,
                          engine=engine)  # fmt: skip
    assert result.similar
    assert all(born[p.player_id] == 1995 for p in result.similar)


# --- errors and warnings -----------------------------------------------------------------------


def test_unknown_ambiguous_and_invalid_requests_raise(engine: Engine) -> None:
    with pytest.raises(ValueError, match="No player"):
        find_similar("Nobody", SEASON, league=LEAGUE, engine=engine)
    with pytest.raises(ValueError, match="ambiguous"):
        find_similar("Winner", SEASON, league=LEAGUE, engine=engine)
    with pytest.raises(ValueError, match="at least 1"):
        find_similar("Winner 0", SEASON, n=0, league=LEAGUE, engine=engine)


def test_a_player_below_the_minutes_threshold_is_not_compared(engine: Engine) -> None:
    pd.DataFrame(
        [{"player_id": "statsbomb:x", "player_name": "Fringe Man", "team": "Team A",
          "position_group_fine": "CM", "season": SEASON, "league": LEAGUE,
          "reason": "minutes_total 200.0 < 900"}]
    ).to_sql(INSUFFICIENT_TABLE, engine, index=False)  # fmt: skip
    with pytest.raises(ValueError, match="below the minutes threshold"):
        find_similar("Fringe Man", SEASON, league=LEAGUE, engine=engine)


def test_small_groups_and_mixed_roles_are_warned_about(engine: Engine) -> None:
    result = find_similar("Striker 0", SEASON, league=LEAGUE, engine=engine)  # 10 strikers
    assert any("coarse" in w for w in result.warnings)
    frame = _league()
    frame.loc[frame["player_name"] == "Winner 0", "position_group_fine_share"] = 0.5
    frame.to_sql(season_table_name(SEASON), engine, index=False, if_exists="replace")
    mixed = find_similar("Winner 0", SEASON, league=LEAGUE, engine=engine)
    assert any("Mixed role" in w for w in mixed.warnings)
