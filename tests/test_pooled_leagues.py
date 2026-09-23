"""Pooling several leagues: aggregation, percentiles, profiles, similarity and heatmaps.

Two synthetic leagues. Every outfield group has players in both, so a pooled group is the union.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, text

from src.analytics import heatmaps, percentiles
from src.analytics.metrics import OUTFIELD_GROUPS
from src.analytics.profile import profile
from src.analytics.similarity import find_similar
from src.transform import normalize
from src.utils.config import settings
from tests.analytics_fixtures import SEASON, load_into, player_rows

LEAGUE_A, LEAGUE_B = "ENG-Premier League", "ESP-La Liga"


def _league_rows(league: str, tag: str, first_pid: int, per_group: int, *, shift: float) -> list:
    """`per_group` players per outfield group in one league; stats rise with the index."""
    rows: list[dict[str, object]] = []
    pid = first_pid
    for group in OUTFIELD_GROUPS:
        for i in range(per_group):
            pid += 1
            rows += player_rows(
                pid, f"{tag} {group} {i:02d}", f"{tag} Team {i % 3}", group, league=league,
                first_match=1 if league == LEAGUE_A else 1000,
                goals=(i + shift) / 10, shots=2 + i / 10, tackles=1 + (i + shift) / 10,
                passes_attempted=50, passes_completed=30 + i, key_passes=(i + shift) / 10,
            )  # fmt: skip
    return rows


def _frame() -> pd.DataFrame:
    rows = _league_rows(LEAGUE_A, "A", 0, 12, shift=0) + _league_rows(
        LEAGUE_B, "B", 1000, 12, shift=0.5
    )
    # a mover: 6 matches in A, 4 in B (main league A), 90 minutes each -> 900 minutes in all
    rows += player_rows(5000, "Mover", "A Team 0", "CM", league=LEAGUE_A, matches=6, goals=0.3)
    rows += player_rows(
        5000, "Mover", "B Team 0", "CM", league=LEAGUE_B, matches=4, first_match=2000, goals=0.3
    )
    return pd.DataFrame(rows)


@pytest.fixture()
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(settings, "processed_dir", tmp_path / "processed")
    eng = create_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    load_into(eng, _frame())
    return eng


def test_default_is_pooled_across_leagues_and_a_league_restricts_it(engine: Engine) -> None:
    pooled = normalize.run(SEASON, engine=engine).qualifying
    assert set(pooled["league"]) == {LEAGUE_A, LEAGUE_B}
    assert len(pooled) == 7 * 12 * 2 + 1  # both leagues' players and the mover, once
    only_a = normalize.build_player_season_stats(
        normalize.load_match_stats(engine, SEASON, LEAGUE_A)
    ).qualifying
    assert set(only_a["league"]) == {LEAGUE_A}
    # the mover has only 540 minutes in league A, so he qualifies through pooling alone
    assert len(only_a) == 7 * 12 and "Mover" not in set(only_a["player_name"])


def test_a_player_in_two_leagues_is_one_row_with_his_main_league(engine: Engine) -> None:
    result = normalize.run(SEASON, engine=engine)
    mover = result.qualifying[result.qualifying["player_name"] == "Mover"]
    assert len(mover) == 1
    row = mover.iloc[0]
    assert (row["league"], row["n_leagues"], row["n_teams"]) == (LEAGUE_A, 2, 2)
    assert row["minutes_total"] == pytest.approx(900.0)  # all ten matches count
    assert "Mover" in set(result.multi_team["player_name"])
    assert list(result.multi_team.query("player_name == 'Mover'")["n_leagues"]) == [2]


def test_pooled_percentiles_rank_across_leagues(engine: Engine) -> None:
    normalize.run(SEASON, engine=engine)
    pooled = percentiles.run(SEASON, engine=engine)
    goals = pooled[(pooled["metric"] == "goals_p90") & (pooled["position_group_fine"] == "ST")]
    assert (goals["group_size"] == 24).all()  # 12 + 12, not 12 per league
    assert set(goals["league"]) == {LEAGUE_A, LEAGUE_B}
    assert set(goals["pool"]) == {f"{LEAGUE_A}|{LEAGUE_B}"}
    top = goals.sort_values("percentile").iloc[-1]
    assert top["player_name"] == "B ST 11"  # the highest goals value in either league

    per_league = percentiles.run(SEASON, LEAGUE_A, engine=engine)
    a_goals = per_league[
        (per_league["metric"] == "goals_p90") & (per_league["position_group_fine"] == "ST")
    ]
    assert (a_goals["group_size"] == 12).all()  # the same players, ranked among 12 instead


def test_rerunning_pooled_replaces_the_whole_season(engine: Engine) -> None:
    normalize.run(SEASON, engine=engine)
    for _ in range(2):
        result = percentiles.run(SEASON, engine=engine)
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM player_percentiles")).scalar()
    assert n == len(result)


def test_profile_finds_a_player_in_any_league_by_default(engine: Engine) -> None:
    normalize.run(SEASON, engine=engine)
    percentiles.run(SEASON, engine=engine)
    p = profile("B ST 11", SEASON, engine=engine)
    assert p["league"] == LEAGUE_B and p["qualified"]
    assert {"goals_p90"} <= {i["metric"] for i in p["strengths"]}
    with pytest.raises(ValueError, match="No player"):
        profile("B ST 11", SEASON, LEAGUE_A, engine)  # restricted to league A: not there


def test_similarity_pools_leagues_and_a_league_argument_restricts(engine: Engine) -> None:
    normalize.run(SEASON, engine=engine)
    pooled = find_similar("A CM 05", SEASON, n=50, engine=engine)
    assert pooled.pool_leagues == [LEAGUE_A, LEAGUE_B] and pooled.group_size == 25
    assert any(p.player_name.startswith("B ") for p in pooled.similar)  # cross-league neighbours

    only_a = find_similar("A CM 05", SEASON, n=50, league=LEAGUE_A, engine=engine)
    assert only_a.pool_leagues == [LEAGUE_A] and only_a.group_size == 13
    assert all(not p.player_name.startswith("B ") for p in only_a.similar)


def test_replace_season_rows_without_a_league_replaces_the_whole_season(tmp_path: Path) -> None:
    eng = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    old = pd.DataFrame(
        {
            "season": ["2015", "2015", "2014"],
            "league": [LEAGUE_A, LEAGUE_B, LEAGUE_A],
            "x": [1, 2, 3],
        }
    )
    old.to_sql("t", eng, index=False)
    new = pd.DataFrame({"season": ["2015"], "league": [LEAGUE_A], "x": [9]})
    normalize.replace_season_rows(eng, "t", new, "2015")  # pooled: all 2015 rows go
    assert sorted(pd.read_sql(text("SELECT x FROM t"), eng)["x"]) == [3, 9]
    normalize.replace_season_rows(eng, "t", new, "2015", LEAGUE_A)  # one league only
    assert sorted(pd.read_sql(text("SELECT x FROM t"), eng)["x"]) == [3, 9]


def test_heatmaps_cover_the_matches_of_every_league(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    normalize.run(SEASON, engine=engine)
    pd.DataFrame(
        {"player_id": [f"statsbomb:{i}" for i in (1, 1001)], "canonical_name": ["a", "b"],
         "statsbomb_id": ["1", "1001"]}
    ).to_sql("players", engine, if_exists="append", index=False)  # fmt: skip
    rows = [(LEAGUE_A, m) for m in range(1, 11)] + [(LEAGUE_B, m) for m in range(1000, 1010)]
    pd.DataFrame(
        [
            {"source": "statsbomb", "match_id": m, "season": SEASON, "league": lg,
             "match_date": date(2016, 1, 1), "home_team": "X", "away_team": "Y"}
            for lg, m in rows
        ]
    ).to_sql("matches", engine, if_exists="append", index=False)  # fmt: skip
    loaded: list[int] = []

    def load_events(match_id: int) -> list[dict[str, Any]]:
        loaded.append(match_id)
        pid = 1 if match_id < 1000 else 1001
        return [
            {"period": 1, "type": {"name": "Pass"}, "player": {"id": pid}, "location": [30, 40]}
        ]

    result = heatmaps.run(SEASON, engine=engine, load_events=load_events)
    assert sorted(loaded) == sorted(m for _, m in rows)  # matches of both leagues were read
    passing = result[result["heatmap_type"] == "passing"].set_index("player_id")
    assert passing.loc["statsbomb:1", "event_count"] == 10
    assert passing.loc["statsbomb:1001", "event_count"] == 10
    assert set(result["league"]) == {LEAGUE_A, LEAGUE_B}
