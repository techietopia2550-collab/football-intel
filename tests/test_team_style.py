"""Team style axes, similarity and profile helpers, on hand-built events (no network, no DB)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.analytics import team_profile, team_similarity, team_style
from src.models.team import SimilarTeamsResult

A, B = {"id": 1, "name": "Alpha"}, {"id": 2, "name": "Bravo"}


def ev(team, kind, loc, minute=1, poss=1, pattern="Regular Play", period=1, **extra):
    return {
        "type": {"name": kind}, "team": team, "possession_team": team, "possession": poss,
        "play_pattern": {"name": pattern}, "period": period, "minute": minute, "second": 0,
        "location": loc, "player": {"id": 9, "name": "P"}, **extra,
    }  # fmt: skip


def pas(team, start, end, poss=1, complete=True, cross=False, **kw):
    p = {"end_location": end, "length": 10.0}
    if not complete:
        p["outcome"] = {"name": "Incomplete"}
    if cross:
        p["cross"] = True
    return {**ev(team, "Pass", start, poss=poss, **kw), "pass": p}


def shot(team, xg, minute=2, poss=1, pattern="Regular Play", shot_type="Open Play"):
    return {
        **ev(team, "Shot", [100.0, 40.0], minute=minute, poss=poss, pattern=pattern),
        "shot": {"statsbomb_xg": xg, "type": {"name": shot_type}},
    }


def test_possession_ppda_and_passing_shape() -> None:
    events = [
        pas(A, [30, 40], [50, 40]),  # +20 x, completed, cuts distance to goal by 20 -> progressive
        pas(A, [50, 10], [50, 70]),  # sideways
        pas(B, [40, 40], [30, 40], poss=2),  # B pass in own 60% (x <= 72)
        pas(B, [100, 40], [90, 40], poss=2),  # B pass outside the PPDA zone
        ev(A, "Interception", [80, 40], poss=2, interception={"outcome": {"name": "Won"}}),
        ev(A, "Foul Committed", [20, 40], poss=2),  # too deep for the pressing zone
    ]
    out = team_style.accumulate_match(events)
    a = team_style.style_from_counts(out["Alpha"])
    assert out["Alpha"].passes == 2 and out["Alpha"].opp_passes == 2
    assert a["possession_pct"] == pytest.approx(50.0)
    assert a["ppda"] == pytest.approx(1.0)  # 1 opponent pass in zone / 1 own action in zone
    assert a["direct_speed"] == pytest.approx(10.0)  # (+20 and 0) / 2
    assert a["progressive_pass_rate"] == pytest.approx(0.5)
    assert a["width"] == pytest.approx(15.0)  # y = 40 and 10 -> population std 15


def test_buildup_shots_setpieces_and_transition() -> None:
    events = [
        pas(A, [10, 40], [20, 40], poss=1, minute=1),
        pas(A, [20, 40], [30, 40], poss=1, minute=1),
        pas(A, [30, 40], [40, 40], poss=2, minute=3, cross=True, pattern="From Corner"),
        shot(A, 0.2, minute=1, poss=1),  # open play, shot 0 s after possession start
        shot(A, 0.3, minute=3, poss=2, pattern="From Corner", shot_type="Open Play"),
        shot(A, 0.5, minute=5, poss=3, shot_type="Penalty"),
        ev(B, "Duel", [90, 40], duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}),
    ]
    out = team_style.accumulate_match(events)
    a = team_style.style_from_counts(out["Alpha"])
    assert a["buildup_length"] == pytest.approx(1.5)  # 3 passes over 2 possessions with a pass
    assert a["shots_pm"] == 3 and a["xg_per_shot"] == pytest.approx(1.0 / 3)
    assert a["set_piece_xg_pct"] == pytest.approx(80.0)  # corner-phase + penalty = 0.8 of 1.0
    assert a["crosses_p90"] == 1
    assert out["Alpha"].transition_n == 1 and a["transition_secs"] == pytest.approx(0.0)
    # Bravo's high tackle counts as a defensive action at x = 90
    assert team_style.style_from_counts(out["Bravo"])["def_line_height"] == pytest.approx(90.0)


def test_shootout_events_are_ignored() -> None:
    out = team_style.accumulate_match([pas(A, [10, 40], [20, 40], period=5)])
    assert out == {}


def test_empty_denominators_are_nan() -> None:
    a = team_style.style_from_counts(team_style.TeamCounts())
    assert all(math.isnan(v) for v in a.values())


def _style_table() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for i in range(12):
        rows.append({"team_name": f"T{i}", "league": "L", "season": "2015", "matches_used": 38,
                     "league_position": i + 1, "points": 90 - i, "played": 38,
                     **{c: float(v) for c, v in zip(team_style.AXIS_COLUMNS, rng.normal(size=12), strict=True)}})  # fmt: skip
    df = pd.DataFrame(rows)
    df.loc[1, list(team_style.AXIS_COLUMNS)] = df.loc[0, list(team_style.AXIS_COLUMNS)] * 1.1  # T1 ~ T0
    return df


def test_similarity_ranks_the_near_copy_first() -> None:
    res = team_similarity.find_similar_teams("T0", 2015, n=5, style=_style_table())
    assert isinstance(res, SimilarTeamsResult)
    assert res.similar[0].team_name == "T1" and res.similar[0].similarity > 0.95
    assert len(res.similar) == 5 and "T0" not in {s.team_name for s in res.similar}
    with pytest.raises(ValueError):
        team_similarity.find_similar_teams("Nobody", 2015, style=_style_table())


def test_zscores_are_standardised_and_constant_axis_is_zero() -> None:
    df = _style_table()
    df["width"] = 5.0
    z = team_similarity.zscores(df)
    assert z["possession_pct"].mean() == pytest.approx(0, abs=1e-9)
    assert z["possession_pct"].std(ddof=0) == pytest.approx(1)
    assert (z["width"] == 0).all()


def test_axis_scores_flip_and_headlines() -> None:
    df = _style_table()
    df["ppda"] = np.arange(12, dtype=float)  # T0 lowest PPDA = presses hardest
    scores = team_profile.axis_scores(df)
    assert scores.loc[0, "ppda"] == 100 and scores.loc[11, "ppda"] == 0
    p = team_profile.team_profile("T0", "2015", style=df)
    assert "High press (low PPDA)" in p.headline_facts and len(p.headline_facts) <= 3
    assert team_profile.headline_facts({a: 50.0 for a in team_style.AXIS_COLUMNS}) == [
        "Close to the average of the 80 teams on every axis"
    ]
