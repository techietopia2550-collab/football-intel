import os
from pathlib import Path

# soccerdata defaults its own cache to ~/soccerdata, outside the project and
# potentially on a different disk than data/raw. Redirect it under data/raw
# so every download lands on the same disk as the rest of the project. This
# runs on first import of the `src` package, before any submodule can
# `import soccerdata`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("SOCCERDATA_DIR", str(_PROJECT_ROOT / "data" / "raw" / "soccerdata"))
