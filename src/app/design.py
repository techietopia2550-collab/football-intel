"""The design system: every color, font size, spacing and radius, and every reusable component.

Nothing outside this module hardcodes a color, spacing, radius or font. Components return HTML
strings (render them with `st.markdown(..., unsafe_allow_html=True)` or `render`) or Plotly
figures. `.streamlit/config.toml` mirrors the theme tokens (Streamlit reads it before any Python
runs); `tests/test_app_design.py` checks the two stay in step.

Accessibility: text is never the only carrier of meaning to colour alone (every badge and bar has
a number and a word or symbol), text pairs meet 4.5:1 against their background (`TEXT_TERTIARY` is
decoration only, never used for text that has to be read), and interactive elements get a visible
focus ring.
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from typing import Any

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from src.models.finance import TeamWageContext

# ---- typography ------------------------------------------------------------------------------
FONT_FAMILY = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
FONT_URL = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap"
WEIGHT_HEADING, WEIGHT_BODY, WEIGHT_NUMBER = 600, 400, 500
SIZE_H1, SIZE_H2, SIZE_H3, SIZE_BODY, SIZE_SMALL = 32, 24, 18, 14, 12

# ---- colours ---------------------------------------------------------------------------------
BG_PRIMARY, BG_SECONDARY, BG_TERTIARY = "#0E1117", "#1A1D23", "#252932"
BORDER = "#2E323C"
TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY = "#E8EAED", "#9AA0A6", "#5F6368"
POSITIVE, NEGATIVE, NEUTRAL, HIGHLIGHT, WARNING = "#3FB950", "#F85149", "#8B949E", "#58A6FF", "#D29922"
LOGO_BG = "#F0F0F0"  # light disc behind club crests so dark ones (Spurs, Verona) stay visible

POSITION_COLORS: dict[str, str] = {
    "GK": "#F0883E", "CB": "#58A6FF", "FB": "#79C0FF", "DM": "#3FB950",
    "CM": "#56D364", "AM": "#D2A8FF", "W": "#FF7B72", "ST": "#F85149",
}  # fmt: skip
POSITION_NAMES: dict[str, str] = {
    "GK": "Goalkeeper", "CB": "Centre-back", "FB": "Full-back", "DM": "Defensive midfielder",
    "CM": "Central midfielder", "AM": "Attacking midfielder", "W": "Winger", "ST": "Striker",
}  # fmt: skip
POSITION_ORDER = tuple(POSITION_COLORS)
# One colour per player in an overlay chart; positions are not used here so it works across groups.
COMPARE_COLORS: tuple[str, ...] = ("#58A6FF", "#F0883E", "#D2A8FF", "#56D364", "#FF7B72")
# Ten distinguishable colours for the archetype scatter (colour is backed by legend + hover names).
CATEGORICAL: tuple[str, ...] = (
    "#58A6FF", "#F0883E", "#3FB950", "#D2A8FF", "#FF7B72",
    "#D29922", "#F778BA", "#39C5CF", "#E3B341", "#A5D6FF",
)  # fmt: skip
PITCH_BLUES: list[list[Any]] = [[0.0, BG_SECONDARY], [0.5, "#1F4E8C"], [1.0, HIGHLIGHT]]

# ---- spacing (8 px base), radius -------------------------------------------------------------
SP_XS, SP_S, SP_M, SP_L, SP_XL, SP_XXL = 4, 8, 16, 24, 32, 48
RADIUS_CARD, RADIUS_BUTTON, RADIUS_BADGE, RADIUS_AVATAR = "12px", "8px", "6px", "50%"
BREAKPOINT_MOBILE = 768
PERCENTILE_HIGH, PERCENTILE_LOW = 70, 30

TONES: dict[str, tuple[str, str]] = {  # tone -> (text colour, tinted background)
    "positive": (POSITIVE, "rgba(63,185,80,0.14)"),
    "negative": (NEGATIVE, "rgba(248,81,73,0.14)"),
    "neutral": (TEXT_SECONDARY, BG_TERTIARY),
    "highlight": (HIGHLIGHT, "rgba(88,166,255,0.14)"),
    "warning": (WARNING, "rgba(210,153,34,0.14)"),
}
TONE_SYMBOL = {"positive": "▲", "negative": "▼", "neutral": "●", "highlight": "●", "warning": "!"}

# ---- CSS -------------------------------------------------------------------------------------
_CSS = f"""
@import url('{FONT_URL}');
:root {{
  --bg1:{BG_PRIMARY}; --bg2:{BG_SECONDARY}; --bg3:{BG_TERTIARY}; --border:{BORDER};
  --t1:{TEXT_PRIMARY}; --t2:{TEXT_SECONDARY}; --t3:{TEXT_TERTIARY};
  --pos:{POSITIVE}; --neg:{NEGATIVE}; --neu:{NEUTRAL}; --hi:{HIGHLIGHT}; --warn:{WARNING};
}}
html, body, [class*="css"], .stApp, .stMarkdown, button, input, textarea, select {{
  font-family:{FONT_FAMILY} !important; font-size:{SIZE_BODY}px; font-weight:{WEIGHT_BODY};
}}
.stApp {{ background:var(--bg1); color:var(--t1); }}
header[data-testid="stHeader"] {{ background:transparent; }}
[data-testid="stAppViewContainer"] .block-container {{
  padding:{SP_XL}px {SP_L}px {SP_XXL}px; max-width:1240px;
}}
h1, h2, h3, h4 {{ font-weight:{WEIGHT_HEADING} !important; color:var(--t1); letter-spacing:-0.01em; }}
h1 {{ font-size:{SIZE_H1}px !important; }} h2 {{ font-size:{SIZE_H2}px !important; }}
h3 {{ font-size:{SIZE_H3}px !important; }}
p, li, label, .stMarkdown {{ color:var(--t1); }}
.stCaption, [data-testid="stCaptionContainer"] {{ color:var(--t2) !important; font-size:{SIZE_SMALL}px; }}
.fi-num, [data-testid="stMetricValue"], td, th {{ font-variant-numeric:tabular-nums; font-weight:{WEIGHT_NUMBER}; }}
[data-testid="stSidebar"] {{ background:var(--bg2); border-right:1px solid var(--border); }}
[data-testid="stSidebar"] * {{ color:var(--t1); }}
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {{ color:var(--t2) !important; }}
input, textarea, [data-baseweb="select"] > div, [data-baseweb="input"] {{
  background:var(--bg3) !important; border-color:var(--border) !important;
  border-radius:{RADIUS_BUTTON} !important; color:var(--t1) !important;
}}
[data-baseweb="tag"] {{ background:var(--bg1) !important; border-radius:{RADIUS_BADGE} !important; }}
.stButton > button, .stDownloadButton > button, [data-testid="stBaseButton-secondary"] {{
  background:var(--bg3); color:var(--t1); border:1px solid var(--border);
  border-radius:{RADIUS_BUTTON}; font-weight:{WEIGHT_NUMBER}; padding:{SP_S}px {SP_M}px;
  transition:border-color .15s, background .15s;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{ border-color:var(--hi); color:var(--t1); background:var(--bg3); }}
.stButton > button[kind="primary"] {{ background:var(--hi); color:var(--bg1); border-color:var(--hi); }}
.stButton > button:disabled {{ opacity:.55; cursor:not-allowed; }}
:focus-visible, button:focus-visible, input:focus-visible, [role="tab"]:focus-visible,
a:focus-visible {{ outline:2px solid var(--hi) !important; outline-offset:2px !important; }}
[class*="st-key-ficard"], .fi-card {{
  background:var(--bg2); border:1px solid var(--border); border-radius:{RADIUS_CARD};
}}
[class*="st-key-ficard"] {{ padding:{SP_M}px; margin-bottom:{SP_S}px; }}
.fi-card {{ padding:{SP_M}px {SP_L}px; margin-bottom:{SP_M}px; }}
.fi-card-title {{ font-size:{SIZE_H3}px; font-weight:{WEIGHT_HEADING}; margin:0 0 {SP_S}px; color:var(--t1); }}
.fi-row {{ display:flex; align-items:center; gap:{SP_M}px; flex-wrap:wrap; }}
.fi-col {{ display:flex; flex-direction:column; gap:{SP_XS}px; }}
.fi-muted {{ color:var(--t2); font-size:{SIZE_SMALL}px; }}
.fi-body {{ color:var(--t1); font-size:{SIZE_BODY}px; }}
.fi-h1 {{ font-size:{SIZE_H1}px; font-weight:{WEIGHT_HEADING}; margin:0; line-height:1.15; }}
.fi-h2 {{ font-size:{SIZE_H2}px; font-weight:{WEIGHT_HEADING}; margin:0; }}
.fi-name {{ font-size:{SIZE_H3}px; font-weight:{WEIGHT_HEADING}; margin:0; line-height:1.2; }}
.fi-avatar {{ border-radius:{RADIUS_AVATAR}; object-fit:cover; background:var(--bg3); border:1px solid var(--border); flex:none; }}
.fi-badge {{
  display:inline-flex; align-items:center; gap:{SP_XS}px; padding:2px {SP_S}px; border-radius:{RADIUS_BADGE};
  font-size:{SIZE_SMALL}px; font-weight:{WEIGHT_NUMBER}; line-height:1.5; white-space:nowrap;
  font-variant-numeric:tabular-nums; border:1px solid transparent;
}}
.fi-badges {{ display:flex; flex-wrap:wrap; gap:{SP_XS}px; }}
.fi-stat {{ text-align:right; min-width:96px; }}
.fi-stat-value {{ font-size:{SIZE_H2}px; font-weight:{WEIGHT_NUMBER}; font-variant-numeric:tabular-nums; line-height:1.1; }}
.fi-stat-label {{ font-size:{SIZE_SMALL}px; color:var(--t2); }}
.fi-bar {{ display:flex; align-items:center; gap:{SP_S}px; margin:{SP_XS}px 0; }}
.fi-bar-label {{ flex:0 0 40%; font-size:{SIZE_BODY}px; }}
.fi-bar-track {{ flex:1; height:8px; background:var(--bg3); border-radius:{RADIUS_BADGE}; overflow:hidden; }}
.fi-bar-fill {{ height:100%; border-radius:{RADIUS_BADGE}; }}
.fi-bar-num {{ flex:0 0 72px; text-align:right; font-size:{SIZE_SMALL}px; font-variant-numeric:tabular-nums; font-weight:{WEIGHT_NUMBER}; }}
.fi-section {{ margin:{SP_XL}px 0 {SP_M}px; display:flex; justify-content:space-between; align-items:flex-end; gap:{SP_M}px; flex-wrap:wrap; }}
.fi-section h2 {{ margin:0; }}
.fi-mini {{ display:flex; align-items:center; gap:{SP_S}px; padding:{SP_S}px 0; }}
.fi-note {{ border-left:3px solid var(--warn); background:rgba(210,153,34,0.10); padding:{SP_S}px {SP_M}px;
  border-radius:0 {RADIUS_BUTTON} {RADIUS_BUTTON} 0; margin:{SP_S}px 0; font-size:{SIZE_BODY}px; }}
.fi-note.info {{ border-left-color:var(--hi); background:rgba(88,166,255,0.10); }}
.fi-empty {{ text-align:center; padding:{SP_XXL}px {SP_M}px; color:var(--t2); border:1px dashed var(--border);
  border-radius:{RADIUS_CARD}; }}
.fi-empty b {{ color:var(--t1); font-size:{SIZE_H3}px; display:block; margin-bottom:{SP_S}px; }}
.fi-btn {{ display:inline-block; background:var(--hi); color:var(--bg1) !important; text-decoration:none;
  padding:{SP_S}px {SP_M}px; border-radius:{RADIUS_BUTTON}; font-weight:{WEIGHT_NUMBER}; }}
.fi-credit {{ font-size:{SIZE_SMALL}px; color:var(--t2); }}
[data-testid="stTabs"] [role="tab"] {{ color:var(--t2); font-weight:{WEIGHT_NUMBER}; }}
[data-testid="stTabs"] [aria-selected="true"] {{ color:var(--t1); }}
[data-testid="stExpander"] {{ border:1px solid var(--border); border-radius:{RADIUS_BUTTON}; background:var(--bg2); }}
a {{ color:var(--hi); }}
hr {{ border-color:var(--border); }}
@media (max-width:{BREAKPOINT_MOBILE}px) {{
  [data-testid="stAppViewContainer"] .block-container {{ padding:{SP_M}px {SP_S}px {SP_XL}px; }}
  [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap !important; }}
  [data-testid="stColumn"] {{ min-width:100% !important; flex:1 1 100% !important; }}
  .fi-h1 {{ font-size:{SIZE_H2}px; }} .fi-stat {{ text-align:left; }}
  .fi-bar-label {{ flex-basis:48%; }}
}}
"""


def _h(markup: str) -> str:
    """Collapse indentation and blank lines: Markdown would turn indented HTML into a code block."""
    return "".join(line.strip() for line in markup.splitlines())


def inject_css() -> None:
    """Apply the global stylesheet (call once per run, before any page content)."""
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)


def render(markup: str) -> None:
    st.markdown(_h(markup), unsafe_allow_html=True)


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


# ---- helpers ---------------------------------------------------------------------------------


def position_color(group: str | None) -> str:
    return POSITION_COLORS.get(group or "", NEUTRAL)


def ordinal(n: float) -> str:
    i = int(round(n))
    suffix = "th" if 10 <= i % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(i % 10, "th")
    return f"{i}{suffix}"


def tone_for(score: float | None) -> str:
    """Semantic tone of a 0-100 score (already flipped where lower is better)."""
    if score is None or np.isnan(score):
        return "neutral"
    if score >= PERCENTILE_HIGH:
        return "positive"
    if score < PERCENTILE_LOW:
        return "negative"
    return "neutral"


def band_word(score: float | None) -> str:
    return {"positive": "Strong", "negative": "Weak", "neutral": "Average"}[tone_for(score)]


def fmt_match_pct(similarity: float | None) -> str:
    """A cosine similarity (0-1) as a whole-number percentage for non-technical readers."""
    if similarity is None or np.isnan(similarity):
        return "–"
    return f"{min(max(similarity, 0.0), 1.0) * 100:.0f}%"


# Match-% tiers, measured on the labelled test set (docs/similarity_thresholds.md,
# ANALYTICS_DECISIONS.md section 23): (word, lowest cosine, tone), highest first. Strong (90%
# precision) and Good (75%) are absent because no threshold reached them; the old judgement cuts
# were 85 / 70 / 55. Everything under the last cut is MATCH_TIER_BELOW.
MATCH_TIERS: tuple[tuple[str, float, str], ...] = (("Fair", 0.60, "neutral"),)
MATCH_TIER_BELOW: tuple[str, str] = ("Weak", "negative")
MATCH_TIER_NOTE = (
    "Measured tier: at 60%+ about half of labelled test pairs shared a role and style; "
    "below 60%, fewer than half. No higher score band proved more reliable than that."
)


def match_tier(similarity: float | None) -> tuple[str, str] | None:
    """(tier word, tone) for a cosine similarity, from the measured MATCH_TIERS; None if missing."""
    if similarity is None or np.isnan(similarity):
        return None
    for word, cut, tone in MATCH_TIERS:
        if similarity >= cut - 1e-12:
            return word, tone
    return MATCH_TIER_BELOW


def match_tier_badge(similarity: float | None) -> str:
    """The tier word as a pill (empty string when there is no score)."""
    tier = match_tier(similarity)
    return badge(tier[0], tier[1], MATCH_TIER_NOTE) if tier else ""


def fmt_match(similarity: float | None) -> str:
    """Match % with its tier word, e.g. '72% · Fair', for plain-text cells."""
    tier = match_tier(similarity)
    return f"{fmt_match_pct(similarity)} · {tier[0]}" if tier else fmt_match_pct(similarity)


def cross_position_note(group: str | None, source_group: str | None) -> str:
    """'cross-position (AM vs W)' when a similar player's position group differs from the source
    player's, else ''. Informational only: similarity scores ignore position inside an archetype."""
    if not group or not source_group or group == source_group:
        return ""
    return f"cross-position ({group} vs {source_group})"


WAGE_TIP = "The club's total annual wage bill: team context, not any individual player's wage."


def wage_text(ctx: TeamWageContext, label: str = "Wage bill") -> str:
    """'Wage bill: £187m/year · Deloitte, aggregated (rank 3 of 12 with data, 80 clubs)' as HTML.
    The source and confidence always travel with the figure, so an estimate is never shown bare.
    No figure -> 'Wage bill: not available' (never £0, never blank). Pure."""
    if ctx.wage_bill_annual_gbp is None:
        body = f'{esc(label)}: <span class="fi-muted">not available</span>'
    else:
        of = f"{ctx.teams_ranked}" + (
            f" with data, {ctx.teams_in_season} clubs" if ctx.teams_ranked < ctx.teams_in_season else ""
        )
        body = (
            f"{esc(label)}: £{ctx.wage_bill_annual_gbp / 1e6:,.0f}m/year · "
            f"{esc(ctx.source or 'unknown source')}, {esc(ctx.confidence or 'unknown')} "
            f'<span class="fi-muted">(rank {ctx.rank} of {of})</span>'
        )
    return f'<span title="{esc(WAGE_TIP)}">{body}</span> ' + badge("team context", "neutral", WAGE_TIP)


def fmt_eur(value: float | None) -> str:
    """'€60m', '€1.5m', '€750k'; '' for a missing value (callers say "no value available")."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if value >= 1e6:
        m = value / 1e6
        return f"€{m:,.0f}m" if m >= 10 or m == int(m) else f"€{m:.1f}m"
    return f"€{value / 1e3:,.0f}k"


def fmt_value(value: float | None, unit: str = "") -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "–"
    if abs(value) >= 100:
        text = f"{value:,.0f}"
    elif abs(value) >= 10:
        text = f"{value:.1f}"
    else:
        text = f"{value:.2f}"
    return f"{text}{unit}"


# ---- components ------------------------------------------------------------------------------


def card(title: str | None, content: str, accent: str | None = None) -> str:
    """A bordered card: optional title, then `content` (HTML). `accent` colours the left edge."""
    edge = f"border-left:4px solid {accent};" if accent else ""
    head = f'<div class="fi-card-title">{esc(title)}</div>' if title else ""
    return _h(f'<div class="fi-card" style="{edge}">{head}{content}</div>')


def badge(text: str, tone: str = "neutral", title: str = "") -> str:
    """A small labelled pill. The tone symbol makes the meaning readable without colour."""
    fg, bg = TONES[tone]
    tip = f' title="{esc(title)}"' if title else ""
    return (
        f'<span class="fi-badge" style="color:{fg};background:{bg};border-color:{fg}33"{tip}>'
        f"{esc(text)}</span>"
    )


def position_badge(group: str | None) -> str:
    color = position_color(group)
    name = POSITION_NAMES.get(group or "", group or "")
    return (
        f'<span class="fi-badge" style="color:{color};background:{color}22;border-color:{color}55" '
        f'title="{esc(name)}">{esc(group)}</span>'
    )


def metric_badge(
    label: str,
    value: float | str | None,
    percentile: float | None,
    position_group: str | None = None,
    tone: str | None = None,
) -> str:
    """`label`, its value and percentile as one pill: '▲ Tackles 99th'. The tone follows the
    percentile (high, low, or middle) unless `tone` forces one; the symbol repeats it without
    colour. `position_group` adds a left edge in the group's colour."""
    tone = tone or tone_for(percentile)
    fg, bg = TONES[tone]
    pct = "" if percentile is None or np.isnan(percentile) else f" {ordinal(percentile)}"
    val = "" if value is None else f" · {esc(value)}"
    edge = f"border-left:3px solid {position_color(position_group)};" if position_group else ""
    return (
        f'<span class="fi-badge" style="color:{fg};background:{bg};border-color:{fg}33;{edge}" '
        f'title="{esc(label)}: percentile {pct.strip() or "n/a"}">'
        f"{TONE_SYMBOL[tone]} {esc(label)}{pct}{val}</span>"
    )


def player_avatar(name: str, size: int, src: str | None = None) -> str:
    """A circular photo of `size` px. `src` is a data URI (see `player_images.avatar_data_uri`);
    without one the silhouette is used, so an image is never broken."""
    if src is None:
        from src.app import player_images  # local import: keeps design.py free of I/O at import

        src = player_images.silhouette_uri(size * 2)
    return (
        f'<img class="fi-avatar" src="{src}" width="{size}" height="{size}" '
        f'style="width:{size}px;height:{size}px" alt="Photo of {esc(name)}" loading="lazy">'
    )


def team_logo(team_name: str, size: int) -> str:
    """A club crest of `size` px on a light circular disc (the FotMob / Sofascore pattern), so dark
    crests read on the dark theme. Decorative: the club name is always written next to it. Reads
    the local logo cache only (the generic badge when there is none), never the network."""
    from src.app import player_images  # local import: keeps design.py free of I/O at import

    uri = player_images.team_logo_uri(team_name, size)
    if not uri:
        return ""
    pad = min(SP_XS, size // 10)  # 4 px, eased on tiny icons so the crest keeps ~80-90% of the disc
    return (
        f'<span class="fi-logo" style="display:inline-flex;align-items:center;justify-content:center;'
        f"width:{size}px;height:{size}px;padding:{pad}px;box-sizing:border-box;border-radius:50%;"
        f'background:{LOGO_BG};vertical-align:middle;flex:none">'
        f'<img src="{uri}" alt="" style="width:100%;height:100%;object-fit:contain;display:block">'
        "</span>"
    )


def stat_bar(label: str, value: float | str | None, percentile: float | None, score: float | None = None) -> str:
    """A horizontal 0-100 bar with the number and a word ('Strong'/'Average'/'Weak') beside it."""
    s = percentile if score is None else score
    tone = tone_for(s)
    fg = TONES[tone][0]
    width = 0 if percentile is None or np.isnan(percentile) else max(2, min(100, percentile))
    word = band_word(s) if s is not None else "n/a"
    num = "n/a" if percentile is None or np.isnan(percentile) else ordinal(percentile)
    val = "" if value is None else f" ({esc(value)})"
    return _h(
        f'<div class="fi-bar" role="img" aria-label="{esc(label)}: {num} percentile, {word}">'
        f'<div class="fi-bar-label">{esc(label)}<span class="fi-muted">{val}</span></div>'
        f'<div class="fi-bar-track"><div class="fi-bar-fill" style="width:{width}%;background:{fg}"></div></div>'
        f'<div class="fi-bar-num" style="color:{fg}">{num}</div></div>'
    )


def section_header(
    title: str, subtitle: str | None = None, actions: str | None = None, anchor: str | None = None
) -> str:
    sub = f'<div class="fi-muted">{esc(subtitle)}</div>' if subtitle else ""
    right = f'<div class="fi-row">{actions}</div>' if actions else ""
    ident = f' id="{esc(anchor)}"' if anchor else ""
    return _h(f'<div class="fi-section"{ident}><div><h2>{esc(title)}</h2>{sub}</div>{right}</div>')


def link_button(label: str, href: str) -> str:
    """An in-page link that looks like a primary button (for jumping to a section anchor)."""
    return f'<a class="fi-btn" href="{esc(href)}">{esc(label)}</a>'


def note(text: str, kind: str = "warning") -> str:
    cls = "fi-note info" if kind == "info" else "fi-note"
    return _h(f'<div class="{cls}">{text}</div>')


def empty_state(title: str, hint: str = "") -> str:
    return _h(f'<div class="fi-empty"><b>{esc(title)}</b>{esc(hint)}</div>')


def big_stat(label: str, value: str) -> str:
    return _h(
        f'<div class="fi-stat"><div class="fi-stat-value">{esc(value)}</div>'
        f'<div class="fi-stat-label">{esc(label)}</div></div>'
    )


# ---- charts ----------------------------------------------------------------------------------


def _base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=SP_L, r=SP_L, t=SP_L, b=SP_L),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_FAMILY, size=SIZE_BODY, color=TEXT_PRIMARY),
        hoverlabel=dict(bgcolor=BG_TERTIARY, bordercolor=BORDER, font_size=SIZE_BODY),
    )  # fmt: skip
    return fig


def radar_chart(
    player_data: Sequence[dict[str, Any]],
    position_group: str,
    mode: str = "percentile",
    reference: dict[str, dict[str, float]] | None = None,
    labels: dict[str, str] | None = None,
) -> go.Figure:
    """A radar of one or several players. Each entry of `player_data`: {name, items, color?} with
    items as `directory.radar_items`. `mode="percentile"` plots the 0-100 score (a dashed ring
    marks the 50th percentile); `mode="raw"` plots each axis as a share of the group maximum
    (`reference[metric]["max"]`) and the hover shows the raw value. With one player the line
    takes his position group's colour."""
    labels = labels or {}
    fig = go.Figure()
    single = len(player_data) == 1
    for i, series in enumerate(player_data):
        items = series["items"]
        color = series.get("color") or (
            position_color(position_group) if single else COMPARE_COLORS[i % len(COMPARE_COLORS)]
        )
        names = [labels.get(it["metric"], it["metric"]) for it in items]
        if mode == "raw" and reference:
            r = [
                100 * it["value"] / reference[it["metric"]]["max"]
                if reference.get(it["metric"], {}).get("max") else 0.0
                for it in items
            ]  # fmt: skip
        else:
            r = [it["score"] for it in items]
        custom = [
            [it["value"], it["percentile"], "lower is better, plotted flipped" if it["flipped"] else ""]
            for it in items
        ]  # fmt: skip
        fig.add_trace(
            go.Scatterpolar(
                r=[*r, r[0]], theta=[*names, names[0]], name=series["name"], fill="toself",
                line=dict(color=color, width=2), fillcolor=_rgba(color, 0.22),
                customdata=[*custom, custom[0]],
                hovertemplate=(
                    "<b>%{theta}</b><br>Raw: %{customdata[0]:.2f}<br>"
                    "Percentile: %{customdata[1]:.0f}<br>%{customdata[2]}<extra>"
                    + series["name"] + "</extra>"
                ),
            )
        )  # fmt: skip
    if mode != "raw":
        n = len(player_data[0]["items"])
        fig.add_trace(
            go.Scatterpolar(
                r=[50] * (n + 1),
                theta=[labels.get(it["metric"], it["metric"]) for it in player_data[0]["items"]]
                + [labels.get(player_data[0]["items"][0]["metric"], "")],
                mode="lines", line=dict(color=TEXT_SECONDARY, width=1, dash="dot"),
                name="50th percentile", hoverinfo="skip",
            )
        )  # fmt: skip
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(range=[0, 100], tickvals=[25, 50, 75, 100], gridcolor=BORDER,
                            linecolor=BORDER, tickfont=dict(size=SIZE_SMALL, color=TEXT_SECONDARY)),
            angularaxis=dict(gridcolor=BORDER, linecolor=BORDER,
                             tickfont=dict(size=SIZE_SMALL, color=TEXT_PRIMARY)),
        ),
        showlegend=len(player_data) > 1,
        legend=dict(orientation="h", y=-0.1, font=dict(size=SIZE_BODY)),
    )  # fmt: skip
    return _base_layout(fig, 460)


TEAM_COLOR = "#58A6FF"  # fixed neutral blue for a team's style radar


def team_radar(
    team: str, labels: Sequence[str], scores: Sequence[float], values: Sequence[float],
    value_texts: Sequence[str] | None = None,
) -> go.Figure:
    """A team's style axes as 0-100 ranks among all teams (the dashed ring is the median team).
    Hover shows the raw value, so nothing depends on colour or on reading the ring."""
    texts = list(value_texts) if value_texts else [f"{v:.2f}" for v in values]
    theta = [*labels, labels[0]]
    fig = go.Figure()
    fig.add_trace(
        go.Scatterpolar(
            r=[*scores, scores[0]], theta=theta, name=team, fill="toself",
            line=dict(color=TEAM_COLOR, width=2), fillcolor=_rgba(TEAM_COLOR, 0.22),
            customdata=[*texts, texts[0]],
            hovertemplate="<b>%{theta}</b><br>Raw: %{customdata}<br>Rank score: %{r:.0f} / 100<extra></extra>",
        )
    )  # fmt: skip
    fig.add_trace(
        go.Scatterpolar(
            r=[50] * len(theta), theta=theta, mode="lines", name="Median team",
            line=dict(color=TEXT_SECONDARY, width=1, dash="dot"), hoverinfo="skip",
        )
    )  # fmt: skip
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",
            radialaxis=dict(range=[0, 100], tickvals=[25, 50, 75, 100], gridcolor=BORDER,
                            linecolor=BORDER, tickfont=dict(size=SIZE_SMALL, color=TEXT_SECONDARY)),
            angularaxis=dict(gridcolor=BORDER, linecolor=BORDER,
                             tickfont=dict(size=SIZE_SMALL, color=TEXT_PRIMARY)),
        ),
        showlegend=False,
    )  # fmt: skip
    return _base_layout(fig, 500)


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


PITCH_LENGTH, PITCH_WIDTH = 120, 80  # StatsBomb yards; the stored grid is 12 x 8 cells of 10 yards


def _pitch_shapes() -> list[dict[str, Any]]:
    line = dict(color=TEXT_SECONDARY, width=1)
    rect = lambda x0, y0, x1, y1: dict(  # noqa: E731
        type="rect", x0=x0, y0=y0, x1=x1, y1=y1, line=line, layer="above"
    )
    return [
        rect(0, 0, PITCH_LENGTH, PITCH_WIDTH),
        dict(type="line", x0=60, y0=0, x1=60, y1=PITCH_WIDTH, line=line, layer="above"),
        dict(type="circle", x0=50, y0=30, x1=70, y1=50, line=line, layer="above"),
        rect(0, 18, 18, 62), rect(102, 18, 120, 62),
        rect(0, 30, 6, 50), rect(114, 30, 120, 50),
    ]  # fmt: skip


def heatmap_pitch(grid_json: str | Sequence[Sequence[float]], title: str = "", height: int = 380) -> go.Figure:
    """Event counts (8 rows x 12 cells, 10-yard cells) over a pitch, single blue hue. The player's
    team attacks left to right; y runs down the screen towards the player's right-hand side."""
    grid = np.array(json.loads(grid_json) if isinstance(grid_json, str) else grid_json, dtype=float)
    rows, cols = grid.shape
    cw, ch = PITCH_LENGTH / cols, PITCH_WIDTH / rows
    xs = [(c + 0.5) * cw for c in range(cols)]
    ys = [(r + 0.5) * ch for r in range(rows)]
    fig = go.Figure(
        go.Heatmap(
            z=grid, x=xs, y=ys, colorscale=PITCH_BLUES, showscale=False, xgap=1, ygap=1,
            customdata=[[(f"{int(x - cw / 2)}-{int(x + cw / 2)}", f"{int(y - ch / 2)}-{int(y + ch / 2)}")
                         for x in xs] for y in ys],
            hovertemplate="x %{customdata[0]} yd, y %{customdata[1]} yd<br><b>%{z:.0f} events</b><extra></extra>",
        )
    )  # fmt: skip
    fig.update_layout(shapes=_pitch_shapes(), title=dict(text=title, font=dict(size=SIZE_BODY)))
    fig.update_xaxes(range=[0, PITCH_LENGTH], visible=False, fixedrange=True)
    fig.update_yaxes(range=[PITCH_WIDTH, 0], visible=False, scaleanchor="x", fixedrange=True)
    return _base_layout(fig, height)


def percentile_styles(scores: Sequence[float | None]) -> list[str]:
    """CSS for each cell of a percentile column: green for strong, red for weak, gray otherwise
    (with a text band column alongside, so colour is never the only signal)."""
    out = []
    for s in scores:
        tone = tone_for(None if s is None else float(s))
        fg, bg = TONES[tone]
        out.append(f"color:{fg};background-color:{bg};font-weight:{WEIGHT_NUMBER}")
    return out


def quick_profile_radar(items: Sequence[dict[str, Any]], position_group: str) -> go.Figure:
    """A compact six-axis hexagon, FotMob style: each axis label carries its percentile as a big
    number. `items`: {label, percentile, score} with `score` the good-end percentile (None when the
    metric has no percentile, drawn at 0 and labelled "n/a"). Filled in the position colour."""
    color = position_color(position_group)
    theta = []
    for it in items:
        pct = it["percentile"]
        num = "n/a" if pct is None else f"{pct:.0f}"
        theta.append(
            f"{esc(it['label'])}<br>"
            f"<span style='font-size:{SIZE_H2}px;color:{color}'><b>{num}</b></span>"
        )
    r = [0.0 if it["score"] is None else float(it["score"]) for it in items]
    hover = ["no percentile (sample too small)" if it["percentile"] is None
             else f"{it['percentile']:.0f}th percentile" for it in items]  # fmt: skip
    fig = go.Figure(
        go.Scatterpolar(
            r=[*r, r[0]], theta=[*theta, theta[0]], fill="toself", mode="lines+markers",
            line=dict(color=color, width=2), fillcolor=_rgba(color, 0.30),
            marker=dict(size=6, color=color), customdata=[*hover, hover[0]],
            hovertemplate="%{customdata}<extra></extra>",
        )
    )  # fmt: skip
    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)", gridshape="linear",
            radialaxis=dict(range=[0, 100], tickvals=[25, 50, 75, 100], showticklabels=False,
                            gridcolor=BORDER, linecolor=BORDER, angle=90),
            angularaxis=dict(gridcolor=BORDER, linecolor=BORDER, rotation=90, direction="clockwise",
                             tickfont=dict(size=SIZE_SMALL, color=TEXT_PRIMARY)),
        ),
        showlegend=False,
    )  # fmt: skip
    fig = _base_layout(fig, 360)
    fig.update_layout(margin=dict(l=SP_XXL * 2, r=SP_XXL * 2, t=SP_XL + SP_M, b=SP_XL + SP_M))
    return fig


# Where each position group plays, in StatsBomb yards (x 0-120 towards goal, y 0-80 from the
# team's left touchline). Full-backs and wingers have a left (y=15) and a right (y=65) spot.
POSITION_SPOTS: dict[str, tuple[float, float | tuple[float, float]]] = {
    "GK": (5, 40), "CB": (18, 40), "FB": (25, (15, 65)), "DM": (35, 40),
    "CM": (50, 40), "AM": (65, 40), "W": (80, (15, 65)), "ST": (100, 40),
}  # fmt: skip


def position_pitch(position_group: str, side: str | None = None, height: int = 200) -> go.Figure:
    """A small dark full pitch with a dot where the position plays (attacking left to right).
    For a wide position `side` is "left" or "right"; None draws both spots as hollow rings."""
    color = position_color(position_group)
    x, y = POSITION_SPOTS[position_group]
    wide = isinstance(y, tuple)
    if wide and side in ("left", "right"):
        ys, hollow = [y[0] if side == "left" else y[1]], False
    else:
        ys, hollow = (list(y) if isinstance(y, tuple) else [y]), wide
    fig = go.Figure(
        go.Scatter(
            x=[x] * len(ys), y=ys, mode="markers", hoverinfo="skip",
            marker=dict(size=18, color=BG_SECONDARY if hollow else color,
                        line=dict(color=color, width=3)),
        )
    )  # fmt: skip
    fig.update_layout(shapes=_pitch_shapes(), showlegend=False)
    fig.update_xaxes(range=[-2, PITCH_LENGTH + 2], visible=False, fixedrange=True)
    fig.update_yaxes(range=[PITCH_WIDTH + 2, -2], visible=False, scaleanchor="x", fixedrange=True)
    fig = _base_layout(fig, height)
    fig.update_layout(
        margin=dict(l=SP_XS, r=SP_XS, t=SP_XS, b=SP_XS), plot_bgcolor=BG_SECONDARY
    )  # fmt: skip
    return fig


def position_card(position_group: str, side: str | None, note: str = "", key: str = "") -> None:
    """The "Position" card: the position name on the left, a mini pitch with its spot on the right.
    `note` (plain text) is shown muted under the name, e.g. for a player with a mixed role."""
    with st.container(key=f"ficard_position_{key}"):
        left, right = st.columns([2, 3], vertical_alignment="center")
        with left:
            where = {"left": "Left side", "right": "Right side"}.get(side or "", "")
            render(
                '<div class="fi-card-title">Position</div>'
                f'<div class="fi-name">{esc(POSITION_NAMES.get(position_group, position_group))}'
                "</div>"
                f'<div class="fi-row" style="margin-top:{SP_XS}px">{position_badge(position_group)}'
                + (f'<span class="fi-muted">{esc(where)}</span>' if where else "")
                + "</div>"
                + (f'<div class="fi-muted" style="margin-top:{SP_S}px">{esc(note)}</div>'
                   if note else "")
            )
        with right:
            st.plotly_chart(
                position_pitch(position_group, side), use_container_width=True,
                config={"displayModeBar": False, "staticPlot": True}, key=f"pos_pitch_{key}",
            )  # fmt: skip
