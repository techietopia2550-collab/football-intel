"""Tactical Composer: suggest a starting XI whose players statistically fit a target team style
within a total market-value budget. Greedy, by design: no ILP/LP solver. ANALYTICS_DECISIONS.md
section 29.

It builds a squad that fits a style on paper; it does not build tactics and predicts nothing
(results, form, chemistry). Inputs are `player_style_fit` (section 28) and the Transfermarkt
market values (sections 25 and 27).

Fit of one player to a target. The target is 0-100 per axis on the Team Fingerprint scale (50 =
the median of the 80 teams). Both vectors are centred (player score - 0.5, target / 100 - 0.5) and
compared with cosine similarity over the axes both have, so "above the median where the style is
above the median" is what counts, not raw size. Fit = (cosine + 1) / 2, in 0-1. When the target is
flat (every used axis within a few points of 50) a cosine has no direction, so fit falls back to
closeness: 1 - mean absolute gap * 2 (clipped at 0).

Greedy build (`build_squad`):
1. Pool: players with a style-fit row (so a market value), `min_minutes`, optional leagues.
2. Slots are filled in scarcity order: ST, GK, CB, W, AM, DM, CM, FB.
3. For each slot, candidates are unpicked players of that position group. Budget checks:
   - hard: the price must leave enough for the cheapest possible players in every slot still open
     (a conservative bound), so the XI can always be completed within budget;
   - soft: prefer a price that also leaves the median price of every open slot's group. When no
     candidate passes the soft check, the hard one alone applies.
4. Score = fit + VALUE_BONUS x the candidate's value rank among the slot's candidates (0-1), so the
   cheapest player is not always taken. Highest score wins.
Variants change one rule each: best_fit (no value bonus, no soft check), budget_lean (the cheapest
player whose fit is in the top FIT_BAND of the slot's affordable candidates), most_balanced (the
standard build with at most one player per club).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import numpy as np
import pandas as pd

from src.analytics.formations import Slot, get_formation
from src.analytics.style_fit import PLAYER_AXES
from src.analytics.team_profile import FLIPPED_AXES, RADAR_LABELS
from src.analytics.team_style import AXIS_COLUMNS
from src.models.composer import AxisBreakdown, AxisContribution, Squad, SquadSlot, Variant

SCARCITY_ORDER: tuple[str, ...] = ("ST", "GK", "CB", "W", "AM", "DM", "CM", "FB")
VALUE_BONUS = 0.05  # weight of the value rank (0-1) added to a 0-1 fit
FIT_BAND = 0.20  # budget_lean: "fits the style" = fit in the top 20% of the slot's candidates
FLAT_TARGET = 0.05  # centred target norm below this = flat target: closeness instead of cosine
MIN_AXES = 2  # a player needs scores on at least this many used axes to be fitted
TOP_AXES = 3
MAX_PER_CLUB_BALANCED = 1

# Target presets on the 0-100 Team Fingerprint scale. Hand-set, not fitted to any team; the three
# team-level-only axes are kept at 50 (they are shown but never used). Section 29.
PRESETS: dict[str, dict[str, float]] = {
    "High-press direct": {
        "possession_pct": 45, "ppda": 90, "direct_speed": 80, "buildup_length": 30,
        "def_line_height": 80, "width": 50, "crosses_p90": 50, "progressive_pass_rate": 70,
        "xg_per_shot": 55, "shots_pm": 70, "transition_secs": 85, "set_piece_xg_pct": 50,
    },
    "Possession control": {
        "possession_pct": 90, "ppda": 70, "direct_speed": 20, "buildup_length": 90,
        "def_line_height": 75, "width": 50, "crosses_p90": 50, "progressive_pass_rate": 45,
        "xg_per_shot": 65, "shots_pm": 65, "transition_secs": 40, "set_piece_xg_pct": 50,
    },
    "Low-block counter": {
        "possession_pct": 15, "ppda": 20, "direct_speed": 80, "buildup_length": 20,
        "def_line_height": 15, "width": 50, "crosses_p90": 50, "progressive_pass_rate": 60,
        "xg_per_shot": 55, "shots_pm": 35, "transition_secs": 85, "set_piece_xg_pct": 50,
    },
    "Balanced": {a: (60 if a in PLAYER_AXES else 50) for a in AXIS_COLUMNS},
}  # fmt: skip
VARIANT_TITLES: dict[Variant, str] = {
    "standard": "Standard", "best_fit": "Best fit", "budget_lean": "Budget lean",
    "most_balanced": "Most balanced",
}  # fmt: skip


class InfeasibleBudget(ValueError):
    """Even the cheapest eligible XI costs more than the budget, or a slot has no candidate."""


# ---- fit ---------------------------------------------------------------------------------------


def _target_vector(target: dict[str, float]) -> np.ndarray:
    return np.array([float(target.get(a, 50.0)) / 100 - 0.5 for a in PLAYER_AXES])


def fit_scores(pool: pd.DataFrame, target: dict[str, float]) -> pd.DataFrame:
    """Fit (0-1) of every row of `pool` to `target`, plus each used axis's contribution. Pure.

    Returns a frame indexed like `pool` with columns `fit`, `n_axes` and `c_<axis>` (the axis's
    share of the cosine; they sum to the cosine). Rows with fewer than MIN_AXES scored axes get
    fit NaN (never picked)."""
    t = _target_vector(target)
    p = pool[list(PLAYER_AXES)].astype(float).to_numpy() - 0.5
    have = ~np.isnan(p)
    p0 = np.where(have, p, 0.0)
    t_masked = np.where(have, t, 0.0)
    n_axes = have.sum(axis=1)
    out = pd.DataFrame(index=pool.index)
    if np.linalg.norm(t) < FLAT_TARGET:
        gap = np.where(have, np.abs(p0 - t_masked), 0.0).sum(axis=1) / np.maximum(n_axes, 1)
        fit = np.clip(1 - 2 * gap, 0, 1)
        contrib = -np.where(have, np.abs(p0 - t_masked), 0.0)  # smaller gap = better
    else:
        norm = np.linalg.norm(p0, axis=1) * np.linalg.norm(t_masked, axis=1)
        safe = np.where(norm > 0, norm, 1.0)
        contrib = p0 * t_masked / safe[:, None]
        cos = np.where(norm > 0, contrib.sum(axis=1), 0.0)
        fit = (cos + 1) / 2
    out["fit"] = np.where(n_axes >= MIN_AXES, fit, np.nan)
    out["n_axes"] = n_axes
    for i, a in enumerate(PLAYER_AXES):
        out[f"c_{a}"] = np.where(have[:, i], contrib[:, i], np.nan)
    return out


def _top_contributions(row: pd.Series, n: int = TOP_AXES) -> list[AxisContribution]:
    cs = [(a, float(row[f"c_{a}"])) for a in PLAYER_AXES if pd.notna(row[f"c_{a}"])]
    cs.sort(key=lambda kv: -kv[1])
    return [
        AxisContribution(axis=a, label=RADAR_LABELS[a], contribution=round(c, 4)) for a, c in cs[:n]
    ]


# ---- greedy build ------------------------------------------------------------------------------


def eligible_pool(
    style_fit: pd.DataFrame, league_filter: list[str] | None = None, min_minutes: float = 900
) -> pd.DataFrame:
    """Players who may be suggested: a market value, enough minutes, in the chosen leagues. Pure."""
    pool = style_fit.loc[
        style_fit["market_value_eur"].notna() & (style_fit["minutes_total"] >= min_minutes)
    ]
    if league_filter:
        pool = pool.loc[pool["league"].isin(league_filter)]
    return pool.reset_index(drop=True)


def _slot_order(slots: list[Slot]) -> list[int]:
    return sorted(range(len(slots)), key=lambda i: (SCARCITY_ORDER.index(slots[i].group), i))


def _reserve(
    open_groups: list[str], avail: pd.DataFrame, how: str, exclude_cheapest_of: str | None = None
) -> float:
    """Money to keep back for the open slots. how="min": the k cheapest per group (skipping the
    very cheapest of `exclude_cheapest_of`, since the player picked now may be him, so the bound
    stays safe). how="median": k x the group's median price."""
    total = 0.0
    for group, k in pd.Series(open_groups, dtype=object).value_counts().items():
        prices = np.sort(
            avail.loc[avail["position_group_fine"] == group, "market_value_eur"].to_numpy()
        )
        if how == "median":
            total += k * (float(np.median(prices)) if len(prices) else 0.0)
        else:
            start = 1 if group == exclude_cheapest_of else 0
            chosen = prices[start : start + k]
            if len(chosen) < k:
                return float("inf")  # not enough players left for the open slots
            total += float(chosen.sum())
    return total


def build_squad(
    target_style: dict[str, float],
    budget_eur: float,
    formation: str,
    style_fit: pd.DataFrame,
    league_filter: list[str] | None = None,
    min_minutes: float = 900,
    variant: Variant = "standard",
) -> Squad:
    """Greedy XI for the target style within the budget (module docstring). Pure: `style_fit` is
    the `player_style_fit` table. Raises InfeasibleBudget when no XI fits the budget."""
    form = get_formation(formation)
    pool = eligible_pool(style_fit, league_filter, min_minutes)
    scored = pool.join(fit_scores(pool, target_style)).dropna(subset=["fit"])
    cheapest = _reserve([s.group for s in form.slots], scored, "min")
    if cheapest > budget_eur:
        raise InfeasibleBudget(
            f"The cheapest possible {formation} XI from this pool costs about "
            f"€{cheapest / 1e6:,.1f}m, above the €{budget_eur / 1e6:,.1f}m budget."
        )
    max_per_club = MAX_PER_CLUB_BALANCED if variant == "most_balanced" else None
    picked: dict[int, pd.Series] = {}
    spent = 0.0
    notes: list[str] = []
    order = _slot_order(form.slots)
    for step, i in enumerate(order):
        slot = form.slots[i]
        open_groups = [form.slots[j].group for j in order[step + 1 :]]
        taken = {r["player_id"] for r in picked.values()}
        avail = scored.loc[~scored["player_id"].isin(taken)]
        remaining = budget_eur - spent
        hard = remaining - _reserve(open_groups, avail, "min", exclude_cheapest_of=slot.group)
        cands = avail.loc[(avail["position_group_fine"] == slot.group)]
        if max_per_club is not None:
            counts = pd.Series([r["team"] for r in picked.values()], dtype=object).value_counts()
            capped = cands.loc[~cands["team"].map(counts).fillna(0).ge(max_per_club)]
            if capped.loc[capped["market_value_eur"] <= hard].empty:
                notes.append(f"{slot.label}: club limit relaxed, no other affordable candidate.")
            else:
                cands = capped
        feasible = cands.loc[cands["market_value_eur"] <= hard]
        if feasible.empty:
            raise InfeasibleBudget(f"No affordable {slot.group} left for {slot.label}.")
        if variant != "best_fit":
            soft = remaining - _reserve(open_groups, avail, "median")
            preferred = feasible.loc[feasible["market_value_eur"] <= soft]
            feasible = preferred if not preferred.empty else feasible
        if variant == "budget_lean":
            cut = feasible["fit"].quantile(1 - FIT_BAND)
            band = feasible.loc[feasible["fit"] >= cut]
            choice = band.sort_values(["market_value_eur", "fit"], ascending=[True, False]).iloc[0]
        else:
            bonus = 0.0 if variant == "best_fit" else VALUE_BONUS
            rank = cands["market_value_eur"].rank(pct=True).reindex(feasible.index)
            score = feasible["fit"] + bonus * rank
            choice = feasible.loc[score.sort_values(ascending=False, kind="stable").index[0]]
        picked[i] = choice
        spent += float(choice["market_value_eur"])
    return _assemble(form.name, form.slots, picked, target_style, budget_eur, variant, notes)


def _assemble(
    formation: str, slots: list[Slot], picked: dict[int, pd.Series], target: dict[str, float],
    budget: float, variant: Variant, notes: list[str],
) -> Squad:  # fmt: skip
    out_slots = []
    for i, s in enumerate(slots):
        r = picked[i]
        out_slots.append(SquadSlot(
            slot_label=s.label, group=s.group, x=s.x, y=s.y, player_id=r["player_id"],
            player_name=r["player_name"], team=r["team"], league=r["league"],
            market_value_eur=float(r["market_value_eur"]), fit=round(float(r["fit"]), 4),
            axis_scores={a: None if pd.isna(r[a]) else float(r[a]) for a in AXIS_COLUMNS},
            top_axes=_top_contributions(r),
        ))  # fmt: skip
    xi = pd.DataFrame([picked[i] for i in range(len(slots))])
    breakdown = []
    for a in AXIS_COLUMNS:
        used = a in PLAYER_AXES
        mean = xi[a].astype(float).mean() if used else float("nan")
        breakdown.append(AxisBreakdown(
            axis=a, label=RADAR_LABELS[a], target=float(target.get(a, 50.0)),
            squad_mean=None if pd.isna(mean) else round(float(mean) * 100, 1), used=used,
        ))  # fmt: skip
    totals = {a: float(xi[f"c_{a}"].sum(skipna=True)) for a in PLAYER_AXES}
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:TOP_AXES]
    return Squad(
        variant=variant, title=VARIANT_TITLES[variant], formation=formation, budget_eur=budget,
        total_value_eur=float(xi["market_value_eur"].sum()),
        avg_fit=round(float(xi["fit"].mean()), 4),
        slots=out_slots, breakdown=breakdown,
        top_axes=[AxisContribution(axis=a, label=RADAR_LABELS[a], contribution=round(c, 4))
                  for a, c in top],
        distinct_clubs=int(xi["team"].nunique()), notes=notes,
    )  # fmt: skip


def axis_raw_at(team_style: pd.DataFrame, axis: str, score: float) -> float:
    """The team value that sits at `score` (0-100, Team Fingerprint orientation) on an axis: a
    quantile of the 80 teams, reversed for the flipped axes (PPDA, transition seconds). Pure."""
    q = score / 100
    if axis in FLIPPED_AXES:
        q = 1 - q
    return float(team_style[axis].astype(float).quantile(q))


def build_squad_variants(
    target_style: dict[str, float],
    budget_eur: float,
    formation: str,
    style_fit: pd.DataFrame,
    league_filter: list[str] | None = None,
    min_minutes: float = 900,
) -> list[Squad]:
    """Best fit, Budget lean and Most balanced, in that order. Raises InfeasibleBudget."""
    return [
        build_squad(target_style, budget_eur, formation, style_fit, league_filter, min_minutes, v)
        for v in ("best_fit", "budget_lean", "most_balanced")
    ]
