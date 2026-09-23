"""Player profile: hero, radar, strengths and weaknesses, heatmap, stats, similar players."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import pandas as pd
import streamlit as st

from src.analytics import directory as analytics_dir
from src.app import data, design, player_images, shell
from src.app.labels import metric_description, metric_label

SIMILAR_N = 10


def _selected_id(directory: pd.DataFrame) -> str | None:
    pid = st.session_state.get("selected_player") or st.query_params.get("player")
    return pid if pid in set(directory["player_id"]) else None


def _pick_player(directory: pd.DataFrame) -> None:
    design.render('<h1 class="fi-h1">Player profile</h1>')
    q = st.text_input("Find a player", placeholder="Try 'Kanté'", key="profile_q")
    if not q.strip():
        design.render(design.empty_state("Pick a player to see a profile.", "Search by name above."))
        return
    hits = analytics_dir.search(directory, q).head(8)
    if hits.empty:
        design.render(design.empty_state(f"No player matching '{q}'", "Try 'Kanté' or 'Özil'."))
        return
    for _, row in hits.iterrows():
        label = f"{row['player_name']} · {row['position_group_fine']} · {row['team']}"
        if st.button(label, key=f"pick_{row['player_id']}"):
            shell.open_player(row["player_id"])


def _hero(row: pd.Series, table: pd.DataFrame) -> None:
    pid, name, group = row["player_id"], row["player_name"], row["position_group_fine"]
    photo = shell.photo_uri(pid, name, 128)
    credit = player_images.CREDIT_LINE if shell.has_photo(pid) else "No photo available"
    arch = row["archetype_label"] if isinstance(row["archetype_label"], str) else None

    def headline(metric: str) -> tuple[str, str]:
        hit = table[table["metric"] == metric]
        if hit.empty:
            return metric_label(metric), "–"
        r = hit.iloc[0]
        val = r["per90"] if r["per90"] is not None and not pd.isna(r["per90"]) else r["value"]
        pct = "" if r["percentile"] is None or pd.isna(r["percentile"]) else f" · {design.ordinal(r['percentile'])}"
        suffix = "%" if r["unit"] else ""
        label = metric_label(metric) + ("" if r["unit"] else " /90")
        return label, f"{design.fmt_value(val, suffix)}{pct}"

    primary = headline(analytics_dir.PRIMARY_METRIC[group])
    secondary = headline(analytics_dir.SECONDARY_METRIC[group])
    with st.container(key="ficard_hero"):
        left, mid, right = st.columns([1.1, 3, 2.2], vertical_alignment="center")
        with left:
            design.render(
                f'<div class="fi-col" style="align-items:center">'
                f"{design.player_avatar(name, 128, photo)}"
                f'<span class="fi-credit">{design.esc(credit)}</span></div>'
            )
        with mid:
            design.render(
                f'<h1 class="fi-h1">{design.esc(name)}</h1>'
                f'<div class="fi-row" style="margin:{design.SP_S}px 0">'
                f"{design.position_badge(group)}"
                + (design.badge(f"Archetype: {arch}", "highlight") if arch else "")
                + "</div>"
                f'<div class="fi-body">{design.esc(row["team"])} · '
                f"{design.esc(shell.LEAGUE_SHORT[row['league']])} · {data.SEASON_LABEL}</div>"
            )
            if st.button(f"Plays for → {row['team']}", key=f"team_link_{pid}"):
                shell.open_team(row["team"])
        with right:
            design.render(
                '<div class="fi-row" style="justify-content:flex-end">'
                + design.big_stat("Minutes", f"{row['minutes_total']:,.0f}")
                + design.big_stat(*primary)
                + design.big_stat(*secondary)
                + "</div>"
            )
        design.render(design.link_button("Find similar players", "#similar-players"))


# ---- B. radar --------------------------------------------------------------------------------


@st.fragment
def _radar_section(row: pd.Series, table: pd.DataFrame) -> None:
    group, pid = row["position_group_fine"], row["player_id"]
    design.render(design.section_header("Profile radar", f"Within {group}, pooled across 4 leagues"))
    items = analytics_dir.radar_items(data.percentiles(pid), group)
    if len(items) < 3:
        design.render(design.empty_state("Not enough ranked metrics for a radar."))
        return
    mode_label = st.radio(
        "Show", ["Percentiles", "Raw values"], horizontal=True, key=f"radar_mode_{pid}",
        help="Raw values are drawn as a share of the highest value in the position group.",
    )
    mode = "percentile" if mode_label == "Percentiles" else "raw"
    ref = data.reference(group, tuple(i["metric"] for i in items)) if mode == "raw" else None
    labels = {i["metric"]: metric_label(i["metric"]) for i in items}
    fig = design.radar_chart(
        [{"name": row["player_name"], "items": items}], group, mode=mode, reference=ref, labels=labels
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    ordered = sorted(items, key=lambda i: -i["score"])
    summary = "; ".join(
        f"{labels[i['metric']]} {design.ordinal(i['percentile'])} percentile" for i in ordered
    )
    with st.expander("Chart description (text alternative)"):
        st.write(f"Radar of {row['player_name']} on {len(items)} metrics: {summary}.")
        if any(i["flipped"] for i in items):
            st.caption("Metrics where a lower number is better are plotted flipped, so outward is always better.")
    shown = {i["metric"] for i in items}
    explain = table[table["metric"].isin(shown)].copy()
    explain["Metric"] = explain["metric"].map(metric_label)
    explain["What it measures"] = explain["metric"].map(metric_description)
    explain["Value"] = [
        design.fmt_value(v if p is None or pd.isna(p) else p, "%" if u else "")
        for v, p, u in zip(explain["value"], explain["per90"], explain["unit"], strict=True)
    ]
    explain["Percentile"] = explain["percentile"]
    st.dataframe(
        explain[["Metric", "What it measures", "Value", "Percentile"]],
        hide_index=True, use_container_width=True,
        column_config={"Percentile": st.column_config.NumberColumn(format="%.0f")},
    )  # fmt: skip


# ---- C. strengths and weaknesses -------------------------------------------------------------


def _bar_rows(items: list[dict], group: str, tone: str) -> str:
    if not items:
        return '<span class="fi-muted">None flagged: no metric in the top or bottom 20%.</span>'
    out = []
    for it in items:
        out.append(
            '<div class="fi-row" style="justify-content:space-between;flex-wrap:nowrap;margin:'
            f'{design.SP_XS}px 0"><span>{design.esc(metric_label(it["metric"]))}</span>'
            + design.metric_badge(
                "Percentile", None, it["score"], group, tone=tone,
            )
            + "</div>"
        )
    return "".join(out)


def _strengths_weaknesses(row: pd.Series) -> None:
    group = row["position_group_fine"]
    cls = data.classified(row["player_id"], group)
    design.render(design.section_header("Strengths & weaknesses"))
    a, b = st.columns(2)
    with a:
        design.render(design.card("Strengths", _bar_rows(cls["strengths"][:8], group, "positive")))
    with b:
        design.render(design.card("Weaknesses", _bar_rows(cls["weaknesses"][:8], group, "negative")))
    st.caption("Percentiles within position group, pooled across 4 leagues.")


# ---- D. heatmap ------------------------------------------------------------------------------


@st.fragment
def _heatmap_section(row: pd.Series) -> None:
    pid = row["player_id"]
    design.render(design.section_header("Where he plays", "Event locations, 2015/16"))
    tabs = st.tabs(list(analytics_dir.HEATMAP_TYPES))
    for tab, (title, htype) in zip(tabs, analytics_dir.HEATMAP_TYPES.items(), strict=True):
        with tab:
            hm = data.heatmap(pid, htype)
            if hm is None or hm[1] == 0:
                design.render(
                    design.empty_state(
                        "No data for this heatmap type",
                        "Heatmaps are only generated for Premier League players so far."
                        if row["league"] != "ENG-Premier League" else "",
                    )
                )
                continue
            grid, n = hm
            fig = design.heatmap_pitch(grid.tolist(), f"{title}: {row['player_name']}")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False},
                            key=f"hm_{pid}_{htype}")
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


# ---- E. full stats table ---------------------------------------------------------------------


def _stats_table(row: pd.Series, table: pd.DataFrame) -> None:
    design.render(design.section_header("Full stats", "Grouped by category; click a header to sort"))
    view = pd.DataFrame(
        {
            "Category": table["category"],
            "Metric": table["metric"].map(metric_label),
            "Value": table["value"],
            "Per 90": table["per90"],
            "Percentile": table["percentile"],
            "Reading": table["band"],
        }
    ).sort_values("Category", kind="stable")
    scores = table.loc[view.index, "score"].tolist()
    styles = design.percentile_styles(scores)
    pct_col = view.columns.get_loc("Percentile")

    def paint(frame: pd.DataFrame) -> pd.DataFrame:
        out = pd.DataFrame("", index=frame.index, columns=frame.columns)
        out.iloc[:, pct_col] = styles
        return out

    st.dataframe(
        view.style.apply(paint, axis=None),
        hide_index=True, use_container_width=True, height=420,
        column_config={
            "Value": st.column_config.NumberColumn(format="%.2f", help="Season total for counts; the ratio itself for % metrics"),
            "Per 90": st.column_config.NumberColumn(format="%.2f"),
            "Percentile": st.column_config.NumberColumn(format="%.0f"),
        },
    )  # fmt: skip
    st.caption(
        "Percentile colour: strong (70+), average, weak (under 30), judged on the good end "
        "(for goals conceded a low number is good). Descriptive metrics stay gray. "
        "The Reading column says the same in words."
    )
    st.download_button(
        "Download CSV", view.to_csv(index=False).encode("utf-8"),
        file_name=f"{row['player_name'].replace(' ', '_')}_2015_16_stats.csv", mime="text/csv",
    )  # fmt: skip


# ---- F. similar players ----------------------------------------------------------------------


def _why(shared: list) -> str:
    return "Why similar: " + "; ".join(
        f"{metric_label(m.metric)} {design.fmt_value(m.target_value)} vs {design.fmt_value(m.candidate_value)}"
        for m in shared
    )


@st.fragment
def _similar_section(row: pd.Series) -> None:
    pid, name = row["player_id"], row["player_name"]
    design.render(
        design.section_header(
            "Similar players",
            "Ranked inside his archetype on the metrics that define it (a heuristic, not validated)",
            anchor="similar-players",
        )
    )
    c1, c2 = st.columns([2, 2])
    team_mode = c1.radio(
        "Team", ["Any team", "Same team only", "Exclude same team"], horizontal=True,
        key=f"sim_team_{pid}",
    )
    min_minutes = c2.slider("Minimum minutes", 900, 3400, 900, step=100, key=f"sim_min_{pid}")
    try:
        with st.spinner("Finding similar players..."):
            res = data.similar(
                name, SIMILAR_N, team_mode == "Same team only", team_mode == "Exclude same team",
                float(min_minutes),
            )
    except ValueError as exc:
        design.render(design.note(design.esc(str(exc))))
        return
    for w in res.warnings:
        design.render(design.note(design.esc(w)))
    if not res.similar:
        design.render(design.empty_state("No similar players with these filters.", "Loosen a filter."))
        return
    directory = shell.load_directory().set_index("player_id")
    cols = st.columns(2)
    for i, s in enumerate(res.similar):
        with cols[i % 2], st.container(key=f"ficard_sim_{s.player_id}"):
            league = shell.LEAGUE_SHORT.get(directory.loc[s.player_id, "league"], "")
            why = _why(s.shared_metrics)
            design.render(
                f'<div class="fi-mini" title="{design.esc(why)}">'
                + design.player_avatar(s.player_name, 32, shell.photo_uri(s.player_id, s.player_name, 32))
                + f'<div class="fi-col" style="flex:1"><span class="fi-name" style="font-size:{design.SIZE_BODY}px">'
                f"{s.rank}. {design.esc(s.player_name)}</span>"
                f'<span class="fi-muted">{design.position_badge(s.position_group_fine)} '
                f"{design.esc(s.team)} · {design.esc(league)}</span></div>"
                f'<div class="fi-stat" style="min-width:64px"><div class="fi-num" style="font-size:{design.SIZE_H3}px">'
                f'{design.fmt_match_pct(s.similarity)}</div><div class="fi-stat-label">match</div></div></div>'
            )
            if st.button("Open profile", key=f"sim_open_{pid}_{s.player_id}", help=why):
                shell.open_player(s.player_id)
    if st.button("Compare with these players", key=f"cmp_{pid}"):
        shell.open_compare([pid, *(s.player_id for s in res.similar[:2])])
    st.caption(
        f"Pool: {res.group_size} players in the '{res.archetype_label}' archetype, "
        f"{res.candidates_considered} after filters. Match % shows how close the playing profile is: 100% would be an identical profile."
    )


# ---- G, H ------------------------------------------------------------------------------------


def _career_placeholder() -> None:
    design.render(design.section_header("Career context"))
    with st.container(key="ficard_career"):
        design.render(
            '<div class="fi-name">Career &amp; Transfers</div>'
            '<div class="fi-muted">Coming in a future update. No career or transfer data '
            "is shown here because none is loaded.</div>"
        )
        st.button("Learn more", disabled=True, key="career_more")


def _quality_notes(row: pd.Series) -> None:
    notes: list[str] = []
    if row["n_teams"] > 1:
        notes.append(
            f"<b>Played for {int(row['n_teams'])} clubs.</b> Minutes are split: "
            f"{design.esc(row['team_split'])}. The team shown is the main one."
        )
    corrections = data.correction_counts().get(row["player_name"], 0)
    if corrections:
        notes.append(
            f"<b>This player has {corrections} lineup correction{'s' if corrections > 1 else ''}.</b> "
            "StatsBomb's lineup clock was repaired for his matches. See Methodology."
        )
    conf = row["cluster_confidence"]
    if analytics_dir.confidence_is_soft(conf):
        notes.append(f"<b>Archetype assignment is soft for this player</b> (confidence {conf:.2f}).")
    if row["position_group_fine_share"] < 0.6:
        notes.append(
            f"<b>Mixed role:</b> only {row['position_group_fine_share']:.0%} of his minutes were "
            f"as a {row['position_group_fine']}; his percentiles compare him with that group."
        )
    design.render(design.section_header("Data quality"))
    if notes:
        for n in notes:
            design.render(design.note(n))
    else:
        design.render(design.note("No data-quality flags for this player.", kind="info"))
    st.page_link("pages/5_methodology.py", label="Methodology and known limitations →")


# ---- page ------------------------------------------------------------------------------------

directory = shell.load_directory()
pid = _selected_id(directory)
if pid is None:
    _pick_player(directory)
    st.stop()

row = analytics_dir.player_row(directory, pid)
assert row is not None
try:
    table = analytics_dir.stats_table(
        data.percentiles(pid), data.totals(pid), row["position_group_fine"]
    )
except Exception:  # noqa: BLE001 - a clear message, not a traceback
    shell.fail(f"Could not load the stats for {row['player_name']}.")
    st.stop()

_hero(row, table)
_radar_section(row, table)
_strengths_weaknesses(row)
_heatmap_section(row)
_stats_table(row, table)
_similar_section(row)
_career_placeholder()
_quality_notes(row)
shell.refresh_when_photos_ready()
