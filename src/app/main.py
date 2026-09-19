"""Streamlit entry point.

UI ONLY. This module must never contain analytics, similarity, or
metric-calculation logic — call into /src/analytics instead.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Football Intel", layout="wide")
st.title("Football Intel")
st.caption("Foundation skeleton — features land in later sessions.")
