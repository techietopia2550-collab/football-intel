"""Read-side functions the chatbot's tools need that no single existing function provides
(docs/RAG_DESIGN.md section 9.3): player stats, player comparison, percentile filters, metric
explanations, team-axis rankings and the methodology text. Plus player / team name resolution.

Everything is assembled from existing engines and tables (`directory`, `games`, `profile`,
`market_values`, `team_profile`, `player_percentiles`); nothing is re-tuned. Every function
returns plain, JSON-safe dicts and lists (no DataFrames, no NaN, no numpy scalars), and reports a
bad name as {"error": "not_found"} or {"error": "ambiguous", "candidates": [...]} instead of
raising.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import difflib
import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Engine, inspect, text

from src.analytics.directory import (
    load_player_percentiles,
    load_player_totals,
    score_of,
    search,
    stats_table,
)
from src.analytics.games import load_player_matches, season_game_stats
from src.analytics.market_values import load_market_values, player_value
from src.analytics.metrics import METRICS_BY_NAME
from src.analytics.percentiles import MIN_GROUP_SIZE, PERCENTILES_TABLE
from src.analytics.profile import MIXED_ROLE_SHARE, RELEVANT_METRICS, _fold, classify
from src.analytics.similarity import ARCHETYPES_TABLE
from src.analytics.team_profile import FLIPPED_AXES, RADAR_LABELS, axis_scores
from src.analytics.team_similarity import load_team_style
from src.analytics.team_style import AXIS_COLUMNS
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON, INSUFFICIENT_TABLE, season_table_name

MAX_CANDIDATES = 8  # names listed in an "ambiguous" reply
MAX_SUGGESTIONS = 5
VALUE_DECIMALS = 3  # raw metric values sent to the LLM
OPERATORS: dict[str, Any] = {
    ">=": lambda s, t: s >= t, "<=": lambda s, t: s <= t,
    ">": lambda s, t: s > t, "<": lambda s, t: s < t,
}  # fmt: skip
PERCENTILE_BASIS = (
    "Ranked within the player's position group (GK, CB, FB, DM, CM, AM, W, ST), pooled across "
    "the four leagues; qualifying players (900+ minutes) only."
)


# ---- JSON safety -------------------------------------------------------------------------------


def clean(obj: Any) -> Any:
    """Recursively turn numpy scalars, NaN, dates and tuples into plain JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [clean(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if obj is pd.NaT:
        return None
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


# ---- name resolution ---------------------------------------------------------------------------

_POOLS: dict[tuple[str, str, str], pd.DataFrame] = {}


def _engine(engine: Engine | None) -> Engine:
    return engine or get_engine()


def _tier(key: str, q: str) -> int:
    """How a folded name matches a folded query, in `directory.search`'s order: 4 exact, 3 a whole
    word, 2 a name or word prefix, 1 a substring, 0 only fuzzy (or not at all)."""
    words = key.replace("'", " ").replace("-", " ").split()
    if key == q:
        return 4
    if q in words:
        return 3
    if key.startswith(q) or any(w.startswith(q) for w in words):
        return 2
    return 1 if q in key else 0


def _player_pool(season: str, engine: Engine) -> pd.DataFrame:
    """Every player of the season, qualified (900+ minutes) or not, with a folded name key."""
    cache_key = ("player", season, str(engine.url))
    if cache_key not in _POOLS:
        cols = "player_id, player_name, team, league, position_group_fine, minutes_total, matches"
        frames = [
            pd.read_sql(text(f"SELECT {cols} FROM {season_table_name(season)}"), engine).assign(
                qualified=True
            )
        ]
        if inspect(engine).has_table(INSUFFICIENT_TABLE):
            frames.append(
                pd.read_sql(
                    text(f"SELECT {cols} FROM {INSUFFICIENT_TABLE} WHERE season = :s"),
                    engine,
                    params={"s": season},
                ).assign(qualified=False)
            )
        pool = pd.concat(frames, ignore_index=True)
        pool["name_key"] = pool["player_name"].map(_fold)
        _POOLS[cache_key] = pool.sort_values("minutes_total", ascending=False).reset_index(
            drop=True
        )
    return _POOLS[cache_key]


def _team_pool(season: str, engine: Engine) -> pd.DataFrame:
    cache_key = ("team", season, str(engine.url))
    if cache_key not in _POOLS:
        style = load_team_style(season, engine)
        pool = style[["team_name", "league", "league_position"]].copy()
        # `directory.search` reads `name_key` and orders ties by `minutes_total`
        pool["name_key"] = pool["team_name"].map(_fold)
        pool["minutes_total"] = 0.0
        _POOLS[cache_key] = pool.reset_index(drop=True)
    return _POOLS[cache_key]


def _resolve(pool: pd.DataFrame, query: str, name_col: str, fields: list[str]) -> dict[str, Any]:
    q = _fold(query.strip())
    if not q:
        return {"error": "not_found", "query": query, "suggestions": []}
    hits = search(pool, q)
    tiers = hits["name_key"].map(lambda k: _tier(k, q))
    best = int(tiers.max()) if len(hits) else 0
    if best == 0:  # fuzzy only: never guess, offer the names instead
        return {
            "error": "not_found", "query": query,
            "suggestions": hits[name_col].head(MAX_SUGGESTIONS).tolist(),
        }  # fmt: skip
    top = hits[tiers == best]
    if len(top) > 1:
        return clean(
            {
                "error": "ambiguous", "query": query,
                "candidates": top[fields].head(MAX_CANDIDATES).to_dict("records"),
            }
        )  # fmt: skip
    return clean(top.iloc[0][fields].to_dict())


def resolve_player_name(
    query: str, season: str | int = DEFAULT_SEASON, engine: Engine | None = None
) -> dict[str, Any]:
    """The one player matching `query` (accent- and case-insensitive), or an error dict."""
    fields = [
        "player_id", "player_name", "team", "league", "position_group_fine", "minutes_total",
        "matches", "qualified",
    ]  # fmt: skip
    return _resolve(_player_pool(str(season), _engine(engine)), query, "player_name", fields)


def resolve_team_name(
    query: str, season: str | int = DEFAULT_SEASON, engine: Engine | None = None
) -> dict[str, Any]:
    """The one team matching `query` (accent- and case-insensitive), or an error dict."""
    try:
        pool = _team_pool(str(season), _engine(engine))
    except LookupError:
        return {"error": "no_data", "detail": "team_style is not built"}
    return _resolve(pool, query, "team_name", ["team_name", "league", "league_position"])


# ---- 1. player_stats ---------------------------------------------------------------------------


def _archetype(player_id: str, season: str, engine: Engine) -> dict[str, Any] | None:
    if not inspect(engine).has_table(ARCHETYPES_TABLE):
        return None
    df = pd.read_sql(
        text(
            f"SELECT archetype_id, archetype_label, cluster_confidence FROM {ARCHETYPES_TABLE} "
            "WHERE player_id = :p AND season = :s"
        ),
        engine,
        params={"p": player_id, "s": season},
    )
    return None if df.empty else clean(df.iloc[0].to_dict())


def player_stats(
    player_name: str, season: str | int = DEFAULT_SEASON, engine: Engine | None = None
) -> dict[str, Any]:
    """Everything the profile page shows for one player-season, as plain dicts."""
    season, engine = str(season), _engine(engine)
    who = resolve_player_name(player_name, season, engine)
    if "error" in who:
        return who
    pid, group = who["player_id"], who["position_group_fine"]
    matches = load_player_matches(pid, season, engine)
    counts = season_game_stats(matches, group).model_dump() if len(matches) else None
    mv = player_value(pid, load_market_values(season, engine))
    out: dict[str, Any] = {
        "player": who, "season": season, "season_counts": counts,
        "market_value": mv.model_dump() if mv else None,
        "archetype": None, "stats": [], "strengths": [], "weaknesses": [], "warnings": [],
    }  # fmt: skip
    if not who["qualified"]:
        out["warnings"].append(
            "Below the 900-minute threshold: season counts only, no per-90 ranks or percentiles."
        )
        return clean(out)
    totals = load_player_totals(pid, season, engine)
    pct = load_player_percentiles(pid, season, engine)
    cls = classify(pct, group)
    out["archetype"] = _archetype(pid, season, engine)
    out["stats"] = [
        {**r, "value": _round(r["value"]), "per90": _round(r["per90"])}
        for r in stats_table(pct, totals, group).to_dict("records")
    ]
    out["strengths"], out["weaknesses"] = cls["strengths"], cls["weaknesses"]
    if len(pct) and bool(pct["small_group"].any()):
        out["warnings"].append(
            f"Only {int(pct['group_size'].iloc[0])} qualifying {group} players "
            f"(< {MIN_GROUP_SIZE}): percentiles are coarse."
        )
    if float(totals["position_group_fine_share"]) < MIXED_ROLE_SHARE:
        out["warnings"].append(
            f"Mixed role: only {float(totals['position_group_fine_share']):.0%} of minutes as "
            f"{group}."
        )
    if int(totals["n_teams"]) > 1:
        out["warnings"].append(
            f"Played for several clubs ({totals['team_split']}); team shown is the main one."
        )
    return clean(out)


# ---- 2. compare_players ------------------------------------------------------------------------


def _round(v: Any) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), VALUE_DECIMALS)


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 1)


def compare_players(
    player_a: str, player_b: str, season: str | int = DEFAULT_SEASON,
    engine: Engine | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Two players' stats side by side on the metrics both have, with percentile deltas (a - b)."""
    season, engine = str(season), _engine(engine)
    a, b = player_stats(player_a, season, engine), player_stats(player_b, season, engine)
    for side, res in (("a", a), ("b", b)):
        if "error" in res:
            return {**res, "side": side}
    rows_a = {r["metric"]: r for r in a["stats"]}
    rows_b = {r["metric"]: r for r in b["stats"]}
    keep = ("value", "per90", "percentile", "score", "band")
    metrics = [
        {
            "metric": m, "category": rows_a[m]["category"], "unit": rows_a[m]["unit"],
            "a": {k: rows_a[m][k] for k in keep}, "b": {k: rows_b[m][k] for k in keep},
            "percentile_delta": _delta(rows_a[m]["percentile"], rows_b[m]["percentile"]),
            "score_delta": _delta(rows_a[m]["score"], rows_b[m]["score"]),
        }
        for m in rows_a
        if m in rows_b
    ]  # fmt: skip
    ga, gb = a["player"]["position_group_fine"], b["player"]["position_group_fine"]
    notes = []
    if ga != gb:
        notes.append(
            f"Different position groups ({ga} vs {gb}): each percentile is against the player's "
            "own group, so a delta is not a like-for-like comparison."
        )
    for res in (a, b):
        if not res["player"]["qualified"]:
            notes.append(f"{res['player']['player_name']} is below 900 minutes: no percentiles.")
    return clean(
        {
            "season": season, "same_group": ga == gb,
            "a": {k: a[k] for k in ("player", "season_counts", "market_value", "archetype")},
            "b": {k: b[k] for k in ("player", "season_counts", "market_value", "archetype")},
            "metrics": metrics,
            "only_a": [m for m in rows_a if m not in rows_b],
            "only_b": [m for m in rows_b if m not in rows_a],
            "warnings": {"a": a["warnings"], "b": b["warnings"]}, "notes": notes,
        }
    )  # fmt: skip


# ---- 3. find_players_by_profile ----------------------------------------------------------------


def _metric_suggestions(name: str) -> list[str]:
    return difflib.get_close_matches(name, list(METRICS_BY_NAME), n=MAX_SUGGESTIONS, cutoff=0.5)


def _parse_conditions(
    conditions: Any,
) -> tuple[list[tuple[str, str, float]], dict[str, Any] | None]:
    parsed: list[tuple[str, str, float]] = []
    if not conditions:
        return parsed, {"error": "bad_arguments", "detail": "at least one condition is needed"}
    for c in conditions:
        if isinstance(c, dict):
            c = (c.get("metric"), c.get("op", c.get("operator")), c.get("threshold"))
        try:
            metric, op, threshold = str(c[0]), str(c[1]), float(c[2])
        except (TypeError, ValueError, IndexError):
            return parsed, {"error": "bad_arguments", "detail": f"bad condition {c!r}"}
        if metric not in METRICS_BY_NAME:
            return parsed, {
                "error": "unknown_metric", "metric": metric,
                "suggestions": _metric_suggestions(metric),
            }  # fmt: skip
        if op not in OPERATORS:
            return parsed, {
                "error": "bad_arguments",
                "detail": f"operator must be {list(OPERATORS)}",
            }
        if not 0 <= threshold <= 100:
            return parsed, {"error": "bad_arguments", "detail": "threshold is a 0-100 percentile"}
        parsed.append((metric, op, threshold))
    return parsed, None


def find_players_by_profile(
    position_group: str | list[str] | None,
    conditions: list[Any],
    season: str | int = DEFAULT_SEASON,
    leagues: list[str] | None = None,
    limit: int = 10,
    engine: Engine | None = None,
) -> dict[str, Any]:
    """Qualifying players whose percentile scores meet every (metric, operator, threshold)."""
    parsed, err = _parse_conditions(conditions)
    if err:
        return err
    metrics = [m for m, _, _ in parsed]
    if position_group is None:
        groups = [g for g, rel in RELEVANT_METRICS.items() if all(m in rel for m in metrics)]
        if not groups:
            return {"error": "metric_not_judged", "metrics": metrics,
                    "detail": "no position group judges all of these metrics together"}  # fmt: skip
    else:
        groups = [position_group] if isinstance(position_group, str) else list(position_group)
        unknown = [g for g in groups if g not in RELEVANT_METRICS]
        if unknown:
            return {"error": "bad_arguments", "detail": f"unknown position group(s) {unknown}",
                    "valid_groups": list(RELEVANT_METRICS)}  # fmt: skip
        for g in groups:
            for m in metrics:
                if m not in RELEVANT_METRICS[g]:
                    return {
                        "error": "metric_not_judged", "metric": m, "position_group": g,
                        "relevant_for_groups": [x for x, r in RELEVANT_METRICS.items() if m in r],
                    }  # fmt: skip
    season, engine = str(season), _engine(engine)
    gmarks = ",".join(f":g{i}" for i in range(len(groups)))
    mmarks = ",".join(f":m{i}" for i in range(len(metrics)))
    params: dict[str, Any] = {
        "s": season, **{f"g{i}": g for i, g in enumerate(groups)},
        **{f"m{i}": m for i, m in enumerate(metrics)},
    }  # fmt: skip
    pct = pd.read_sql(
        text(
            f"SELECT player_id, player_name, team, league, position_group_fine, metric, value, "
            f"percentile FROM {PERCENTILES_TABLE} WHERE season = :s "
            f"AND position_group_fine IN ({gmarks}) AND metric IN ({mmarks})"
        ),
        engine,
        params=params,
    )
    if leagues:
        pct = pct[pct["league"].isin(leagues)]
    minutes = _player_pool(season, engine).set_index("player_id")["minutes_total"]
    players = []
    for pid, rows in pct.groupby("player_id"):
        by_metric = rows.set_index("metric")
        detail: dict[str, dict[str, Any]] = {}
        margins: list[float] = []
        ok = True
        for m, op, thr in parsed:
            if m not in by_metric.index or pd.isna(by_metric.at[m, "percentile"]):
                ok = False
                break
            p = float(by_metric.at[m, "percentile"])
            s = score_of(m, p)
            judged = s is not None
            s = p if s is None else s  # descriptive metric: filter on the raw percentile
            if not OPERATORS[op](s, thr):
                ok = False
                break
            margins.append(s - thr if op in (">=", ">") else thr - s)
            detail[m] = {"value": _round(by_metric.at[m, "value"]), "percentile": p, "score": s,
                         "judged": judged}  # fmt: skip
        if not ok:
            continue
        first = rows.iloc[0]
        players.append(
            {
                "player_id": pid, "player_name": first["player_name"], "team": first["team"],
                "league": first["league"], "position_group_fine": first["position_group_fine"],
                "minutes_total": minutes.get(pid), "metrics": detail,
                "margin": round(float(np.mean(margins)), 1),
            }
        )  # fmt: skip
    players.sort(key=lambda p: (-p["margin"], p["player_name"]))
    return clean(
        {
            "season": season, "position_groups": groups,
            "conditions": [{"metric": m, "op": o, "threshold": t} for m, o, t in parsed],
            "score_note": "score = percentile, flipped where lower is better",
            "n_matching": len(players), "players": players[: max(1, int(limit))],
        }
    )  # fmt: skip


# ---- 4. explain_metric -------------------------------------------------------------------------


def _formula(name: str) -> str:
    m = METRICS_BY_NAME[name]
    if m.kind == "rate":
        return f"{m.numerator} / minutes played x 90"
    scale = f" x {m.scale:g}" if m.scale != 1 else ""
    guard = f"; empty when {m.denominator} < {m.min_denominator:g}" if m.min_denominator else ""
    return f"{m.numerator} / {m.denominator}{scale} (season totals{guard})"


def explain_metric(
    metric_name: str, player_name: str | None = None, season: str | int = DEFAULT_SEASON,
    engine: Engine | None = None,
) -> dict[str, Any]:  # fmt: skip
    """A metric's definition, formula and direction, plus one player's value and percentile."""
    if metric_name not in METRICS_BY_NAME:
        return {"error": "unknown_metric", "metric": metric_name,
                "suggestions": _metric_suggestions(metric_name)}  # fmt: skip
    m = METRICS_BY_NAME[metric_name]
    direction = {True: "higher is better", False: "lower is better",
                 None: "descriptive: never judged good or bad"}[m.higher_is_better]  # fmt: skip
    out: dict[str, Any] = {
        "metric": m.name, "category": m.group, "scope": m.scope, "kind": m.kind,
        "formula": _formula(m.name), "numerator": m.numerator, "denominator": m.denominator,
        "higher_is_better": m.higher_is_better, "direction": direction, "note": m.note,
        "relevant_for_groups": [g for g, rel in RELEVANT_METRICS.items() if m.name in rel],
        "percentile_basis": PERCENTILE_BASIS,
    }  # fmt: skip
    if player_name is None:
        return clean(out)
    season, engine = str(season), _engine(engine)
    who = resolve_player_name(player_name, season, engine)
    if "error" in who:
        return who
    group = who["position_group_fine"]
    info: dict[str, Any] = {**who, "judged_for_group": m.name in RELEVANT_METRICS.get(group, [])}
    if not who["qualified"]:
        info["note"] = "Below the 900-minute threshold: no percentiles."
    else:
        rows = load_player_percentiles(who["player_id"], season, engine)
        hit = rows[rows["metric"] == m.name]
        if hit.empty:
            info["note"] = f"{m.name} is not ranked for {group} players."
        else:
            r = hit.iloc[0]
            pct = None if pd.isna(r["percentile"]) else float(r["percentile"])
            info.update(
                value=_round(r["value"]), percentile=pct, score=score_of(m.name, pct),
                sample_size=r["sample_size"], group_size=r["group_size"],
                small_group=bool(r["small_group"]),
            )  # fmt: skip
            if pct is None:
                info["note"] = "Not ranked: the sample is under the metric's minimum."
    out["player"] = info
    return clean(out)


# ---- 5. list_team_axis -------------------------------------------------------------------------


def list_team_axis(
    axis_name: str, direction: str = "top", n: int = 10, season: str | int = DEFAULT_SEASON,
    engine: Engine | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The top or bottom `n` teams on one team-style axis (0-100 rank, radar orientation)."""
    if axis_name not in AXIS_COLUMNS:
        return {"error": "unknown_axis", "axis": axis_name,
                "valid_axes": {a: RADAR_LABELS[a] for a in AXIS_COLUMNS}}  # fmt: skip
    if direction not in ("top", "bottom"):
        return {"error": "bad_arguments", "detail": "direction must be 'top' or 'bottom'"}
    try:
        style = load_team_style(str(season), _engine(engine))
    except LookupError:
        return {"error": "no_data", "detail": "team_style is not built"}
    ranked = style[["team_name", "league", "league_position", axis_name]].rename(
        columns={axis_name: "value"}
    )
    ranked["value"] = ranked["value"].astype(float).round(VALUE_DECIMALS)
    ranked["score"] = axis_scores(style)[axis_name].round(1)
    ranked = ranked.sort_values(
        ["score", "team_name"], ascending=[direction == "bottom", True], kind="stable"
    ).head(max(1, min(int(n), len(ranked))))
    teams = [{"rank": i, **r} for i, r in enumerate(ranked.to_dict("records"), start=1)]
    note = (
        "Flipped axis: a high score means a LOW raw value (fewer opponent passes per defensive "
        "action = harder press; fewer seconds = faster)."
        if axis_name in FLIPPED_AXES
        else "A high score means a high raw value."
    )
    return clean(
        {
            "season": str(season), "axis": axis_name, "label": RADAR_LABELS[axis_name],
            "direction": direction, "teams_in_pool": len(style), "orientation": note,
            "teams": teams,
        }
    )  # fmt: skip


# ---- 6. methodology ----------------------------------------------------------------------------

# Copied from the Methodology page (src/app/pages/5_methodology.py); keep the two in step.
METHODOLOGY: dict[str, str] = {
    "data_sources": (
        "Data provided by StatsBomb. Event data for the 2015/16 season of the Premier League, "
        "La Liga, Serie A and Ligue 1 comes from the StatsBomb Open Data repository and is used "
        "under its user agreement. 1,507 of the 1,517 matches are ingested; 1,337 players have "
        "900 or more minutes and are ranked. Player photos come from Wikipedia."
    ),
    "what_is_measured": (
        "Event-level metrics: counts of what a player did (passes, carries, tackles, pressures, "
        "shots and so on), taken from StatsBomb events; every metric is a count or a ratio of "
        "counts. Per-90 rates: season counts divided by minutes played, times 90. Ratios (pass "
        "completion, tackle win %) are computed from season totals and left empty when the "
        "sample is too small. Percentile ranks: each metric is ranked within position group "
        "(goalkeeper, centre-back, full-back, defensive, central and attacking midfielder, "
        "winger, striker), pooled across the four leagues; a 90th percentile means higher than "
        "90% of players in that position group. Archetypes: 10 outfield styles and 3 goalkeeper "
        "styles found by clustering 14 per-90 metrics (K-Means on principal components); position "
        "labels are not an input, and the names are descriptions a person chose. Similarity: "
        "metrics are z-scored, reduced with PCA (85% of variance) and compared with cosine "
        "similarity, by default inside the player's archetype using the 12 metrics that most "
        "define it; each result lists the metrics that explain it."
    ),
    "not_measured": (
        "Leadership, positioning intelligence, communication, decision-making under pressure and "
        "other mental attributes are not measured. Event data records what happened on the ball, "
        "not why, and not what a player did to shape play away from it. A few metrics are "
        "labelled as proxies (for example how often teammates pass to a player); they describe "
        "behaviour and never rate character."
    ),
    "known_limitations": (
        "Eight position groups, from the lineup position strings only; a player who moves around "
        "is filed by where he spent most minutes (Kanté is a central midfielder here, flagged as "
        "a mixed role). Archetype boundaries are soft: silhouette scores are low and the labels "
        "are judgement calls (known misses: Mata in Deep playmaker, M'Vila in Defensive "
        "full-back). Data flaws: 16 of 1,517 matches are affected by StatsBomb lineup-clock "
        "errors; 10 could not be repaired and are left out, 6 carry small minutes errors. No "
        "current-season data, no ages, no career history. Leagues are pooled as one population; "
        "whether tagging or pace differ between leagues has not been checked. Similarity is "
        "statistical, not categorical: on a 100-pair test, precision is roughly 50% at every "
        "threshold."
    ),
    "why_2015_16": (
        "No free source gives current-season defensive and advanced passing data. FBref sits "
        "behind a Cloudflare challenge and lost its advanced data in January 2026; WhoScored and "
        "Sofascore need browser automation. StatsBomb's free open data is the one legal source "
        "with event-level detail for full leagues, and its complete league coverage is 2015/16."
    ),
    "market_values": (
        "Source: the dcaribou transfermarkt-datasets dump (CC0), read from local files; nothing "
        "is scraped. A market value is Transfermarkt's community estimate of a transfer fee, not "
        "a wage and not a fee that was paid. Snapshot: each player's latest valuation dated "
        "1 Oct 2015 to 1 Mar 2016. Players are matched by name against Transfermarkt players of "
        "the same club (exact, normalized, then fuzzy); an ambiguous name is left unmatched, and "
        "unmatched or unvalued players have no value, never zero. A club's squad market value "
        "sums only its profiled (900+ minute) players and says how many have a value."
    ),
    "tactical_composer": (
        "Suggests a starting XI whose players statistically fit a target team style within a "
        "market-value budget and a formation. It builds a squad on paper, not tactics, and "
        "predicts no results, form, chemistry, fitness or development. Style fit is a heuristic: "
        "nine of the twelve team-style axes are mirrored by player metrics; crossing volume, "
        "width and set-piece reliance are not used. Fit measures style, not quality. The build "
        "is greedy (strikers first), not an optimiser."
    ),
    "similarity_engine": (
        "1. Choose the pool: the target's archetype (or his position group). 2. Choose the "
        "features: the archetype's 12 most distinctive metrics. 3. Z-score each feature within "
        "the pool. 4. PCA, keeping 85% of the variance. 5. Cosine similarity between the target "
        "and each candidate, ranked. 6. Explain: the score splits exactly into per-metric shares."
    ),
    "match_percent": (
        "The match % is cosine similarity of statistical profiles within an archetype. It does "
        "not measure role equivalence. Validated on a 100-pair sample: precision is roughly 50% "
        "across thresholds. The app shows one measured tier: Fair at 60% or more, Weak below."
    ),
}


def methodology() -> dict[str, str]:
    """The fixed methodology text of the Methodology page, as a dict of sections."""
    return dict(METHODOLOGY)


__all__ = [
    "clean", "compare_players", "explain_metric", "find_players_by_profile", "list_team_axis",
    "methodology", "player_stats", "resolve_player_name", "resolve_team_name",
]  # fmt: skip
