"""Minimal Groq chat-completions client over plain `requests` (OpenAI-compatible format).

    python -m src.chat.llm_client --test    # one "pong" round trip; exit 0 on success, 1 on error

The key comes from `settings.groq_api_key` (GROQ_API_KEY in .env). No SDK.

`chat` is one attempt. `chat_with_retry` adds the pacing the free tier needs: a sliding 60-second
token window (`TokenWindow`, budget `settings.groq_tpm_budget`) that sleeps before a request would
exceed the budget, and on HTTP 429 a wait of 5 s then 15 s (or Groq's retry-after, if longer)
before retrying; the third 429 raises `GroqRateLimitError` with the suggested wait.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import requests

from src.utils.config import settings
from src.utils.ratelimit import rate_limited

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"  # overridden by settings.groq_model when set
TIMEOUT_SECONDS = 30.0
AUTH_MESSAGE = "GROQ_API_KEY is invalid or missing. Check .env."
RATE_LIMIT_MESSAGE = "Groq rate limit reached. Wait and retry."
BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0)  # waits after the 1st and 2nd 429
WINDOW_SECONDS = 60.0
CHARS_PER_TOKEN = 4  # request-size estimate before Groq reports the real usage
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


class GroqError(RuntimeError):
    """Any failed Groq call (network, HTTP error, unexpected payload)."""


class GroqAuthError(GroqError):
    """401: the key is missing, wrong or revoked."""


class GroqRateLimitError(GroqError):
    """429: requests or tokens per minute exceeded. `wait_seconds` = how long to wait."""

    def __init__(self, message: str, wait_seconds: float | None = None, detail: str = "") -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds
        self.detail = detail


RateLimitError = GroqRateLimitError  # the name the chat layer uses


def parse_duration(value: str | None) -> float | None:
    """Groq durations in seconds: '7.66s', '1m2.5s', '120ms', or a plain number."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    parts = _DURATION_PART.findall(value.strip())
    if not parts:
        return None
    unit = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(n) * unit[u] for n, u in parts)


def _int(value: str | None) -> int | None:
    try:
        return None if value is None else int(float(value))
    except ValueError:
        return None


class TokenWindow:
    """Tokens spent in the last `window` seconds; `wait_for_room` sleeps until a request fits."""

    def __init__(
        self,
        budget: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        window: float = WINDOW_SECONDS,
    ) -> None:
        self.budget, self.window = budget, window
        self._clock, self._sleep = clock, sleep
        self._spent: deque[tuple[float, int]] = deque()
        self.blocked_until = 0.0  # from Groq's own remaining-tokens headers

    def _trim(self, now: float) -> None:
        while self._spent and now - self._spent[0][0] >= self.window:
            self._spent.popleft()

    def used(self) -> int:
        self._trim(self._clock())
        return sum(t for _, t in self._spent)

    def wait_for_room(self, estimate: int) -> float:
        """Sleep until `estimate` more tokens fit in the budget; returns the seconds slept."""
        slept = 0.0
        now = self._clock()
        if self.blocked_until > now:
            self._sleep(self.blocked_until - now)
            slept += self.blocked_until - now
        while True:
            now = self._clock()
            self._trim(now)
            if not self._spent or self.used() + estimate <= self.budget:
                return slept
            pause = self.window - (now - self._spent[0][0]) + 0.05
            self._sleep(pause)
            slept += pause

    def record(self, tokens: int) -> None:
        self._spent.append((self._clock(), int(tokens)))

    def note_headers(self, remaining_tokens: int | None, reset_seconds: float | None) -> None:
        """Groq reports the tokens left and when they reset: never send before that."""
        if remaining_tokens is not None and reset_seconds is not None and remaining_tokens <= 0:
            self.blocked_until = self._clock() + reset_seconds


class GroqClient:
    """One Groq account. `chat` sends one request; `chat_with_retry` paces and retries."""

    def __init__(
        self,
        api_key: str,
        window: TokenWindow | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise GroqAuthError(AUTH_MESSAGE)
        self._api_key = api_key
        self._sleep = sleep
        self.window = window or TokenWindow(settings.groq_tpm_budget, sleep=sleep)
        self.last_limits: dict[str, str] = {}  # x-ratelimit-* headers of the last response

    @rate_limited(delay_seconds=0.0, max_retries=1)
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]:
        """POST one chat completion; raises GroqAuthError / GroqRateLimitError / GroqError."""
        payload: dict[str, Any] = {
            "model": model or settings.groq_model or DEFAULT_MODEL,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        try:
            resp = requests.post(
                GROQ_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise GroqError(f"Could not reach Groq: {exc}") from exc
        headers = getattr(resp, "headers", None) or {}
        self.last_limits = {
            str(k).lower(): str(v)
            for k, v in headers.items()
            if str(k).lower().startswith("x-ratelimit")
        }
        if resp.status_code == 401:
            raise GroqAuthError(AUTH_MESSAGE)
        if resp.status_code == 429:
            raise GroqRateLimitError(
                RATE_LIMIT_MESSAGE,
                wait_seconds=parse_duration(headers.get("retry-after")),
                detail=resp.text[:300],
            )
        if not resp.ok:
            raise GroqError(f"Groq returned HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise GroqError("Groq returned a non-JSON response") from exc
        return data

    def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 2,
        model: str | None = None,
        tool_choice: str | None = None,
        on_retry: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """`chat` behind the token window, retrying 429s with backoff (module docstring).
        Raises GroqRateLimitError (with `wait_seconds`) once `max_retries` retries are spent."""
        size = len(json.dumps({"m": messages, "t": tools}, ensure_ascii=False))
        estimate = size // CHARS_PER_TOKEN
        for attempt in range(max_retries + 1):
            waited = self.window.wait_for_room(estimate)
            if waited and on_retry:
                on_retry(
                    {"kind": "tpm_wait", "seconds": round(waited, 2), "used": self.window.used()}
                )
            try:
                resp = self.chat(messages, tools=tools, model=model, tool_choice=tool_choice)
            except GroqRateLimitError as exc:
                backoff = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
                wait = max(backoff, exc.wait_seconds or 0.0)
                if on_retry:
                    on_retry(
                        {
                            "kind": "429", "attempt": attempt + 1,
                            "wait_seconds": None if attempt == max_retries else wait,
                            "tokens_in_window": self.window.used(), "detail": exc.detail,
                            "limits": dict(self.last_limits),
                        }
                    )  # fmt: skip
                if attempt == max_retries:
                    raise GroqRateLimitError(
                        RATE_LIMIT_MESSAGE, wait_seconds=wait, detail=exc.detail
                    ) from exc
                self._sleep(wait)
                continue
            usage = resp.get("usage") or {}
            self.window.record(int(usage.get("total_tokens") or estimate))
            self.window.note_headers(
                _int(self.last_limits.get("x-ratelimit-remaining-tokens")),
                parse_duration(self.last_limits.get("x-ratelimit-reset-tokens")),
            )
            return resp
        raise GroqRateLimitError(RATE_LIMIT_MESSAGE)  # unreachable: the loop returns or raises


def message_text(response: dict[str, Any]) -> str:
    """The assistant text of the first choice ('' when the model only called tools)."""
    try:
        return str(response["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError("Unexpected Groq response shape") from exc


_CLIENT: GroqClient | None = None


def client_from_settings() -> GroqClient:
    """One shared client per process, so its token window covers every chat turn."""
    global _CLIENT
    if _CLIENT is None or _CLIENT._api_key != settings.groq_api_key:
        _CLIENT = GroqClient(settings.groq_api_key)
    return _CLIENT


def smoke_test() -> int:
    """Send one 'pong' request; print the reply. 0 on success, 1 on any error."""
    try:
        reply = message_text(
            client_from_settings().chat(
                [{"role": "user", "content": "Reply with the single word: pong"}]
            )
        )
    except (GroqAuthError, GroqRateLimitError) as exc:
        print(exc)
        return 1
    except GroqError as exc:
        print(f"Groq smoke test failed: {exc}")
        return 1
    print(reply)
    return 0


def _main() -> None:
    parser = argparse.ArgumentParser(description="Groq client.")
    parser.add_argument("--test", action="store_true", help="Run the one-call smoke test.")
    args = parser.parse_args()
    if not args.test:
        parser.print_help()
        sys.exit(0)
    sys.exit(smoke_test())


if __name__ == "__main__":
    _main()
