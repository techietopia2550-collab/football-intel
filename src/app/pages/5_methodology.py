"""Methodology: what the data is, what is and is not measured, and how similarity works."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from pathlib import Path

import streamlit as st

from src.app import design, shell
from src.utils.config import PROJECT_ROOT

STATSBOMB_LOGO = shell.STATSBOMB_LOGO


@st.cache_data(show_spinner=False)
def _read(name: str) -> str:
    path: Path = PROJECT_ROOT / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return f"{name} could not be read."


design.render('<h1 class="fi-h1">Methodology</h1>')
st.caption("What this app measures, what it cannot, and why the data is from 2015/16.")

design.render(design.section_header("Data sources"))
c1, c2 = st.columns([1, 3], vertical_alignment="center")
with c1:
    if STATSBOMB_LOGO.exists():
        st.image(str(STATSBOMB_LOGO), width=180)
with c2:
    st.markdown(
        "**Data provided by [StatsBomb](https://statsbomb.com).** Event data for the 2015/16 season "
        "of the Premier League, La Liga, Serie A and Ligue 1 comes from the "
        "[StatsBomb Open Data](https://github.com/statsbomb/open-data) repository and is used under "
        "its user agreement. 1,507 of the 1,517 matches are ingested; 1,337 players have 900 or "
        "more minutes and are ranked. Player photos come from Wikipedia."
    )

design.render(design.section_header("What is measured"))
st.markdown(
    """
- **Event-level metrics.** Counts of what a player did (passes, carries, tackles, pressures, shots
  and so on), taken from StatsBomb events. Nothing is invented: every metric is a count or a
  ratio of counts.
- **Per-90 rates.** Season counts divided by minutes played, times 90, so players with different
  playing time can be compared. Ratios (pass completion, tackle win %) are computed from season
  totals and left empty when the sample is too small.
- **Percentile ranks.** Each metric is ranked *within position group* (goalkeeper, centre-back,
  full-back, defensive, central and attacking midfielder, winger, striker), pooled across the four
  leagues. A 90th percentile means higher than 90% of players in that position group.
- **Archetypes.** 10 outfield styles and 3 goalkeeper styles found by clustering 14 per-90 metrics
  (K-Means on principal components). Position labels are not an input, so players who play alike
  can share an archetype. The names are descriptions a person chose.
- **Similarity.** Metrics are z-scored, reduced with PCA (85% of variance) and compared with
  cosine similarity. By default the search runs inside the player's archetype, using the 12
  metrics that most define that archetype. Each result lists the metrics that explain it.
"""
)

design.render(design.section_header("What is NOT measured"))
design.render(
    design.note(
        "Leadership, positioning intelligence, communication, decision-making under pressure and "
        "other mental attributes are <b>not measured</b>. Event data records what happened on the "
        "ball, not why, and not what a player did to shape play away from it. A few metrics are "
        "labelled as proxies (for example how often teammates pass to a player); they describe "
        "behaviour and never rate character.",
        kind="info",
    )
)

design.render(design.section_header("Known limitations"))
st.markdown(
    """
- **Eight position groups, from the lineup position strings only.** A player who moves around is
  filed by where he spent most minutes (Kanté is a central midfielder here, flagged as a mixed
  role). There is no role model beyond that.
- **Archetype boundaries are soft.** Silhouette scores are low and the labels are judgement calls.
  A player near an edge (low confidence) fits two styles; the app says so on his profile. Two known
  misses: Mata lands in *Deep playmaker*, M'Vila in *Defensive full-back*.
- **Data flaws.** 16 of 1,517 matches are affected by StatsBomb lineup-clock errors: 10 could not
  be repaired and are left out, and 6 stored matches carry small minutes errors (0.6 to 3.5
  minutes). Repaired lineups are counted on the affected players' profiles.
- **Heatmaps** exist for players in all four leagues (Premier League, La Liga, Serie A, Ligue 1).
- **No current-season data, no ages, no career history.** StatsBomb's open data has no birth
  dates, so age filters are unavailable. Career and transfers are a placeholder.
- **Leagues are pooled as one population.** Whether tagging or pace of play differ enough between
  leagues to bias comparisons has not been checked.
- **Similarity is statistical, not categorical.** On a 100-pair test the match % did not separate
  same-role from different-role pairs: precision is roughly 50% at every threshold (see *What the %
  means* below). Weak archetypes (defensive full-back, destroyer, pressing wide midfielder) give
  less reliable lists.
"""
)

design.render(design.section_header("Why 2015/16"))
st.markdown(
    "No free source gives current-season defensive and advanced passing data. FBref, the usual "
    "free source, sits behind a Cloudflare challenge and lost its advanced data in January 2026; "
    "WhoScored and Sofascore need browser automation. StatsBomb's free open data is the one legal "
    "source with event-level detail for full leagues, and its complete league coverage is 2015/16."
)

design.render(design.section_header("Similarity engine, step by step"))
st.markdown(
    """
1. **Choose the pool:** the target's archetype (or, optionally, his position group).
2. **Choose the features:** the archetype's 12 most distinctive metrics (high *or* low counts).
3. **Z-score** each feature within the pool, so no metric dominates because of its units.
4. **PCA**, keeping enough components for 85% of the variance.
5. **Cosine similarity** between the target and each candidate, ranked.
6. **Explain:** the score splits exactly into per-metric shares, so each result can say why.
"""
)
st.markdown(
    "**What the % means:** the match % is cosine similarity of statistical profiles within an "
    "archetype. It does not measure role equivalence. A 90% match can be two players in the same "
    "archetype who play different positions. Position context is shown on each similar-player "
    "card. The % was validated on a 100-pair sample (ANALYTICS_DECISIONS section 23): precision is "
    "roughly 50% across thresholds, and this is documented as a known limitation."
)

design.render(design.section_header("Read the full documents"))
with st.expander("ANALYTICS_DECISIONS.md: every definition choice behind the metrics"):
    st.markdown(_read("ANALYTICS_DECISIONS.md"))
with st.expander("BLOCKERS.md: data problems, dead sources and known gaps"):
    st.markdown(_read("BLOCKERS.md"))
st.caption(
    "Both files live in the project root. Photos: Wikipedia, credited per image in "
    "data/player_images/credits.json; fine for local or portfolio use, a commercial use would "
    "need a licence check for each image."
)
