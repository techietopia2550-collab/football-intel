"""UI-layer tests that need no browser: design tokens, components, search and image helpers."""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import tomllib
from pathlib import Path

import pandas as pd

from src.analytics import directory
from src.app import design, player_images
from src.utils.config import PROJECT_ROOT


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    chans = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_streamlit_theme_mirrors_design_tokens() -> None:
    theme = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text())["theme"]
    assert theme["backgroundColor"] == design.BG_PRIMARY
    assert theme["secondaryBackgroundColor"] == design.BG_SECONDARY
    assert theme["textColor"] == design.TEXT_PRIMARY
    assert theme["primaryColor"] == design.HIGHLIGHT


def test_readable_text_meets_contrast() -> None:
    for fg in (design.TEXT_PRIMARY, design.TEXT_SECONDARY, design.POSITIVE, design.HIGHLIGHT):
        for bg in (design.BG_PRIMARY, design.BG_SECONDARY, design.BG_TERTIARY):
            assert _contrast(fg, bg) >= 4.5, (fg, bg)


def test_metric_badge_never_relies_on_colour_alone() -> None:
    strong = design.metric_badge("Tackles", None, 99.7, "CM")
    weak = design.metric_badge("Goals", None, 5.0, "CM")
    assert "99.7" not in strong and "100th" in strong and "▲" in strong
    assert "5th" in weak and "▼" in weak


def test_avatar_falls_back_to_silhouette_and_is_never_broken(tmp_path: Path) -> None:
    assert player_images.SILHOUETTE_PATH.exists()
    uri = player_images.avatar_data_uri(tmp_path / "missing.jpg", 32)
    assert uri.startswith("data:image/jpeg;base64,")
    (tmp_path / "bad.jpg").write_bytes(b"not an image")
    assert player_images.avatar_data_uri(tmp_path / "bad.jpg", 32).startswith("data:image/jpeg")
    assert 'alt="Photo of' in design.player_avatar("N'Golo Kanté", 64)


def test_heatmap_figure_covers_the_pitch() -> None:
    grid = [[0.0] * 12 for _ in range(8)]
    grid[4][6] = 5.0
    fig = design.heatmap_pitch(grid, "t")
    assert tuple(fig.layout.xaxis.range) == (0, 120) and tuple(fig.layout.yaxis.range) == (80, 0)


def _dir() -> pd.DataFrame:
    names = ["N'Golo Kanté", "Mesut Özil", "Harry Kane", "Idrissa Gueye"]
    return pd.DataFrame(
        {"player_name": names, "minutes_total": [3000, 2000, 3100, 2900],
         "name_key": [directory._fold(n) for n in names]}
    )  # fmt: skip


def test_search_ignores_accents_and_tolerates_typos() -> None:
    assert directory.search(_dir(), "kante").iloc[0]["player_name"] == "N'Golo Kanté"
    assert directory.search(_dir(), "ozil").iloc[0]["player_name"] == "Mesut Özil"
    assert directory.search(_dir(), "Kanteh").iloc[0]["player_name"] == "N'Golo Kanté"
    assert directory.search(_dir(), "zzzzzz").empty
    assert len(directory.search(_dir(), "")) == 4


def test_score_flips_lower_is_better_and_skips_descriptive() -> None:
    assert directory.score_of("gk_goals_conceded_p90", 10.0) == 90.0
    assert directory.score_of("tackles_p90", 10.0) == 10.0
    assert directory.score_of("fouls_committed_p90", 10.0) is None
