"""Longest / best / worst game, hero counts, age, per-match heatmap and Wikipedia parsing.
Pure functions on small synthetic frames: no database and no network."""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.analytics import games, heatmaps
from src.app import player_images


def _matches(rows: list[dict]) -> pd.DataFrame:
    base = {
        "match_id": 0, "match_date": date(2015, 9, 1), "team": "A", "opponent": "B", "home": True,
        "team_goals": 1, "opponent_goals": 1, "minutes": 90.0, "position_played": "X",
        "goals": 0, "assists": 0, "xg": 0.0, "xa": 0.0, "progressive_passes": 0, "tackles": 0,
        "key_passes": 0, "interceptions": 0, "clearances": 0, "gk_saves": 0,
        "gk_goals_conceded": 0, "clean_sheet": 0.0, "save_pct": np.nan,
    }  # fmt: skip
    return pd.DataFrame([{**base, "match_id": i, **r} for i, r in enumerate(rows, start=1)])


def test_rating_uses_the_position_specific_counts() -> None:
    m = _matches([{"goals": 1, "assists": 1, "xg": 0.5, "xa": 0.25, "tackles": 9}]).iloc[0]
    assert games.rate_match(m, "ST") == pytest.approx(2.75)  # tackles ignored for attackers
    d = _matches([{"tackles": 2, "interceptions": 3, "clearances": 4, "goals": 5}]).iloc[0]
    assert games.rate_match(d, "CB") == 9
    c = _matches([{"progressive_passes": 5, "tackles": 1, "key_passes": 2}]).iloc[0]
    assert games.rate_match(c, "CM") == 8


def test_goalkeeper_rating_and_clean_sheet_bonus() -> None:
    g = _matches([{"gk_saves": 4, "clean_sheet": 1.0, "save_pct": 1.0}]).iloc[0]
    assert games.rate_match(g, "GK") == 4 + 3 + 2
    no_shots = _matches([{"clean_sheet": 1.0}]).iloc[0]  # save % unknown counts as 0
    assert games.rate_match(no_shots, "GK") == 3


def test_best_worst_longest_and_the_45_minute_rule() -> None:
    m = _matches([
        {"tackles": 5, "minutes": 60.0},                       # 1: rating 5
        {"tackles": 1, "interceptions": 1, "minutes": 90.0},   # 2: rating 2
        {"tackles": 0, "minutes": 10.0},                       # 3: rating 0 but only 10 minutes
        {"tackles": 9, "minutes": 90.0, "match_date": date(2016, 1, 1)},  # 4: best and longest
    ])  # fmt: skip
    pg = games.player_games(m, "FB")
    assert pg.best is not None and pg.best.match_id == 4 and pg.best.rating == 9
    assert pg.worst is not None and pg.worst.match_id == 2  # the 10-minute game is not eligible
    assert pg.longest is not None and pg.longest.match_id == 2  # 90 min, earlier date wins the tie
    assert pg.longest.rating is None
    assert [label for label, _ in pg.best.components] == ["Tackles", "Interceptions", "Clearances"]


def test_worst_is_none_when_no_match_reaches_45_minutes() -> None:
    pg = games.player_games(_matches([{"minutes": 20.0}, {"minutes": 30.0}]), "CB")
    assert pg.worst is None and pg.best is not None


def test_season_game_stats_outfield_and_goalkeeper() -> None:
    o = games.season_game_stats(_matches([{"goals": 2, "assists": 1}, {"goals": 0, "assists": 3}]), "W")
    assert (o.games, o.goals, o.assists, o.clean_sheets, o.saves) == (2, 2, 4, None, None)
    gk = games.season_game_stats(
        _matches([
            {"gk_saves": 3, "gk_goals_conceded": 1, "clean_sheet": 0.0},
            {"gk_saves": 5, "gk_goals_conceded": 0, "clean_sheet": 1.0},
        ]),
        "GK",
    )  # fmt: skip
    assert (gk.games, gk.clean_sheets, gk.saves, gk.goals_conceded) == (2, 1, 8, 1)
    assert gk.save_pct == pytest.approx(8 / 9 * 100) and gk.goals is None


def test_goalkeeper_with_no_shots_has_no_save_pct() -> None:
    gk = games.season_game_stats(_matches([{}]), "GK")
    assert gk.save_pct is None and gk.saves == 0


def test_age_on_season_start() -> None:
    start = date(2015, 8, 1)
    assert games.age_on(date(1989, 3, 21), start) == 26
    assert games.age_on(date(1989, 8, 1), start) == 26  # birthday on the day
    assert games.age_on(date(1989, 8, 2), start) == 25
    assert games.age_on(None, start) is None
    assert games.age_on(date(2020, 1, 1), start) is None


# ---- per-match heatmap ------------------------------------------------------------------------


def _event(pid: int, kind: str, x: float, y: float, period: int = 1) -> dict:
    return {"player": {"id": pid}, "type": {"name": kind}, "location": [x, y], "period": period}


def test_match_heatmaps_only_count_that_player_and_have_all_types(tmp_path, monkeypatch) -> None:
    events = [_event(7, "Pass", 5, 5), _event(7, "Pass", 115, 75, period=2), _event(8, "Pass", 60, 40)]
    path = tmp_path / "99.json"
    path.write_text(json.dumps(events), encoding="utf-8")
    monkeypatch.setattr(heatmaps, "events_cache_path", lambda match_id: path)
    grids = heatmaps.match_heatmaps(99, 7)
    assert grids is not None and set(grids) == set(heatmaps.HEATMAP_TYPES)
    assert all(g.shape == (8, 12) for g in grids.values())
    assert grids["all_actions"].sum() == 2 and grids["passing"].sum() == 2
    assert grids["all_actions"][0, 0] == 1 and grids["all_actions"][7, 11] == 1
    assert grids["all_actions_1h"].sum() == 1 and grids["all_actions_2h"].sum() == 1
    assert grids["shots"].sum() == 0


def test_match_heatmaps_none_when_events_are_not_cached(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(heatmaps, "events_cache_path", lambda match_id: tmp_path / "missing.json")
    assert heatmaps.match_heatmaps(1, 7) is None


def test_match_heatmaps_none_for_a_corrupt_cache_file(tmp_path, monkeypatch) -> None:
    bad = tmp_path / "1.json"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(heatmaps, "events_cache_path", lambda match_id: bad)
    assert heatmaps.match_heatmaps(1, 7) is None


# ---- Wikipedia / Wikidata parsing -------------------------------------------------------------


def _time_claim(value: str, precision: int) -> dict:
    return {"mainsnak": {"datavalue": {"value": {"time": value, "precision": precision}}}}


def test_birth_date_needs_day_precision() -> None:
    assert player_images.parse_birth_date({"P569": [_time_claim("+1989-03-21T00:00:00Z", 11)]}) == date(1989, 3, 21)
    assert player_images.parse_birth_date({"P569": [_time_claim("+1989-00-00T00:00:00Z", 9)]}) is None
    assert player_images.parse_birth_date({}) is None


def test_nationality_skips_ended_citizenships() -> None:
    def claim(qid: str, ended: bool) -> dict:
        c: dict = {"mainsnak": {"datavalue": {"value": {"id": qid}}}}
        if ended:
            c["qualifiers"] = {"P582": [{}]}
        return c

    assert player_images.parse_nationality_qid({"P27": [claim("Q1", True), claim("Q2", False)]}) == "Q2"
    assert player_images.parse_nationality_qid({}) is None


def test_team_slug_and_placeholder(tmp_path) -> None:
    assert player_images.team_slug("Atlético Madrid") == "atletico-madrid"
    assert player_images.team_slug("Paris Saint-Germain") == "paris-saint-germain"
    out = player_images.make_team_placeholder(tmp_path / "p.png", 64)
    assert out.exists()
