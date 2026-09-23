"""Cluster players into playing archetypes from their metrics, independent of position label.

Why: `position_group_fine` comes from StatsBomb lineup position strings, so players who play alike
can sit in different groups (Özil AM; Mata and Eriksen W; Cazorla DM; Pjanić CM). Archetypes group
by what a player DOES, and similarity can then search within an archetype across positions.

Method (outfield players; goalkeepers are clustered separately on goalkeeper metrics):
  1. Raw per-90 rates and ratios (`OUTFIELD_FEATURES`) from the pooled season table. NOT the
     within-position percentiles: those rank a player against his own position group, which would
     make an elite centre-back and an elite winger look alike and erase the very differences an
     across-position clustering needs. The values are the same numbers `player_percentiles.value`
     holds. Position labels are never a feature.
  2. Z-score each feature across the whole outfield pool. A ratio left empty by its sample-size
     guard is set to the pool mean (z = 0); a feature empty for over MAX_MISSING_SHARE is dropped.
  3. PCA keeping VARIANCE_TARGET of the variance.
  4. K-Means (fixed seed, many restarts) for every k in K_RANGE; silhouette per k. The k kept is a
     judgement call recorded in ANALYTICS_DECISIONS.md, not the arg-max.
  5. Clusters are numbered by their centroid's first principal component so ids are stable for a
     given dataset; the human labels in `OUTFIELD_LABELS` / `GK_LABELS` are keyed by those ids and
     must be re-checked whenever the data or the feature list changes (`--describe` prints the
     centroids).

`cluster_confidence` = (d2 - d1) / d2 with d1, d2 the distances to the nearest and second-nearest
centroid in PCA space: 0 on the boundary between two archetypes, tending to 1 deep inside one. It
is a geometric margin, not a probability.

Archetypes describe on-pitch behaviour in this one season's numbers. They say nothing about
leadership, mentality or ability, and the labels are descriptive names a person chose.

Archetype-distinctive features (`distinctive_features`): similarity inside an archetype does not use
every metric. Each archetype's centroid (mean z-score per feature, z-scored across the outfield
pool) is compared with the global centroid, and the DISTINCTIVE_N metrics with the largest absolute
difference define that archetype. Low is distinctive too: a Stopper is defined as much by how little
he carries as by how much he clears.

Run: `python -m src.analytics.archetypes` with `--sweep`, `--describe`, `--rebuild`, `--distinctive`
or `--export`.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sqlalchemy import Engine, text

from src.analytics.metrics import GK
from src.analytics.similarity import DISTINCTIVE_N, archetype_features, group_features
from src.models.schema import get_engine, init_db
from src.transform.normalize import DEFAULT_SEASON, replace_season_rows, season_table_name
from src.utils.config import PROJECT_ROOT
from src.utils.logging import logger

ARCHETYPES_TABLE = "player_archetypes"
SEED = 0
N_INIT = 50
VARIANCE_TARGET = 0.85
MAX_MISSING_SHARE = 0.30
K_RANGE = range(8, 19)
GK_K_RANGE = range(2, 6)
GK_ID_OFFSET = 100
EXPORT_DIR = PROJECT_ROOT / "docs" / "archetypes"

# The common outfield feature set, fixed before any result was seen (rationale per metric in
# ANALYTICS_DECISIONS.md section 14). Per-90 rates and ratios only; no position-specific metric.
OUTFIELD_FEATURES: tuple[str, ...] = (
    "tackles_p90", "interceptions_p90", "clearances_p90", "pressures_p90",
    "aerials_won_p90",
    "progressive_passes_p90", "pass_completion_pct", "passes_received_p90",
    "progressive_carries_p90", "dribbles_p90",
    "shots_p90", "xg_p90", "xa_p90", "key_passes_p90",
)  # fmt: skip

# Filled in after the sweep and a read of the centroids (see ANALYTICS_DECISIONS.md section 14).
CHOSEN_K: int | None = 10
CHOSEN_GK_K: int | None = 3
OUTFIELD_LABELS: dict[int, str] = {
    0: "Stopper centre-back",
    1: "Ball-playing centre-back",
    2: "Defensive full-back",
    3: "Destroyer",
    4: "Deep playmaker",
    5: "Pressing wide midfielder",
    6: "Target man",
    7: "Poacher",
    8: "Dribbling winger",
    9: "Chance creator",
}
# Goalkeeper ids are stored offset by GK_ID_OFFSET. These clusters mostly separate save % and
# shots faced, which depend on the defence in front of the keeper: descriptive, not a quality rank.
GK_LABELS: dict[int, str] = {
    0: "Protected high-save keeper",
    1: "Balanced keeper",
    2: "Busy low-save keeper",
}


@dataclass
class ArchetypeModel:
    """A fitted archetype clustering of one pool (outfield or goalkeepers)."""

    pool: str
    members: pd.DataFrame  # the pool's qualifying players, row-aligned with everything below
    features: list[str]
    dropped: dict[str, str]
    z: np.ndarray  # players x features
    pca: PCA
    scores: np.ndarray  # players x components
    k: int
    labels: np.ndarray  # cluster id (0..k-1, canonical order) per player
    centers: np.ndarray  # k x components, canonical order
    id_offset: int = 0

    @property
    def explained_variance(self) -> float:
        return float(self.pca.explained_variance_ratio_.sum())

    def distances(self) -> np.ndarray:
        """Players x k distances to every centroid, in PCA space."""
        return np.linalg.norm(self.scores[:, None, :] - self.centers[None, :, :], axis=2)


def load_pool(engine: Engine, season: str = DEFAULT_SEASON) -> pd.DataFrame:
    """Every qualifying player of the pooled season table."""
    return pd.read_sql(text(f"SELECT * FROM {season_table_name(season)}"), engine)


def zscore_matrix(
    members: pd.DataFrame, features: list[str] | tuple[str, ...]
) -> tuple[np.ndarray, list[str], dict[str, str]]:
    """Z-score each feature across `members`; return (matrix, kept features, dropped reasons)."""
    kept: list[str] = []
    dropped: dict[str, str] = {}
    columns: list[np.ndarray] = []
    for name in features:
        values = members[name].to_numpy(dtype=float)
        missing = float(np.isnan(values).mean())
        std = np.nanstd(values)
        if missing > MAX_MISSING_SHARE:
            dropped[name] = f"empty for {missing:.0%} of the pool"
        elif not np.isfinite(std) or std == 0:
            dropped[name] = "no variance in the pool"
        else:
            z = (values - np.nanmean(values)) / std
            columns.append(np.where(np.isnan(z), 0.0, z))
            kept.append(name)
    if not kept:
        raise ValueError("No usable features")
    return np.column_stack(columns), kept, dropped


def reduce(z: np.ndarray) -> tuple[PCA, np.ndarray]:
    pca = PCA(n_components=VARIANCE_TARGET, svd_solver="full").fit(z)
    return pca, pca.transform(z)


def sweep_k(scores: np.ndarray, ks: range, seed: int = SEED) -> pd.DataFrame:
    """Silhouette, inertia and cluster sizes for each k."""
    rows = []
    for k in ks:
        km = KMeans(n_clusters=k, n_init=N_INIT, random_state=seed).fit(scores)
        sizes = np.bincount(km.labels_, minlength=k)
        rows.append(
            {
                "k": k, "silhouette": float(silhouette_score(scores, km.labels_)),
                "inertia": float(km.inertia_), "min_size": int(sizes.min()),
                "max_size": int(sizes.max()),
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def stability(scores: np.ndarray, k: int, seeds: range = range(1, 6)) -> float:
    """Mean adjusted Rand index between the seed-0 clustering and clusterings from other seeds
    (1 = identical partitions): does the answer depend on the random start?"""
    base = KMeans(n_clusters=k, n_init=N_INIT, random_state=SEED).fit(scores).labels_
    return float(
        np.mean(
            [
                adjusted_rand_score(
                    base, KMeans(n_clusters=k, n_init=N_INIT, random_state=s).fit(scores).labels_
                )
                for s in seeds
            ]
        )
    )


def fit_pool(
    members: pd.DataFrame,
    features: list[str] | tuple[str, ...],
    k: int,
    pool: str,
    id_offset: int = 0,
) -> ArchetypeModel:
    """Z-score, PCA and K-Means one pool; clusters numbered by centroid PC1 (ascending)."""
    members = members.reset_index(drop=True)
    z, kept, dropped = zscore_matrix(members, features)
    pca, scores = reduce(z)
    km = KMeans(n_clusters=k, n_init=N_INIT, random_state=SEED).fit(scores)
    order = np.argsort(km.cluster_centers_[:, 0])  # canonical numbering
    rank = np.empty(k, dtype=int)
    rank[order] = np.arange(k)
    return ArchetypeModel(
        pool, members, kept, dropped, z, pca, scores, k,
        rank[km.labels_], km.cluster_centers_[order], id_offset,
    )  # fmt: skip


def centroid_profile(model: ArchetypeModel) -> pd.DataFrame:
    """Per-cluster mean z-score of every feature (rows = cluster id) plus its size."""
    z = pd.DataFrame(model.z, columns=model.features)
    out = z.groupby(model.labels).mean()
    out.insert(0, "size", np.bincount(model.labels, minlength=model.k))
    return out


def distinctive_table(
    outfield: pd.DataFrame, archetype_id: int, features: list[str] | None = None
) -> pd.DataFrame:
    """Every usable feature ranked by how far the archetype's centroid sits from the global one.

    `outfield` is the outfield pool with an `archetype_id` column. Features are z-scored across the
    whole pool (`zscore_matrix`), so the global centroid is ~0 (empty ratios are imputed as 0);
    features empty for over MAX_MISSING_SHARE of the pool cannot be scored and are absent.
    Columns: feature, archetype_z, global_z, distinctiveness = |archetype_z - global_z|.
    """
    outfield = outfield.reset_index(drop=True)
    z, kept, _ = zscore_matrix(outfield, features or archetype_features())
    frame = pd.DataFrame(z, columns=kept)
    arch = frame[(outfield["archetype_id"] == archetype_id).to_numpy()].mean()
    out = pd.DataFrame(
        {"feature": kept, "archetype_z": arch.to_numpy(), "global_z": frame.mean().to_numpy()}
    )
    out["distinctiveness"] = (out["archetype_z"] - out["global_z"]).abs()
    return out.sort_values("distinctiveness", ascending=False, kind="stable").reset_index(drop=True)


def distinctive_features(
    outfield: pd.DataFrame, archetype_id: int, n: int = DISTINCTIVE_N
) -> list[str]:
    """The `n` metrics that define an outfield archetype (see `distinctive_table`)."""
    return distinctive_table(outfield, archetype_id)["feature"].head(n).tolist()


def assignments(model: ArchetypeModel, labels: dict[int, str]) -> pd.DataFrame:
    """The `player_archetypes` rows for a fitted pool."""
    dist = model.distances()
    nearest = dist[np.arange(len(dist)), model.labels]
    other = np.where(np.arange(model.k)[None, :] == model.labels[:, None], np.inf, dist)
    second = other.min(axis=1)
    confidence = np.clip((second - nearest) / second, 0.0, 1.0)
    ids = model.labels + model.id_offset
    missing = set(ids) - set(labels)
    if missing:
        raise ValueError(f"No label for {model.pool} archetype ids {sorted(missing)}")
    m = model.members
    return pd.DataFrame(
        {
            "player_id": m["player_id"], "player_name": m["player_name"], "season": m["season"],
            "league": m["league"], "team": m["team"], "source": m["source"],
            "position_group_fine": m["position_group_fine"], "pool": model.pool,
            "archetype_id": ids, "archetype_label": [labels[i] for i in ids],
            "cluster_confidence": confidence.round(4), "distance_to_centroid": nearest.round(4),
        }
    )  # fmt: skip


def all_labels() -> dict[int, str]:
    return {**OUTFIELD_LABELS, **{GK_ID_OFFSET + i: v for i, v in GK_LABELS.items()}}


def fit_all(stats: pd.DataFrame, k: int, gk_k: int) -> tuple[ArchetypeModel, ArchetypeModel]:
    """(outfield model, goalkeeper model) from a pooled season table."""
    gk_mask = stats["position_group_fine"] == GK
    outfield = fit_pool(stats[~gk_mask], OUTFIELD_FEATURES, k, "outfield")
    keepers = fit_pool(stats[gk_mask], group_features(GK), gk_k, "gk", GK_ID_OFFSET)
    return outfield, keepers


def rebuild(
    season: str = DEFAULT_SEASON, k: int | None = None, gk_k: int | None = None,
    engine: Engine | None = None,
) -> pd.DataFrame:  # fmt: skip
    """Cluster the pooled season table and store `player_archetypes` (replacing the season)."""
    k, gk_k = k or CHOSEN_K, gk_k or CHOSEN_GK_K
    if k is None or gk_k is None:
        raise ValueError("k has not been chosen yet: run --sweep, then set CHOSEN_K/CHOSEN_GK_K")
    engine = engine or init_db(get_engine())
    stats = load_pool(engine, season)
    outfield, keepers = fit_all(stats, k, gk_k)
    labels = {**OUTFIELD_LABELS, **{GK_ID_OFFSET + i: v for i, v in GK_LABELS.items()}}
    result = pd.concat(
        [assignments(outfield, labels), assignments(keepers, labels)], ignore_index=True
    )
    replace_season_rows(engine, ARCHETYPES_TABLE, result, season)
    logger.info(
        "{t}: {n} players, {o} outfield archetypes ({ov:.1%} variance, {of} features), "
        "{g} goalkeeper archetypes", t=ARCHETYPES_TABLE, n=len(result), o=k,
        ov=outfield.explained_variance, of=len(outfield.features), g=gk_k,
    )  # fmt: skip
    if outfield.dropped or keepers.dropped:
        logger.warning("Features dropped: {a} {b}", a=outfield.dropped, b=keepers.dropped)
    return result


def export(engine: Engine | None = None, season: str = DEFAULT_SEASON) -> list[Path]:
    """Write archetype_summary.csv, player_archetype_map.csv and archetype_scatter.png to
    EXPORT_DIR. The scatter is the outfield players on the first two principal components of the
    clustering space (the same PCA the archetypes were fitted in), coloured by archetype."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    engine = engine or init_db(get_engine())
    stats = load_pool(engine, season)
    outfield, keepers = fit_all(stats, CHOSEN_K or 10, CHOSEN_GK_K or 3)
    table = pd.concat(
        [assignments(outfield, all_labels()), assignments(keepers, all_labels())],
        ignore_index=True,
    )
    pool = table[table["pool"] == "outfield"].reset_index(drop=True)
    pool_stats = stats.set_index("player_id").loc[pool["player_id"]].reset_index()
    pool_stats["archetype_id"] = pool["archetype_id"].to_numpy()
    rows = []
    for aid, label in all_labels().items():
        sub = table[table["archetype_id"] == aid]
        top = distinctive_table(pool_stats, aid).head(DISTINCTIVE_N) if aid < GK_ID_OFFSET else None
        rows.append(
            {
                "archetype_id": aid, "archetype_label": label, "pool": sub["pool"].iloc[0],
                "players": len(sub), "mean_confidence": round(sub["cluster_confidence"].mean(), 3),
                "leagues": "; ".join(f"{k} {v}" for k, v in sub["league"].value_counts().items()),
                "position_groups": "; ".join(
                    f"{k} {v}" for k, v in sub["position_group_fine"].value_counts().head(4).items()
                ),
                "distinctive_features": "" if top is None else "; ".join(
                    f"{r.feature} ({r.archetype_z:+.2f})" for r in top.itertuples()
                ),
                "most_typical_players": "; ".join(
                    sub.nsmallest(5, "distance_to_centroid")["player_name"]
                ),
            }
        )  # fmt: skip
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = EXPORT_DIR / "archetype_summary.csv"
    map_path = EXPORT_DIR / "player_archetype_map.csv"
    plot_path = EXPORT_DIR / "archetype_scatter.png"
    pd.DataFrame(rows).to_csv(summary_path, index=False, encoding="utf-8")
    table.sort_values(["archetype_id", "distance_to_centroid"]).to_csv(
        map_path, index=False, encoding="utf-8"
    )
    fig, ax = plt.subplots(figsize=(11, 8))
    cmap = plt.get_cmap("tab10")
    for aid, label in OUTFIELD_LABELS.items():
        pts = outfield.scores[outfield.labels == aid]
        ax.scatter(pts[:, 0], pts[:, 1], s=9, alpha=0.6, color=cmap(aid), label=label)
    ax.set(xlabel="PC1", ylabel="PC2", title=f"Outfield archetypes, {season}/16 (k={outfield.k})")
    ax.legend(markerscale=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=130)
    plt.close(fig)
    return [summary_path, map_path, plot_path]


# Players the brief names, and where they must land (checked by `sanity_check`).
SAME_ARCHETYPE: tuple[tuple[str, str], ...] = (("Mesut Özil", "David Silva"),)
DIFFERENT_ARCHETYPE: tuple[tuple[str, str], ...] = (("N'Golo Kanté", "Mesut Özil"),)


def sanity_check(table: pd.DataFrame) -> list[str]:
    """Hard failures of the brief's two structural conditions (empty list = passed)."""
    by_name = {n: r for n, r in zip(table["player_name"], table.itertuples(), strict=True)}
    failures = []
    for a, b in SAME_ARCHETYPE:
        if by_name[a].archetype_id != by_name[b].archetype_id:
            failures.append(f"{a} and {b} are in different archetypes")
    for a, b in DIFFERENT_ARCHETYPE:
        if by_name[a].archetype_id == by_name[b].archetype_id:
            failures.append(f"{a} and {b} are in the SAME archetype")
    return failures


def _main() -> None:
    parser = argparse.ArgumentParser(description="Cluster players into playing archetypes.")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--sweep", action="store_true", help="Silhouette for every k.")
    parser.add_argument("--describe", action="store_true", help="Print centroid profiles.")
    parser.add_argument("--rebuild", action="store_true", help="Cluster and store the table.")
    parser.add_argument("--distinctive", action="store_true", help="Print distinctive features.")
    parser.add_argument("--export", action="store_true", help="Write the CSV/PNG exports.")
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument("--gk-k", type=int, default=None)
    args = parser.parse_args()
    engine = init_db(get_engine())
    stats = load_pool(engine, args.season)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    if args.sweep:
        gk = stats["position_group_fine"] == GK
        z, _, _ = zscore_matrix(stats[~gk], OUTFIELD_FEATURES)
        pca, scores = reduce(z)
        print(f"outfield: {len(z)} players, {z.shape[1]} features, {pca.n_components_} components")
        print(sweep_k(scores, K_RANGE).round(4).to_string(index=False))
        zg, _, _ = zscore_matrix(stats[gk], group_features(GK))
        pg, sg = reduce(zg)
        print(f"goalkeepers: {len(zg)}, {zg.shape[1]} features, {pg.n_components_} components")
        print(sweep_k(sg, GK_K_RANGE).round(4).to_string(index=False))
    if args.describe:
        k, gk_k = args.k or CHOSEN_K, args.gk_k or CHOSEN_GK_K
        if k is None or gk_k is None:
            raise SystemExit("k has not been chosen yet")
        for model in fit_all(stats, k, gk_k):
            print(f"\n{model.pool}: k={model.k}, stability ARI "
                  f"{stability(model.scores, model.k):.3f}")  # fmt: skip
            print(centroid_profile(model).round(2).to_string())
    if args.distinctive:
        merged = stats.merge(
            pd.read_sql(text(f"SELECT player_id, archetype_id FROM {ARCHETYPES_TABLE}"), engine),
            on="player_id",
        )
        merged = merged[merged["archetype_id"] < GK_ID_OFFSET]
        for aid, label in OUTFIELD_LABELS.items():
            print(f"\n{aid} {label}")
            print(distinctive_table(merged, aid).head(DISTINCTIVE_N).round(2).to_string())
    if args.export:
        print("\n".join(str(p) for p in export(engine, args.season)))
    if args.rebuild:
        result = rebuild(args.season, args.k, args.gk_k, engine)
        failures = sanity_check(result)
        print(f"{len(result)} players stored; sanity failures: {failures or 'none'}")


if __name__ == "__main__":
    _main()
