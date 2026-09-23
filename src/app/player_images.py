"""Player photos from Wikipedia, fetched in the background and cached on disk.

The UI never waits for the network: `photo_path` returns the cached file at once, or None while a
background thread fetches it (the caller shows the silhouette and refreshes later). A miss is
remembered in `credits.json`, so a player with no page image is not asked about again.

Attribution is stored per image in `credits.json` (page title, page URL, image URL, licence note).
For local or portfolio use this is fine; commercial use needs a per-image licence check
(see ANALYTICS_DECISIONS.md section 16).

Safeguard against a wrong-person photo: the page's intro must call the subject a footballer, so a
disambiguation page or an unrelated namesake is rejected and the silhouette is shown instead.

Also from Wikipedia/Wikidata, cached the same way: a player's birth date and nationality (table
`player_bio`, written in the same background fetch) and a club's logo (`fetch_team_logo`, cached to
`data/team_logos/<slug>.png` with attribution in that folder's `credits.json`; filled ahead of time
by `python -m src.app.preload_logos`, and the UI only ever reads the cache). A club logo is only
kept when the page's intro says it is a football club. Logos are trademarks: fine for local or
portfolio use, per-image licence check needed for anything commercial (section 20).
"""

from __future__ import annotations

from src.utils import disk_bootstrap  # isort: skip  # noqa: F401  (must be first)

import base64
import io
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw
from sqlalchemy import text

from src.models.schema import get_engine, init_db
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
_CLUB_INTRO = re.compile(r"football club|soccer club|association football|sports club|football team", re.IGNORECASE)
_LOGO_NAME = re.compile(r"logo|crest|badge|emblem|shield|coat", re.IGNORECASE)
WIKIDATA_URL = "https://www.wikidata.org/w/api.php"
TEAM_LOGO_PX = 96
TEAM_PLACEHOLDER_PATH = STATIC_DIR / "team_placeholder.png"
LOGO_CREDIT_LINE = "Logo: Wikipedia (club trademark)"
# Wikipedia titles that differ from the dataset's club names
_TEAM_TITLES = {
    "Barcelona": ["FC Barcelona"], "Athletic Club": ["Athletic Bilbao"],
    "Atlético Madrid": ["Atlético Madrid"], "Real Madrid": ["Real Madrid CF"],
    "Inter Milan": ["Inter Milan"], "AC Milan": ["AC Milan"], "AS Roma": ["AS Roma"],
    "Lyon": ["Olympique Lyonnais"], "Marseille": ["Olympique de Marseille"],
    "Monaco": ["AS Monaco FC"], "Nice": ["OGC Nice"], "Lille": ["Lille OSC"],
    "Paris Saint-Germain": ["Paris Saint-Germain F.C."], "Manchester United": ["Manchester United F.C."],
    "Manchester City": ["Manchester City F.C."], "Leicester City": ["Leicester City F.C."],
    "Tottenham Hotspur": ["Tottenham Hotspur F.C."], "Newcastle United": ["Newcastle United F.C."],
    "West Ham United": ["West Ham United F.C."], "Swansea City": ["Swansea City A.F.C."],
    "Norwich City": ["Norwich City F.C."], "Stoke City": ["Stoke City F.C."],
    "Bournemouth": ["A.F.C. Bournemouth"], "Celta Vigo": ["RC Celta de Vigo"],
    "Real Sociedad": ["Real Sociedad"], "Real Betis": ["Real Betis"],
    "Deportivo La Coruña": ["Deportivo de La Coruña"], "Juventus": ["Juventus F.C."],
    "Napoli": ["S.S.C. Napoli"], "Lazio": ["S.S. Lazio"], "Torino": ["Torino F.C."],
    "Chievo": ["A.C. ChievoVerona"], "Hellas Verona": ["Hellas Verona F.C."],
    "Sampdoria": ["U.C. Sampdoria"], "Udinese": ["Udinese Calcio"], "Bologna": ["Bologna F.C. 1909"],
    "Reims": ["Stade de Reims"], "Rennes": ["Stade Rennais F.C."],
    "Saint-Étienne": ["AS Saint-Étienne"], "Nantes": ["FC Nantes"],
    "Angers": ["Angers SCO"], "Aston Villa": ["Aston Villa F.C."], "Atalanta": ["Atalanta B.C."],
    "Caen": ["Stade Malherbe Caen"], "Carpi": ["Carpi F.C. 1909"], "Eibar": ["SD Eibar"],
    "Genoa": ["Genoa C.F.C."], "Las Palmas": ["UD Las Palmas"], "Sassuolo": ["U.S. Sassuolo Calcio"],
    "Troyes": ["ES Troyes AC"], "Villarreal": ["Villarreal CF"], "Watford": ["Watford F.C."],
}  # fmt: skip

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
            "action": "query", "titles": title, "prop": "pageimages|extracts|info|pageprops",
            "inprop": "url", "format": "json", "pithumbsize": THUMB_PX, "redirects": 1,
            "exintro": 1, "explaintext": 1, "exchars": 500,
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


def find_player_page(name: str) -> dict[str, object] | None:
    """The footballer's Wikipedia page (its intro must call him a footballer), or None."""
    for title in (name, f"{name} (footballer)"):
        page = _query_page(title)
        if page and _FOOTBALL_INTRO.search(str(page.get("extract", ""))):
            return page
    return None


def find_page_image(name: str) -> tuple[str, dict[str, object]] | None:
    """(thumbnail URL, page) of the footballer's Wikipedia page, or None if there is no usable
    image. Tries the plain name, then 'Name (footballer)'."""
    page = find_player_page(name)
    thumb = page.get("thumbnail") if page else None
    if page and isinstance(thumb, dict) and thumb.get("source"):
        return str(thumb["source"]), page
    return None


# ---- bio: birth date and nationality (Wikidata) ----------------------------------------------


@rate_limited(REQUEST_DELAY_SECONDS, max_retries=2)
def _wikidata_entity(qid: str, props: str) -> dict[str, Any]:
    resp = requests.get(
        WIKIDATA_URL,
        params={"action": "wbgetentities", "ids": qid, "props": props, "languages": "en",
                "format": "json"},
        headers={"User-Agent": USER_AGENT}, timeout=10,
    )  # fmt: skip
    resp.raise_for_status()
    return resp.json().get("entities", {}).get(qid, {})  # type: ignore[no-any-return]


def parse_birth_date(claims: dict[str, Any]) -> date | None:
    """P569 as a date, only when Wikidata knows the exact day (precision 11)."""
    for claim in claims.get("P569", []):
        value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if value.get("precision") == 11:
            try:
                return date.fromisoformat(str(value["time"]).lstrip("+")[:10])
            except ValueError:
                continue
    return None


def parse_nationality_qid(claims: dict[str, Any]) -> str | None:
    """First P27 (citizenship) claim that has no end date."""
    for claim in claims.get("P27", []):
        if "P582" in claim.get("qualifiers", {}):
            continue
        qid = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        if qid:
            return str(qid)
    return None


def fetch_bio(page: dict[str, object]) -> tuple[date | None, str | None]:
    """(birth date, nationality) of the Wikidata item behind a Wikipedia page."""
    props = page.get("pageprops")
    qid = props.get("wikibase_item") if isinstance(props, dict) else None
    if not qid:
        return None, None
    claims = _wikidata_entity(str(qid), "claims").get("claims", {})
    country = parse_nationality_qid(claims)
    label = None
    if country:
        label = _wikidata_entity(country, "labels").get("labels", {}).get("en", {}).get("value")
    return parse_birth_date(claims), label


def load_bio(player_id: str) -> tuple[date | None, str | None, bool]:
    """(birth date, nationality, looked_up) from `player_bio`. `looked_up` is False when the
    player has not been fetched yet. Never raises."""
    try:
        engine = init_db(get_engine())
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT birth_date, nationality FROM player_bio WHERE player_id = :p"),
                {"p": player_id},
            ).fetchone()
    except Exception:  # noqa: BLE001 - a missing bio must never break the page
        return None, None, False
    if row is None:
        return None, None, False
    return (date.fromisoformat(str(row[0])[:10]) if row[0] else None), row[1], True


def _save_bio(player_id: str, born: date | None, nationality: str | None) -> None:
    engine = init_db(get_engine())
    with _lock, engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO player_bio (player_id, birth_date, nationality, fetched_at) "
                "VALUES (:p, :b, :n, :t) ON CONFLICT(player_id) DO UPDATE SET "
                "birth_date = :b, nationality = :n, fetched_at = :t"
            ),
            {
                "p": player_id, "b": born, "n": nationality,
                "t": datetime.now(UTC).replace(tzinfo=None),
            },
        )  # fmt: skip


def _fetch_one(player_id: str, name: str, need_photo: bool, need_bio: bool) -> None:
    global _cooldown_until
    try:
        page = find_player_page(name)
        if need_bio:
            born, nationality = fetch_bio(page) if page else (None, None)
            _save_bio(player_id, born, nationality)
        if not need_photo:
            return
        thumb = page.get("thumbnail") if page else None
        if page is None or not (isinstance(thumb, dict) and thumb.get("source")):
            _save_credit(
                player_id,
                {"status": "missing", "name": name, "checked": datetime.now(UTC).isoformat()},
            )
            return
        url = str(thumb["source"])
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
        logger.warning("Photo/bio fetch failed for {n}: {e}", n=name, e=exc)
        _cooldown_until = time.monotonic() + FAILURE_COOLDOWN_SECONDS
    finally:
        with _lock:
            _pending.discard(player_id)


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="photos")
    return _executor


def photo_path(player_id: str, name: str, want_bio: bool = False) -> Path | None:
    """The cached photo, or None. When the photo is not cached yet, a background fetch is started
    (unless the player is known to have none, or the network recently failed). `want_bio` (set
    only by the profile hero, never by list avatars) also fetches birth date and nationality.
    Never blocks."""
    path = image_file(player_id)
    have_photo = path.exists()
    need_photo = not have_photo and load_credits().get(player_id, {}).get("status") != "missing"
    need_bio = want_bio and not load_bio(player_id)[2]
    if not (need_photo or need_bio):
        return path if have_photo else None
    with _lock:
        if player_id in _pending or time.monotonic() < _cooldown_until:
            return path if have_photo else None
        _pending.add(player_id)
    _pool().submit(_fetch_one, player_id, name, need_photo, need_bio)
    return path if have_photo else None


# ---- team logos ------------------------------------------------------------------------------


def team_slug(team_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", team_name).encode(
        "ascii", "ignore").decode().lower()).strip("-")


def logos_dir() -> Path:
    path = settings.team_logos_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def logo_file(team_name: str) -> Path:
    return logos_dir() / f"{team_slug(team_name)}.png"


def load_logo_credits() -> dict[str, dict[str, str]]:
    try:
        data = json.loads((logos_dir() / CREDITS_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_logo_credit(team_name: str, entry: dict[str, str]) -> None:
    with _lock:
        credits = load_logo_credits()
        credits[team_name] = entry
        path = logos_dir() / CREDITS_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(credits, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def _team_titles(team_name: str) -> list[str]:
    plain = [team_name, f"{team_name} F.C.", f"{team_name} FC", f"FC {team_name}"]
    return [*_TEAM_TITLES.get(team_name, []), *plain]


_INFOBOX_IMAGE = re.compile(r"\|\s*(?:image|logo)\s*=\s*(?:\[\[)?(?:File:|Image:)?([^\n|\]]+\.(?:svg|png))", re.IGNORECASE)


@rate_limited(REQUEST_DELAY_SECONDS, max_retries=2)
def _infobox_logo(title: str) -> tuple[str, str] | None:
    """(thumbnail URL, file name) of the crest named in the page's football-club infobox. Many
    crests are non-free files that the page-image API does not return, but the infobox names them."""
    headers = {"User-Agent": USER_AGENT}
    parsed = requests.get(
        API_URL, params={"action": "parse", "page": title, "prop": "wikitext", "section": 0,
                         "redirects": 1, "format": "json"}, headers=headers, timeout=10,
    )  # fmt: skip
    parsed.raise_for_status()
    wikitext = str(parsed.json().get("parse", {}).get("wikitext", {}).get("*", ""))
    found = _INFOBOX_IMAGE.search(wikitext)
    if not found:
        return None
    name = found.group(1).strip()
    info = requests.get(
        API_URL, params={"action": "query", "titles": f"File:{name}", "prop": "imageinfo",
                         "iiprop": "url", "iiurlwidth": THUMB_PX, "format": "json"},
        headers=headers, timeout=10,
    )  # fmt: skip
    info.raise_for_status()
    page = next(iter(info.json().get("query", {}).get("pages", {}).values()), {})
    infos = page.get("imageinfo") or []
    url = (infos[0].get("thumburl") or infos[0].get("url")) if infos else None
    return (str(url), name) if url else None


def find_team_logo(team_name: str) -> tuple[str, dict[str, object]] | None:
    """(logo URL, page) from the club's Wikipedia page, or None. The page's lead image is used
    when its file name says logo / crest / badge; otherwise the crest named in the club infobox.
    A page whose intro does not describe a football club is rejected (wrong-page safeguard)."""
    for title in _team_titles(team_name):
        page = _query_page(title)
        if not page or not _CLUB_INTRO.search(str(page.get("extract", ""))):
            continue
        thumb = page.get("thumbnail")
        if isinstance(thumb, dict) and thumb.get("source") and _LOGO_NAME.search(
            str(page.get("pageimage", ""))
        ):
            return str(thumb["source"]), page
        boxed = _infobox_logo(str(page.get("title", title)))
        if boxed:
            return boxed[0], {**page, "pageimage": boxed[1]}
    return None


def _fetch_logo(team_name: str) -> None:
    global _cooldown_until
    key = f"logo:{team_name}"
    try:
        found = find_team_logo(team_name)
        if found is None:
            _save_logo_credit(
                team_name, {"status": "missing", "checked": datetime.now(UTC).isoformat()}
            )
            return
        url, page = found
        img = Image.open(io.BytesIO(_download(url))).convert("RGBA")
        img.thumbnail((TEAM_LOGO_PX * 2, TEAM_LOGO_PX * 2))
        img.save(logo_file(team_name), format="PNG")
        _save_logo_credit(
            team_name,
            {
                "status": "ok", "title": str(page.get("title", "")),
                "page_url": str(page.get("fullurl", "")), "image_url": url,
                "file": str(page.get("pageimage", "")), "attribution": LOGO_CREDIT_LINE,
                "fetched": datetime.now(UTC).isoformat(),
            },
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - a logo failure must never surface in the UI
        logger.warning("Logo fetch failed for {n}: {e}", n=team_name, e=exc)
        _cooldown_until = time.monotonic() + FAILURE_COOLDOWN_SECONDS
    finally:
        with _lock:
            _pending.discard(key)


def fetch_team_logo(team_name: str) -> Path | None:
    """Fetch (blocking) and cache a club's logo; the cached file, or None if Wikipedia has no
    trustworthy one. Use `team_logo_path` from the UI, which never blocks."""
    if not logo_file(team_name).exists():
        _fetch_logo(team_name)
    path = logo_file(team_name)
    return path if path.exists() else None


def team_logo_path(team_name: str) -> Path | None:
    """The cached logo, or None. Cache only: this never touches the network, so the UI can call
    it during render. Logos are filled by `python -m src.app.preload_logos` or `fetch_missing_logos`."""
    path = logo_file(team_name)
    return path if path.exists() else None


def fetch_missing_logos(team_names: list[str]) -> int:
    """Fetch (blocking, user-initiated) the logos of these teams that are not cached and not known
    to be missing on Wikipedia. Returns how many were newly cached."""
    credits = load_logo_credits()
    got = 0
    for name in team_names:
        if logo_file(name).exists() or credits.get(name, {}).get("status") == "missing":
            continue
        got += fetch_team_logo(name) is not None
    return got


def make_team_placeholder(path: Path = TEAM_PLACEHOLDER_PATH, size: int = 256) -> Path:
    """Draw the generic shield used when a club has no logo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    w, top, bottom = size * 0.72, size * 0.08, size * 0.92
    left, right = (size - w) / 2, (size + w) / 2
    outline = [(left, top), (right, top), (right, size * 0.55), (size / 2, bottom), (left, size * 0.55)]
    d.polygon(outline, fill=(37, 41, 50, 255), outline=(95, 99, 104, 255), width=max(2, size // 40))
    img.save(path, format="PNG")
    return path


def team_logo_uri(team_name: str, px: int) -> str:
    """A PNG data URI of the club's logo sized `px` (x2 for high-density screens), or the
    placeholder badge. Cache only, never touches the network, never raises."""
    path = team_logo_path(team_name) or TEAM_PLACEHOLDER_PATH
    try:
        return _logo_uri(str(path), path.stat().st_mtime_ns if path.exists() else 0, px)
    except OSError:
        return ""


@lru_cache(maxsize=512)
def _logo_uri(path_str: str, mtime_ns: int, px: int) -> str:
    """Encoded once per (file version, size); later renders reuse the string."""
    try:
        path = Path(path_str)
        if not path.exists():
            make_team_placeholder(path)
        img = Image.open(path).convert("RGBA")
        img.thumbnail((px * 2, px * 2))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 - a corrupt file must fall back, not break the page
        return ""


def has_team_logo(team_name: str) -> bool:
    return logo_file(team_name).exists()


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
