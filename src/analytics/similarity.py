"""Find players who play like a given player: a heuristic similarity ranking, within position.

`find_similar(player_name, season, n=10, filters=None)` runs, for the target's
`position_group_fine`:
  1. Select the features for that group: the per-90 rates and ratios that mean something for the
     role (`profile.RELEVANT_METRICS`), minus rare-event counts (`EXCLUDED_FEATURES`). Raw totals
     are never used, only per-90 rates and ratios.
  2. Z-score each feature WITHIN the group (mean 0, standard deviation 1 across the group's
     qualifying players), so no metric dominates because of its units and no group is compared
     against another group's scale. A ratio left empty by its sample-size guard is imputed with
     the group mean (z = 0); a feature empty for over MAX_MISSING_SHARE of the group, or with no
     variance, is dropped for that group and listed in `features_dropped`.
  3. PCA on the z-scored matrix, keeping enough components for at least VARIANCE_TARGET (85%) of
     the variance.
  4. Cosine similarity between the target's and each candidate's PCA vector. Ranked, top `n`.

Only players of the same `position_group_fine` are ever compared. Everyone in the group is used to
fit the scaling and the PCA; filters only narrow which candidates are ranked.

Explainability. With `W` the PCA loadings (components x features), `P = W^T W` projects a z-score
vector onto the retained subspace, and the cosine numerator is `sum_j z_t[j] * (P z_c)[j]`, so
  shared_metrics     feature j's term of that sum, divided by the two vector norms: the terms add
                     up exactly to the similarity. Large positive = both players are unusual in
                     the same direction on that metric (within the retained components).
  differing_metrics  `(P (z_c - z_t))[j]`: the feature delta carried through the PCA loadings.
                     Large magnitude = the players differ most on that metric; sign is candidate
                     minus target.
Both are read in the reduced space, so a metric that the components barely keep contributes
little even if the raw values differ.

This is a heuristic: nothing here has been validated against scouting judgement, and a high
score means "similar on these rates in this season", not "will play the same role".

Age: StatsBomb's open data carries no birth dates, so `max_age` is supported only when
`players.birth_year` is filled. With no age data it raises `AgeDataUnavailable` instead of
silently ignoring the filter.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sqlalchemy import Engine, inspect, text

from src.analytics.percentiles import MIN_GROUP_SIZE
from src.analytics.profile import RELEVANT_METRICS, find_player
from src.models.schema import get_engine
from src.models.similarity import (
    MetricContribution,
    SimilarityFilters,
    SimilarityResult,
    SimilarPlayer,
)
from src.transform.normalize import (
    DEFAULT_LEAGUE,
    INSUFFICIENT_TABLE,
    season_table_name,
)

VARIANCE_TARGET = 0.85
MAX_MISSING_SHARE = 0.30
TOP_EXPLAINERS = 3
MIXED_ROLE_SHARE = 0.6
# Yellow and red cards are rare, chance-driven counts: they add noise, not style.
EXCLUDED_FEATURES: frozenset[str] = frozenset({"red_p90", "yellow_p90"})


class AgeDataUnavailable(ValueError):
    """`max_age` was requested but no candidate has a birth year."""


def group_features(group: str) -> list[str]:
    """The metrics used to compare players of one fine position group."""
    return [m for m in RELEVANT_METRICS[group] if m not in EXCLUDED_FEATURES]


@dataclass
class GroupModel:
    """The fitted scaling + PCA for one position group of one league-season."""

    members: pd.DataFrame  # the group's qualifying players, row-aligned with `z` and `scores`
    features: list[str]
    dropped: dict[str, str]
    z: np.ndarray  # players x features, within-group z-scores (empty values imputed as 0)
    pca: PCA
    scores: np.ndarray  # players x components
    explained_variance: float
    _cache: dict[str, np.ndarray] = field(default_factory=dict, repr=False)

    @property
    def projection(self) -> np.ndarray:
        """P = W^T W, the projection onto the retained components (features x features)."""
        if "P" not in self._cache:
            w = self.pca.components_
            self._cache["P"] = w.T @ w
        return self._cache["P"]


def fit_group(members: pd.DataFrame, group: str) -> GroupModel:
    """Z-score `members` (one position group's qualifying players) and fit the PCA."""
    if len(members) < 3:
        raise ValueError(f"Only {len(members)} {group} players: too few to compare")
    kept: list[str] = []
    dropped: dict[str, str] = {}
    columns: list[np.ndarray] = []
    for name in group_features(group):
        values = members[name].to_numpy(dtype=float)
        missing = float(np.isnan(values).mean())
        if missing > MAX_MISSING_SHARE:
            dropped[name] = f"empty for {missing:.0%} of {group} players"
            continue
        mean, std = np.nanmean(values), np.nanstd(values)
        if not np.isfinite(std) or std == 0:
            dropped[name] = "no variance in the group"
            continue
        z = (values - mean) / std
        columns.append(np.where(np.isnan(z), 0.0, z))
        kept.append(name)
    if not kept:
        raise ValueError(f"No usable features for {group}")

    z_matrix = np.column_stack(columns)
    pca = PCA(n_components=VARIANCE_TARGET, svd_solver="full").fit(z_matrix)
    return GroupModel(
        members=members.reset_index(drop=True),
        features=kept,
        dropped=dropped,
        z=z_matrix,
        pca=pca,
        scores=pca.transform(z_matrix),
        explained_variance=float(pca.explained_variance_ratio_.sum()),
    )


def cosine(a: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of vector `a` with each row of `matrix` (0 for a zero-length vector)."""
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(a)
    with np.errstate(invalid="ignore", divide="ignore"):
        sims = matrix @ a / norms
    return np.where(norms > 0, sims, 0.0)


def pair_contributions(
    model: GroupModel, target: int, candidate: int
) -> tuple[np.ndarray, np.ndarray]:
    """(shared, differing) contribution of every feature for a target/candidate pair.

    `shared` sums to the cosine similarity of the two PCA vectors; `differing` is the feature
    delta carried through the loadings (candidate minus target). Both are indexed like
    `model.features`.
    """
    centre = model.pca.mean_
    zt, zc = model.z[target] - centre, model.z[candidate] - centre
    p = model.projection
    norm = np.linalg.norm(model.scores[target]) * np.linalg.norm(model.scores[candidate])
    shared = zt * (p @ zc) / norm if norm > 0 else np.zeros_like(zt)
    return shared, p @ (zc - zt)


def explain(
    model: GroupModel, target: int, candidate: int
) -> tuple[list[MetricContribution], list[MetricContribution]]:
    """(shared_metrics, differing_metrics): the top TOP_EXPLAINERS of `pair_contributions`."""
    shared, differing = pair_contributions(model, target, candidate)

    def build(scores: np.ndarray, order: np.ndarray) -> list[MetricContribution]:
        out = []
        for j in order[:TOP_EXPLAINERS]:
            name = model.features[j]
            t_raw = model.members.iloc[target][name]
            c_raw = model.members.iloc[candidate][name]
            out.append(
                MetricContribution(
                    metric=name,
                    target_value=None if pd.isna(t_raw) else round(float(t_raw), 3),
                    candidate_value=None if pd.isna(c_raw) else round(float(c_raw), 3),
                    target_z=round(float(model.z[target][j]), 2),
                    candidate_z=round(float(model.z[candidate][j]), 2),
                    contribution=round(float(scores[j]), 4),
                )
            )
        return out

    return build(shared, np.argsort(-shared)), build(differing, np.argsort(-np.abs(differing)))


def _load_tables(engine: Engine, season: str, league: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = {"l": league, "s": season}
    qualified = pd.read_sql(
        text(f"SELECT * FROM {season_table_name(season)} WHERE league = :l AND season = :s"),
        engine,
        params=params,
    )
    if inspect(engine).has_table(INSUFFICIENT_TABLE):
        excluded = pd.read_sql(
            text(f"SELECT * FROM {INSUFFICIENT_TABLE} WHERE league = :l AND season = :s"),
            engine,
            params=params,
        )
    else:
        excluded = pd.DataFrame(columns=qualified.columns)
    return qualified, excluded


def _apply_filters(
    candidates: pd.DataFrame,
    target: pd.Series,
    filters: SimilarityFilters,
    season: str,
    engine: Engine,
    warnings: list[str],
) -> pd.DataFrame:
    out = candidates
    if filters.same_team_only:
        out = out[out["team"] == target["team"]]
    if filters.exclude_same_team:
        out = out[out["team"] != target["team"]]
    if filters.min_minutes is not None:
        out = out[out["minutes_total"] >= filters.min_minutes]
    if filters.max_age is not None:
        births = pd.read_sql(
            text("SELECT player_id, birth_year FROM players WHERE birth_year IS NOT NULL"), engine
        ).set_index("player_id")["birth_year"]
        if births.reindex(out["player_id"]).isna().all():
            raise AgeDataUnavailable(
                "max_age needs birth years, and none are stored: StatsBomb's open data has no "
                "birth dates. Remove max_age or load ages from another source."
            )
        age = int(season) - out["player_id"].map(births)  # age at the start of the season
        unknown = int(age.isna().sum())
        if unknown:
            warnings.append(f"{unknown} candidates without a birth year were dropped by max_age.")
        out = out[age <= filters.max_age]
    return out


def find_similar(
    player_name: str,
    season: str = "2015",
    n: int = 10,
    filters: SimilarityFilters | dict[str, object] | None = None,
    league: str = DEFAULT_LEAGUE,
    engine: Engine | None = None,
) -> SimilarityResult:
    """The `n` players most similar to `player_name` in `season`, within his position group.

    Raises ValueError if the name matches no player or several, if the player is below the minutes
    threshold (no rates to compare), or if `filters` are contradictory; `AgeDataUnavailable` for
    `max_age` without age data.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    flt = (
        filters
        if isinstance(filters, SimilarityFilters)
        else SimilarityFilters.model_validate(filters or {})
    )
    engine = engine or get_engine()
    qualified, excluded = _load_tables(engine, season, league)
    everyone = pd.concat([qualified, excluded], ignore_index=True)
    target = find_player(everyone, player_name)
    if target["player_id"] not in set(qualified["player_id"]):
        raise ValueError(
            f"{target['player_name']} is below the minutes threshold ({target.get('reason')}): "
            "no per-90 rates to compare"
        )

    group = target["position_group_fine"]
    members = qualified[qualified["position_group_fine"] == group].reset_index(drop=True)
    model = fit_group(members, group)
    t_idx = int(members.index[members["player_id"] == target["player_id"]][0])

    warnings: list[str] = []
    if len(members) < MIN_GROUP_SIZE:
        warnings.append(
            f"Only {len(members)} qualifying {group} players (< {MIN_GROUP_SIZE}): the PCA and "
            "the similarity scores are coarse."
        )
    if target["position_group_fine_share"] < MIXED_ROLE_SHARE:
        warnings.append(
            f"Mixed role: only {target['position_group_fine_share']:.0%} of minutes as {group}; "
            "similar players are searched within that group only."
        )
    if model.dropped:
        warnings.append(f"Features left out for {group}: {model.dropped}")

    pool = members[members["player_id"] != target["player_id"]]
    pool = _apply_filters(pool, target, flt, season, engine, warnings)
    sims = cosine(model.scores[t_idx], model.scores)
    order = sorted(pool.index, key=lambda i: -sims[i])[:n]

    similar = []
    for rank, i in enumerate(order, start=1):
        shared, differing = explain(model, t_idx, i)
        row = members.iloc[i]
        similar.append(
            SimilarPlayer(
                rank=rank,
                player_id=row["player_id"],
                player_name=row["player_name"],
                team=row["team"],
                position_group_fine=group,
                minutes_total=round(float(row["minutes_total"]), 1),
                similarity=round(float(sims[i]), 4),
                shared_metrics=shared,
                differing_metrics=differing,
            )
        )
    return SimilarityResult(
        target_id=target["player_id"],
        target_name=target["player_name"],
        target_team=target["team"],
        season=season,
        league=league,
        position_group_fine=group,
        position_group_fine_share=float(target["position_group_fine_share"]),
        group_size=len(members),
        candidates_considered=len(pool),
        features_used=model.features,
        features_dropped=model.dropped,
        n_components=int(model.pca.n_components_),
        explained_variance=round(model.explained_variance, 4),
        filters=flt,
        warnings=warnings,
        similar=similar,
    )


__all__ = [
    "EXCLUDED_FEATURES",
    "AgeDataUnavailable",
    "find_similar",
    "fit_group",
    "group_features",
]
