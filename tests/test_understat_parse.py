"""Offline test of the Understat normalize step, using a trimmed real-data parquet fixture."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.ingest.understat import SOURCE, _to_records

FIXTURE = Path(__file__).parent / "fixtures" / "understat_sample.parquet"
LEAGUE = "ENG-Premier League"


def _records() -> list:
    return _to_records(pd.read_parquet(FIXTURE), season=2024, league=LEAGUE)


def test_parse_shape_and_provenance() -> None:
    df = pd.read_parquet(FIXTURE)
    records = _records()
    assert len(records) == len(df) == 5
    for rec in records:
        assert rec.source == SOURCE
        assert rec.season == "2024"
        assert rec.league == LEAGUE
        assert rec.player_id.startswith("understat:")


def test_parse_maps_known_player() -> None:
    salah = next(r for r in _records() if r.canonical_name == "Mohamed Salah")
    assert salah.team == "Liverpool"
    assert salah.minutes == 3392
    assert salah.raw_stats["xg"] > 20  # metrics live in raw_stats
    assert "minutes" not in salah.raw_stats  # promoted to a column, not duplicated


def test_parse_drops_rows_without_player_name() -> None:
    df = pd.read_parquet(FIXTURE)
    df.loc[df.index[0], "player"] = None
    assert len(_to_records(df, season=2024, league=LEAGUE)) == len(df) - 1


def test_goalkeeper_position_group_is_mapped() -> None:
    gk = next(r for r in _records() if r.position == "GK")
    assert gk.position_group == "Goalkeeper"


def test_outfield_position_group_known_gap() -> None:
    """Documents a real bug: Understat outfield positions are letters ('F M', 'D F M S'),
    but understat._POSITION_GROUPS keys on 'FWD'/'MID'/'DEF', so they map to None.
    Flip this assertion when the mapping is fixed."""
    salah = next(r for r in _records() if r.canonical_name == "Mohamed Salah")
    assert salah.position == "F M"
    assert salah.position_group is None
