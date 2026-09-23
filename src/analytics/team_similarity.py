"""Which teams produce alike event patterns: cosine similarity of z-scored team style vectors.

Every axis is z-scored across all teams of the season (4 leagues x 20 = 80). No PCA: 80 teams and
12 axes leave nothing to compress. A heuristic ranking of observable event patterns, not validated
and not a claim about tactics. See ANALYTICS_DECISIONS.md section 17.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from src.analytics.team_style import AXES, AXIS_COLUMNS, AXIS_LABELS, TEAM_STYLE_TABLE
from src.models.schema import get_engine
from src.models.team import SimilarTeam, SimilarTeamsResult
from src.transform.normalize import DEFAULT_SEASON

DETAIL_AXES = 3  # axes named as "closest" / "biggest gap" per neighbour


def load_team_style(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame:
    """The stored `team_style` rows of a season. Raises LookupError if the table has none."""
    engine = engine or get_engine()
    try:
        df = pd.read_sql(f"SELECT * FROM {TEAM_STYLE_TABLE} WHERE season = ?", engine, params=(str(season),))
    except Exception as exc:  # noqa: BLE001 - a missing table reads the same as an empty one
        raise LookupError(f"{TEAM_STYLE_TABLE} is not built: run `python -m src.analytics.team_style`") from exc
    if df.empty:
        raise LookupError(f"No {TEAM_STYLE_TABLE} rows for {season}: run `python -m src.analytics.team_style`")
    return df.reset_index(drop=True)


def zscores(df: pd.DataFrame) -> pd.DataFrame:
    """Each axis standardised across all teams (population std). A constant axis becomes 0."""
    values = df[list(AXIS_COLUMNS)].astype(float)
    std = values.std(ddof=0).replace(0, np.nan)
    return ((values - values.mean()) / std).fillna(0.0)


def cosine_matrix(z: pd.DataFrame) -> np.ndarray:
    m = z.to_numpy()
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    unit = m / np.where(norms == 0, 1, norms)
    return unit @ unit.T


def find_similar_teams(
    team_name: str,
    season: str | int = DEFAULT_SEASON,
    n: int = 10,
    engine: Engine | None = None,
    style: pd.DataFrame | None = None,
) -> SimilarTeamsResult:
    """The `n` teams whose style vector points closest to `team_name`'s, across all leagues."""
    style = style if style is not None else load_team_style(str(season), engine)
    names = style["team_name"].tolist()
    if team_name not in names:
        raise ValueError(f"Unknown team '{team_name}' for {season}")
    z = zscores(style)
    sims = cosine_matrix(z)
    t = names.index(team_name)
    order = [i for i in np.argsort(-sims[t]) if i != t][:n]
    zt = z.iloc[t].to_numpy()
    similar = []
    for rank, i in enumerate(order, start=1):
        zi = z.iloc[i].to_numpy()
        shared = np.argsort(-(zt * zi))[:DETAIL_AXES]  # same side of the mean, both far from it
        gaps = np.argsort(-np.abs(zt - zi))[:DETAIL_AXES]
        similar.append(
            SimilarTeam(
                rank=rank, team_name=names[i], league=str(style.at[i, "league"]),
                similarity=float(sims[t, i]),
                closest_axes=[AXIS_LABELS[AXIS_COLUMNS[j]] for j in shared],
                biggest_gaps=[AXIS_LABELS[AXIS_COLUMNS[j]] for j in gaps],
            )
        )  # fmt: skip
    return SimilarTeamsResult(
        team_name=team_name, league=str(style.at[t, "league"]), season=str(season),
        teams_in_pool=len(style), similar=similar,
    )


__all__ = ["AXES", "find_similar_teams", "load_team_style", "zscores"]
