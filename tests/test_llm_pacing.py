"""Groq pacing: the token window, 429 retry with backoff, and the per-turn delay in the
conversation loop. Fake clocks and sleeps only: no network, no real waiting."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.chat import conversation, llm_client
from src.chat.llm_client import GroqClient, GroqRateLimitError, TokenWindow, parse_duration


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 2))
        self.now += seconds


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("7.66s", 7.66), ("1m2.5s", 62.5), ("120ms", 0.12), ("2", 2.0), (None, None), ("x", None)],
)
def test_parse_duration(raw: str | None, seconds: float | None) -> None:
    got = parse_duration(raw)
    assert got == seconds if seconds is None else got == pytest.approx(seconds)


def test_token_window_sleeps_until_the_oldest_request_leaves() -> None:
    t = FakeTime()
    w = TokenWindow(10_000, clock=t.clock, sleep=t.sleep)
    assert w.wait_for_room(4_000) == 0
    w.record(6_000)
    t.now += 10
    w.record(3_000)
    assert w.used() == 9_000
    assert w.wait_for_room(500) == 0  # 9.5k fits
    slept = w.wait_for_room(4_000)  # 13k would not: wait for the 6k entry to age out (50 s)
    assert slept == pytest.approx(50.05, abs=0.01)
    assert w.used() == 3_000


def test_token_window_honours_groq_headers() -> None:
    t = FakeTime()
    w = TokenWindow(10_000, clock=t.clock, sleep=t.sleep)
    w.note_headers(remaining_tokens=0, reset_seconds=7.5)
    assert w.wait_for_room(100) == pytest.approx(7.5)
    w.note_headers(remaining_tokens=500, reset_seconds=3.0)  # tokens left: no block
    assert w.wait_for_room(100) == 0


class _Resp:
    def __init__(self, status: int, body: dict[str, Any] | None = None,
                 headers: dict[str, str] | None = None) -> None:  # fmt: skip
        self.status_code, self._body = status, body or {}
        self.text, self.headers = json.dumps(body or {}), headers or {}
        self.ok = 200 <= status < 300

    def json(self) -> dict[str, Any]:
        return self._body


_OK = {"choices": [{"message": {"content": "pong"}}], "usage": {"total_tokens": 1_234}}


def _client(monkeypatch: pytest.MonkeyPatch, responses: list[_Resp]) -> tuple[GroqClient, FakeTime]:
    t = FakeTime()
    sent: list[int] = []

    def fake_post(url: str, **kwargs: Any) -> _Resp:
        sent.append(1)
        return responses.pop(0)

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    client = GroqClient(
        "k", window=TokenWindow(10_000, clock=t.clock, sleep=t.sleep), sleep=t.sleep
    )
    client.sent = sent  # type: ignore[attr-defined]
    return client, t


def test_retry_waits_5_then_15_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    client, t = _client(monkeypatch, [_Resp(429), _Resp(429), _Resp(200, _OK)])
    events: list[dict[str, Any]] = []
    out = client.chat_with_retry([{"role": "user", "content": "hi"}], on_retry=events.append)
    assert out == _OK and t.slept == [5.0, 15.0]
    assert [e["kind"] for e in events] == ["429", "429"]
    assert [e["wait_seconds"] for e in events] == [5.0, 15.0]
    assert client.window.used() == 1_234  # real usage recorded, not the estimate


def test_third_429_raises_with_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    client, t = _client(monkeypatch, [_Resp(429)] * 3)
    with pytest.raises(GroqRateLimitError) as info:
        client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert t.slept == [5.0, 15.0] and len(client.sent) == 3  # type: ignore[attr-defined]
    assert info.value.wait_seconds == 15.0


def test_retry_after_header_wins_when_longer(monkeypatch: pytest.MonkeyPatch) -> None:
    client, t = _client(monkeypatch, [_Resp(429, headers={"retry-after": "12"}), _Resp(200, _OK)])
    client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert t.slept == [12.0]


def test_rate_limit_headers_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = {"x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "0",
               "x-ratelimit-reset-tokens": "6s", "content-type": "application/json"}  # fmt: skip
    client, t = _client(monkeypatch, [_Resp(200, _OK, headers), _Resp(200, _OK)])
    client.chat_with_retry([{"role": "user", "content": "hi"}])
    assert client.last_limits["x-ratelimit-limit-tokens"] == "8000"
    assert "content-type" not in client.last_limits
    client.chat_with_retry([{"role": "user", "content": "again"}])
    assert t.slept == [6.0]  # waited for Groq's reset before the second request


# ---- conversation pacing -----------------------------------------------------------------------


class _ScriptedRetryClient:
    """Has chat_with_retry, like GroqClient; replays scripted outcomes and retry events."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes

    def chat(self, *a: Any, **k: Any) -> dict[str, Any]:  # pragma: no cover - not used
        raise AssertionError

    def chat_with_retry(self, messages: Any, tools: Any = None, tool_choice: Any = None,
                        on_retry: Any = None, **_: Any) -> dict[str, Any]:  # fmt: skip
        outcome = self.outcomes.pop(0)
        for event in outcome.get("events", []):
            on_retry(event)
        if outcome.get("raise"):
            raise GroqRateLimitError("limit", wait_seconds=15.0)
        return outcome["response"]


@pytest.fixture
def paced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[FakeTime, Path]:
    t = FakeTime()
    log = tmp_path / "chat_logs.jsonl"
    monkeypatch.setattr(conversation, "_sleep", t.sleep)
    monkeypatch.setattr(conversation, "_clock", t.clock)
    monkeypatch.setattr(conversation, "log_path", lambda: log)
    monkeypatch.setattr(conversation, "system_prompt", lambda: "SYSTEM")
    monkeypatch.setattr(conversation.guardrails, "check_player_names", lambda q: [])
    monkeypatch.setattr(conversation.settings, "groq_min_delay_seconds", 3.0)
    return t, log


def _tool_call() -> dict[str, Any]:
    call = {
        "id": "c1",
        "type": "function",
        "function": {"name": "methodology", "arguments": '{"topic": "match_percent"}'},
    }
    return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}  # fmt: skip


def test_min_delay_between_calls_in_one_turn(paced: tuple[FakeTime, Path]) -> None:
    t, _ = paced
    client = _ScriptedRetryClient([
        {"response": _tool_call()},
        {"response": {"choices": [{"message": {"content": "The match percent is cosine."}}]}},
    ])  # fmt: skip
    out = conversation.ask("How is the match percent for similar players computed?", client=client)
    assert not out["refused"] and out["llm_calls"] == 2
    assert t.slept == [3.0]  # one gap, before the second call


def test_retries_are_counted_and_logged(paced: tuple[FakeTime, Path]) -> None:
    _, log = paced
    event = {"kind": "429", "attempt": 1, "wait_seconds": 5.0, "tokens_in_window": 9000}
    client = _ScriptedRetryClient([
        {"events": [event], "response": {"choices": [{"message": {"content": "Plain answer."}}]}},
    ])  # fmt: skip
    out = conversation.ask("How is the match percent for similar players computed?", client=client)
    assert out["llm_calls"] == 2 and out["retries"][0]["kind"] == "429"
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["retries"][0]["wait_seconds"] == 5.0 and record["llm_calls"] == 2


def test_rate_limit_after_retries_is_unavailable(paced: tuple[FakeTime, Path]) -> None:
    _, log = paced
    events = [{"kind": "429", "attempt": i, "wait_seconds": w} for i, w in ((1, 5.0), (2, 15.0))]
    events.append({"kind": "429", "attempt": 3, "wait_seconds": None})
    client = _ScriptedRetryClient([{"events": events, "raise": True}])
    out = conversation.ask("How is the match percent for similar players computed?", client=client)
    assert out["answer"] == conversation.UNAVAILABLE and out["error"] == "rate_limited"
    assert out["llm_calls"] == 3 and len(out["retries"]) == 3
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["error"] == "rate_limited" and len(record["retries"]) == 3
