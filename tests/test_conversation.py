"""The conversation loop (`src/chat/conversation.py`) with a scripted fake LLM: no network calls.

Dispatch and name checks read the real 2015/16 database, so these tests are skipped when it is not
built locally (except the ones that never get past the rules).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect

from src.chat import conversation
from src.chat.llm_client import GroqError
from src.utils.config import settings


def _real_db_ready() -> bool:
    if not settings.db_path.exists():
        return False
    return inspect(create_engine(f"sqlite:///{settings.db_path}")).has_table(
        "player_season_stats_2015_16"
    )


needs_db = pytest.mark.skipif(not _real_db_ready(), reason="2015/16 database not built locally")


class FakeClient:
    """Returns scripted responses in order and records every request."""

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[list[dict[str, Any]]] = []
        self.with_tools: list[bool] = []  # whether each request carried the tool schemas

    def chat(
        self, messages: list[dict[str, Any]], tools: Any = None, model: Any = None,
        tool_choice: Any = None,
    ) -> dict[str, Any]:  # fmt: skip
        self.requests.append([dict(m) for m in messages])
        self.with_tools.append(bool(tools) and tool_choice == "auto")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _tool_call(name: str, args: dict[str, Any], cid: str = "c1") -> dict[str, Any]:
    call = {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}  # fmt: skip
    return {"choices": [{"message": {"role": "assistant", "reasoning": "x", "tool_calls": [call]}}]}


def _text(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


@pytest.fixture(autouse=True)
def _tmp_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "chat_logs.jsonl"
    monkeypatch.setattr(conversation, "log_path", lambda: path)
    monkeypatch.setattr(conversation, "system_prompt", lambda: "SYSTEM")
    monkeypatch.setattr(conversation, "_sleep", lambda seconds: None)  # no real pacing waits
    return path


def test_prediction_is_refused_without_an_llm_call(_tmp_log: Path) -> None:
    fake = FakeClient()
    out = conversation.ask("Who will win the 2016 Champions League?", client=fake)
    assert out["refused"] and out["verdict"] == "PREDICTION" and out["llm_calls"] == 0
    assert out["answer"].startswith("I don't make predictions.")
    assert fake.requests == []
    record = json.loads(_tmp_log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["refused"] is True and record["question"].startswith("Who will win")
    assert {"timestamp", "tool_calls", "answer_length"} <= set(record)


@needs_db
def test_ambiguous_name_is_refused_without_an_llm_call() -> None:
    fake = FakeClient()
    out = conversation.ask("Tell me about Pogba", client=fake)
    assert out["verdict"] == "AMBIGUOUS" and fake.requests == []
    assert "Paul Pogba (Juventus)" in out["answer"]


@needs_db
def test_unknown_name_is_refused() -> None:
    out = conversation.ask("How many goals did Erling Haaland score?", client=FakeClient())
    assert out["verdict"] == "NOT_IN_DB" and '"Erling Haaland"' in out["answer"]


@needs_db
def test_factual_answer_runs_the_tool_and_verifies_numbers(_tmp_log: Path) -> None:
    fake = FakeClient(
        _tool_call("player_stats", {"player": "Jamie Vardy"}),
        _text("Jamie Vardy scored 24 goals in 2015/16.\n\nSources:\n- player_stats -> goals=24"),
    )
    out = conversation.ask("How many goals did Vardy score?", client=fake)
    assert not out["refused"] and "24 goals" in out["answer"]
    assert out["tool_calls"] == [{"name": "player_stats", "arguments": {"player": "Jamie Vardy"},
                                  "ok": True}]  # fmt: skip
    assert out["sources"][0]["tool"] == "player_stats" and out["llm_calls"] == 2
    second = fake.requests[1]
    assert second[0] == {"role": "system", "content": "SYSTEM"}
    assistant = second[-2]
    assert "reasoning" not in assistant and assistant["tool_calls"]
    tool_msg = second[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert json.loads(tool_msg["content"])["season_counts"]["goals"] == 24
    assert fake.with_tools == [True, True]


@needs_db
def test_two_tool_rounds_then_an_answer_without_tool_schemas() -> None:
    """Two tool rounds, then the answer: tools on the first two calls, none on the last, which
    may only answer."""
    fake = FakeClient(
        _tool_call("market_value", {"player": "Jamie Vardy"}),
        _tool_call("player_stats", {"player": "Jamie Vardy"}, "c2"),
        _text("Jamie Vardy scored 24 goals in 2015/16.\n\nSources:\n- player_stats -> goals=24"),
    )
    out = conversation.ask("How many goals did Vardy score?", client=fake)
    assert not out["refused"] and "24 goals" in out["answer"] and out["llm_calls"] == 3
    assert [c["name"] for c in out["tool_calls"]] == ["market_value", "player_stats"]
    assert fake.with_tools == [True, True, False]


@needs_db
def test_hallucinated_number_is_withheld() -> None:
    fake = FakeClient(
        _tool_call("player_stats", {"player": "Jamie Vardy"}),
        _text("Jamie Vardy scored 31 goals in 2015/16."),
    )
    out = conversation.ask("How many goals did Vardy score?", client=fake)
    assert out["refused"] and out["answer"] == conversation.VERIFY_FAILED
    assert out["verdict"] == "UNVERIFIED" and "31.0" in out["warnings"][0]


@needs_db
def test_similarity_answer_gets_tiers_and_the_caveat() -> None:
    fake = FakeClient(_tool_call("find_similar", {"player_name": "Sergio Busquets", "n": 3}),
                      _text("Players similar to Sergio Busquets are listed."))  # fmt: skip
    out = conversation.ask("Find me a player like Busquets", client=fake)
    assert not out["refused"]
    assert out["answer"].count("Similarity is a heuristic") == 1
    sent = json.loads(fake.requests[1][-1]["content"])
    assert sent["similarity_verdict"] in {"FAIR", "WARN"}
    assert all(r["tier"] in {"Fair", "Weak"} for r in sent["similar"])


@needs_db
def test_tool_error_becomes_a_verbatim_refusal() -> None:
    fake = FakeClient(_tool_call("find_similar", {"player_name": "Jack Wilshere"}))
    out = conversation.ask("Who is similar to Wilshere?", client=fake)
    assert out["refused"] and out["verdict"] == "BELOW_THRESHOLD"
    assert out["answer"].startswith("Jack Wilshere played 143 league minutes in 2015/16")
    assert len(fake.requests) == 1


@needs_db
def test_fixable_tool_error_goes_back_to_the_model() -> None:
    fake = FakeClient(
        _tool_call("explain_metric", {"metric": "aerials_won_pct"}),
        _tool_call("explain_metric", {"metric": "aerial_win_pct"}, "c2"),
        _text("Aerial win % is won over contested aerial duels."),
    )
    out = conversation.ask("What does aerial win percentage mean?", client=fake)
    assert not out["refused"] and out["llm_calls"] == 3
    assert [c["ok"] for c in out["tool_calls"]] == [False, True]


@needs_db
def test_loop_guard_stops_a_third_tool_round() -> None:
    call = _tool_call("methodology", {})
    fake = FakeClient(call, call, call)
    out = conversation.ask("How is the match percent computed for players?", client=fake)
    assert out["refused"] and out["answer"] == conversation.LOOP_GUARD
    assert out["error"] == "too_many_tool_rounds" and len(fake.requests) == 3


@needs_db
def test_groq_failure_is_reported_as_unavailable() -> None:
    fake = FakeClient(GroqError("boom"))
    out = conversation.ask("How many goals did Vardy score?", client=fake)
    assert out["answer"] == conversation.UNAVAILABLE and out["error"] == "llm_unavailable"


@needs_db
def test_history_is_passed_and_trimmed() -> None:
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
               for i in range(30)]  # fmt: skip
    fake = FakeClient(_text("Leicester City is in the data."))
    conversation.ask("How did Leicester play?", history=history, client=fake)
    sent = fake.requests[0]
    assert len(sent) == 1 + conversation.MAX_HISTORY_MESSAGES + 1
    assert sent[1]["content"] == "m10" and sent[-1]["content"] == "How did Leicester play?"
