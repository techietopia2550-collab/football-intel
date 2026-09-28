"""Minimal Groq chat-completions client over plain `requests` (OpenAI-compatible format).

    python -m src.chat.llm_client --test    # one "pong" round trip; exit 0 on success, 1 on error

The key comes from `settings.groq_api_key` (GROQ_API_KEY in .env). No SDK, no retry loop: a
single attempt per call, so a bad key or a rate limit never multiplies requests.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import requests

from src.utils.config import settings
from src.utils.ratelimit import rate_limited

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"  # overridden by settings.groq_model when set
TIMEOUT_SECONDS = 30.0
AUTH_MESSAGE = "GROQ_API_KEY is invalid or missing. Check .env."
RATE_LIMIT_MESSAGE = "Groq rate limit reached. Wait and retry."


class GroqError(RuntimeError):
    """Any failed Groq call (network, HTTP error, unexpected payload)."""


class GroqAuthError(GroqError):
    """401: the key is missing, wrong or revoked."""


class GroqRateLimitError(GroqError):
    """429: requests or tokens per minute exceeded."""


class GroqClient:
    """One Groq account. `chat` sends one request and returns the parsed JSON response."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise GroqAuthError(AUTH_MESSAGE)
        self._api_key = api_key

    @rate_limited(delay_seconds=0.0, max_retries=1)
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """POST one chat completion; raises GroqAuthError / GroqRateLimitError / GroqError."""
        payload: dict[str, Any] = {
            "model": model or settings.groq_model or DEFAULT_MODEL,
            "messages": messages,
            "temperature": 0,
        }
        if tools:
            payload["tools"] = tools
        try:
            resp = requests.post(
                GROQ_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise GroqError(f"Could not reach Groq: {exc}") from exc
        if resp.status_code == 401:
            raise GroqAuthError(AUTH_MESSAGE)
        if resp.status_code == 429:
            raise GroqRateLimitError(RATE_LIMIT_MESSAGE)
        if not resp.ok:
            raise GroqError(f"Groq returned HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise GroqError("Groq returned a non-JSON response") from exc
        return data


def message_text(response: dict[str, Any]) -> str:
    """The assistant text of the first choice ('' when the model only called tools)."""
    try:
        return str(response["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise GroqError("Unexpected Groq response shape") from exc


def client_from_settings() -> GroqClient:
    return GroqClient(settings.groq_api_key)


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
