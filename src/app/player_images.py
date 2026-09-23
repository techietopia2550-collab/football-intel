"""Player photos from Wikipedia, fetched in the background and cached on disk.

The UI never waits for the network: `photo_path` returns the cached file at once, or None while a
background thread fetches it (the caller shows the silhouette and refreshes later). A miss is
remembered in `credits.json`, so a player with no page image is not asked about again.

Attribution is stored per image in `credits.json` (page title, page URL, image URL, licence note).
For local or portfolio use this is fine; commercial use needs a per-image licence check
(see ANALYTICS_DECISIONS.md section 16).

Safeguard against a wrong-person photo: the page's intro must call the subject a footballer, so a
disambiguation page or an unrelated namesake is rejected and the silhouette is shown instead.
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import base64
import io
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from src.utils.config import settings
from src.utils.logging import logger
from src.utils.ratelimit import rate_limited

API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "FootballIntel/0.1 (local portfolio project; Wikipedia pageimages API)"
THUMB_PX = 400
CREDIT_LINE = "Photo: Wikipedia / CC-BY-SA"
STATIC_DIR = Path(__file__).resolve().parent / "static"
SILHOUETTE_PATH = STATIC_DIR / "silhouette.png"
CREDITS_FILE = "credits.json"
REQUEST_DELAY_SECONDS = 0.5
FAILURE_COOLDOWN_SECONDS = 300.0  # after a network failure, do not ask again for a while
_FOOTBALL_INTRO = re.compile(r"football|soccer", re.IGNORECASE)

_lock = threading.Lock()
_pending: set[str] = set()
_cooldown_until = 0.0
_executor: ThreadPoolExecutor | None = None


def images_dir() -> Path:
    path = settings.player_images_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def image_file(player_id: str) -> Path:
    """Cache path of a player's photo: `<images dir>/<player_id>.jpg` (':' is not filename-safe)."""
    return images_dir() / f"{player_id.replace(':', '_')}.jpg"


def make_silhouette(path: Path = SILHOUETTE_PATH, size: int = 256) -> Path:
    """Draw the neutral head-and-shoulders placeholder (used when a photo is missing)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    bg, fg = (37, 41, 50, 255), (95, 99, 104, 255)  # design tokens: bg tertiary, text tertiary
    img = Image.new("RGBA", (size, size), bg)
    draw = ImageDraw.Draw(img)
    head = size * 0.22
    cx, top = size / 2, size * 0.20
    draw.ellipse([cx - head, top, cx + head, top + 2 * head], fill=fg)
    draw.ellipse([size * 0.14, size * 0.62, size * 0.86, size * 1.30], fill=fg)
    img.save(path, format="PNG")
    return path


def silhouette_bytes() -> bytes:
    if not SILHOUETTE_PATH.exists():
        make_silhouette()
    return SILHOUETTE_PATH.read_bytes()


# ---- credits ---------------------------------------------------------------------------------


def load_credits() -> dict[str, dict[str, str]]:
    path = images_dir() / CREDITS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_credit(player_id: str, entry: dict[str, str]) -> None:
    with _lock:
        credits = load_credits()
        credits[player_id] = entry
        path = images_dir() / CREDITS_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(credits, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


# ---- fetching --------------------------------------------------------------------------------


@rate_limited(REQUEST_DELAY_SECONDS, max_retries=2)
def _query_page(title: str) -> dict[str, object] | None:
    resp = requests.get(
        API_URL,
        params={
            "action": "query", "titles": title, "prop": "pageimages|extracts|info",
            "inprop": "url", "format": "json", "pithumbsize": THUMB_PX, "redirects": 1,
            "exintro": 1, "explaintext": 1, "exchars": 300,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )  # fmt: skip
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()), None)
    if not page or "missing" in page:
        return None
    return page  # type: ignore[no-any-return]


@rate_limited(REQUEST_DELAY_SECONDS, max_retries=2)
def _download(url: str) -> bytes:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    return resp.content


def find_page_image(name: str) -> tuple[str, dict[str, object]] | None:
    """(thumbnail URL, page) of the footballer's Wikipedia page, or None if there is no usable
    image. Tries the plain name, then 'Name (footballer)'."""
    for title in (name, f"{name} (footballer)"):
        page = _query_page(title)
        if not page:
            continue
        intro = str(page.get("extract", ""))
        thumb = page.get("thumbnail")
        if _FOOTBALL_INTRO.search(intro) and isinstance(thumb, dict) and thumb.get("source"):
            return str(thumb["source"]), page
    return None


def _fetch_one(player_id: str, name: str) -> None:
    global _cooldown_until
    try:
        found = find_page_image(name)
        if found is None:
            _save_credit(
                player_id,
                {"status": "missing", "name": name, "checked": datetime.now(UTC).isoformat()},
            )
            return
        url, page = found
        raw = _download(url)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((THUMB_PX, THUMB_PX))
        img.save(image_file(player_id), format="JPEG", quality=88)
        _save_credit(
            player_id,
            {
                "status": "ok", "name": name, "title": str(page.get("title", "")),
                "page_url": str(page.get("fullurl", "")), "image_url": url,
                "attribution": CREDIT_LINE, "fetched": datetime.now(UTC).isoformat(),
            },
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - a photo failure must never surface in the UI
        logger.warning("Photo fetch failed for {n}: {e}", n=name, e=exc)
        _cooldown_until = time.monotonic() + FAILURE_COOLDOWN_SECONDS
    finally:
        with _lock:
            _pending.discard(player_id)


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="photos")
    return _executor


def photo_path(player_id: str, name: str) -> Path | None:
    """The cached photo, or None. When it is not cached yet, a background fetch is started
    (unless the player is known to have none, or the network recently failed). Never blocks."""
    path = image_file(player_id)
    if path.exists():
        return path
    if load_credits().get(player_id, {}).get("status") == "missing":
        return None
    with _lock:
        if player_id in _pending or time.monotonic() < _cooldown_until:
            return None
        _pending.add(player_id)
    _pool().submit(_fetch_one, player_id, name)
    return None


def fetches_pending() -> bool:
    with _lock:
        return bool(_pending)


def has_credit(player_id: str) -> bool:
    return image_file(player_id).exists()


# ---- rendering helpers -----------------------------------------------------------------------


def _square_jpeg_uri(data: bytes, px: int) -> str:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, 0  # portraits: keep the top, where the face is
    img = img.crop((left, top, left + side, top + side)).resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def silhouette_uri(px: int = 128) -> str:
    return _square_jpeg_uri(silhouette_bytes(), px)


def avatar_data_uri(path: Path | None, px: int) -> str:
    """A data URI for the photo at `path` sized `px` (x2 for high-density screens), or the
    silhouette when there is no photo or it cannot be read. Never raises."""
    try:
        data = path.read_bytes() if path is not None else silhouette_bytes()
        return _square_jpeg_uri(data, px * 2)
    except Exception:  # noqa: BLE001 - a corrupt file must fall back, not break the page
        return _square_jpeg_uri(silhouette_bytes(), px * 2)
