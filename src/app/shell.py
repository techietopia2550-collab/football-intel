"""Shared page chrome: the persistent sidebar, filter state, navigation between players, photos."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from src.analytics import directory as analytics_dir
from src.app import data, design, player_images
from src.utils.config import PROJECT_ROOT

LEAGUE_SHORT = {
    "ENG-Premier League": "EPL", "ESP-La Liga": "La Liga", "ITA-Serie A": "Serie A",
    "FRA-Ligue 1": "Ligue 1",
}  # fmt: skip
SHORT_TO_LEAGUE = {v: k for k, v in LEAGUE_SHORT.items()}
MIN_MINUTES_FLOOR = 900  # players below this have no percentiles at all
STATSBOMB_LOGO = PROJECT_ROOT / "src" / "app" / "static" / "statsbomb-logo.png"


@dataclass(frozen=True)
class Filters:
    leagues: list[str]
    groups: list[str]
    min_minutes: int


def current_filters() -> Filters:
    ss = st.session_state
    shorts = ss.get("f_leagues") or list(SHORT_TO_LEAGUE)
    return Filters(
        [SHORT_TO_LEAGUE[x] for x in shorts],
        list(ss.get("f_groups", [])),
        int(ss.get("f_minutes", MIN_MINUTES_FLOOR)),
    )


def sidebar() -> None:
    """The persistent sidebar: name, scope, filters, sources, limitations."""
    with st.sidebar:
        design.render(
            '<div class="fi-h2">Football Intel</div>'
            f'<div class="fi-muted">{data.SEASON_LABEL} Big-4 · StatsBomb Open Data</div>'
        )
        st.multiselect(
            "Leagues", list(SHORT_TO_LEAGUE), default=list(SHORT_TO_LEAGUE), key="f_leagues",
            help="Percentiles are always pooled across all four leagues; this only filters lists.",
        )  # fmt: skip
        st.multiselect(
            "Position groups", list(design.POSITION_ORDER), key="f_groups",
            format_func=lambda g: f"{g} · {design.POSITION_NAMES[g]}",
            placeholder="All positions",
        )  # fmt: skip
        st.slider(
            "Minimum minutes", MIN_MINUTES_FLOOR, 3400, MIN_MINUTES_FLOOR, step=100,
            key="f_minutes",
            help="Only players with 900+ minutes have percentiles, so 900 is the lowest setting.",
        )  # fmt: skip
        with st.expander("Data sources"):
            if STATSBOMB_LOGO.exists():
                st.image(str(STATSBOMB_LOGO), width=160)
            st.markdown(
                "Event data by [StatsBomb](https://statsbomb.com), from the "
                "[StatsBomb Open Data](https://github.com/statsbomb/open-data) repository, "
                "used under its user agreement. Photos: Wikipedia."
            )
        with st.expander("Limitations"):
            st.markdown(
                "One season (2015/16), four leagues, men's football. Event data cannot see "
                "leadership, communication or off-ball reading of the game. Archetype "
                "boundaries are soft."
            )
            st.page_link("pages/5_methodology.py", label="Read the methodology →")


def open_player(player_id: str) -> None:
    st.session_state["selected_player"] = player_id
    st.switch_page("pages/2_player.py")


def open_team(team_name: str) -> None:
    st.session_state["selected_team"] = team_name
    st.switch_page("pages/6_team.py")


def open_match(player_id: str, match_id: int) -> None:
    st.session_state["selected_player"] = player_id
    st.session_state["selected_match"] = match_id
    st.switch_page("pages/7_match.py")


def open_compare(player_ids: list[str]) -> None:
    st.session_state["compare_ids"] = player_ids[:5]
    st.switch_page("pages/3_compare.py")


def photo_uri(player_id: str, name: str, px: int, want_bio: bool = False) -> str:
    """Data URI of the player's photo (silhouette until the background fetch finishes)."""
    return player_images.avatar_data_uri(
        player_images.photo_path(player_id, name, want_bio), px
    )


def has_photo(player_id: str) -> bool:
    return player_images.has_credit(player_id)


def fail(message: str) -> None:
    design.render(design.note(f"<b>Something went wrong.</b> {design.esc(message)}"))
    st.stop()


def load_directory() -> pd.DataFrame:
    """The player directory, or a clear message (never a traceback) if the database is unusable."""
    try:
        return data.directory()
    except data.DataUnavailable:
        fail(
            "The player database could not be read. Check that data/football.db exists and "
            "the pipeline has been run (see README)."
        )
    raise RuntimeError  # unreachable: `fail` stops the script


def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    f = current_filters()
    return analytics_dir.filter_directory(df, f.leagues, f.groups, f.min_minutes)


def refresh_when_photos_ready() -> None:
    """While background photo fetches are running, poll every 2 s and rerun once they finish, so
    silhouettes turn into photos without the user doing anything. Costs nothing when idle."""
    if not player_images.fetches_pending():
        return

    @st.fragment(run_every=2)
    def _poll() -> None:
        if not player_images.fetches_pending():
            st.rerun()

    _poll()
