"""Transfermarkt market values: name normalization, snapshot selection, club-restricted matching,
the unmatched path, and the read side (player value, squad sum). Offline, no CSVs needed."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine

from src.analytics import market_values as mv_read
from src.models.schema import init_db
from src.transform import market_values as mv


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("José Giménez", "jose gimenez"),
        ("N'Golo Kanté", "ngolo kante"),
        ("Jean-Philippe Mateta", "jean philippe mateta"),
        ("Filip Đorđević", "filip dordevic"),
        ("Martin Ødegaard", "martin odegaard"),
        ("Nelson Oliveira Jr.", "nelson oliveira"),
        ("Tim Howard II", "tim howard"),
        ("  Mesut   Özil ", "mesut ozil"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert mv.normalize_name(raw) == expected


def test_normalize_keeps_junior_as_a_name() -> None:
    assert mv.normalize_name("Júnior Moraes") == "junior moraes"


def _valuations() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "player_id": [1, 1, 1, 1, 2, 3],
            "date": ["2015-07-01", "2015-10-14", "2016-02-09", "2016-06-01", "2015-09-30", "2016-03-01"],
            "market_value_in_eur": [45e6, 60e6, 65e6, 70e6, 1e6, 2e6],
            "current_club_name": ["Wolfsburg", "Man City", "Man City", "Man City", "X", "Y"],
        }
    )


def test_snapshot_takes_latest_inside_window() -> None:
    snap = mv.select_snapshot(_valuations()).set_index("player_id")
    assert snap.loc[1, "market_value_in_eur"] == 65e6  # 2016-02-09, not the June value
    assert snap.loc[1, "date"] == pd.Timestamp("2016-02-09")


def test_snapshot_window_is_inclusive_and_excludes_outside() -> None:
    snap = mv.select_snapshot(_valuations())
    assert 3 in set(snap["player_id"])  # dated exactly on 2016-03-01
    assert 2 not in set(snap["player_id"])  # 2015-09-30 is a day early: no value, not a guess


def _candidates() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"tm_id": 10, "team": "Atlético Madrid", "league": "ESP-La Liga",
             "names": ("José María Giménez",)},
            {"tm_id": 11, "team": "Atlético Madrid", "league": "ESP-La Liga", "names": ("Koke",)},
            {"tm_id": 12, "team": "Real Madrid", "league": "ESP-La Liga", "names": ("Pepe",)},
            {"tm_id": 13, "team": "Napoli", "league": "ITA-Serie A", "names": ("Pepe Reina",)},
            {"tm_id": 14, "team": "Celta Vigo", "league": "ESP-La Liga", "names": ("Jonny Otto",)},
            {"tm_id": 20, "team": "Swansea City", "league": "ENG-Premier League",
             "names": ("Sung-yueng Ki",)},
            {"tm_id": 30, "team": "Chelsea", "league": "ENG-Premier League", "names": ("Oscar",)},
            {"tm_id": 31, "team": "Chelsea", "league": "ENG-Premier League",
             "names": ("Oscar Emboaba",)},
            {"tm_id": 40, "team": "Getafe", "league": "ESP-La Liga", "names": ("Juanfran Torres",)},
            {"tm_id": 41, "team": "Getafe", "league": "ESP-La Liga", "names": ("Juanfran Moreno",)},
        ]
    )  # fmt: skip


def _ours(rows: list[tuple[str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"player_id": pid, "player_name": n, "team": t, "team_split": f"{t} 2000",
          "league": "ESP-La Liga"} for pid, n, t in rows]
    )  # fmt: skip


def test_match_stages() -> None:
    ours = _ours([("a", "José Giménez", "Atlético Madrid"), ("b", "Koke", "Atlético Madrid"),
                  ("c", "Ki Sung-Yeung", "Swansea City")])  # fmt: skip
    res = mv.match_players(ours, _candidates()).set_index("player_id")
    assert res.loc["b", "match_method"] == "exact" and res.loc["b", "transfermarkt_id"] == 11
    assert res.loc["a", "match_method"] == "fuzzy" and res.loc["a", "transfermarkt_id"] == 10
    assert res.loc["c", "match_method"] == "fuzzy" and res.loc["c", "transfermarkt_id"] == 20


def test_no_match_across_a_club_conflict() -> None:
    # "Pepe" exists at Real Madrid; a "Pepe" profiled at Napoli must not take him.
    res = mv.match_players(_ours([("p", "Pepe", "Napoli")]), _candidates()).iloc[0]
    assert res["transfermarkt_id"] != 12


def test_unmatched_path_is_null_not_forced() -> None:
    # "Jonny Castro" is Transfermarkt's "Jonny Otto": a nickname the rules must not bridge.
    res = mv.match_players(_ours([("j", "Jonny Castro", "Celta Vigo")]), _candidates()).iloc[0]
    assert res["match_method"] == "unmatched"
    assert pd.isna(res["transfermarkt_id"])
    assert "no candidate" in res["match_note"]


def test_ambiguous_name_is_left_unmatched() -> None:
    # Two "Juanfran"s at one club tie on the same stage and score: never guess between them.
    res = mv.match_players(_ours([("j", "Juanfran", "Getafe")]), _candidates()).iloc[0]
    assert res["match_method"] == "unmatched"
    assert res["match_note"].startswith("ambiguous")


def test_exact_name_beats_a_looser_candidate() -> None:
    res = mv.match_players(_ours([("o", "Oscar", "Chelsea")]), _candidates()).iloc[0]
    assert res["match_method"] == "exact" and res["transfermarkt_id"] == 30


def test_one_transfermarkt_player_backs_only_one_profile() -> None:
    ours = _ours([("x", "Koke", "Atlético Madrid"), ("y", "Kóke", "Atlético Madrid")])
    res = mv.match_players(ours, _candidates()).set_index("player_id")
    assert res.loc["x", "match_method"] == "exact"
    assert res.loc["y", "match_method"] == "unmatched"
    assert "already matched" in res.loc["y", "match_note"]


def test_build_records_unmatched_and_unvalued_stay_null() -> None:
    ours = _ours([("a", "José Giménez", "Atlético Madrid"), ("b", "Koke", "Atlético Madrid"),
                  ("j", "Jonny Castro", "Celta Vigo")])  # fmt: skip
    matches = mv.match_players(ours, _candidates())
    snapshot = pd.DataFrame({"player_id": [10], "date": [pd.Timestamp("2016-01-04")],
                             "market_value_in_eur": [12e6], "current_club_name": ["Atlético"]})  # fmt: skip
    names = pd.Series({10: "José María Giménez", 11: "Koke"})
    recs = {r.player_id: r for r in mv.build_records(ours, matches, snapshot, names)}
    assert recs["a"].market_value_eur == 12e6 and recs["a"].market_value_gbp == 9e6
    assert recs["a"].valuation_date == date(2016, 1, 4)
    assert recs["b"].transfermarkt_id == 11 and recs["b"].market_value_eur is None
    assert "no valuation" in (recs["b"].match_note or "")
    assert recs["j"].match_method == "unmatched"
    assert recs["j"].market_value_eur is None and recs["j"].transfermarkt_id is None


def test_upsert_is_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = init_db(create_engine(f"sqlite:///{tmp_path / 't.db'}"))
    ours = _ours([("a", "José Giménez", "Atlético Madrid")])
    snapshot = pd.DataFrame({"player_id": [10], "date": [pd.Timestamp("2016-01-04")],
                             "market_value_in_eur": [12e6], "current_club_name": ["Atlético"]})  # fmt: skip
    recs = mv.build_records(ours, mv.match_players(ours, _candidates()), snapshot, pd.Series(dtype=str))
    mv.upsert_records(engine, recs)
    mv.upsert_records(engine, recs)
    values = mv_read.load_market_values("2015", engine)
    assert len(values) == 1 and values.iloc[0]["market_value_eur"] == 12e6


def _values() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "player_id": ["a", "b", "c", "d"],
            "team": ["Atlético Madrid"] * 3 + ["Getafe"],
            "league": ["ESP-La Liga"] * 4,
            "match_method": ["exact", "fuzzy", "unmatched", "exact"],
            "transfermarkt_name": ["Koke", "José María Giménez", None, "X"],
            "valuation_date": [date(2016, 2, 22), date(2016, 1, 4), None, None],
            "market_value_eur": [35e6, 12e6, None, None],
        }
    )


def test_squad_value_sums_known_values_only() -> None:
    sv = mv_read.squad_value("Atlético Madrid", "ESP-La Liga", _values())
    assert sv.total_eur == 47e6 and sv.n_valued == 2 and sv.n_profiled == 3
    assert mv_read.coverage_is_thin(sv)  # 2 of 3 is under 80%
    none = mv_read.squad_value("Getafe", "ESP-La Liga", _values())
    assert none.total_eur is None and none.n_valued == 0  # never 0 euros


def test_player_value_missing_is_none() -> None:
    assert mv_read.player_value("c", _values()).market_value_eur is None  # type: ignore[union-attr]
    assert mv_read.player_value("zz", _values()) is None
    assert mv_read.values_by_player(_values())["c"] is None


def test_coverage_counts() -> None:
    cov = mv_read.coverage(_values())
    assert (cov.n_profiled, cov.n_matched, cov.n_valued) == (4, 3, 2)
    assert cov.by_league["ESP-La Liga"] == (4, 3, 2)
