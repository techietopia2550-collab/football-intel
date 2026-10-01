"""Compare up to five players: overlay radar, side-by-side stats, heatmaps, similarity matrix."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import pandas as pd
import streamlit as st

from src.analytics import directory as analytics_dir
from src.app import data, design, shell
from src.app.labels import metric_label

MAX_PLAYERS = 5
EXAMPLES = (("Kanté", "Gueye"), ("Vardy", "Kane"))


def _find(directory: pd.DataFrame, name: str) -> str | None:
    hits = analytics_dir.search(directory, name)
    return None if hits.empty else str(hits.iloc[0]["player_id"])


def _set_ids(ids: list[str]) -> None:
    st.session_state["compare_ids"] = [i for i in ids if i]
    st.session_state["compare_select"] = list(st.session_state["compare_ids"])


@st.cache_data(show_spinner=False)
def _labels(_directory: pd.DataFrame) -> dict[str, str]:
    return {
        r.player_id: f"{r.player_name} · {r.position_group_fine} · {r.team}"
        for r in _directory.itertuples()
    }


def _empty(directory: pd.DataFrame) -> None:
    design.render(
        design.empty_state(
            "Select at least 2 players to compare.", "Or start from one of these pairs:"
        )
    )
    cols = st.columns(len(EXAMPLES) + 3)
    for col, (a, b) in zip(cols, EXAMPLES, strict=False):
        col.button(
            f"{a} vs {b}", key=f"ex_{a}_{b}",
            on_click=lambda a=a, b=b: _set_ids([_find(directory, a) or "", _find(directory, b) or ""]),
        )  # fmt: skip


def _player_chips(directory: pd.DataFrame, ids: list[str]) -> None:
    chips = []
    for i, pid in enumerate(ids):
        r = analytics_dir.player_row(directory, pid)
        color = design.COMPARE_COLORS[i % len(design.COMPARE_COLORS)]
        chips.append(
            f'<span class="fi-badge" style="color:{color};background:{color}22;border-color:{color}66">'
            f"● {i + 1}. {design.esc(r['player_name'])} · {design.esc(r['position_group_fine'])}</span>"
        )
    design.render('<div class="fi-badges">' + "".join(chips) + "</div>")


@st.fragment
def _overlay(directory: pd.DataFrame, ids: list[str]) -> None:
    design.render(design.section_header("Radar overlay", "Percentile within each player's own position group"))
    rows = [analytics_dir.player_row(directory, i) for i in ids]
    basis = rows[0]["position_group_fine"]
    is_gk = basis == "GK"
    comparable = [(i, r) for i, r in enumerate(rows) if (r["position_group_fine"] == "GK") == is_gk]
    if len(comparable) < len(rows):
        design.render(
            design.note("Goalkeepers and outfield players are measured on different metrics, so the "
                        "radar shows only the players who match the first one selected.")
        )
    series, items_by_player = [], {}
    metrics = analytics_dir.RADAR_METRICS[basis]
    for i, r in comparable:
        pct = data.percentiles(r["player_id"])
        items = {it["metric"]: it for it in analytics_dir.radar_items(pct, r["position_group_fine"])}
        items_by_player[r["player_id"]] = items
    shared = [m for m in metrics if all(m in items for items in items_by_player.values())]
    if len(shared) < 3:
        design.render(design.empty_state("Not enough shared ranked metrics for a radar."))
        return
    labels = {m: metric_label(m) for m in shared}
    for i, r in comparable:
        series.append(
            {"name": r["player_name"], "items": [items_by_player[r["player_id"]][m] for m in shared],
             "color": design.COMPARE_COLORS[i % len(design.COMPARE_COLORS)]}
        )  # fmt: skip
    if len({r["position_group_fine"] for _, r in comparable}) > 1:
        st.caption("Players from different position groups: each percentile is within his own group.")
    fig = design.radar_chart(series, basis, labels=labels)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with st.expander("Chart description (text alternative)"):
        for s in series:
            st.write(
                f"{s['name']}: "
                + "; ".join(f"{labels[i['metric']]} {design.ordinal(i['percentile'])}" for i in s["items"])
            )
    _stat_table(comparable, shared)


def _stat_table(comparable: list[tuple[int, pd.Series]], metrics: list[str]) -> None:
    design.render(design.section_header("Side by side", "Value per 90 and percentile; cells coloured by how good the percentile is"))
    values: dict[str, list[str]] = {}
    scores: dict[str, list[float | None]] = {}
    for _, r in comparable:
        pct = data.percentiles(r["player_id"]).set_index("metric")
        col_v, col_s = [], []
        for m in metrics:
            v, p = pct.loc[m, "value"], pct.loc[m, "percentile"]
            unit = "%" if m.endswith("_pct") or m == "shot_accuracy" else ""
            col_v.append("–" if pd.isna(p) else f"{design.fmt_value(float(v), unit)} · {design.ordinal(p)}")
            col_s.append(analytics_dir.score_of(m, None if pd.isna(p) else float(p)))
        values[r["player_name"]] = col_v
        scores[r["player_name"]] = col_s
    frame = pd.DataFrame(values, index=[metric_label(m) for m in metrics])
    styles = pd.DataFrame(
        {k: design.percentile_styles(v) for k, v in scores.items()}, index=frame.index
    )
    st.dataframe(frame.style.apply(lambda _: styles, axis=None), use_container_width=True)
    st.caption("Each cell reads 'value · percentile'. Green is a strong percentile (70+), red weak (under 30); "
               "descriptive metrics stay gray. The number is always shown.")


def _heatmaps(directory: pd.DataFrame, ids: list[str]) -> None:
    design.render(design.section_header("Where they play", "All actions, 10-yard cells"))
    cols = st.columns(min(len(ids), 3))
    for k, pid in enumerate(ids):
        r = analytics_dir.player_row(directory, pid)
        with cols[k % len(cols)]:
            hm = data.heatmap(pid, "all_actions")
            if hm is None:
                design.render(design.empty_state(r["player_name"], "No heatmap (EPL players only so far)."))
                continue
            st.plotly_chart(
                design.heatmap_pitch(hm[0].tolist(), r["player_name"], height=240),
                use_container_width=True, config={"displayModeBar": False}, key=f"cmp_hm_{pid}",
            )
            st.caption(f"{hm[1]:,} events")


def _matrix(directory: pd.DataFrame, ids: list[str]) -> None:
    design.render(design.section_header("Pairwise similarity", "How close two players' profiles are: a measured tier word, or the raw cosine score (heuristic)"))
    rows = [analytics_dir.player_row(directory, i) for i in ids]
    names = tuple(r["player_name"] for r in rows)
    labels = [f"{r['player_name']} · {r['position_group_fine']}" for r in rows]
    tab_a, tab_b = st.tabs(["Archetype-weighted", "Position group"])
    for tab, arch in ((tab_a, True), (tab_b, False)):
        with tab:
            try:
                mat = data.similarity_matrix(names, arch)
            except ValueError as exc:
                st.write(str(exc))
                continue
            # tiers were measured on archetype-weighted scores only; the diagonal is self vs self
            shown = pd.DataFrame(
                [
                    [
                        "self" if i == j else design.fmt_match(v) if arch else design.fmt_cosine(v)
                        for j, v in enumerate(row)
                    ]
                    for i, row in enumerate(mat.to_numpy())
                ],
                index=labels, columns=labels,
            )
            st.dataframe(shown, use_container_width=True)
            st.caption(
                "A dash means the two are not in the same "
                + ("archetype" if arch else "position group")
                + ": similarity is only defined inside one pool."
                + (
                    f" {design.MATCH_TIER_NOTE}"
                    if arch
                    else " No tier word here: tiers were measured on archetype-weighted scores, so the raw "
                    "cosine (-1 to 1) is shown. It is not a probability or a %."
                )
            )


design.render('<h1 class="fi-h1">Compare players</h1>')
directory = shell.load_directory()
options = directory.sort_values("player_name")["player_id"].tolist()
if "compare_select" not in st.session_state:
    st.session_state["compare_select"] = [i for i in st.session_state.get("compare_ids", []) if i in options]
selected = st.multiselect(
    "Players (up to 5)", options, key="compare_select", max_selections=MAX_PLAYERS,
    format_func=lambda pid: _labels(directory).get(pid, pid), placeholder="Search a player to add",
)
st.session_state["compare_ids"] = list(selected)
if len(selected) < 2:
    _empty(directory)
    st.stop()

_player_chips(directory, selected)
_overlay(directory, selected)
_heatmaps(directory, selected)
_matrix(directory, selected)
shell.refresh_when_photos_ready()
