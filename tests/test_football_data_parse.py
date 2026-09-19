"""Offline test of the football-data.co.uk parse step, using a trimmed real-CSV fixture."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.ingest.football_data_csv import (
    SOURCE,
    _resolve_league,
    _to_records,
    parse_csv,
    season_code,
)

FIXTURE = Path(__file__).parent / "fixtures" / "football_data_sample.csv"


def test_season_code() -> None:
    assert season_code(2024) == "2425"
    assert season_code(1999) == "9900"


def test_resolve_league_aliases() -> None:
    assert _resolve_league("EPL") == ("E0", "ENG-Premier League")
    assert _resolve_league("e0") == ("E0", "ENG-Premier League")
    assert _resolve_league("ENG-Premier League") == ("E0", "ENG-Premier League")
    with pytest.raises(ValueError):
        _resolve_league("Eredivisie")


def test_parse_csv_handles_bom() -> None:
    df = parse_csv(FIXTURE.read_bytes())
    assert "Div" in df.columns  # a BOM would turn this into '﻿Div'
    assert len(df) == 6  # 5 real rows + 1 junk row


def test_to_records_shape_and_values() -> None:
    records = _to_records(parse_csv(FIXTURE.read_bytes()), season=2024, league="EPL")
    assert len(records) == 5  # junk row dropped
    first = records[0]
    assert first.source == SOURCE
    assert first.season == "2024"
    assert first.league == "ENG-Premier League"
    assert first.match_date == date(2024, 8, 16)
    assert (first.home_team, first.away_team) == ("Manchester United", "Fulham")  # normalized from "Man United"
    assert (first.home_goals, first.away_goals) == (1, 0)
    assert (first.home_shots, first.away_shots) == (14, 10)
    assert first.home_corners == 7
    assert all(isinstance(r.home_goals, int) for r in records)
