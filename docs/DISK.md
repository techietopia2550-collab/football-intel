# Disk discipline: keep everything on D:

Rule: every download, cache, temp file and library default for this project lives under
`D:\Keagz\vs codes\Scout`, nothing on C:.

## How it is enforced

`src/utils/disk_bootstrap.py` (standard library only) sets these environment variables on import, forced, before
any other library reads them, and creates the directories:

| Variable | Directory |
|---|---|
| `PIP_CACHE_DIR` | `.cache\pip` |
| `PYTEST_DEBUG_TEMPROOT` | `.cache\pytest` |
| `TMPDIR`, `TEMP`, `TMP` (and `tempfile.tempdir`) | `.cache\tmp` |
| `HF_HOME` | `.cache\huggingface` |
| `TRANSFORMERS_CACHE` | `.cache\transformers` |
| `TORCH_HOME` | `.cache\torch` |
| `XDG_CACHE_HOME` | `.cache\xdg` |
| `SCIKIT_LEARN_DATA`, `MPLCONFIGDIR` | `.cache\scikit_learn`, `.cache\matplotlib` |
| `SOCCERDATA_DIR` | `data\raw\soccerdata` (soccerdata defaults to `~\soccerdata` on C:; its existing cache was already here) |

If any target would land on a different drive from the project, `apply()` raises `DiskBootstrapError` before
changing anything.

It is imported by `src/__init__.py` (so any `import src.<module>` is covered), and explicitly first in `main.py`,
every ingest CLI, `normalize`, `percentiles`, `heatmaps`, `similarity`, `evaluate_similarity`, `schema` and
`tests/conftest.py`. `.cache/` is gitignored. `.venv\pip.ini` sets `global.cache-dir` so `pip` run from a shell in
the venv also uses `.cache\pip` (`PIP_CACHE_DIR` alone only reaches pip processes started from Python).

Check at any time:

```bash
python -c "from src.utils import disk_bootstrap as d; print(d.offenders() or 'clean')"
```

Not configurable and already on D: `tls_requests` keeps its native DLL in `.venv\Lib\site-packages\tls_requests\bin`
and has no cache-dir setting. Cannot be moved: the base Python interpreter (`C:\Users\User\AppData\Local\Programs\Python\Python312`) that the venv points at; it is read, not written, by this project.

## Audit (2026-09-20)

Before (measured at the start of the session):

| | C: used | D: used |
|---|---|---|
| Before | 138.74 GB | 36.55 GB |
| After  | 138.78 GB | 36.57 GB |

The C: change (+0.04 GB) cannot be attributed to this project; see below.

Project-related files found on C: at the start:

| Path | Size | Action |
|---|---|---|
| `%LOCALAPPDATA%\pip\Cache` | 0 MB | nothing to delete |
| pytest temp (`%TEMP%\pytest-of-User`) | none | nothing to delete |
| `%TEMP%\pip-*` (19 build/unpack leftovers from the 2026-09-18 install) | about 21 MB | **left**: they are pip's build temp, not its cache, and they sit under `C:\Users\`, which was not to be touched |
| `C:\Users\User\.cache\huggingface` | 519 MB | **left**: dated June-July 2026, before this project's venv, and this venv has no transformers or torch. Not attributable to this project. |
| `C:\Users\User\soccerdata` (empty config/data dirs and a 363-byte log) | 363 B | **created by my own audit** (`import soccerdata` without the bootstrap, at 14:36); deletion was refused by the permission classifier, so it is still there. Safe to delete by hand. |
| `__pycache__` (0.9 MB) and tool caches (`.pytest_cache`, `.ruff_cache`, `.mypy_cache`, not measured) | | already on D (project directory) |

## Verification during the session

The test suite (262 tests), the full StatsBomb re-ingest, normalize, percentiles, heatmaps and the similarity
evaluation all ran after the bootstrap was in place. Since then:

- **Python-style temp files on C: none.** No `tmp*`, `etilqs_*` (SQLite), `pytest-*` or `pip-*` entries in
  `%TEMP%` newer than the start of the run; `%LOCALAPPDATA%\pip`, `~\.cache` and `~\soccerdata` gained nothing
  (apart from my stub above).
- `.cache\pytest` holds pytest's temp dirs (8.4 MB) and `.cache\tmp` is empty, so pytest and `tempfile` were
  redirected.
- **18 other files in `%TEMP%` on C: changed during the session** (GUID-named `.tmp` files, `1ds_telemetry_public.db`,
  `cv_debug.log`, `mat-debug-*.log`, a `.ses` file). None has a Python, pytest or pip name. They are consistent with
  Microsoft Edge/WebView2 and OneDrive, which were running (8 Edge and 24 WebView2 processes), and with Claude Code's
  own scratch space. That attribution is inferred, not proven. So "zero new files on C:" is verified for everything
  this project's Python touches, not for the machine as a whole.
