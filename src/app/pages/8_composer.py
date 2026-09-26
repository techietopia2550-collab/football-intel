"""Tactical Composer: pick a target team style, a market-value budget and a formation; see three
suggested XIs whose players statistically fit that style. Presentation only: fit scores and the
greedy build come from `src.analytics.composer` through `src.app.data`."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import streamlit as st

from src.analytics.composer import PRESETS, InfeasibleBudget
from src.analytics.formations import FORMATIONS
from src.analytics.style_fit import PLAYER_AXES
from src.analytics.team_profile import RADAR_LABELS
from src.analytics.team_style import AXIS_COLUMNS
from src.app import data, design, shell
from src.models.composer import Squad

BUDGET_DEFAULT_M, BUDGET_MAX_M = 150, 500
UNITS = {
    "possession_pct": "%", "ppda": " PPDA", "direct_speed": " yd/pass", "buildup_length": " passes",
    "def_line_height": " yd", "width": " yd", "crosses_p90": " /match", "progressive_pass_rate": "",
    "xg_per_shot": " xG", "shots_pm": " /match", "transition_secs": " s", "set_piece_xg_pct": "%",
}  # fmt: skip


def _key(axis: str) -> str:
    return f"cmp_{axis}"


def _init_state() -> None:
    ss = st.session_state
    for axis in AXIS_COLUMNS:
        ss.setdefault(_key(axis), 50)
    ss.setdefault("cmp_preset", None)
    ss.setdefault("cmp_budget", BUDGET_DEFAULT_M)


def _apply_preset(name: str) -> None:
    for axis, value in PRESETS[name].items():
        st.session_state[_key(axis)] = int(value)
    st.session_state["cmp_preset"] = name
    st.session_state.pop("cmp_result", None)


def _raw_text(axis: str, score: float) -> str:
    raw = data.axis_raw_at(axis, score)
    if axis == "progressive_pass_rate":
        return f"≈ {raw * 100:.1f}% of passes"
    return f"≈ {raw:.2f}{UNITS[axis]}" if raw < 10 else f"≈ {raw:.1f}{UNITS[axis]}"


def _presets() -> None:
    design.render(design.section_header(
        "1. Target style", "Pick a preset, then adjust any axis (0-100 = rank among the 80 teams)"
    ))  # fmt: skip
    cols = st.columns(len(PRESETS))
    for col, name in zip(cols, PRESETS, strict=True):
        active = st.session_state["cmp_preset"] == name
        col.button(name, key=f"preset_{name}", width="stretch",
                   type="primary" if active else "secondary",
                   on_click=_apply_preset, args=(name,))  # fmt: skip


def _sliders() -> dict[str, float]:
    preset = PRESETS.get(st.session_state["cmp_preset"] or "")
    left, right = st.columns([3, 2], gap="large")
    with left:
        grid = st.columns(3)
        for i, axis in enumerate(AXIS_COLUMNS):
            used = axis in PLAYER_AXES
            with grid[i % 3]:
                st.slider(
                    RADAR_LABELS[axis], 0, 100, key=_key(axis), disabled=not used,
                    help=(f"50 = the median team. Preset: {preset[axis]:.0f}." if preset else
                          "50 = the median team.") + ("" if used else
                          " Team-level only: no player metric measures it, so it is not used."),
                )  # fmt: skip
                st.caption(_raw_text(axis, st.session_state[_key(axis)]) if used else "not used")
    target = {a: float(st.session_state[_key(a)]) for a in AXIS_COLUMNS}
    with right:
        rows = [{"label": RADAR_LABELS[a], "value": target[a], "used": a in PLAYER_AXES,
                 "preset": preset[a] if preset else None} for a in AXIS_COLUMNS]  # fmt: skip
        st.plotly_chart(design.style_target_chart(rows), width="stretch",
                        config={"displayModeBar": False}, key="cmp_target_chart")  # fmt: skip
    return target


def _constraints() -> tuple[float, str, list[str]]:
    design.render(design.section_header("2. Budget, formation, leagues"))
    c1, c2, c3 = st.columns([2, 2, 2], gap="large")
    with c1:
        budget_m = st.slider("Budget (€m)", 0, BUDGET_MAX_M, step=5, key="cmp_budget")
        st.caption(
            f"Total squad market value must not exceed €{budget_m}m (Transfermarkt, 2015/16)."
        )
        leagues = st.multiselect(
            "Leagues (optional)", list(shell.SHORT_TO_LEAGUE), key="cmp_leagues",
            placeholder="All four leagues",
        )  # fmt: skip
    with c2:
        formation = st.selectbox("Formation", list(FORMATIONS), key="cmp_formation")
        if FORMATIONS[formation].note:
            st.caption(FORMATIONS[formation].note)
    with c3:
        pts = [{"x": s.x, "y": s.y, "group": s.group, "label": s.label}
               for s in FORMATIONS[formation].slots]  # fmt: skip
        st.plotly_chart(design.formation_pitch(pts, height=200), width="stretch",
                        config={"displayModeBar": False, "staticPlot": True},
                        key="cmp_shape")  # fmt: skip
    return budget_m * 1e6, formation, [shell.SHORT_TO_LEAGUE[x] for x in leagues]


def _pct(fit: float) -> str:
    return f"{fit * 100:.0f}%"


def _squad_view(sq: Squad, tab: str) -> None:
    value = f"{design.fmt_eur(sq.total_value_eur)} of {design.fmt_eur(sq.budget_eur)} budget"
    design.render(
        '<div class="fi-row">'
        + design.big_stat("Total market value", value)
        + design.big_stat("Average fit", _pct(sq.avg_fit))
        + design.big_stat("Clubs", str(sq.distinct_clubs))
        + "</div>"
    )
    for n in sq.notes:
        st.caption(n)
    pts = [{"x": s.x, "y": s.y, "group": s.group, "label": s.slot_label, "name": s.player_name,
            "hover": f"{s.player_name} · {s.team} · {design.fmt_eur(s.market_value_eur)} · "
                     f"fit {_pct(s.fit)}"} for s in sq.slots]  # fmt: skip
    st.plotly_chart(design.formation_pitch(pts, height=420, names=True), width="stretch",
                    config={"displayModeBar": False}, key=f"cmp_pitch_{tab}")  # fmt: skip
    cols = st.columns(4)
    for i, s in enumerate(sq.slots):
        with cols[i % 4], st.container(key=f"ficard_cmp_{tab}_{i}"):
            design.render(
                '<div class="fi-row" style="flex-wrap:nowrap">'
                + design.player_avatar(
                    s.player_name, 44, shell.photo_uri(s.player_id, s.player_name, 44)
                )
                + f'<div class="fi-col"><span class="fi-name">{design.esc(s.player_name)}</span>'
                f"<span>{design.position_badge(s.group)} "
                f'<span class="fi-muted">{design.esc(s.slot_label)}</span></span>'
                f'<span class="fi-muted">{design.esc(s.team)} · '
                f"{design.esc(shell.LEAGUE_SHORT.get(s.league, s.league))}</span>"
                f"<span>{design.fmt_eur(s.market_value_eur)} · fit {_pct(s.fit)}</span></div></div>"
            )
            if st.button("Profile →", key=f"cmp_open_{tab}_{i}_{s.player_id}"):
                shell.open_player(s.player_id)
    with st.expander("Why these players"):
        st.markdown(
            "**Axes that drove the picks** (summed contribution to the XI's fit): "
            + ", ".join(f"**{a.label}**" for a in sq.top_axes)
        )
        st.markdown(
            "\n".join(["| Axis | Target | XI average | Used |", "|---|---|---|---|"] + [
                f"| {b.label} | {b.target:.0f} | "
                f"{'–' if b.squad_mean is None else f'{b.squad_mean:.0f}'} | "
                f"{'yes' if b.used else 'no, team-level only'} |" for b in sq.breakdown
            ])
        )  # fmt: skip
        st.markdown(
            "\n".join(["| Slot | Player | Fit | Top 3 axes |", "|---|---|---|---|"] + [
                f"| {s.slot_label} | {s.player_name} | {_pct(s.fit)} | "
                f"{', '.join(a.label for a in s.top_axes)} |" for s in sq.slots
            ])
        )  # fmt: skip
        st.caption(
            "Fit compares where each player sits against his own position group (above or below "
            "the median) with where the target sits against the 80 teams. It describes playing "
            "style from event counts, not quality: a cheap player can fit a style better than a "
            "star. See Methodology."
        )


def _results(target: dict[str, float], budget: float, formation: str, leagues: list[str]) -> None:
    design.render(design.section_header("3. Suggested XIs"))
    if st.button("Build", type="primary", key="cmp_build"):
        try:
            st.session_state["cmp_result"] = data.composer_variants(
                tuple(sorted(target.items())), budget, formation, tuple(leagues),
                shell.MIN_MINUTES_FLOOR,
            )  # fmt: skip
            st.session_state.pop("cmp_error", None)
        except InfeasibleBudget as exc:
            st.session_state.pop("cmp_result", None)
            st.session_state["cmp_error"] = str(exc)
    if "cmp_error" in st.session_state:
        design.render(design.note(design.esc(st.session_state["cmp_error"])))
        return
    squads: list[Squad] | None = st.session_state.get("cmp_result")
    if not squads:
        design.render(design.empty_state("Pick a style preset and a budget, then click Build."))
        return
    tabs = st.tabs([sq.title for sq in squads])
    for tab, sq in zip(tabs, squads, strict=True):
        with tab:
            _squad_view(sq, sq.variant)


design.render(
    '<div class="fi-h1">Tactical Composer</div>'
    '<div class="fi-muted">Suggests an XI whose players statistically fit a team style within a '
    "market-value budget. It does not build tactics and predicts no results, form or "
    "chemistry.</div>"
)
if data.style_fit().empty:
    shell.fail(
        "Style-fit scores are not built yet: run `python -m src.analytics.style_fit --rebuild`."
    )
_init_state()
_presets()
_target = _sliders()
_budget, _formation, _leagues = _constraints()
_results(_target, _budget, _formation, _leagues)
shell.methodology_link("How the Composer works →")
