"""Player profile: a hero card above three tabs. Profile: quick radar, position, full radar, strengths and
weaknesses, heatmap, similar players, transfer value. Matches: standout games. Stats: full table."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from datetime import date

import pandas as pd
import streamlit as st

from src.analytics import directory as analytics_dir
from src.analytics import games as games_mod
from src.app import data, design, player_images, shell
from src.app.labels import QUICK_RADAR, QUICK_RADAR_STAND_INS, metric_description, metric_label
from src.models.match import GameCard, SeasonGameStats

SIMILAR_N = 10
SEASON_START = date(2015, 8, 1)  # age is quoted at the start of 2015/16


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


def _hero_stats(
    row: pd.Series, table: pd.DataFrame, gs: SeasonGameStats | None
) -> list[tuple[str, str]]:
    """The position-aware tiles, most important first. A stat that is unavailable gets no tile."""
    group = row["position_group_fine"]

    def headline(metric: str) -> tuple[str, str] | None:
        hit = table[table["metric"] == metric]
        if hit.empty:
            return None
        r = hit.iloc[0]
        val = r["per90"] if r["per90"] is not None and not pd.isna(r["per90"]) else r["value"]
        if val is None or pd.isna(val):
            return None
        pct = "" if r["percentile"] is None or pd.isna(r["percentile"]) else f" · {design.ordinal(r['percentile'])}"
        suffix = "%" if r["unit"] else ""
        label = metric_label(metric) + ("" if r["unit"] else " /90")
        return label, f"{design.fmt_value(val, suffix)}{pct}"

    minutes = ("Minutes", f"{row['minutes_total']:,.0f}")
    games = None if gs is None else ("Games", str(gs.games))
    if group == "GK":
        tiles: list[tuple[str, str] | None] = [
            minutes, games,
            None if gs is None or gs.clean_sheets is None else ("Clean sheets", str(gs.clean_sheets)),
            None if gs is None or gs.saves is None else ("Saves", str(gs.saves)),
            None if gs is None or gs.save_pct is None else ("Save %", design.fmt_value(gs.save_pct, "%")),
            None if gs is None or gs.goals_conceded is None else ("Goals conceded", str(gs.goals_conceded)),
        ]  # fmt: skip
    else:
        tiles = [
            minutes, games,
            None if gs is None or gs.goals is None else ("Goals", str(gs.goals)),
            None if gs is None or gs.assists is None else ("Assists", str(gs.assists)),
            headline(analytics_dir.PRIMARY_METRIC[group]),
            headline(analytics_dir.SECONDARY_METRIC[group]),
        ]  # fmt: skip
    return [t for t in tiles if t is not None]


def _bio_line(pid: str) -> str:
    """Age on 1 Aug 2015 and nationality (text), or "" when Wikipedia has neither."""
    born, nationality, _ = player_images.load_bio(pid)
    age = games_mod.age_on(born, SEASON_START)
    parts = []
    if age is not None and born is not None:
        parts.append(f'Age {age} <span class="fi-muted">(born {born:%d %b %Y})</span>')
    if nationality:
        parts.append(design.badge(nationality, "neutral", "Nationality (Wikidata)"))
    return " · ".join(parts)


def _hero(row: pd.Series, table: pd.DataFrame) -> None:
    pid, name, group = row["player_id"], row["player_name"], row["position_group_fine"]
    photo = shell.photo_uri(pid, name, 128, want_bio=True)
    credit = player_images.CREDIT_LINE if shell.has_photo(pid) else "No photo available"
    arch = row["archetype_label"] if isinstance(row["archetype_label"], str) else None
    tiles = _hero_stats(row, table, data.season_game_stats(pid, group))
    bio = _bio_line(pid)
    with st.container(key="ficard_hero"):
        left, mid = st.columns([1.1, 5.2], vertical_alignment="center")
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
                f'<div class="fi-body">{design.team_logo(row["team"], 24)} '
                f'{design.esc(row["team"])} · '
                f"{design.esc(shell.LEAGUE_SHORT[row['league']])} · {data.SEASON_LABEL}</div>"
                + (f'<div class="fi-body" style="margin-top:{design.SP_XS}px">{bio}</div>' if bio else "")
                + f'<div class="fi-body" style="margin-top:{design.SP_XS}px">'
                f"{shell.wage_line(row['team'], row['league'], 'Team wage bill')}</div>"
                + f'<div class="fi-body" style="margin-top:{design.SP_XS}px">'
                f"{shell.market_value_line(pid)}</div>"
            )
            if st.button(f"Plays for → {row['team']}", key=f"team_link_{pid}"):
                shell.open_team(row["team"])
        design.render(
            f'<div class="fi-row" style="justify-content:space-between;margin-top:{design.SP_S}px">'
            + "".join(design.big_stat(label, value) for label, value in tiles)
            + "</div>"
        )
        design.render(design.link_button("Find similar players", "#similar-players"))


# ---- B2. longest / best / worst game ---------------------------------------------------------


def _fmt_component(label: str, value: float) -> str:
    if label == "Save %":
        return f"{value * 100:.0f}%"
    return f"{value:g}" if float(value).is_integer() else f"{value:.2f}"


def _game_card(kind: str, symbol: str, card: GameCard | None, pid: str) -> None:
    with st.container(key=f"ficard_game_{kind}_{pid}"):
        if card is None:
            design.render(design.empty_state(f"No {kind} game", "Needs a match of 45+ minutes."))
            return
        venue = "home" if card.home else "away"
        body = (
            f'<div class="fi-card-title">{symbol} {design.esc(kind.title())} game</div>'
            f'<div class="fi-name">vs {design.esc(card.opponent)}</div>'
            f'<div class="fi-muted">{card.match_date:%d %b %Y} · {venue} · result '
            f"{design.esc(card.score)} · {card.minutes:.0f} min · "
            f"{design.esc(card.position_played or '-')}</div>"
        )
        if card.rating is not None:
            body += (
                f'<div class="fi-row" style="margin-top:{design.SP_S}px">'
                + design.big_stat("Rating", f"{card.rating:.1f}")
                + "".join(
                    design.big_stat(label, _fmt_component(label, v)) for label, v in card.components
                )
                + "</div>"
            )
        design.render(body)
        if st.button("View match", key=f"view_match_{kind}_{pid}"):
            shell.open_match(pid, card.match_id)


def _games_section(row: pd.Series) -> None:
    pid, group = row["player_id"], row["position_group_fine"]
    pg = data.player_games(pid, group)
    if pg.best is None:
        design.render(design.empty_state("No standout games", "Needs a match of 45+ minutes."))
        return
    design.render(
        design.section_header(
            "Standout games", f"Longest, best and worst of his {data.SEASON_LABEL} matches"
        )
    )
    a, b, c = st.columns(3)
    with a:
        _game_card("longest", "●", pg.longest, pid)
    with b:
        _game_card("best", "▲", pg.best, pid)
    with c:
        _game_card("worst", "▼", pg.worst, pid)
    st.caption(
        "Rating is a simple count for his position, comparing his own matches with each other "
        "(see Methodology). The worst game must have 45+ minutes."
    )


# ---- A2. quick radar and position ------------------------------------------------------------


def _quick_radar(row: pd.Series) -> None:
    group, pid = row["position_group_fine"], row["player_id"]
    by_metric = data.percentiles(pid).set_index("metric")["percentile"]
    items = []
    for metric, label in QUICK_RADAR[group]:
        pct = by_metric.get(metric)
        pct = None if pct is None or pd.isna(pct) else float(pct)
        score = analytics_dir.score_of(metric, pct)
        # a descriptive (volume) metric has no good end: plot its plain percentile
        items.append({"label": label, "percentile": pct, "score": pct if score is None else score})
    design.render(design.section_header("At a glance", f"Six key metrics for a {group}"))
    st.plotly_chart(
        design.quick_profile_radar(items, group), use_container_width=True,
        config={"displayModeBar": False}, key=f"quick_radar_{pid}",
    )  # fmt: skip
    caption = "Percentile vs position group (pooled 4 leagues). 100 = best."
    if group in QUICK_RADAR_STAND_INS:
        caption += " " + QUICK_RADAR_STAND_INS[group]
    st.caption(caption)


def _position_section(row: pd.Series) -> None:
    group, pid = row["position_group_fine"], row["player_id"]
    wide = isinstance(design.POSITION_SPOTS[group][1], tuple)
    side = data.side(pid) if wide else None
    notes = []
    if wide and side is None:
        notes.append("No clear side: both flanks shown.")
    share = row["position_group_fine_share"]
    if share < 0.9:
        notes.append(f"Primary position only: {share:.0%} of his minutes were as a {group}.")
    design.position_card(group, side, " ".join(notes), key=pid)


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
                        "This player has no events of this type in the 2015/16 data.",
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
def _value_text(eur: float | None) -> str:
    return f"Market value {design.fmt_eur(eur)}" if eur is not None else "Market value: no value available"


def _similar_section(row: pd.Series) -> None:
    pid, name, group = row["player_id"], row["player_name"], row["position_group_fine"]
    design.render(
        design.section_header(
            "Similar players",
            "Ranked inside his archetype by statistical closeness, not by role (see Methodology)",
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
    values = data.market_value_map()
    cols = st.columns(2)
    for i, s in enumerate(res.similar):
        with cols[i % 2], st.container(key=f"ficard_sim_{s.player_id}"):
            league = shell.LEAGUE_SHORT.get(directory.loc[s.player_id, "league"], "")
            why = _why(s.shared_metrics)
            cross = design.cross_position_note(s.position_group_fine, group)
            cross_html = (
                f'<span class="fi-muted" style="font-style:italic">{design.esc(cross)}</span>'
                if cross
                else ""
            )
            design.render(
                f'<div class="fi-mini" title="{design.esc(why)}">'
                + design.player_avatar(s.player_name, 32, shell.photo_uri(s.player_id, s.player_name, 32))
                + f'<div class="fi-col" style="flex:1"><span class="fi-name" style="font-size:{design.SIZE_BODY}px">'
                f"{s.rank}. {design.esc(s.player_name)} · "
                f"{design.position_badge(s.position_group_fine)}</span>"
                f'<span class="fi-muted">{design.esc(s.team)} · {design.esc(league)}</span>'
                f'<span class="fi-muted">{_value_text(values.get(s.player_id))}</span>'
                f"{cross_html}</div>"
                f'<div class="fi-stat" style="min-width:64px"><div class="fi-num" style="font-size:{design.SIZE_H3}px">'
                f'{design.fmt_match_pct(s.similarity)}</div><div class="fi-stat-label">match</div>'
                f"{design.match_tier_badge(s.similarity)}</div></div>"
            )
            if st.button("Open profile", key=f"sim_open_{pid}_{s.player_id}", help=why):
                shell.open_player(s.player_id)
    if st.button("Compare with these players", key=f"cmp_{pid}"):
        shell.open_compare([pid, *(s.player_id for s in res.similar[:2])])
    top = res.similar[0]
    with st.container(key=f"ficard_closest_{pid}"):
        c_img, c_txt, c_btn = st.columns([0.6, 5, 1.6], vertical_alignment="center")
        with c_img:
            design.render(
                design.player_avatar(
                    top.player_name, 48, shell.photo_uri(top.player_id, top.player_name, 48)
                )
            )
        with c_txt:
            design.render(
                f'<div class="fi-name">Closest profile match: {design.esc(top.player_name)} · '
                f"{design.position_badge(top.position_group_fine)} ({design.esc(top.team)}) &mdash; "
                f"{design.fmt_match_pct(top.similarity)} match "
                f"{design.match_tier_badge(top.similarity)}</div>"
            )
        if c_btn.button("View profile", key=f"closest_open_{pid}_{top.player_id}"):
            shell.open_player(top.player_id)
    st.caption(
        f"Pool: {res.group_size} players in the '{res.archetype_label}' archetype, "
        f"{res.candidates_considered} after filters. Match % shows how close the playing profile is: 100% would be an identical profile. "
        f"{design.MATCH_TIER_NOTE} Position is context only: the score does not use it, so "
        "'cross-position' marks a statistically close player in another position group."
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


def _transfer_value(pid: str) -> None:
    mv = data.player_market_value(pid)
    if mv is not None and mv.market_value_eur is not None and mv.valuation_date is not None:
        body = (
            f'<div class="fi-num" style="font-size:{design.SIZE_H2}px">'
            f"Market value: {design.fmt_eur(mv.market_value_eur)} ({mv.valuation_date:%b %Y})</div>"
            f'<div class="fi-muted">Transfermarkt, valued {mv.valuation_date:%d %b %Y}</div>'
        )
        why = (
            "Transfermarkt's community estimate of his transfer fee on that date: not a wage and "
            "not a fee that was paid. It is the latest valuation between 1 Oct 2015 and 1 Mar 2016. "
            f"Matched to Transfermarkt's '{mv.transfermarkt_name}' by name within his club "
            f"({mv.match_method} match). Source, licence and match rate: Methodology."
        )
    else:
        body = '<div class="fi-name">No value available</div>'
        why = (
            "He could not be matched to a Transfermarkt player at his club with confidence, so no "
            "value is shown and none is estimated."
            if mv is None or mv.match_method == "unmatched"
            else "He was matched to Transfermarkt, but it has no valuation for him between "
            "1 Oct 2015 and 1 Mar 2016, so no value is shown and none is estimated."
        )
    with st.container(key="ficard_transfer_value"):
        design.render('<div class="fi-card-title">Transfer value</div>' + body)
        with st.expander("About this value"):
            st.write(why)


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
    shell.methodology_link("Methodology and known limitations →")


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
profile_tab, matches_tab, stats_tab = st.tabs(["Profile", "Matches", "Stats"])
with profile_tab:
    _quick_radar(row)
    _position_section(row)
    _radar_section(row, table)
    _strengths_weaknesses(row)
    _heatmap_section(row)
    _similar_section(row)
    _career_placeholder()
    _quality_notes(row)
    _transfer_value(row["player_id"])
with matches_tab:
    _games_section(row)
with stats_tab:
    _stats_table(row, table)
shell.refresh_when_photos_ready()
