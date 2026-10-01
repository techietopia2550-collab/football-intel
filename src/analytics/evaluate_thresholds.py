"""Measure what a similarity score means, from the hand-labelled test set.

`python -m src.analytics.evaluate_thresholds` reads `data/manual/similarity_test_set.csv` (built by
`build_test_pairs`, labelled "similar" / "not similar" by hand) and writes
`docs/similarity_thresholds.md` and `docs/similarity_thresholds.png`.

For each threshold t in THRESHOLDS: precision (share of pairs scoring >= t labelled similar),
recall (share of similar pairs scoring >= t), F1 and the number of pairs >= t, each precision with
a Wilson 95% interval. Precision per score band is reported too: the test set is stratified by
band, so band precision is the unbiased answer to "what does a score of 0.87 mean", while the
cumulative numbers depend on the stratified mix (ANALYTICS_DECISIONS.md section 22).

Tier cut-offs are read off the curve by a rule fixed before any label was seen (`recommend`):
  Strong = lowest threshold t where precision reaches 0.90, Good 0.75, Fair 0.50, on BOTH readings:
  cumulative (pairs >= t) and local (pairs in [t, t + LOCAL_WIDTH), i.e. pairs scoring about t),
  each with at least MIN_PAIRS pairs. The local reading stops a cut being carried by the pairs far
  above it: a tier label is shown on a pair that scores right at the cut, so precision there is
  what the label promises. "Weak" is kept only if the pairs below the Fair cut are mostly (over
  half) labelled not similar. A tier no threshold reaches is reported as not supported.

Until every row is labelled the script stops with the "ready for labeling" message and writes
nothing (`--allow-partial` evaluates the labelled rows only, and says so in the report).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.analytics.build_test_pairs import READY_MESSAGE, SCORE_BANDS, band_label, default_path
from src.utils.config import settings

THRESHOLDS: tuple[float, ...] = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
SIMILAR, NOT_SIMILAR = "similar", "not similar"
TIER_PRECISION: tuple[tuple[str, float], ...] = (("Strong", 0.90), ("Good", 0.75), ("Fair", 0.50))
CURRENT_TIERS: dict[str, float] = {
    "Strong": 0.85,
    "Good": 0.70,
    "Fair": 0.55,
}  # judgement, unmeasured
MIN_PAIRS = 10
LOCAL_WIDTH = 0.10  # two 0.05 bands: about 20 pairs per window in the 100-pair design
REPORT_NAME, CHART_NAME = "similarity_thresholds.md", "similarity_thresholds.png"


class NotLabelled(ValueError):
    """The test set has no labels yet, or (without allow_partial) some rows are blank."""


@dataclass(frozen=True)
class Recommendation:
    cuts: dict[str, float | None]  # tier -> lowest qualifying threshold, None = not supported
    keep_weak: bool
    reasons: list[str]

    @property
    def matches_current(self) -> bool:
        return self.cuts == CURRENT_TIERS and self.keep_weak


def load_labelled(path: Path, allow_partial: bool = False) -> tuple[pd.DataFrame, int]:
    """(labelled rows with a boolean `similar` column, number of blank rows). Raises NotLabelled
    when nothing is labelled, or rows are blank without `allow_partial`; ValueError for a label
    that is neither "similar" nor "not similar"."""
    df = pd.read_csv(path, dtype={"label": str}, keep_default_na=False)
    labels = df["label"].str.strip().str.lower()
    blank = labels.eq("")
    if blank.all():
        raise NotLabelled(READY_MESSAGE.format(path=path))
    if blank.any() and not allow_partial:
        ids = df.loc[blank, "pair_id"].tolist()
        raise NotLabelled(
            f"{int(blank.sum())} rows still unlabelled (pair_id {ids[:10]}...): "
            "label them, or pass --allow-partial"
        )
    bad = ~blank & ~labels.isin([SIMILAR, NOT_SIMILAR])
    if bad.any():
        raise ValueError(
            f"Labels must be '{SIMILAR}' or '{NOT_SIMILAR}'; pair_id "
            f"{df.loc[bad, 'pair_id'].tolist()} have {sorted(set(labels[bad]))}"
        )
    out = df[~blank].copy()
    out["similar"] = labels[~blank].eq(SIMILAR).to_numpy()
    out["cosine_score"] = out["cosine_score"].astype(float)
    return out, int(blank.sum())


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes in n (nan, nan for n = 0)."""
    if n == 0:
        return math.nan, math.nan
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def threshold_table(df: pd.DataFrame, thresholds: tuple[float, ...] = THRESHOLDS) -> pd.DataFrame:
    """Precision / recall / F1 / count per threshold (score >= t counts as predicted similar)."""
    total_similar = int(df["similar"].sum())
    rows = []
    for t in thresholds:
        above = df["cosine_score"] >= t - 1e-12
        n_above, tp = int(above.sum()), int((above & df["similar"]).sum())
        precision = tp / n_above if n_above else math.nan
        recall = tp / total_similar if total_similar else math.nan
        f1 = (
            2 * precision * recall / (precision + recall)
            if n_above and total_similar and precision + recall > 0
            else math.nan
        )
        lo, hi = wilson(tp, n_above)
        near = above & (df["cosine_score"] < t + LOCAL_WIDTH - 1e-12)
        n_near, k_near = int(near.sum()), int((near & df["similar"]).sum())
        rows.append(
            {
                "threshold": t,
                "n_above": n_above,
                "similar_above": tp,
                "precision": precision,
                "precision_lo": lo,
                "precision_hi": hi,
                "recall": recall,
                "f1": f1,
                "local_n": n_near,
                "local_precision": k_near / n_near if n_near else math.nan,
            }
        )
    return pd.DataFrame(rows)


def band_table(df: pd.DataFrame) -> pd.DataFrame:
    """Precision inside each score band of the sampling design (unaffected by stratification)."""
    rows = []
    for lo, hi in SCORE_BANDS:
        inside = (df["cosine_score"] >= lo) & (df["cosine_score"] < hi)
        n, k = int(inside.sum()), int((inside & df["similar"]).sum())
        p_lo, p_hi = wilson(k, n)
        rows.append(
            {
                "band": band_label(lo, hi),
                "n": n,
                "similar": k,
                "precision": k / n if n else math.nan,
                "precision_lo": p_lo,
                "precision_hi": p_hi,
            }
        )
    return pd.DataFrame(rows)


def position_split(df: pd.DataFrame) -> pd.DataFrame:
    """Precision for same-position and cross-position pairs (position_a == position_b or not),
    with the mean cosine of each. Caveat: the labels were drawn from position and archetype
    rules, so part of the gap is built into the labels themselves."""
    same = df["position_a"] == df["position_b"]
    rows = []
    for kind, mask in (("same", same), ("other", ~same)):
        n, k = int(mask.sum()), int((mask & df["similar"]).sum())
        p_lo, p_hi = wilson(k, n)
        rows.append(
            {
                "position": kind,
                "n": n,
                "similar": k,
                "precision": k / n if n else math.nan,
                "precision_lo": p_lo,
                "precision_hi": p_hi,
                "mean_cosine": float(df.loc[mask, "cosine_score"].mean()) if n else math.nan,
            }
        )
    return pd.DataFrame(rows)


def recommend(table: pd.DataFrame, df: pd.DataFrame) -> Recommendation:
    """Tier cut-offs from the curve, by the rule in the module docstring."""
    cuts: dict[str, float | None] = {}
    reasons: list[str] = []
    for tier, need in TIER_PRECISION:
        ok = table[
            (table["precision"] >= need)
            & (table["n_above"] >= MIN_PAIRS)
            & (table["local_precision"] >= need)
            & (table["local_n"] >= MIN_PAIRS)
        ]
        cuts[tier] = float(ok["threshold"].min()) if not ok.empty else None
        if cuts[tier] is None:
            reasons.append(
                f"{tier}: no threshold reaches precision {need:.0%} (cumulative and "
                f"local, >= {MIN_PAIRS} pairs each); tier not supported by the data"
            )
        else:
            row = ok.loc[ok["threshold"].idxmin()]
            reasons.append(
                f"{tier}: from {cuts[tier]:.2f}, precision {row['precision']:.0%} "
                f"(95% CI {row['precision_lo']:.0%}-{row['precision_hi']:.0%}, "
                f"n={int(row['n_above'])}); local {row['local_precision']:.0%} "
                f"(n={int(row['local_n'])}); the lowest cut reaching {need:.0%} on both"
            )
    fair = cuts.get("Fair")
    below = df[df["cosine_score"] < fair] if fair is not None else df.iloc[0:0]
    keep_weak = bool(len(below) and below["similar"].mean() < 0.5)
    if fair is None:
        reasons.append("Weak: no Fair cut, so no Weak tier can be placed under it")
    elif not len(below):
        reasons.append(f"Weak: no labelled pair scores below {fair:.2f}; nothing to call Weak")
    else:
        reasons.append(
            f"Weak: below {fair:.2f}, {below['similar'].mean():.0%} of {len(below)} pairs "
            f"are similar -> {'keep' if keep_weak else 'drop'} the Weak tier"
        )
    return Recommendation(cuts, keep_weak, reasons)


def plot(table: pd.DataFrame, path: Path) -> Path:
    """Precision (with Wilson band), recall and F1 against the threshold."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    t = table["threshold"].to_numpy()
    ax.fill_between(
        t,
        table["precision_lo"],
        table["precision_hi"],
        color="#1f77b4",
        alpha=0.15,
        label="Precision 95% CI",
    )
    ax.plot(t, table["precision"], "o-", color="#1f77b4", label="Precision")
    ax.plot(
        t,
        table["local_precision"],
        "d-",
        color="#9467bd",
        alpha=0.8,
        label=f"Local precision [t, t+{LOCAL_WIDTH:.2f})",
    )
    ax.plot(t, table["recall"], "s--", color="#ff7f0e", label="Recall")
    ax.plot(t, table["f1"], "^:", color="#2ca02c", label="F1")
    for tier, need in TIER_PRECISION:
        ax.axhline(need, color="#888888", lw=0.6, ls=":")
        ax.text(t[-1] + 0.005, need, f"{tier} {need:.0%}", va="center", fontsize=7, color="#555555")
    ax.set(
        xlabel="Cosine threshold (score >= t counts as 'similar')",
        ylabel="Share",
        ylim=(0, 1.02),
        xlim=(t[0] - 0.01, t[-1] + 0.06),
        title="Similarity: precision vs threshold (labelled pairs)",
    )
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _pct(x: float) -> str:
    return "–" if isinstance(x, float) and np.isnan(x) else f"{x:.0%}"


def _position_lines(df: pd.DataFrame) -> list[str]:
    """The report's same-position vs other-positions table (needs position_a / position_b)."""
    lines = [
        "",
        "## Same position vs other positions",
        "",
        "Pairs are split on the two players' position groups (`position_a` vs `position_b`). The "
        "score itself ignores position; the app uses this split to show same-position players "
        "first. The labelling rules used position, so part of this gap is built into the labels.",
        "",
        "| Positions | Pairs | Similar | Precision | 95% CI | Mean cosine |",
        "|---|---|---|---|---|---|",
    ]
    for r in position_split(df).itertuples():
        lines.append(
            f"| {r.position} | {r.n} | {r.similar} | {_pct(r.precision)} | "
            f"{_pct(r.precision_lo)}-{_pct(r.precision_hi)} | {r.mean_cosine:.3f} |"
        )
    return lines


def report(
    df: pd.DataFrame,
    table: pd.DataFrame,
    bands: pd.DataFrame,
    rec: Recommendation,
    blank: int,
    chart_name: str,
) -> str:
    lines = [
        "# Similarity thresholds: measured precision",
        "",
        f"Generated {date.today().isoformat()} by `python -m src.analytics.evaluate_thresholds` "
        "from "
        f"`data/manual/similarity_test_set.csv`: {len(df)} labelled pairs, "
        f"{int(df['similar'].sum())} labelled similar. The labels were generated by an LLM from "
        "position and archetype rules (ANALYTICS_DECISIONS.md section 23): a weak but honest "
        "baseline, not a human-expert ground truth.",
        "",
    ]
    if blank:
        lines += [f"> **Partial:** {blank} rows were unlabelled and are excluded.", ""]
    lines += [
        "The pairs are stratified by score (one per band per archetype, `build_test_pairs`), so "
        "the "
        "per-band precision below is the direct reading of a score; the cumulative precision and "
        "recall describe this stratified mix, not all pairs in the database. Local precision is "
        f"over pairs scoring in [t, t + {LOCAL_WIDTH:.2f}).",
        "",
        "## Precision, recall and F1 by threshold",
        "",
        "| Threshold | Pairs >= t | Similar >= t | Precision | 95% CI | Recall | F1 "
        "| Local precision (n) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in table.itertuples():
        lines.append(
            f"| {r.threshold:.2f} | {r.n_above} | {r.similar_above} | {_pct(r.precision)} | "
            f"{_pct(r.precision_lo)}-{_pct(r.precision_hi)} | {_pct(r.recall)} | "
            f"{_pct(r.f1)} | {_pct(r.local_precision)} ({r.local_n}) |"
        )
    lines += [
        "",
        f"![Precision vs threshold]({chart_name})",
        "",
        "## Precision within each score band",
        "",
        "| Band | Pairs | Similar | Precision | 95% CI |",
        "|---|---|---|---|---|",
    ]
    for r in bands.itertuples():
        lines.append(
            f"| {r.band} | {r.n} | {r.similar} | {_pct(r.precision)} | "
            f"{_pct(r.precision_lo)}-{_pct(r.precision_hi)} |"
        )
    if {"position_a", "position_b"} <= set(df.columns):
        lines += _position_lines(df)

    def cut(v: float | None) -> str:
        return "not supported" if v is None else f"{v:.0%}+"

    lines += [
        "",
        "## Recommended tiers (rule fixed before labelling)",
        "",
        "| Tier | Current (judgement) | Measured |",
        "|---|---|---|",
    ]
    for tier, _ in TIER_PRECISION:
        lines.append(f"| {tier} | {cut(CURRENT_TIERS[tier])} | {cut(rec.cuts[tier])} |")
    lines.append(f"| Weak | below Fair | {'below Fair' if rec.keep_weak else 'drop'} |")
    lines += [
        "",
        *[f"- {x}" for x in rec.reasons],
        "",
        "Verdict: "
        + (
            "the current tiers are confirmed."
            if rec.matches_current
            else "the measured tiers differ from the current ones."
        ),
        "",
    ]
    return "\n".join(lines)


def run(
    csv_path: Path, out_dir: Path, allow_partial: bool = False
) -> tuple[pd.DataFrame, Recommendation, Path, Path]:
    """Evaluate the labelled CSV and write the report and chart into `out_dir`."""
    df, blank = load_labelled(csv_path, allow_partial)
    table = threshold_table(df)
    rec = recommend(table, df)
    chart = plot(table, out_dir / CHART_NAME)
    md = out_dir / REPORT_NAME
    md.write_text(report(df, table, band_table(df), rec, blank, CHART_NAME), encoding="utf-8")
    return table, rec, md, chart


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, default=None, help="default data/manual/")
    parser.add_argument("--out-dir", type=Path, default=None, help="default docs/")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    try:
        table, rec, md, chart = run(
            args.csv or default_path(), args.out_dir or settings.docs_dir, args.allow_partial
        )
    except NotLabelled as exc:
        print(exc)
        sys.exit(1)
    print(table.round(3).to_string(index=False))
    print("\n".join(rec.reasons))
    print(f"Wrote {md} and {chart}")


if __name__ == "__main__":
    _main()
