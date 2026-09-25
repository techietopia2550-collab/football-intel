"""Similarity test set (`build_test_pairs`) and threshold evaluation (`evaluate_thresholds`)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine

from src.analytics import archetypes as arch
from src.analytics import build_test_pairs as btp
from src.analytics import evaluate_thresholds as ev
from src.analytics.similarity import find_similar
from src.transform import normalize
from src.utils.config import settings
from tests.analytics_fixtures import SEASON, league_rows, load_into


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    monkeypatch.setattr(arch, "OUTFIELD_LABELS", {0: "Low", 1: "Mid", 2: "High"})
    monkeypatch.setattr(arch, "GK_LABELS", {0: "Keeper A", 1: "Keeper B"})
    eng = create_engine(f"sqlite:///{tmp_path / 'pairs.db'}")
    load_into(eng, league_rows(per_group=22))
    normalize.run(SEASON, engine=eng)
    arch.rebuild(SEASON, k=3, gk_k=2, engine=eng)
    return eng


def test_pairs_are_outfield_blank_and_score_exactly_as_find_similar(engine: Engine) -> None:
    pairs = btp.build_pairs(engine, SEASON, n=9)
    assert list(pairs.columns) == btp.COLUMNS and len(pairs) == 9
    assert pairs["label"].eq("").all()
    assert (pairs["archetype_a"] == pairs["archetype_b"]).all()
    assert pairs.groupby("archetype_a").size().eq(3).all()
    assert "Keeper" not in " ".join(pairs["archetype_a"])
    assert not (pairs["player_a_id"] == pairs["player_b_id"]).any()
    for r in pairs.head(3).itertuples():
        res = find_similar(r.player_a, SEASON, n=500, engine=engine)
        shown = {p.player_id: p.similarity for p in res.similar}[r.player_b_id]
        assert shown == pytest.approx(r.cosine_score, abs=1e-4)


def test_pairs_need_a_multiple_of_the_archetypes(engine: Engine) -> None:
    with pytest.raises(ValueError, match="multiple"):
        btp.build_pairs(engine, SEASON, n=10)


def test_labelled_file_is_never_overwritten_without_force(tmp_path: Path) -> None:
    path = tmp_path / "set.csv"
    df = pd.DataFrame({"pair_id": [1], "label": [""]})
    btp.write_test_set(df, path)
    btp.write_test_set(df, path)  # blank labels: fine to regenerate
    df.assign(label="similar").to_csv(path, index=False)
    with pytest.raises(FileExistsError):
        btp.write_test_set(df, path)
    btp.write_test_set(df, path, force=True)
    assert not btp.has_labels(path)


def _synthetic(path: Path, blank: int = 0) -> Path:
    """100 pairs (the real design's size): similar iff score >= 0.70, bar two noisy labels."""
    scores = np.round(np.linspace(0.30, 0.99, 100), 4)
    labels = np.where(scores >= 0.70, ev.SIMILAR, ev.NOT_SIMILAR).astype(object)
    labels[[5, 90]] = [ev.SIMILAR, ev.NOT_SIMILAR]
    labels[:blank] = ""
    pd.DataFrame({"pair_id": range(1, 101), "cosine_score": scores, "label": labels}).to_csv(
        path, index=False
    )
    return path


def test_unlabelled_csv_stops_with_the_ready_message(tmp_path: Path) -> None:
    path = tmp_path / "set.csv"
    pd.DataFrame({"pair_id": [1, 2], "cosine_score": [0.5, 0.9], "label": ["", ""]}).to_csv(
        path, index=False
    )
    with pytest.raises(ev.NotLabelled, match="CSV ready for labeling"):
        ev.run(path, tmp_path / "docs")
    assert not (tmp_path / "docs").exists()


def test_partial_labels_need_the_flag(tmp_path: Path) -> None:
    path = _synthetic(tmp_path / "set.csv", blank=3)
    with pytest.raises(ev.NotLabelled, match="unlabelled"):
        ev.run(path, tmp_path / "docs")
    _, _, md, _ = ev.run(path, tmp_path / "docs", allow_partial=True)
    assert "3 rows were unlabelled" in md.read_text(encoding="utf-8")


def test_bad_label_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "set.csv"
    pd.DataFrame({"pair_id": [1], "cosine_score": [0.8], "label": ["yes"]}).to_csv(
        path, index=False
    )
    with pytest.raises(ValueError, match="similar"):
        ev.load_labelled(path)


def test_metrics_report_and_chart_on_synthetic_labels(tmp_path: Path) -> None:
    path = _synthetic(tmp_path / "set.csv")
    table, rec, md, chart = ev.run(path, tmp_path / "docs")
    df, _ = ev.load_labelled(path)
    at70 = table.set_index("threshold").loc[0.70]
    above = df[df["cosine_score"] >= 0.70]
    assert at70["n_above"] == len(above)
    assert at70["precision"] == pytest.approx(above["similar"].mean())
    assert at70["recall"] == pytest.approx(above["similar"].sum() / df["similar"].sum())
    p, r = at70["precision"], at70["recall"]
    assert at70["f1"] == pytest.approx(2 * p * r / (p + r))
    assert list(table["threshold"]) == list(ev.THRESHOLDS)
    # labels flip at 0.70; [0.65, 0.75) is half similar, so Fair (50%) starts at 0.65, and the
    # local reading keeps it from being carried lower by the similar pairs above
    assert rec.cuts == {"Strong": 0.70, "Good": 0.70, "Fair": 0.65}
    assert rec.keep_weak
    text = md.read_text(encoding="utf-8")
    assert "| 0.85 |" in text and "Recommended tiers" in text
    assert chart.exists() and chart.stat().st_size > 0


def test_wilson_interval() -> None:
    lo, hi = ev.wilson(9, 10)
    assert 0.55 < lo < 0.6 and 0.98 < hi <= 1.0
    assert all(np.isnan(x) for x in ev.wilson(0, 0))


def test_labelled_test_set_is_complete() -> None:
    df, blank = ev.load_labelled(btp.default_path())
    assert blank == 0 and len(df) == 100
    assert set(df["label"]) == {ev.SIMILAR, ev.NOT_SIMILAR}


def test_app_tiers_are_the_measured_ones() -> None:
    """design.MATCH_TIERS must be what the evaluator recommends from the labelled CSV."""
    from src.app import design

    df, _ = ev.load_labelled(btp.default_path())
    rec = ev.recommend(ev.threshold_table(df), df)
    measured = {tier: cut for tier, cut in rec.cuts.items() if cut is not None}
    assert {word: cut for word, cut, _ in design.MATCH_TIERS} == measured
    assert rec.keep_weak == (design.MATCH_TIER_BELOW[0] == "Weak")


def test_match_tier_edges() -> None:
    from src.app import design

    assert design.match_tier(0.60) == ("Fair", "neutral")
    assert design.match_tier(0.97) == ("Fair", "neutral")
    assert design.match_tier(0.5999) == ("Weak", "negative")
    assert design.match_tier(None) is None and design.match_tier(float("nan")) is None
    assert design.fmt_match(0.72) == "72% · Fair"
    assert design.fmt_match(float("nan")) == "–"
    assert "Weak" in design.match_tier_badge(0.3) and design.match_tier_badge(None) == ""


def test_cross_position_note() -> None:
    from src.app import design

    assert design.cross_position_note("AM", "W") == "cross-position (AM vs W)"
    assert design.cross_position_note("W", "W") == ""
    assert design.cross_position_note(None, "W") == ""
