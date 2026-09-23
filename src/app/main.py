"""Streamlit entry point: page config, global design, persistent sidebar, navigation.

UI ONLY. This module must never contain analytics, similarity, or metric-calculation logic: call
into /src/analytics (through `src.app.data`) instead.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import streamlit as st

st.set_page_config(
    page_title="Football Intel", page_icon="⚽", layout="wide", initial_sidebar_state="expanded"
)

from src.app import design, shell  # noqa: E402

design.inject_css()
pages = [
    st.Page("pages/1_search.py", title="Search", icon="🔎", default=True, url_path="search"),
    st.Page("pages/2_player.py", title="Player profile", icon="👤", url_path="player"),
    st.Page("pages/3_compare.py", title="Compare", icon="⚖️", url_path="compare"),
    st.Page("pages/4_archetypes.py", title="Archetype explorer", icon="🧭", url_path="archetypes"),
    st.Page("pages/5_methodology.py", title="Methodology", icon="📖", url_path="methodology"),
]
page = st.navigation(pages)  # registered before the sidebar so its page links resolve
shell.sidebar()
shell.load_directory()  # preload the player list once; every page reads the cached copy
page.run()
