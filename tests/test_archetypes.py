"""Archetype clustering (`src/analytics/archetypes.py`) and archetype-based similarity."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text

from src.analytics import archetypes as arch
from src.analytics.metrics import METRICS
from src.analytics.similarity import archetype_features, find_similar
from src.transform import normalize
from src.utils.config import settings
from tests.analytics_fixtures import SEASON, league_rows, load_into

LABELS = {0: "Low", 1: "Mid", 2: "High"}
GK_LABELS = {0: "Keeper A", 1: "Keeper B"}


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    monkeypatch.setattr(arch, "OUTFIELD_LABELS", LABELS)
    monkeypatch.setattr(arch, "GK_LABELS", GK_LABELS)
    eng = create_engine(f"sqlite:///{tmp_path / 'arch.db'}")
    load_into(eng, league_rows(per_group=22))
    normalize.run(SEASON, engine=eng)
    return eng


def _blobs(rng: np.random.Generator) -> pd.DataFrame:
    """Three well-separated groups on the archetype features; position is deliberately mixed."""
    rows = []
    for centre in (-3.0, 0.0, 3.0):
        for _ in range(30):
            rows.append(rng.normal(centre, 0.3, len(arch.OUTFIELD_FEATURES)))
    df = pd.DataFrame(rows, columns=list(arch.OUTFIELD_FEATURES))
    df["player_id"] = [f"p{i}" for i in range(len(df))]
    df["player_name"] = df["player_id"]
    df["position_group_fine"] = ["CB", "W", "CM"] * 30  # unrelated to the blobs
    df["season"], df["league"], df["team"], df["source"] = SEASON, "L", "T", "statsbomb"
    return df


def test_features_are_real_common_metrics_and_no_position_label() -> None:
    names = {m.name for m in METRICS if m.scope == "outfield"}
    assert set(arch.OUTFIELD_FEATURES) <= names
    assert not any("position" in f for f in arch.OUTFIELD_FEATURES)
    assert not any(f.startswith("gk_") for f in arch.OUTFIELD_FEATURES)
    assert len(arch.OUTFIELD_FEATURES) == len(set(arch.OUTFIELD_FEATURES))


def test_zscore_drops_sparse_and_constant_features_and_imputes_the_mean() -> None:
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, np.nan], "b": [np.nan] * 3 + [1.0], "c": [5.0] * 4})
    z, kept, dropped = arch.zscore_matrix(df, ["a", "b", "c"])
    assert kept == ["a"] and set(dropped) == {"b", "c"}
    assert z[3, 0] == 0.0  # the empty value sits at the mean
    assert abs(np.nanmean(z[:3, 0])) < 1.0


def test_clusters_follow_the_metrics_not_the_position_label() -> None:
    df = _blobs(np.random.default_rng(1))
    model = arch.fit_pool(df, arch.OUTFIELD_FEATURES, 3, "outfield")
    truth = np.repeat([0, 1, 2], 30)
    # each blob is one cluster whatever the position label of its members was
    assert len({tuple(model.labels[truth == b]) for b in range(3)}) == 3
    for b in range(3):
        assert len(set(model.labels[truth == b])) == 1
    # ids run in order of the centroid's first principal component
    assert list(np.argsort(model.centers[:, 0])) == [0, 1, 2]


def test_confidence_is_a_margin_between_zero_and_one_and_low_on_a_boundary() -> None:
    df = _blobs(np.random.default_rng(2))
    between = df.iloc[[0]].copy()
    between[list(arch.OUTFIELD_FEATURES)] = -1.5  # halfway between the first two blobs
    between["player_id"] = between["player_name"] = "between"
    model = arch.fit_pool(
        pd.concat([df, between], ignore_index=True), arch.OUTFIELD_FEATURES, 3, "outfield"
    )
    out = arch.assignments(model, LABELS)
    assert out["cluster_confidence"].between(0, 1).all()
    assert (out["distance_to_centroid"] >= 0).all()
    inside = out[out["player_id"] != "between"]["cluster_confidence"].median()
    assert out.loc[out["player_id"] == "between", "cluster_confidence"].iloc[0] < inside


def test_assignments_refuse_an_unlabelled_cluster() -> None:
    model = arch.fit_pool(_blobs(np.random.default_rng(3)), arch.OUTFIELD_FEATURES, 3, "outfield")
    with pytest.raises(ValueError, match="No label"):
        arch.assignments(model, {0: "only one"})


def test_rebuild_stores_every_player_once_and_is_idempotent(engine: Engine) -> None:
    first = arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    stored = pd.read_sql(text(f"SELECT * FROM {arch.ARCHETYPES_TABLE}"), engine)
    season = pd.read_sql(
        text(f"SELECT player_id, position_group_fine FROM {normalize.season_table_name(SEASON)}"),
        engine,
    )
    assert len(stored) == len(first) == len(season)
    assert set(stored["player_id"]) == set(season["player_id"])
    assert stored["player_id"].is_unique
    gk_ids = set(season.loc[season["position_group_fine"] == "GK", "player_id"])
    keepers = stored[stored["player_id"].isin(gk_ids)]
    assert (keepers["pool"] == "gk").all() and (keepers["archetype_id"] >= arch.GK_ID_OFFSET).all()
    assert (stored[~stored["player_id"].isin(gk_ids)]["archetype_id"] < arch.GK_ID_OFFSET).all()
    arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    again = pd.read_sql(text(f"SELECT * FROM {arch.ARCHETYPES_TABLE}"), engine)
    assert len(again) == len(stored)
    pd.testing.assert_frame_equal(
        again.drop(columns="id").sort_values("player_id").reset_index(drop=True),
        stored.drop(columns="id").sort_values("player_id").reset_index(drop=True),
    )


def test_rebuild_needs_a_chosen_k(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arch, "CHOSEN_K", None)
    with pytest.raises(ValueError, match="not been chosen"):
        arch.rebuild(SEASON, engine=engine)


def test_sanity_check_reports_both_structural_failures() -> None:
    def table(ids: dict[str, int]) -> pd.DataFrame:
        return pd.DataFrame({"player_name": list(ids), "archetype_id": list(ids.values())})

    good = table({"Mesut Özil": 9, "David Silva": 9, "N'Golo Kanté": 3})
    assert arch.sanity_check(good) == []
    bad = table({"Mesut Özil": 9, "David Silva": 8, "N'Golo Kanté": 9})
    assert len(arch.sanity_check(bad)) == 2


def test_similarity_searches_the_archetype_across_position_groups(engine: Engine) -> None:
    arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    result = find_similar("CM 20", SEASON, n=10, engine=engine)
    assert result.pool_kind == "archetype" and result.archetype_label in LABELS.values()
    assert result.cluster_confidence is not None
    assert {p.position_group_fine for p in result.similar} - {"CM"}, "no other positions found"
    assert result.group_size < 7 * 22 and result.group_size > 10
    assert "CM 20" not in {p.player_name for p in result.similar}
    features = set(result.features_used)
    assert features <= set(archetype_features())


def test_similarity_can_still_use_the_position_group(engine: Engine) -> None:
    arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    result = find_similar("CM 20", SEASON, n=10, engine=engine, use_archetypes=False)
    assert result.pool_kind == "position_group" and result.archetype_id is None
    assert {p.position_group_fine for p in result.similar} == {"CM"}
    assert result.group_size == 22


def test_goalkeepers_are_searched_among_goalkeepers(engine: Engine) -> None:
    arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    result = find_similar("Keeper 10", SEASON, n=5, engine=engine)
    assert {p.position_group_fine for p in result.similar} == {"GK"}
    assert result.archetype_label in GK_LABELS.values()


def test_missing_archetype_table_is_an_error_not_a_silent_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    eng = create_engine(f"sqlite:///{tmp_path / 'none.db'}")
    load_into(eng, league_rows(per_group=22))
    normalize.run(SEASON, engine=eng)
    with pytest.raises(LookupError, match="archetypes"):
        find_similar("CM 20", SEASON, engine=eng)


def test_distinctive_table_ranks_by_absolute_distance_from_the_global_centroid() -> None:
    rng = np.random.default_rng(0)
    n = 200
    frame = pd.DataFrame(
        {"high": rng.normal(size=n), "low": rng.normal(size=n), "same": rng.normal(size=n)}
    )
    frame["archetype_id"] = [0] * 50 + [1] * 150
    frame.loc[frame["archetype_id"] == 0, "high"] += 3.0
    frame.loc[frame["archetype_id"] == 0, "low"] -= 2.0
    table = arch.distinctive_table(frame, 0, features=["high", "low", "same"])
    assert table["feature"].tolist() == ["high", "low", "same"]  # a LOW feature counts too
    assert table.loc[1, "archetype_z"] < 0
    assert (table["global_z"].abs() < 1e-9).all()
    assert table["distinctiveness"].is_monotonic_decreasing


def test_similarity_uses_only_the_archetypes_distinctive_features(engine: Engine) -> None:
    arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    weighted = find_similar("CM 20", SEASON, n=5, engine=engine, distinctive_n=3)
    full = find_similar("CM 20", SEASON, n=5, engine=engine, distinctive_n=None)
    assert 0 < len(weighted.features_used) <= 3
    assert set(weighted.features_used) <= set(full.features_used)
    assert len(full.features_used) > len(weighted.features_used)
    assert weighted.group_size == full.group_size  # same pool, different features


def test_distinctive_selection_does_not_touch_goalkeepers(engine: Engine) -> None:
    arch.rebuild(SEASON, k=3, gk_k=2, engine=engine)
    a = find_similar("Keeper 10", SEASON, n=5, engine=engine, distinctive_n=2)
    b = find_similar("Keeper 10", SEASON, n=5, engine=engine, distinctive_n=None)
    assert a.features_used == b.features_used
