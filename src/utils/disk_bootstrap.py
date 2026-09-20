"""Keep every cache, temp file and library default on the project's drive (D:).

Import this module BEFORE any library that reads these variables (pip, pytest, soccerdata,
transformers, torch, ...). Importing it is enough: it sets the environment on import and is
idempotent. `src/__init__.py` imports it, so any `import src.<anything>` is covered, and every
entry point (main.py, ingest CLIs, conftest.py) also imports it explicitly as its first import.

Stdlib only, by design: it must run before pydantic, pandas or anything else is imported.

Variables set (all under `<project>/.cache/<name>` unless noted):
  PIP_CACHE_DIR, PYTEST_DEBUG_TEMPROOT, TMPDIR, TEMP, TMP, HF_HOME, TRANSFORMERS_CACHE,
  TORCH_HOME, XDG_CACHE_HOME, SCIKIT_LEARN_DATA, MPLCONFIGDIR.
  SOCCERDATA_DIR -> `<project>/data/raw/soccerdata` (soccerdata defaults to ~/soccerdata, on C;
  it already lived under data/raw, so it stays there and its existing cache keeps being used).

Not configurable, and why they are still on D:
  - tls_requests keeps its native DLL in `site-packages/tls_requests/bin` (inside `.venv`, on D)
    and has no cache-dir setting; it only supports TLS_LIBRARY_PATH for a pre-downloaded file.
  - `PIP_CACHE_DIR` only affects pip processes started from Python. For pip run from a shell, the
    venv's `pip.ini` sets `global.cache-dir` (`pip config --site`), see docs/DISK.md.

If the project root is not on the same drive as any target, `apply` raises: silently writing to
another disk is exactly what this module exists to prevent.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = PROJECT_ROOT / ".cache"

# environment variable -> directory (relative to CACHE_ROOT)
CACHE_VARS: dict[str, str] = {
    "PIP_CACHE_DIR": "pip",
    "PYTEST_DEBUG_TEMPROOT": "pytest",
    "TMPDIR": "tmp",
    "TEMP": "tmp",
    "TMP": "tmp",
    "HF_HOME": "huggingface",
    "TRANSFORMERS_CACHE": "transformers",
    "TORCH_HOME": "torch",
    "XDG_CACHE_HOME": "xdg",
    "SCIKIT_LEARN_DATA": "scikit_learn",
    "MPLCONFIGDIR": "matplotlib",
}

# Not under .cache (see module docstring).
SOCCERDATA_DIR = PROJECT_ROOT / "data" / "raw" / "soccerdata"


class DiskBootstrapError(RuntimeError):
    """A target directory would land on a different drive from the project."""


def _drive(path: Path) -> str:
    return os.path.splitdrive(str(path.resolve()))[0].upper()


def targets() -> dict[str, Path]:
    """Every environment variable this module controls, mapped to its directory."""
    out = {name: CACHE_ROOT / sub for name, sub in CACHE_VARS.items()}
    out["SOCCERDATA_DIR"] = SOCCERDATA_DIR
    return out


def apply() -> dict[str, Path]:
    """Create the directories and set the environment variables. Returns what was set.

    The variables are forced, not defaulted: an inherited value pointing at C: is the problem.
    """
    root_drive = _drive(PROJECT_ROOT)
    wanted = targets()
    for name, path in wanted.items():
        if _drive(path) != root_drive:
            raise DiskBootstrapError(f"{name} -> {path} is not on the project drive {root_drive}")
    for name, path in wanted.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    # `tempfile` caches its directory on first use, so set it directly as well.
    tempfile.tempdir = str(wanted["TMP"])
    return wanted


def offenders() -> dict[str, str]:
    """Variables whose current value is not on the project drive (empty when all is well)."""
    root_drive = _drive(PROJECT_ROOT)
    bad = {
        name: os.environ.get(name, "")
        for name in targets()
        if _drive(Path(os.environ.get(name) or ".")) != root_drive
    }
    if _drive(Path(tempfile.gettempdir())) != root_drive:
        bad["tempfile.gettempdir()"] = tempfile.gettempdir()
    return bad


APPLIED = apply()

__all__ = ["APPLIED", "CACHE_ROOT", "DiskBootstrapError", "apply", "offenders", "targets"]
