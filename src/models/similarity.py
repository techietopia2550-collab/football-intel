"""Pydantic schemas for the similarity engine (`src/analytics/similarity.py`)."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class SimilarityFilters(BaseModel):
    """Restrictions on which candidates may be returned. The model is always fitted on the whole
    position group; filters only narrow the list that is ranked."""

    same_team_only: bool = False
    exclude_same_team: bool = False
    max_age: int | None = Field(default=None, gt=0)  # needs birth years; see similarity.py
    min_minutes: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _team_filters_conflict(self) -> SimilarityFilters:
        if self.same_team_only and self.exclude_same_team:
            raise ValueError("same_team_only and exclude_same_team cannot both be set")
        return self


class MetricContribution(BaseModel):
    """One metric's part in a pair's similarity (or difference), in z-score space."""

    metric: str
    target_value: float | None  # the raw metric value (per 90, or a ratio), None if it was empty
    candidate_value: float | None
    target_z: float  # within-position z-score (0 when the value was empty and imputed)
    candidate_z: float
    contribution: float  # see similarity.py; larger magnitude = bigger part of the explanation


class SimilarPlayer(BaseModel):
    rank: int
    player_id: str
    player_name: str
    team: str
    position_group_fine: str
    minutes_total: float
    similarity: float  # cosine similarity of the PCA-reduced vectors, -1 to 1
    shared_metrics: list[MetricContribution]  # top metrics adding to the similarity
    differing_metrics: list[MetricContribution]  # top metrics where the two differ
    # Same position group as the target. The score ignores position; this flag only sorts the
    # list, because same-position test pairs were labelled similar far more often (65% vs 25%).
    position_match: bool = True


class SimilarityResult(BaseModel):
    """`find_similar` output. A heuristic ranking, not a validated or 'accurate' model."""

    target_id: str
    target_name: str
    target_team: str
    season: str
    league: str  # the target's own league
    pool_leagues: list[str]  # the leagues the position group was pooled from
    position_group_fine: str  # the target's own position group
    position_group_fine_share: float
    pool_kind: str = "position_group"  # "archetype" or "position_group": what was searched
    archetype_id: int | None = None  # the target's archetype (only when pool_kind == "archetype")
    archetype_label: str | None = None
    cluster_confidence: float | None = None  # margin to the next archetype, 0-1 (archetypes.py)
    group_size: int  # players in the searched pool (archetype or position group), target included
    candidates_considered: int  # after filters, target excluded
    features_used: list[str]
    features_dropped: dict[str, str]  # metric -> why it was left out for this group
    n_components: int
    explained_variance: float  # share of variance the retained components keep
    filters: SimilarityFilters
    warnings: list[str]
    similar: list[SimilarPlayer]  # the full ranking, best score first

    @property
    def same_position(self) -> list[SimilarPlayer]:
        """The ranked players in the target's own position group (the more reliable leads)."""
        return [s for s in self.similar if s.position_match]

    @property
    def other_positions(self) -> list[SimilarPlayer]:
        """The ranked players in another position group: statistically close, less reliable."""
        return [s for s in self.similar if not s.position_match]
