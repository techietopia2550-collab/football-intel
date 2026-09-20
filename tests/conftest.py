"""pytest configuration. The disk bootstrap must be the first import: it sends pytest's temp dirs,
`tempfile`, and every library cache to the project drive before anything can write to C:."""

from src.utils import disk_bootstrap  # noqa: F401  (must be first)
