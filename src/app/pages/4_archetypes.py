"""Archetype explorer: the scatter, every archetype with its exemplars, and a player lookup."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import plotly.graph_objects as go
import streamlit as st

from src.analytics import directory as analytics_dir
from src.analytics.archetypes import GK_ID_OFFSET
from src.app import data, design, shell
from src.app.labels import metric_label
from src.utils.config import PROJECT_ROOT

SCATTER_PNG = PROJECT_ROOT / "docs" / "archetypes" / "archetype_scatter.png"


def _color(archetype_id: int) -> str:
    return (
        design.POSITION_COLORS["GK"] if archetype_id >= GK_ID_OFFSET
        else design.CATEGORICAL[archetype_id % len(design.CATEGORICAL)]
    )


def _scatter_figure(points, exemplars) -> go.Figure:
    fig = go.Figure()
    typical = exemplars[exemplars["archetype_id"] < GK_ID_OFFSET].groupby("archetype_id").head(1)
    for aid, group in points.groupby("archetype_id"):
        fig.add_trace(
            go.Scattergl(
                x=group["x"], y=group["y"], mode="markers", name=group["archetype_label"].iloc[0],
                marker=dict(size=6, color=_color(int(aid)), opacity=0.7),
                customdata=group[["player_name", "team", "archetype_label"]],
                hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{customdata[2]}<extra></extra>",
            )
        )  # fmt: skip
    # Label each archetype with its most typical player, at that player's own position.
    ex = typical.merge(points[["player_id", "x", "y"]], on="player_id")
    fig.add_trace(
        go.Scatter(
            x=ex["x"], y=ex["y"], mode="markers+text", text=ex["player_name"],
            textposition="top center", showlegend=False, hoverinfo="skip",
            marker=dict(size=11, color="rgba(0,0,0,0)", line=dict(color=design.TEXT_PRIMARY, width=2)),
            textfont=dict(size=design.SIZE_SMALL, color=design.TEXT_PRIMARY),
        )
    )  # fmt: skip
    fig.update_layout(
        height=560, margin=dict(l=design.SP_L, r=design.SP_L, t=design.SP_M, b=design.SP_L),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=design.BG_SECONDARY,
        font=dict(family=design.FONT_FAMILY, size=design.SIZE_BODY, color=design.TEXT_PRIMARY),
        xaxis=dict(title="Principal component 1", gridcolor=design.BORDER, zeroline=False),
        yaxis=dict(title="Principal component 2", gridcolor=design.BORDER, zeroline=False),
        legend=dict(orientation="h", y=-0.2, font=dict(size=design.SIZE_SMALL)),
        hoverlabel=dict(bgcolor=design.BG_TERTIARY, bordercolor=design.BORDER),
    )  # fmt: skip
    return fig


def _scatter_section(exemplars) -> None:
    design.render(
        design.section_header(
            "The map of playing styles",
            "Outfield players on the two main axes of the archetype clustering. Ringed names are each archetype's most typical player.",
        )
    )
    try:
        with st.spinner("Building the map..."):
            points = data.scatter()
        st.plotly_chart(_scatter_figure(points, exemplars), use_container_width=True,
                        config={"displayModeBar": False})
    except Exception:  # noqa: BLE001 - fall back to the exported image, never a traceback
        if SCATTER_PNG.exists():
            st.image(str(SCATTER_PNG), caption="Archetype scatter (exported image)")
        else:
            design.render(design.empty_state("The archetype map is unavailable."))
    st.caption(
        "Axes are statistical components, not football concepts. Clusters overlap: archetype "
        "boundaries are soft, and being near an edge means a player fits two styles."
    )
    with st.expander("Text alternative"):
        st.write(
            "A scatter of 1,239 outfield players on two principal components, coloured by "
            "archetype. Defensive archetypes (stoppers, ball-playing centre-backs, full-backs) "
            "sit on one side, attacking archetypes (poachers, target men, chance creators, "
            "dribbling wingers) on the other, with midfield archetypes between them."
        )


def _archetype_cards(exemplars, table) -> None:
    design.render(design.section_header("The 13 archetypes", "Names are descriptions a person gave to clusters, not official roles"))
    directory = shell.load_directory().set_index("player_id")
    for _, a in table.iterrows():
        aid = int(a["archetype_id"])
        top = exemplars[exemplars["archetype_id"] == aid]
        defining = data.defining_metrics(aid)
        with st.container(key=f"ficard_arch_{aid}"):
            design.render(
                f'<div class="fi-row" style="justify-content:space-between">'
                f'<div class="fi-name" style="color:{_color(aid)}">{design.esc(a["archetype_label"])}</div>'
                f'<span class="fi-muted">{int(a["size"])} players · '
                f'{"goalkeepers" if aid >= GK_ID_OFFSET else "outfield"} · '
                f'mean confidence {a["confidence"]:.2f}</span></div>'
                + (
                    '<div class="fi-muted" style="margin:4px 0">Defined by: '
                    + design.esc(", ".join(f"{metric_label(m)} ({'high' if z > 0 else 'low'})" for m, z in defining))
                    + "</div>" if defining else
                    '<div class="fi-muted" style="margin:4px 0">Goalkeepers are clustered on the nine goalkeeper metrics.</div>'
                )
            )
            cols = st.columns(5)
            for col, (_, p) in zip(cols, top.iterrows(), strict=False):
                with col:
                    league = shell.LEAGUE_SHORT.get(directory.loc[p["player_id"], "league"], "")
                    design.render(
                        '<div class="fi-mini">'
                        + design.player_avatar(p["player_name"], 32, shell.photo_uri(p["player_id"], p["player_name"], 32))
                        + f'<div class="fi-col"><span class="fi-body">{design.esc(p["player_name"])}</span>'
                        f'<span class="fi-muted">{design.esc(p["team"])} · {design.esc(league)}</span></div></div>'
                    )
                    if st.button("Profile", key=f"ex_{aid}_{p['player_id']}"):
                        shell.open_player(p["player_id"])
            if st.button(f"Show all {int(a['size'])} players", key=f"show_{aid}"):
                _roster(aid)


@st.dialog("Archetype members", width="large")
def _roster(aid: int) -> None:
    directory = shell.load_directory()
    members = directory[directory["archetype_id"] == aid]
    if members.empty:
        design.render(design.empty_state("No players in this archetype."))
        return
    label = members["archetype_label"].iloc[0]
    design.render(f'<div class="fi-name">{design.esc(label)}</div>'
                  f'<div class="fi-muted">{len(members)} players, most clearly in the archetype first</div>')
    view = members.sort_values("cluster_confidence", ascending=False)
    st.dataframe(
        view[["player_name", "team", "league", "position_group_fine", "minutes_total", "cluster_confidence"]]
        .rename(columns={
            "player_name": "Player", "team": "Team", "league": "League",
            "position_group_fine": "Position", "minutes_total": "Minutes",
            "cluster_confidence": "Confidence",
        }),
        hide_index=True, use_container_width=True, height=380,
        column_config={"Minutes": st.column_config.NumberColumn(format="%.0f"),
                       "Confidence": st.column_config.NumberColumn(format="%.2f")},
    )  # fmt: skip
    names = view.set_index("player_id")["player_name"]
    pick = st.selectbox("Open a profile", list(names.index), index=None,
                        placeholder="Choose a player", format_func=lambda i: names[i], key=f"roster_pick_{aid}")
    if pick:
        shell.open_player(pick)


@st.fragment
def _lookup() -> None:
    design.render(design.section_header("Which archetype is he?", "Search a player to see his archetype and confidence"))
    q = st.text_input("Player name", key="arch_q", placeholder="Try 'Kanté'")
    if not q.strip():
        return
    hits = analytics_dir.search(shell.load_directory(), q).head(5)
    if hits.empty:
        design.render(design.empty_state(f"No player matching '{q}'", "Try 'Kanté' or 'Özil'."))
        return
    for _, r in hits.iterrows():
        conf = r["cluster_confidence"]
        soft = analytics_dir.confidence_is_soft(conf)
        with st.container(key=f"ficard_lookup_{r['player_id']}"):
            design.render(
                '<div class="fi-row">'
                + design.player_avatar(r["player_name"], 48, shell.photo_uri(r["player_id"], r["player_name"], 48))
                + f'<div class="fi-col" style="flex:1"><span class="fi-name">{design.esc(r["player_name"])}</span>'
                f'<span class="fi-muted">{design.esc(r["team"])} · {design.position_badge(r["position_group_fine"])}</span></div>'
                + design.badge(str(r["archetype_label"]), "highlight")
                + design.badge(f"Confidence {conf:.2f}" + (" · soft" if soft else ""), "warning" if soft else "positive")
                + "</div>"
            )
            st.caption(
                "Confidence is the margin to the next-nearest archetype (0 on the boundary, "
                "toward 1 deep inside), not a probability."
            )
            if st.button("Open profile", key=f"lk_{r['player_id']}"):
                shell.open_player(r["player_id"])


design.render('<h1 class="fi-h1">Archetype explorer</h1>')
table, exemplars = data.archetypes(), data.exemplars()
_scatter_section(exemplars)
_lookup()
_archetype_cards(exemplars, table)
shell.refresh_when_photos_ready()
