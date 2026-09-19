"""Offline tests for StatsBomb fetching/caching: no network, temp directories only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.ingest.statsbomb import fetch
from src.ingest.statsbomb.fetch import CorruptCacheError, fetch_json, load_cached_json


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.content = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.utils.ratelimit.time.sleep", lambda _s: None)


def _script_responses(monkeypatch: pytest.MonkeyPatch, bodies: list[bytes]) -> list[str]:
    """Make `requests.get` return the given bodies in order; returns the URLs requested."""
    urls: list[str] = []
    queue = list(bodies)

    def fake_get(url: str, **_kwargs: Any) -> FakeResponse:
        urls.append(url)
        return FakeResponse(queue.pop(0))

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    return urls


GOOD = json.dumps([{"id": 1}, {"id": 2}]).encode()


def test_download_is_cached_then_served_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    urls = _script_responses(monkeypatch, [GOOD])
    cache = tmp_path / "events" / "1.json"

    first = fetch_json("events/1.json", cache, list)
    second = fetch_json(
        "events/1.json", cache, list
    )  # would raise IndexError if it hit the network

    assert first == second == [{"id": 1}, {"id": 2}]
    assert len(urls) == 1
    assert urls[0].endswith("/data/events/1.json")
    assert cache.read_bytes() == GOOD
    assert not list(cache.parent.glob("*.part"))  # atomic write leaves no partial file


def test_truncated_download_is_retried_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    urls = _script_responses(monkeypatch, [GOOD[: len(GOOD) // 2], GOOD])
    cache = tmp_path / "1.json"

    assert fetch_json("events/1.json", cache, list) == [{"id": 1}, {"id": 2}]
    assert len(urls) == 2  # the partial body was rejected and retried
    assert json.loads(cache.read_bytes()) == [{"id": 1}, {"id": 2}]


def test_persistently_bad_download_raises_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    _script_responses(monkeypatch, [b"<html>Not JSON</html>"] * 3)
    cache = tmp_path / "1.json"

    with pytest.raises(ValueError, match="not valid JSON"):
        fetch_json("events/1.json", cache, list)
    assert not cache.exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    ("body", "message"),
    [(b"[]", "empty"), (b"{}", "expected list"), (b'{"a": 1}', "expected list")],
)
def test_wrong_shape_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None, body: bytes, message: str
) -> None:
    _script_responses(monkeypatch, [body] * 3)
    with pytest.raises(ValueError, match=message):
        fetch_json("events/1.json", tmp_path / "1.json", list)


def test_corrupt_cache_fails_loudly_instead_of_being_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "1.json"
    cache.write_bytes(GOOD[:10])  # a truncated file, as a failed download might leave
    urls = _script_responses(monkeypatch, [GOOD])

    with pytest.raises(CorruptCacheError, match="Delete it or re-run with --force"):
        fetch_json("events/1.json", cache, list)
    assert urls == []  # no silent re-download
    assert cache.read_bytes() == GOOD[:10]  # and the bad file is left for inspection


def test_force_replaces_a_corrupt_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_sleep: None
) -> None:
    cache = tmp_path / "1.json"
    cache.write_bytes(b"garbage")
    _script_responses(monkeypatch, [GOOD])

    assert fetch_json("events/1.json", cache, list, force=True) == [{"id": 1}, {"id": 2}]
    assert load_cached_json(cache, list) == [{"id": 1}, {"id": 2}]


def test_cache_paths_live_under_the_configured_cache_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch.settings, "cache_dir", tmp_path)
    assert fetch.events_cache_path(7) == tmp_path / "statsbomb" / "events" / "7.json"
    assert fetch.lineups_cache_path(7) == tmp_path / "statsbomb" / "lineups" / "7.json"
