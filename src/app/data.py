"""Cached read access for the UI. Every function is a thin `st.cache_data` wrapper around
`src.analytics`: no metric, percentile or similarity is computed here."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import Engine

from src.analytics import composer as composer_mod
from src.analytics import directory as analytics_dir
from src.analytics import finance as finance_mod
from src.analytics import games as games_mod
from src.analytics import heatmaps as heatmaps_mod
from src.analytics import market_values as market_values_mod
from src.analytics import match_report as match_report_mod
from src.analytics import style_fit as style_fit_mod
from src.analytics import team_profile as team_profile_mod
from src.analytics.profile import classify
from src.analytics.similarity import find_similar
from src.analytics.team_similarity import find_similar_teams, load_team_style
from src.models.composer import Squad
from src.models.finance import TeamWageContext
from src.models.market_value import MarketValueCoverage, PlayerMarketValue, SquadMarketValue
from src.models.match import PlayerGames, SeasonGameStats
from src.models.match_report import MatchReport
from src.models.schema import get_engine
from src.models.similarity import SimilarityResult
from src.models.team import KeyPlayer, SimilarTeamsResult, TeamProfile
from src.transform.normalize import DEFAULT_SEASON

SEASON = DEFAULT_SEASON
SEASON_LABEL = "2015/16"


class DataUnavailable(RuntimeError):
    """The database could not be read (missing file, missing table)."""


@st.cache_resource(show_spinner=False)
def engine() -> Engine:
    """One pooled SQLAlchemy engine for the whole server process."""
    return get_engine()


@st.cache_data(show_spinner=False)
def directory() -> pd.DataFrame:
    try:
        return analytics_dir.load_directory(engine(), SEASON)
    except Exception as exc:  # noqa: BLE001 - shown as a clear message, never a traceback
        raise DataUnavailable(str(exc)) from exc


@st.cache_data(show_spinner=False)
def percentiles(player_id: str) -> pd.DataFrame:
    return analytics_dir.load_player_percentiles(player_id, SEASON, engine())


@st.cache_data(show_spinner=False)
def totals(player_id: str) -> pd.Series:
    return analytics_dir.load_player_totals(player_id, SEASON, engine())


@st.cache_data(show_spinner=False)
def heatmap(player_id: str, heatmap_type: str) -> tuple[np.ndarray, int] | None:
    return analytics_dir.load_heatmap(player_id, heatmap_type, SEASON, engine())


@st.cache_data(show_spinner=False)
def side(player_id: str) -> str | None:
    return analytics_dir.side_detection(player_id, SEASON, engine())


@st.cache_data(show_spinner=False)
def players_with_heatmaps() -> set[str]:
    return analytics_dir.has_heatmaps(directory()["player_id"].tolist(), SEASON, engine())


@st.cache_data(show_spinner=False)
def reference(group: str, metrics: tuple[str, ...]) -> dict[str, dict[str, float]]:
    return analytics_dir.group_reference(group, list(metrics), SEASON, engine())


@st.cache_data(show_spinner=False)
def similar(
    player_name: str,
    n: int = 10,
    same_team_only: bool = False,
    exclude_same_team: bool = False,
    min_minutes: float = 0.0,
    use_archetypes: bool = True,
) -> SimilarityResult:
    return find_similar(
        player_name,
        SEASON,
        n=n,
        engine=engine(),
        use_archetypes=use_archetypes,
        filters={
            "same_team_only": same_team_only,
            "exclude_same_team": exclude_same_team,
            "min_minutes": min_minutes or None,
        },
    )


@st.cache_data(show_spinner=False)
def similarity_matrix(player_names: tuple[str, ...], use_archetypes: bool) -> pd.DataFrame:
    """Pairwise similarity of the named players (NaN where the two are not in one pool)."""
    out = pd.DataFrame(np.nan, index=list(player_names), columns=list(player_names))
    for a in player_names:
        res = find_similar(a, SEASON, n=2000, engine=engine(), use_archetypes=use_archetypes)
        out.loc[a, a] = 1.0
        for s in res.similar:
            if s.player_name in out.columns:
                out.loc[a, s.player_name] = s.similarity
    return out


@st.cache_data(show_spinner=False)
def archetypes() -> pd.DataFrame:
    return analytics_dir.archetype_table(directory())


@st.cache_data(show_spinner=False)
def exemplars() -> pd.DataFrame:
    return analytics_dir.archetype_exemplars(engine(), SEASON)


@st.cache_data(show_spinner=False)
def defining_metrics(archetype_id: int) -> list[tuple[str, float]]:
    return analytics_dir.archetype_defining_metrics(archetype_id, engine(), SEASON)


@st.cache_data(show_spinner=False)
def scatter() -> pd.DataFrame:
    return analytics_dir.archetype_scatter(engine(), SEASON)


@st.cache_data(show_spinner=False)
def classified(player_id: str, group: str) -> dict[str, list[dict]]:
    """Strengths, weaknesses, neutral and unranked metrics (`profile.classify`)."""
    return classify(percentiles(player_id), group)


@st.cache_data(show_spinner=False)
def correction_counts() -> dict[str, int]:
    return analytics_dir.lineup_correction_counts(SEASON)


# ---- teams (src.analytics.team_*) -------------------------------------------------------------


@st.cache_data(show_spinner=False)
def teams() -> pd.DataFrame:
    return team_profile_mod.list_teams(SEASON, engine())


@st.cache_data(show_spinner=False)
def team_profile(team_name: str) -> TeamProfile:
    return team_profile_mod.team_profile(team_name, SEASON, engine())


@st.cache_data(show_spinner=False)
def similar_teams(team_name: str, n: int = 10) -> SimilarTeamsResult:
    return find_similar_teams(team_name, SEASON, n, engine())


@st.cache_data(show_spinner=False)
def team_key_players(team_name: str, league: str) -> tuple[list[KeyPlayer], str]:
    return team_profile_mod.key_players(team_name, league, SEASON, engine())


@st.cache_data(show_spinner=False)
def market_values() -> pd.DataFrame:
    return market_values_mod.load_market_values(SEASON, engine())


def player_market_value(player_id: str) -> PlayerMarketValue | None:
    return market_values_mod.player_value(player_id, market_values())


@st.cache_data(show_spinner=False)
def market_value_map() -> dict[str, float | None]:
    return market_values_mod.values_by_player(market_values())


def market_value_coverage() -> MarketValueCoverage:
    return market_values_mod.coverage(market_values())


def squad_market_value(team_name: str, league: str) -> SquadMarketValue:
    return market_values_mod.squad_value(team_name, league, market_values())


@st.cache_data(show_spinner=False)
def team_wage(team_name: str, league: str) -> TeamWageContext:
    """The club's wage bill and its rank (team context, never a player's wage)."""
    return finance_mod.team_wage_context(team_name, league, SEASON, engine())


# ---- matches (src.analytics.games / heatmaps) -------------------------------------------------


@st.cache_data(show_spinner=False)
def player_matches(player_id: str) -> pd.DataFrame:
    return games_mod.load_player_matches(player_id, SEASON, engine())


@st.cache_data(show_spinner=False)
def match_heatmaps(match_id: int, player_id: str) -> dict[str, np.ndarray] | None:
    """The seven heatmap grids of one player in one match, computed on demand from the cached raw
    events (None when they are not cached or the player has no StatsBomb id)."""
    sb_id = games_mod.statsbomb_player_id(player_id, engine())
    return None if sb_id is None else heatmaps_mod.match_heatmaps(match_id, sb_id)


@st.cache_data(show_spinner=False)
def match_report(match_id: int) -> MatchReport | None:
    """Events, lineups and team totals of one match from the cached raw StatsBomb files."""
    return match_report_mod.load_match_report(match_id, engine())


@st.cache_data(show_spinner=False)
def season_game_stats(player_id: str, group: str) -> SeasonGameStats:
    return games_mod.season_game_stats(player_matches(player_id), group)


@st.cache_data(show_spinner=False)
def player_games(player_id: str, group: str) -> PlayerGames:
    return games_mod.player_games(player_matches(player_id), group)


@st.cache_data(show_spinner=False)
def style_fit() -> pd.DataFrame:
    return style_fit_mod.load_style_fit(SEASON, engine())


@st.cache_data(show_spinner=False)
def team_style() -> pd.DataFrame:
    return load_team_style(SEASON, engine())


def axis_raw_at(axis: str, score: float) -> float:
    return composer_mod.axis_raw_at(team_style(), axis, score)


@st.cache_data(show_spinner=False)
def composer_variants(
    target: tuple[tuple[str, float], ...], budget_eur: float, formation: str,
    leagues: tuple[str, ...], min_minutes: int, quality_weight: float,
) -> list[Squad]:
    """Best fit / Budget lean / Most balanced XIs. Raises composer.InfeasibleBudget."""
    return composer_mod.build_squad_variants(
        dict(target), budget_eur, formation, style_fit(), list(leagues) or None, min_minutes,
        quality_weight,
    )
