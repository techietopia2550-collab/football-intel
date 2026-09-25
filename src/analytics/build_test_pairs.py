"""Build the similarity test set: player pairs for a human to label "similar" / "not similar".

`python -m src.analytics.build_test_pairs --n 100` writes `data/manual/similarity_test_set.csv`
with the `label` column left blank. `evaluate_thresholds` reads it back once it is labelled.

How the pairs are drawn (the engine is measured as-is; nothing here changes a score):
  * Only the outfield archetypes (`player_archetypes.pool == "outfield"`, 10 in the real data);
    the goalkeeper pool is separate and skipped. `n` is split evenly across them.
  * A pair's score is exactly what `find_similar` shows: both players are members of the same
    archetype, whose model (distinctive features, z-scores within the archetype, PCA at 85%,
    cosine) is fitted with the functions `find_similar` uses. Similarity is only defined inside
    one pool, so `archetype_a == archetype_b` always; both columns are kept so the file says so.
  * Stratified by score, not uniform: a uniformly random pair inside an archetype scores near 0,
    so a random sample would hold almost no pairs near the thresholds under test (0.50-0.90).
    Every archetype contributes one pair per band of `SCORE_BANDS` (10 bands for 10 pairs). A band
    with no pair in an archetype borrows from the nearest band that has one. Consequence for the
    evaluation: precision per band is unbiased, but "precision above a threshold" and recall are
    measured on this stratified mix, not on the population of all pairs (see
    ANALYTICS_DECISIONS.md section 22).
  * Players are reused as little as possible (a player already drawn is avoided while others are
    available), so the labels are not dominated by a few names.
  * Rows are shuffled so the file order does not reveal the score. For blind labelling, hide the
    `cosine_score` and `score_band` columns while labelling.

An existing file that already holds labels is never overwritten unless `--force` is given.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from src.analytics.archetypes import distinctive_features
from src.analytics.similarity import (
    DISTINCTIVE_N,
    GroupModel,
    archetype_features,
    cosine,
    fit_group,
    load_archetypes,
    load_tables,
)
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON
from src.utils.config import settings
from src.utils.logging import logger

TEST_SET_NAME = "similarity_test_set.csv"
SEED = 0
# Band edges on the cosine score; one pair per band per archetype. Half-open [lo, hi).
SCORE_BANDS: tuple[tuple[float, float], ...] = (
    (-1.0, 0.45),
    (0.45, 0.55),
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 0.75),
    (0.75, 0.80),
    (0.80, 0.85),
    (0.85, 0.90),
    (0.90, 1.0001),
)
COLUMNS = [
    "pair_id",
    "player_a",
    "player_b",
    "archetype_a",
    "archetype_b",
    "cosine_score",
    "label",
    "position_a",
    "position_b",
    "team_a",
    "team_b",
    "league_a",
    "league_b",
    "minutes_a",
    "minutes_b",
    "player_a_id",
    "player_b_id",
    "score_band",
    "season",
    "source",
]
READY_MESSAGE = "CSV ready for labeling at {path}. Run PART B after labeling."


def default_path() -> Path:
    return settings.manual_dir / TEST_SET_NAME


def band_label(lo: float, hi: float) -> str:
    return f"<{hi:.2f}" if lo <= -1 else (f">={lo:.2f}" if hi > 1 else f"{lo:.2f}-{hi:.2f}")


def archetype_model(
    qualified: pd.DataFrame, arch_id: int, distinctive_n: int | None = DISTINCTIVE_N
) -> GroupModel:
    """The fitted model `find_similar` uses for an outfield archetype. `qualified` is the season
    table merged with `player_archetypes` (the same frame `find_similar` builds)."""
    members = qualified[qualified["archetype_id"] == arch_id].reset_index(drop=True)
    outfield = qualified[qualified["pool"] == "outfield"]
    features = (
        archetype_features()
        if distinctive_n is None
        else distinctive_features(outfield, arch_id, distinctive_n)
    )
    return fit_group(members, str(members["archetype_label"].iloc[0]), features)


def _pick_pair(
    sims: np.ndarray, band: tuple[float, float], used: set[int], rng: np.random.Generator
) -> tuple[int, int] | None:
    """A random (i, j), i < j, with lo <= sims[i, j] < hi, preferring players not yet used."""
    lo, hi = band
    iu, ju = np.triu_indices(len(sims), k=1)
    vals = sims[iu, ju]
    hit = np.flatnonzero((vals >= lo) & (vals < hi))
    if hit.size == 0:
        return None
    fresh = [h for h in hit if iu[h] not in used and ju[h] not in used]
    half = [h for h in hit if iu[h] not in used or ju[h] not in used]
    choice = rng.choice(fresh or half or list(hit))
    return int(iu[choice]), int(ju[choice])


def _archetype_pairs(
    model: GroupModel, n_pairs: int, rng: np.random.Generator
) -> list[tuple[int, int, str]]:
    """`n_pairs` (i, j, band) for one archetype: one per band, cycling through SCORE_BANDS; an
    empty band borrows from the nearest non-empty one."""
    sims = np.vstack([cosine(model.scores[i], model.scores) for i in range(len(model.scores))])
    used: set[int] = set()
    taken: set[tuple[int, int]] = set()
    out: list[tuple[int, int, str]] = []
    for k in range(n_pairs):
        want = k % len(SCORE_BANDS)
        by_distance = sorted(range(len(SCORE_BANDS)), key=lambda b: (abs(b - want), -b))
        for b in by_distance:
            pair = _pick_pair(sims, SCORE_BANDS[b], used, rng)
            if pair is not None and pair not in taken:
                taken.add(pair)
                used.update(pair)
                out.append((*pair, band_label(*SCORE_BANDS[b])))
                break
        else:
            raise ValueError("Could not draw a distinct pair: archetype too small")
    return out


def build_pairs(
    engine: Engine, season: str = DEFAULT_SEASON, n: int = 100, seed: int = SEED
) -> pd.DataFrame:
    """`n` stratified pairs spread evenly over the outfield archetypes, labels blank."""
    qualified, _ = load_tables(engine, season, None)
    qualified = qualified.merge(load_archetypes(engine, season), on="player_id", how="inner")
    arch_ids = sorted(
        int(a) for a in qualified.loc[qualified["pool"] == "outfield", "archetype_id"].unique()
    )
    if not arch_ids or n % len(arch_ids):
        raise ValueError(f"n={n} must be a positive multiple of the {len(arch_ids)} archetypes")
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for arch_id in arch_ids:
        model = archetype_model(qualified, arch_id)
        m = model.members
        for i, j, band in _archetype_pairs(model, n // len(arch_ids), rng):
            a, b = m.iloc[i], m.iloc[j]
            score = float(cosine(model.scores[i], model.scores[j : j + 1])[0])
            rows.append(
                {
                    "player_a": a["player_name"],
                    "player_b": b["player_name"],
                    "archetype_a": a["archetype_label"],
                    "archetype_b": b["archetype_label"],
                    "cosine_score": round(score, 4),
                    "label": "",
                    "position_a": a["position_group_fine"],
                    "position_b": b["position_group_fine"],
                    "team_a": a["team"],
                    "team_b": b["team"],
                    "league_a": a["league"],
                    "league_b": b["league"],
                    "minutes_a": round(float(a["minutes_total"])),
                    "minutes_b": round(float(b["minutes_total"])),
                    "player_a_id": a["player_id"],
                    "player_b_id": b["player_id"],
                    "score_band": band,
                    "season": season,
                    "source": a["source"],
                }
            )
    out = pd.DataFrame(rows).sample(frac=1.0, random_state=seed).reset_index(drop=True)
    out.insert(0, "pair_id", range(1, len(out) + 1))
    return out[COLUMNS]


def has_labels(path: Path) -> bool:
    if not path.exists():
        return False
    labels = pd.read_csv(path, dtype=str, keep_default_na=False).get("label")
    return labels is not None and bool(labels.str.strip().ne("").any())


def write_test_set(pairs: pd.DataFrame, path: Path, force: bool = False) -> Path:
    """Write the CSV; refuses to overwrite a file that already holds labels unless `force`."""
    if has_labels(path) and not force:
        raise FileExistsError(f"{path} already holds labels; pass --force to overwrite them")
    path.parent.mkdir(parents=True, exist_ok=True)
    pairs.to_csv(path, index=False, encoding="utf-8")
    return path


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=100, help="number of pairs (multiple of 10)")
    parser.add_argument("--season", default=DEFAULT_SEASON)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=None, help="default data/manual/")
    parser.add_argument("--force", action="store_true", help="overwrite a labelled file")
    args = parser.parse_args()
    path = args.out or default_path()
    pairs = build_pairs(get_engine(), args.season, args.n, args.seed)
    write_test_set(pairs, path, args.force)
    logger.info(
        "{n} pairs, bands: {b}", n=len(pairs), b=pairs["score_band"].value_counts().to_dict()
    )
    print(READY_MESSAGE.format(path=path))


if __name__ == "__main__":
    _main()
