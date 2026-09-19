"""Offline test of the Understat normalize step, using a trimmed real-data parquet fixture."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.ingest.team_identity import is_known_team
from src.ingest.understat import SOURCE, _position_group, _to_records

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
    assert gk.position_group == "GK"


def test_outfield_position_group_is_mapped() -> None:
    by_name = {r.canonical_name: r for r in _records()}
    assert by_name["Mohamed Salah"].position == "F M"
    assert by_name["Mohamed Salah"].position_group == "FWD"
    assert by_name["Erling Haaland"].position_group == "FWD"


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        ("GK", "GK"),
        ("GK S", "GK"),
        ("D", "DEF"),
        ("D S", "DEF"),
        ("D M", "DEF"),  # first letter wins, per spec
        ("M S", "MID"),
        ("F M", "FWD"),
        ("F M S", "FWD"),
        ("D F M S", "DEF"),
        ("S", None),  # substitute-only: no position is known
        ("", None),
        (None, None),
    ],
)
def test_position_group_mapping(position: str | None, expected: str | None) -> None:
    assert _position_group(position) == expected


def test_team_names_are_canonical() -> None:
    teams = {r.team for r in _records()}
    assert "Manchester City" in teams
    assert all(is_known_team(t) for t in teams if t)
