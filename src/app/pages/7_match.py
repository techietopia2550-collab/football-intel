"""Match page: one player in one match. Header, his stat line, and THAT match's heatmap.

Presentation only. The heatmap is computed on demand from the cached raw StatsBomb events
(`src.analytics.heatmaps.match_heatmaps`, through `src.app.data`, cached in memory).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import streamlit as st

from src.analytics import directory as analytics_dir
from src.analytics import games as games_mod
from src.app import data, design, shell

directory = shell.load_directory()
pid = st.session_state.get("selected_player") or st.query_params.get("player")
raw_match = st.session_state.get("selected_match") or st.query_params.get("match")
row = analytics_dir.player_row(directory, pid) if pid else None
if row is None or raw_match is None:
    design.render('<h1 class="fi-h1">Match</h1>')
    design.render(
        design.empty_state("No match selected.", "Open a player profile and pick a game card.")
    )
    st.stop()

group = row["position_group_fine"]
match_id = int(raw_match)
matches = data.player_matches(pid)
this = matches[matches["match_id"] == match_id]
if this.empty:
    design.render(design.empty_state("This player has no record in that match."))
    if st.button("← Back to profile"):
        shell.open_player(pid)
    st.stop()

m = this.iloc[0]
card = games_mod.game_card(matches, match_id, group)
assert card is not None

if st.button("← Back to profile", key="match_back"):
    shell.open_player(pid)

home, away = (m["team"], m["opponent"]) if card.home else (m["opponent"], m["team"])
home_goals, away_goals = m["home_score"], m["away_score"]
score = "-" if home_goals != home_goals else f"{int(home_goals)} - {int(away_goals)}"
with st.container(key="ficard_match_header"):
    design.render(
        f'<div class="fi-row" style="justify-content:center;gap:{design.SP_L}px">'
        f'<div class="fi-col" style="align-items:center">{shell.team_logo_img(home, 48)}'
        f'<span class="fi-name">{design.esc(home)}</span></div>'
        f'<div class="fi-stat" style="text-align:center"><div class="fi-stat-value">{design.esc(score)}</div>'
        f'<div class="fi-stat-label">{m["match_date"]:%d %b %Y} · '
        f"{design.esc(shell.LEAGUE_SHORT.get(m['league'], m['league']))}</div></div>"
        f'<div class="fi-col" style="align-items:center">{shell.team_logo_img(away, 48)}'
        f'<span class="fi-name">{design.esc(away)}</span></div></div>'
    )

design.render(
    design.section_header(
        f"{row['player_name']} in this match",
        f"{design.esc(card.position_played or '')} · {card.minutes:.0f} minutes",
    )
)
lines = games_mod.match_stat_line(m, group)
design.render(
    design.card(
        None,
        '<div class="fi-row" style="justify-content:flex-start">'
        + "".join(design.big_stat(label, value) for label, value in lines)
        + "</div>",
    )
)
if card.rating is not None:
    st.caption(f"Match rating {card.rating:.1f} (position-aware count; see Methodology).")

# ---- heatmap, computed on demand ---------------------------------------------------------------


@st.fragment
def _heatmap() -> None:
    design.render(design.section_header("Where he played in this match", "Event locations"))
    with st.spinner("Building the heatmap..."):
        grids = data.match_heatmaps(match_id, pid)
    if grids is None:
        design.render(design.empty_state("Heatmap unavailable for this match"))
        return
    tabs = st.tabs(list(analytics_dir.HEATMAP_TYPES))
    for tab, (title, htype) in zip(tabs, analytics_dir.HEATMAP_TYPES.items(), strict=True):
        with tab:
            grid = grids[htype]
            n = int(grid.sum())
            if n == 0:
                design.render(design.empty_state("No events of this type in this match"))
                continue
            fig = design.heatmap_pitch(grid.tolist(), f"{title}: {row['player_name']}")
            st.plotly_chart(
                fig, use_container_width=True, config={"displayModeBar": False},
                key=f"mhm_{pid}_{match_id}_{htype}",
            )
            st.caption(f"Based on {n:,} events. Cells are 10-yard grid. Attacking left to right.")
            with st.expander("Text alternative"):
                col_sums = grid.sum(axis=0)
                thirds = [col_sums[:4].sum(), col_sums[4:8].sum(), col_sums[8:].sum()]
                total = sum(thirds) or 1
                st.write(
                    f"{title}: {thirds[0] / total:.0%} of events in his own third, "
                    f"{thirds[1] / total:.0%} in the middle third and {thirds[2] / total:.0%} in "
                    "the attacking third."
                )


_heatmap()
shell.refresh_when_photos_ready()
