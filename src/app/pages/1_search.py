"""Home / Search: find a player by name, filter by position, open a profile."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from typing import Any

import pandas as pd
import streamlit as st

from src.analytics import directory as analytics_dir
from src.app import design, shell
from src.app.labels import metric_label

PAGE_SIZE = 50
GRID_COLUMNS = 3
SUGGESTIONS = ("Kanté", "Özil")


def _set_query(value: str) -> None:
    st.session_state["q"] = value


def badges_html(items: list[dict[str, Any]], group: str, tone: str) -> str:
    """Top strengths / weaknesses as badges: label, ordinal score, symbol (tone never alone)."""
    if not items:
        return '<span class="fi-muted">None flagged</span>'
    parts = []
    for it in items:
        label = metric_label(it["metric"])
        parts.append(design.metric_badge(label, None, it["score"], group, tone=tone))
    return '<div class="fi-badges">' + "".join(parts) + "</div>"


def player_card(row: pd.Series) -> None:
    group = row["position_group_fine"]
    pid = row["player_id"]
    with st.container(key=f"ficard_{pid}"):
        arch = row["archetype_label"] if isinstance(row["archetype_label"], str) else None
        design.render(
            '<div class="fi-row" style="flex-wrap:nowrap">'
            + design.player_avatar(row["player_name"], 64, shell.photo_uri(pid, row["player_name"], 64))
            + '<div class="fi-col"><div class="fi-name">'
            + design.esc(row["player_name"])
            + "</div><div>"
            + design.position_badge(group)
            + (f" {design.badge(arch, 'highlight')}" if arch else "")
            + "</div></div></div>"
            + f'<div class="fi-muted" style="margin:{design.SP_S}px 0">'
            + f"{design.esc(row['team'])} · {design.esc(shell.LEAGUE_SHORT[row['league']])}"
            + f" · {design.esc('2015/16')} · {row['minutes_total']:,.0f} min</div>"
            + '<div class="fi-muted">Strengths</div>'
            + badges_html(row["strengths"], group, "positive")
            + f'<div class="fi-muted" style="margin-top:{design.SP_S}px">Weaknesses</div>'
            + badges_html(row["weaknesses"], group, "neutral")
        )
        if st.button("View profile", key=f"view_{pid}", use_container_width=True):
            shell.open_player(pid)


def _empty(query: str) -> None:
    design.render(
        design.empty_state(
            "No players match. Try broadening your search.",
            "Clear a filter, check the spelling, or lower the minimum minutes.",
        )
    )
    st.caption("Try:")
    cols = st.columns(len(SUGGESTIONS) + 4)
    for col, name in zip(cols, SUGGESTIONS, strict=False):
        col.button(f"'{name}'", key=f"sugg_{name}", on_click=_set_query, args=(name,))


@st.fragment
def search_section() -> None:
    directory = shell.load_directory()
    st.text_input(
        "Search by name",
        key="q",
        placeholder="Try 'Kanté' or 'Özil'",
        help="Accents and case are ignored; small typos still match.",
    )
    st.pills(
        "Position group",
        list(design.POSITION_ORDER),
        selection_mode="multi",
        key="chips",
        format_func=lambda g: f"{g} · {design.POSITION_NAMES[g]}",
    )
    query = st.session_state.get("q", "")
    chips = list(st.session_state.get("chips") or [])
    signature = (query, tuple(chips), str(shell.current_filters()))
    if st.session_state.get("search_sig") != signature:
        st.session_state["search_sig"] = signature
        st.session_state["search_limit"] = PAGE_SIZE

    with st.spinner("Searching players..."):
        pool = shell.apply_filters(directory)
        if chips:
            pool = pool[pool["position_group_fine"].isin(chips)]
        results = analytics_dir.search(pool, query)

    if results.empty:
        _empty(query)
        return
    limit = int(st.session_state.get("search_limit", PAGE_SIZE))
    shown = results.head(limit)
    st.caption(
        f"{len(results):,} players"
        + (f" match '{query}'" if query.strip() else " (most minutes first)")
        + f" · showing {len(shown)}"
    )
    rows = [row for _, row in shown.iterrows()]
    for start in range(0, len(rows), GRID_COLUMNS):
        cols = st.columns(GRID_COLUMNS)
        for col, row in zip(cols, rows[start : start + GRID_COLUMNS], strict=False):
            with col:
                player_card(row)
    if len(results) > limit:
        if st.button(f"Show more ({len(results) - limit:,} left)", key="show_more"):
            st.session_state["search_limit"] = limit + PAGE_SIZE
            st.rerun(scope="fragment")
    shell.refresh_when_photos_ready()


design.render('<h1 class="fi-h1">Find a player</h1>')
st.caption(
    f"{len(shell.load_directory()):,} players with 900+ minutes in the EPL, La Liga, Serie A and "
    "Ligue 1, 2015/16. Players below 900 minutes have no percentiles and are not listed."
)
search_section()
