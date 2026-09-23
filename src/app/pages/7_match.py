"""Match page, laid out like a match recap: header, timeline, the player's card, his heatmap in this
match, team-stat bars and both lineups with each player's events.

Presentation only. Events, lineups and team totals come from `src.analytics.match_report` (through
`src.app.data`); the heatmap from `src.analytics.heatmaps`. Team logos are read from the local cache
only (see `player_images`), so nothing here waits on the network.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from urllib.parse import quote

import streamlit as st

from src.analytics import directory as analytics_dir
from src.analytics import games as games_mod
from src.app import data, design, shell
from src.models.match_report import LineupPlayer, MatchEvent, MatchReport, TeamLineup, TeamStats

MAX_WIDTH = 900
HOME_COLOR, AWAY_COLOR = design.HIGHLIGHT, design.NEGATIVE

_CSS = f"""
[data-testid="stAppViewContainer"] .block-container {{ max-width:{MAX_WIDTH}px; }}
.mr-meta {{ text-align:center; color:var(--t2); font-size:{design.SIZE_SMALL}px; margin-bottom:{design.SP_M}px; }}
.mr-head {{ display:grid; grid-template-columns:1fr auto 1fr; align-items:center; gap:{design.SP_M}px; }}
.mr-team {{ display:flex; flex-direction:column; align-items:center; gap:{design.SP_S}px; text-align:center; }}
.mr-score {{ font-size:40px; font-weight:{design.WEIGHT_HEADING}; line-height:1; text-align:center;
  font-variant-numeric:tabular-nums; }}
.mr-status {{ text-align:center; color:var(--t2); font-size:{design.SIZE_SMALL}px; margin-top:{design.SP_S}px; }}
.mr-tl {{ display:flex; flex-direction:column; gap:{design.SP_XS}px; }}
.mr-ev {{ display:grid; grid-template-columns:48px 28px 1fr 24px; align-items:center; gap:{design.SP_S}px;
  padding:{design.SP_XS}px {design.SP_S}px; border-left:3px solid transparent; border-radius:0 {design.RADIUS_BUTTON} {design.RADIUS_BUTTON} 0; }}
.mr-ev.me {{ border-left-color:var(--hi); background:rgba(88,166,255,0.10); }}
.mr-min {{ color:var(--t2); font-variant-numeric:tabular-nums; font-weight:{design.WEIGHT_NUMBER}; }}
.mr-ev-sub {{ color:var(--t2); font-size:{design.SIZE_SMALL}px; }}
.mr-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:{design.SP_M}px; margin-top:{design.SP_M}px; }}
.mr-cell b {{ display:block; font-size:{design.SIZE_H3}px; font-weight:{design.WEIGHT_NUMBER}; font-variant-numeric:tabular-nums; }}
.mr-cell span {{ color:var(--t2); font-size:{design.SIZE_SMALL}px; }}
.mr-stat {{ margin:{design.SP_M}px 0; }}
.mr-stat-top {{ display:grid; grid-template-columns:1fr auto 1fr; align-items:baseline; font-variant-numeric:tabular-nums; }}
.mr-stat-top b {{ font-weight:{design.WEIGHT_NUMBER}; font-size:{design.SIZE_BODY}px; }}
.mr-stat-top span {{ color:var(--t2); font-size:{design.SIZE_SMALL}px; text-align:center; }}
.mr-bars {{ display:grid; grid-template-columns:1fr 1fr; gap:{design.SP_XS}px; margin-top:{design.SP_XS}px; }}
.mr-half {{ height:8px; background:var(--bg3); border-radius:{design.RADIUS_BADGE}; display:flex; overflow:hidden; }}
.mr-half.l {{ flex-direction:row-reverse; }}
.mr-half i {{ display:block; height:100%; border-radius:{design.RADIUS_BADGE}; }}
.mr-side {{ display:flex; align-items:center; gap:{design.SP_S}px; margin-bottom:{design.SP_S}px; }}
.mr-form {{ color:var(--t2); font-size:{design.SIZE_SMALL}px; margin-left:auto; }}
.mr-sub {{ color:var(--t2); font-size:{design.SIZE_SMALL}px; margin:{design.SP_M}px 0 {design.SP_XS}px; }}
a.mr-p, div.mr-p {{ display:block; text-decoration:none; color:var(--t1) !important; background:var(--bg2);
  border:1px solid var(--border); border-radius:{design.RADIUS_BUTTON}; padding:{design.SP_S}px {design.SP_S + 4}px;
  margin-bottom:{design.SP_XS + 2}px; }}
a.mr-p:hover {{ border-color:var(--hi); }}
.mr-p.off {{ cursor:default; }}
.mr-p.me {{ border-color:var(--hi); background:rgba(88,166,255,0.10); }}
.mr-p.bench {{ opacity:.7; }}
.mr-p-row {{ display:flex; align-items:center; gap:{design.SP_S}px; }}
.mr-jersey {{ min-width:24px; color:var(--t2); font-variant-numeric:tabular-nums; font-weight:{design.WEIGHT_NUMBER}; }}
.mr-pname {{ flex:1; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-weight:{design.WEIGHT_NUMBER}; }}
.mr-p .fi-badges {{ margin-top:{design.SP_XS}px; padding-left:32px; }}
@media (max-width:{design.BREAKPOINT_MOBILE}px) {{
  .mr-head {{ grid-template-columns:1fr; justify-items:center; }}
  .mr-grid {{ grid-template-columns:repeat(2,1fr); }}
  .mr-score {{ font-size:32px; }}
}}
"""

_TONE = {"goal": "positive", "own_goal": "negative", "assist": "highlight", "yellow": "warning",
         "red": "negative", "sub_off": "neutral", "sub_on": "neutral"}  # fmt: skip
_ICON = {"goal": "⚽", "own_goal": "⚽", "assist": "A", "yellow": "🟨", "red": "🟥",
         "sub_off": "🔄", "sub_on": "⬆️"}  # fmt: skip
_LABEL = {"goal": "Goal", "own_goal": "Own goal", "assist": "Assist", "yellow": "Yellow card",
          "red": "Red card", "sub_off": "Substituted off", "sub_on": "Substituted on"}  # fmt: skip
KEY_STATS: dict[str, tuple[str, ...]] = {
    "GK": ("Saves", "Goals conceded", "xG on target faced", "Claims / punches", "Passes", "Sweeper actions"),
    "attack": ("Goals", "Assists", "Shots", "xG / xA", "Key passes", "Dribbles"),
    "mid": ("Passes", "Progressive passes", "Key passes", "Tackles", "Interceptions", "Ball recoveries"),
    "defence": ("Passes", "Tackles", "Interceptions", "Clearances", "Blocks", "Progressive carries"),
}  # fmt: skip
STAT_SET = {"GK": "GK", "ST": "attack", "W": "attack", "AM": "attack", "CM": "mid", "DM": "mid",
            "CB": "defence", "FB": "defence"}  # fmt: skip


def _short(name: str | None) -> str:
    """Last word of a name, for the tight event badges (the full name is in the tooltip)."""
    return (name or "").split()[-1] if name else ""


def _event_badge(ev: MatchEvent) -> str:
    tip = f"{_LABEL[ev.kind]} {ev.minute}'" + (f" ({ev.detail})" if ev.detail else "")
    text = f"{_ICON[ev.kind]} {ev.minute}'"
    if ev.kind == "own_goal":
        text = f"{_ICON[ev.kind]} OG {ev.minute}'"
    elif ev.kind == "sub_off":
        text += f" → {_short(ev.related)}"
        tip += f", replaced by {ev.related}"
    elif ev.kind == "sub_on":
        text += f" ← {_short(ev.related)}"
        tip += f", for {ev.related}"
    elif ev.kind == "assist":
        tip += f", goal by {ev.related}"
    return design.badge(text, _TONE[ev.kind], tip)


# ---- A. header ---------------------------------------------------------------------------------


def _header(r: MatchReport) -> None:
    score = "-" if r.home_score is None or r.away_score is None else f"{r.home_score} - {r.away_score}"
    meta = [f"{r.match_date:%d %b %Y}", shell.LEAGUE_SHORT.get(r.league, r.league)]
    if r.match_week:
        meta.append(f"Matchweek {r.match_week}")
    team = lambda name: (  # noqa: E731
        f'<div class="mr-team">{shell.team_logo_img(name, 64)}<span class="fi-name">{design.esc(name)}</span></div>'
    )
    design.render(
        design.card(
            None,
            f'<div class="mr-meta">{design.esc(" · ".join(meta))}</div>'
            f'<div class="mr-head">{team(r.home_team)}'
            f'<div><div class="mr-score">{design.esc(score)}</div><div class="mr-status">Full time</div></div>'
            f"{team(r.away_team)}</div>",
        )
    )


# ---- B. timeline -------------------------------------------------------------------------------


def _timeline(r: MatchReport, sb_id: int | None) -> None:
    design.render(design.section_header("Timeline", "Goals, cards and substitutions"))
    entries = [e for e in r.events if e.kind not in ("assist", "sub_off")]
    if not entries:
        design.render(design.empty_state("No events available for this match"))
        return
    rows = []
    for ev in entries:
        sub = ""
        if ev.kind == "goal":
            sub = " · ".join(x for x in (ev.detail, f"Assist: {ev.related}" if ev.related else "") if x)
        elif ev.kind == "own_goal":
            sub = "Own goal"
        elif ev.kind == "sub_on":
            sub = f"On for {ev.related}"
        elif ev.detail:
            sub = ev.detail
        mine = sb_id is not None and ev.player_sb_id == sb_id
        rows.append(
            f'<div class="mr-ev{" me" if mine else ""}" title="{design.esc(_LABEL[ev.kind])}">'
            f'<span class="mr-min">{ev.minute}\'</span><span aria-hidden="true">{_ICON[ev.kind]}</span>'
            f'<div><b>{design.esc(ev.player_name)}</b>'
            + (f'<div class="mr-ev-sub">{design.esc(sub)}</div>' if sub else "")
            + f"</div>{shell.team_logo_img(ev.team, 20)}</div>"
        )
    design.render(design.card(None, f'<div class="mr-tl">{"".join(rows)}</div>'))


# ---- C. the player's card ----------------------------------------------------------------------


def _player_card(pid: str, row, m, card, group: str) -> None:  # noqa: ANN001
    lines = dict(games_mod.match_stat_line(m, group))
    keys = KEY_STATS[STAT_SET.get(group, "mid")]
    cells = "".join(
        f'<div class="mr-cell"><b>{design.esc(lines[k])}</b><span>{design.esc(k)}</span></div>'
        for k in keys if k in lines
    )
    rating = f"{card.rating:.1f}" if card.rating is not None else "–"
    design.render(design.section_header(f"{row['player_name']} in this match"))
    with st.container(key="ficard_match_player"):
        design.render(
            '<div class="fi-row">'
            + design.player_avatar(row["player_name"], 56, shell.photo_uri(pid, row["player_name"], 56))
            + f'<div class="fi-col" style="flex:1"><span class="fi-name">{design.esc(row["player_name"])}</span>'
            f'<span class="fi-muted">{design.position_badge(group)} {design.esc(card.position_played or "")}</span></div>'
            + design.big_stat("Minutes", f"{card.minutes:.0f}")
            + design.big_stat("Rating", rating)
            + f'</div><div class="mr-grid">{cells}</div>'
        )
        if st.button("← Back to profile", key="match_back_card"):
            shell.open_player(pid)
    if card.rating is not None:
        st.caption("Match rating is a position-aware count of this player's own actions (see Methodology).")


# ---- D. heatmap --------------------------------------------------------------------------------


@st.fragment
def _heatmap(pid: str, name: str, match_id: int) -> None:
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
            st.plotly_chart(
                design.heatmap_pitch(grid.tolist(), f"{title}: {name}"),
                use_container_width=True, config={"displayModeBar": False},
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


# ---- E. team stats -----------------------------------------------------------------------------


def _stat_rows(h: TeamStats, a: TeamStats) -> list[tuple[str, float, float, str, float]]:
    """(label, home, away, format, scale) rows; scale 100 for percentages, 0 = the larger value."""
    return [
        ("Possession", h.possession, a.possession, "{:.0f}%", 100),
        ("Shots", h.shots, a.shots, "{:.0f}", 0),
        ("Shots on target", h.shots_on_target, a.shots_on_target, "{:.0f}", 0),
        ("xG", h.xg, a.xg, "{:.2f}", 0),
        ("Passes", h.passes, a.passes, "{:.0f}", 0),
        ("Pass accuracy", h.pass_accuracy, a.pass_accuracy, "{:.0f}%", 100),
        ("Corners", h.corners, a.corners, "{:.0f}", 0),
        ("Fouls", h.fouls, a.fouls, "{:.0f}", 0),
    ]  # fmt: skip


def _team_stats(r: MatchReport) -> None:
    design.render(design.section_header("Team stats", "Totals from StatsBomb event data"))
    if r.home_stats is None or r.away_stats is None:
        design.render(design.empty_state("Team stats not available for this match"))
        return
    rows = []
    for label, hv, av, fmt, scale in _stat_rows(r.home_stats, r.away_stats):
        top = scale or max(hv, av) or 1
        hw, aw = 100 * hv / top, 100 * av / top
        rows.append(
            f'<div class="mr-stat" role="img" aria-label="{design.esc(label)}: {design.esc(r.home_team)} '
            f'{fmt.format(hv)}, {design.esc(r.away_team)} {fmt.format(av)}">'
            f'<div class="mr-stat-top"><b style="text-align:left">{fmt.format(hv)}</b><span>{label}</span>'
            f'<b style="text-align:right">{fmt.format(av)}</b></div>'
            f'<div class="mr-bars"><div class="mr-half l"><i style="width:{hw:.0f}%;background:{HOME_COLOR}"></i></div>'
            f'<div class="mr-half"><i style="width:{aw:.0f}%;background:{AWAY_COLOR}"></i></div></div></div>'
        )
    legend = (
        f'<div class="fi-row" style="justify-content:space-between;margin-bottom:{design.SP_S}px">'
        f'<span class="fi-muted" style="color:{HOME_COLOR}">● {design.esc(r.home_team)}</span>'
        f'<span class="fi-muted" style="color:{AWAY_COLOR}">{design.esc(r.away_team)} ●</span></div>'
    )
    design.render(design.card(None, legend + "".join(rows)))
    st.caption("Possession is each team's share of all passes. Shots and xG exclude the shoot-out.")


# ---- F/G. lineups with events ------------------------------------------------------------------


def _player_card_html(p: LineupPlayer, sb_id: int | None, bench: bool) -> str:
    mine = sb_id is not None and p.sb_id == sb_id
    cls = "mr-p" + (" me" if mine else "") + (" bench" if bench else "")
    badges = "".join(_event_badge(e) for e in p.events)
    body = (
        f'<div class="mr-p-row"><span class="mr-jersey">{p.jersey if p.jersey is not None else ""}</span>'
        f'<span class="mr-pname">{design.esc(p.name)}</span>'
        + (f'<span class="fi-muted">{design.esc(p.position_abbrev)}</span>' if p.position_abbrev else "")
        + "</div>"
        + (f'<div class="fi-badges">{badges}</div>' if badges else "")
    )
    if p.player_id:
        href = f"player?player={quote(p.player_id, safe='')}"
        return f'<a class="{cls}" href="{href}" target="_self" title="Open {design.esc(p.name)}\'s profile">{body}</a>'
    return f'<div class="{cls} off" title="Not in the database">{body}</div>'


def _lineup_column(lineup: TeamLineup, sb_id: int | None) -> None:
    formation = f'<span class="mr-form">{design.esc(lineup.formation)}</span>' if lineup.formation else ""
    design.render(
        f'<div class="mr-side">{shell.team_logo_img(lineup.team, 32)}'
        f'<span class="fi-name">{design.esc(lineup.team)}</span>{formation}</div>'
    )
    design.render("".join(_player_card_html(p, sb_id, False) for p in lineup.starters))
    if lineup.bench:
        design.render('<div class="mr-sub">Bench</div>')
        design.render("".join(_player_card_html(p, sb_id, True) for p in lineup.bench))


def _lineups(r: MatchReport, sb_id: int | None) -> None:
    design.render(design.section_header("Lineups", "Starting XI and bench, with what each player did"))
    if r.home_lineup is None or r.away_lineup is None:
        design.render(design.empty_state("Lineups not available for this match"))
        return
    left, right = st.columns(2)
    with left:
        _lineup_column(r.home_lineup, sb_id)
    with right:
        _lineup_column(r.away_lineup, sb_id)
    st.caption("Click a player in the database to open their profile. Formation is the kick-off shape.")


# ---- page --------------------------------------------------------------------------------------

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

design.render(f"<style>{_CSS}</style>")
m = this.iloc[0]
card = games_mod.game_card(matches, match_id, group)
assert card is not None
sb_id = games_mod.statsbomb_player_id(pid, data.engine())
report = data.match_report(match_id)

if st.button("← Back to profile", key="match_back"):
    shell.open_player(pid)

if report is None:
    design.render(design.empty_state("This match is not in the database."))
    st.stop()

_header(report)
_timeline(report, sb_id)
_player_card(pid, row, m, card, group)
_heatmap(pid, row["player_name"], match_id)
_team_stats(report)
_lineups(report, sb_id)
shell.refresh_when_photos_ready()
