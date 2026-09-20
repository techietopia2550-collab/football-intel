"""Football Intel.

Importing the `src` package first applies `src.utils.disk_bootstrap`, which points every cache,
temp dir and library default (including soccerdata's, which would otherwise be `~/soccerdata`
on C:) at the project drive. It runs before any submodule can import soccerdata, pip-driven
tooling, pytest, transformers or torch.
"""

from src.utils import disk_bootstrap  # noqa: F401  (import for its side effects; keep first)
