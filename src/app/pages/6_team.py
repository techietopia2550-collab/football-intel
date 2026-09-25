"""Team fingerprint: hero, style radar, similar teams, key players. Presentation only: every number
comes from `src.analytics.team_*` through `src.app.data`."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import pandas as pd
import streamlit as st

from src.analytics.team_profile import FLIPPED_AXES, RADAR_LABELS
from src.analytics.team_style import AXIS_COLUMNS
from src.app import data, design, player_images, shell
from src.models.team import TeamProfile

SIMILAR_N = 10
UNITS = {
    "possession_pct": "%", "direct_speed": " yd/pass", "buildup_length": " passes",
    "def_line_height": " yd from own goal", "width": " yd (st. dev.)", "crosses_p90": " /match",
    "progressive_pass_rate": "", "xg_per_shot": " xG", "shots_pm": " /match", "transition_secs": " s",
    "set_piece_xg_pct": "%", "ppda": "",
}  # fmt: skip


def _value_text(axis: str, value: float) -> str:
    if axis == "progressive_pass_rate":
        return f"{value * 100:.1f}% of passes"
    return f"{value:.2f}{UNITS[axis]}"


def _ordinal_position(pos: int | None) -> str:
    return design.ordinal(pos) if pos is not None else "–"


def _select_team(teams: pd.DataFrame) -> str | None:
    names = teams["team_name"].tolist()
    league_of = dict(zip(teams["team_name"], teams["league"], strict=True))
    current = st.session_state.get("selected_team") or st.query_params.get("team")
    current = current if current in names else None
    choice = st.selectbox(
        "Find a team", names, index=names.index(current) if current else None,
        format_func=lambda n: f"{n} · {shell.LEAGUE_SHORT.get(league_of[n], '')}",
        placeholder="Search a team, e.g. Leicester City", key=f"team_pick_{current}",
    )
    if choice and choice != current:
        st.session_state["selected_team"] = choice
        st.rerun()
    return current


def _hero(p: TeamProfile) -> None:
    with st.container(key="ficard_team_hero"):
        left, right = st.columns([3, 2], vertical_alignment="center")
        with left:
            design.render(
                f'<h1 class="fi-h1">{design.team_logo(p.team_name, 48)} {design.esc(p.team_name)}</h1>'
                f'<div class="fi-body">{design.esc(shell.LEAGUE_SHORT.get(p.league, p.league))} · '
                f"{data.SEASON_LABEL}</div>"
                f'<div class="fi-body" style="margin-top:4px">{shell.wage_line(p.team_name, p.league)}</div>'
                f'<div class="fi-body" style="margin-top:4px">{shell.squad_value_line(p.team_name, p.league)}</div>'
                '<div class="fi-row" style="margin-top:8px">'
                + "".join(design.badge(f, "highlight") for f in p.headline_facts)
                + "</div>"
            )
        with right:
            design.render(
                '<div class="fi-row" style="justify-content:flex-end">'
                + design.big_stat("League position", _ordinal_position(p.league_position))
                + design.big_stat("Points", "–" if p.points is None else str(p.points))
                + design.big_stat("Matches", "–" if p.played is None else str(p.played))
                + "</div>"
            )
    if p.played is not None and p.played < 38:
        design.render(
            design.note(
                f"StatsBomb lists only {p.played} of this team's 38 fixtures, so points and position "
                "are for those matches and may differ from the real final table.", "info"
            )
        )
    if p.matches_used < (p.played or 0):
        st.caption(f"Style is computed from {p.matches_used} matches with stored event data.")


def _radar_section(p: TeamProfile) -> None:
    design.render(
        design.section_header(
            "Style radar", "Rank among all 80 teams (100 = highest), from observable event patterns"
        )
    )
    labels = [RADAR_LABELS[a] for a in AXIS_COLUMNS]
    fig = design.team_radar(
        p.team_name, labels, [p.scores[a] for a in AXIS_COLUMNS], [p.values[a] for a in AXIS_COLUMNS],
        [_value_text(a, p.values[a]) for a in AXIS_COLUMNS],
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    st.caption(
        "The dotted ring is the median team. Pressing and transition speed are flipped so outward always "
        "means more of it (low PPDA, fewer seconds to a shot). This describes what the events look like, "
        "not tactical intent."
    )
    with st.expander("Raw values"):
        table = pd.DataFrame(
            {
                "Axis": [RADAR_LABELS[a] + (" (flipped)" if a in FLIPPED_AXES else "") for a in AXIS_COLUMNS],
                "Value": [_value_text(a, p.values[a]) for a in AXIS_COLUMNS],
                "Rank score": [round(p.scores[a]) for a in AXIS_COLUMNS],
            }
        )
        st.dataframe(table, hide_index=True, use_container_width=True)


def _logo_refresh(names: list[str]) -> None:
    """Logos come from the local cache only; this button fetches the missing ones on demand."""
    credits = player_images.load_logo_credits()
    missing = [n for n in names if not player_images.has_team_logo(n)
               and credits.get(n, {}).get("status") != "missing"]  # fmt: skip
    if missing and st.button(f"Fetch {len(missing)} missing logos", key="fetch_logos",
                             help="Downloads them from Wikipedia now; they are cached for next time."):
        with st.spinner("Fetching logos..."):
            player_images.fetch_missing_logos(missing)
        st.rerun()


def _similar_section(p: TeamProfile) -> None:
    design.render(
        design.section_header(
            "Similar teams", "Closest style vectors across all four leagues (a heuristic, not validated)"
        )
    )
    res = data.similar_teams(p.team_name, SIMILAR_N)
    _logo_refresh([p.team_name, *(s.team_name for s in res.similar)])
    cols = st.columns(2)
    for i, s in enumerate(res.similar):
        with cols[i % 2], st.container(key=f"ficard_simteam_{i}"):
            why = f"Closest on: {', '.join(s.closest_axes)}. Biggest gaps: {', '.join(s.biggest_gaps)}."
            design.render(
                f'<div class="fi-mini" title="{design.esc(why)}">{design.team_logo(s.team_name, 32)}'
                f'<div class="fi-col" style="flex:1"><span class="fi-name" style="font-size:{design.SIZE_BODY}px">'
                f"{s.rank}. {design.esc(s.team_name)}</span>"
                f'<span class="fi-muted">{design.esc(shell.LEAGUE_SHORT.get(s.league, s.league))} · '
                f"closest on {design.esc(', '.join(s.closest_axes[:2]))}</span></div>"
                f'<div class="fi-stat" style="min-width:64px"><div class="fi-num" '
                f'style="font-size:{design.SIZE_H3}px">{design.fmt_match_pct(s.similarity)}</div>'
                '<div class="fi-stat-label">match</div></div></div>'
            )
            if st.button("Open fingerprint", key=f"simteam_open_{i}", help=why):
                shell.open_team(s.team_name)
    st.caption(
        f"Pool: {res.teams_in_pool} teams. Match % shows how close the style profile is: 100% would be an "
        "identical profile. A low or negative score is shown as 0%."
    )


def _key_players_section(p: TeamProfile) -> None:
    design.render(design.section_header("Key players", "Most minutes played, with their playing archetype"))
    players, mix = data.team_key_players(p.team_name, p.league)
    if not players:
        design.render(design.empty_state("No ranked players for this team."))
        return
    design.render(f'<div class="fi-body">Archetype mix: {design.esc(mix)}</div>')
    directory = shell.load_directory().set_index("player_id")
    for pl in players:
        with st.container(key=f"ficard_kp_{pl.player_id}"):
            c1, c2 = st.columns([5, 1], vertical_alignment="center")
            with c1:
                design.render(
                    '<div class="fi-mini">'
                    + design.player_avatar(pl.player_name, 32, shell.photo_uri(pl.player_id, pl.player_name, 32))
                    + f'<div class="fi-col" style="flex:1"><span class="fi-name" style="font-size:{design.SIZE_BODY}px">'
                    f"{design.esc(pl.player_name)}</span>"
                    f'<span class="fi-muted">{design.position_badge(pl.position_group_fine)} '
                    f"{design.esc(pl.archetype_label or 'Goalkeeper')} · {pl.minutes_total:,.0f} min</span></div></div>"
                )
            with c2:
                if st.button("Profile", key=f"kp_open_{pl.player_id}") and pl.player_id in directory.index:
                    shell.open_player(pl.player_id)


design.render('<div class="fi-muted">Team fingerprint</div>')
try:
    teams = data.teams()
except LookupError:
    shell.fail("Team style is not built yet: run `python -m src.analytics.team_style`.")
    st.stop()

selected = _select_team(teams)
if not selected:
    design.render(design.empty_state("Pick a team to see its style fingerprint.", "Search by name above."))
    st.stop()

profile = data.team_profile(selected)
_hero(profile)
left, right = st.columns([3, 2])
with left:
    _radar_section(profile)
with right:
    _similar_section(profile)
_key_players_section(profile)
shell.refresh_when_photos_ready()
