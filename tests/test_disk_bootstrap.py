"""The D-drive bootstrap: every cache, temp dir and library default stays on the project drive."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import src  # noqa: F401  (importing the package applies the bootstrap)
from src.utils import disk_bootstrap
from src.utils.disk_bootstrap import CACHE_ROOT, PROJECT_ROOT, DiskBootstrapError

REQUIRED = {
    "PIP_CACHE_DIR", "PYTEST_DEBUG_TEMPROOT", "TMPDIR", "TEMP", "TMP", "HF_HOME",
    "TRANSFORMERS_CACHE", "TORCH_HOME", "XDG_CACHE_HOME", "SOCCERDATA_DIR",
}  # fmt: skip


def test_every_required_variable_is_set_to_an_existing_directory_in_the_project() -> None:
    assert REQUIRED <= set(disk_bootstrap.targets())
    for name in REQUIRED:
        path = Path(os.environ[name])
        assert path.is_dir(), name
        assert PROJECT_ROOT in path.resolve().parents or path.resolve() == PROJECT_ROOT, name


def test_the_cache_variables_live_under_dot_cache() -> None:
    for name in REQUIRED - {"SOCCERDATA_DIR"}:
        assert CACHE_ROOT in Path(os.environ[name]).resolve().parents, name


def test_soccerdata_keeps_its_existing_cache_under_data_raw() -> None:
    assert Path(os.environ["SOCCERDATA_DIR"]) == PROJECT_ROOT / "data" / "raw" / "soccerdata"


def test_tempfile_and_pytest_temp_are_on_the_project_drive() -> None:
    assert CACHE_ROOT in Path(tempfile.gettempdir()).resolve().parents
    assert not disk_bootstrap.offenders()


def test_apply_is_idempotent() -> None:
    before = {k: os.environ[k] for k in REQUIRED}
    assert disk_bootstrap.apply() == disk_bootstrap.apply()
    assert {k: os.environ[k] for k in REQUIRED} == before


def test_a_target_on_another_drive_is_refused_before_anything_is_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = dict(os.environ)
    monkeypatch.setattr(
        disk_bootstrap, "_drive", lambda p: "C:" if "huggingface" in str(p) else "D:"
    )
    with pytest.raises(DiskBootstrapError, match="HF_HOME"):
        disk_bootstrap.apply()
    assert dict(os.environ) == before  # nothing was half-applied


def test_offenders_reports_a_variable_pointing_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(disk_bootstrap, "_drive", lambda p: "C:" if "pip" in str(p) else "D:")
    assert "PIP_CACHE_DIR" in disk_bootstrap.offenders()
