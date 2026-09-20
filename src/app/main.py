"""Streamlit entry point.

UI ONLY. This module must never contain analytics, similarity, or
metric-calculation logic — call into /src/analytics instead.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import streamlit as st

st.set_page_config(page_title="Football Intel", layout="wide")
st.title("Football Intel")
st.caption("Foundation skeleton — features land in later sessions.")
