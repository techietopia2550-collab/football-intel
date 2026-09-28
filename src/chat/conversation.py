"""The chatbot conversation loop (docs/RAG_DESIGN.md section 1).

`ask(question, history)`:
  1. rule-based refusals (no LLM call);
  2. name check: unknown -> NOT_IN_DB, ambiguous -> AMBIGUOUS (no LLM call);
  3. LLM with the system prompt and the 17 tools; tool calls run through `dispatch.call_tool`,
     at most MAX_TOOL_ROUNDS rounds;
  4. similarity results go through the Decision 3 rule before the LLM sees them;
  5. every number in the final answer must appear in a tool result, else the answer is withheld.
Pacing: at least `settings.groq_min_delay_seconds` between two LLM calls of one turn; the
client's `chat_with_retry` keeps a tokens-per-minute window and retries HTTP 429 twice (5 s,
then 15 s). Retries and waits are written to the chat log with the turn.
Every call is appended to data/processed/chat_logs.jsonl.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from src.chat import guardrails
from src.chat.dispatch import call_tool
from src.chat.llm_client import GroqError, GroqRateLimitError, client_from_settings
from src.chat.prompt import system_prompt
from src.chat.tools import TOOLS
from src.utils.config import settings
from src.utils.logging import logger

MAX_TOOL_ROUNDS = 2
MAX_HISTORY_MESSAGES = 20  # 10 turns
UNAVAILABLE = "Chatbot is unavailable. Try again later."
VERIFY_FAILED = (
    "I couldn't verify the numbers in my answer against the database. Please rephrase the question."
)
LOOP_GUARD = "I needed too many lookups to answer that. Please ask a narrower question."
SIMILARITY_TOOLS = frozenset({"find_similar", "find_similar_teams"})
# Errors the model can fix or explain itself (a wrong metric or axis name): fed back as the tool
# result. Every other error is a verbatim refusal straight away.
MODEL_FIXABLE_ERRORS = frozenset(
    {"bad_arguments", "unknown_metric", "metric_not_judged", "unknown_axis", "unknown_tool"}
)
KEY_FIELDS = 12
_sleep = time.sleep  # module-level so tests can replace them
_clock = time.monotonic


def log_path() -> Path:
    return settings.processed_dir / "chat_logs.jsonl"


class ChatClient(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        tool_choice: str | None = None,
    ) -> dict[str, Any]: ...


# ---- helpers -----------------------------------------------------------------------------------


def _key_fields(result: Any, limit: int = KEY_FIELDS) -> dict[str, Any]:
    """A short, flat view of a tool result for the Sources panel (scalars up to two levels)."""
    out: dict[str, Any] = {}

    def walk(obj: Any, prefix: str, depth: int) -> None:
        if len(out) >= limit:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{prefix}{k}" if not prefix else f"{prefix}.{k}", depth + 1)
                if len(out) >= limit:
                    return
        elif isinstance(obj, list):
            if obj and depth < 2:
                walk(obj[0], f"{prefix}[0]", depth + 1)
        elif depth <= 3:
            out[prefix] = obj

    walk(result, "", 0)
    return out


def _error_refusal(name: str, args: dict[str, Any], result: dict[str, Any]) -> str | None:
    """The verbatim refusal for a tool error, or None when the model should see the error."""
    err = result.get("error")
    if err in MODEL_FIXABLE_ERRORS:
        return None
    if err == "not_found":
        query = result.get("query") or next((v for v in args.values() if isinstance(v, str)), "")
        return guardrails.refusal_text("NOT_IN_DB", name=query)
    if err == "ambiguous":
        cands = result.get("candidates") or []
        kind = "teams" if cands and "team_name" in cands[0] else "players"
        names = [
            f"{c.get('player_name') or c.get('team_name')} ({c.get('team') or c.get('league')})"
            for c in cands
        ]
        return guardrails.refusal_text(
            "AMBIGUOUS", query=result.get("query", ""), kind=kind, candidates=", ".join(names)
        )
    if err == "below_threshold":
        return guardrails.refusal_text(
            "BELOW_THRESHOLD",
            player=result.get("player_name"),
            minutes=round(float(result.get("minutes_total") or 0)),
        )
    if err == "no_data":
        return guardrails.refusal_text("NO_DATA", what_is_missing=result.get("detail", "unknown"))
    if err == "infeasible_budget":
        return str(result.get("detail"))
    return guardrails.refusal_text("FALLBACK")


def _engine_warnings(result: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("warnings", "notes"):
        val = result.get(key)
        if isinstance(val, list):
            found += [str(v) for v in val]
        elif isinstance(val, dict):  # compare_players: {"a": [...], "b": [...]}
            for v in val.values():
                found += [str(x) for x in v] if isinstance(v, list) else []
    squad = result.get("squad")
    if isinstance(squad, dict):
        found += [str(n) for n in squad.get("notes") or []]
    return found


def _log(record: dict[str, Any]) -> None:
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # logging must never break an answer
        logger.warning("chat log not written: {exc}", exc=exc)


def _reply(
    question: str,
    answer: str,
    *,
    refused: bool,
    verdict: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    llm_calls: int = 0,
    error: str | None = None,
    retries: list[dict[str, Any]] | None = None,
    withheld_answer: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "answer": answer, "tool_calls": tool_calls or [], "sources": sources or [],
        "warnings": warnings or [], "refused": refused, "verdict": verdict,
        "llm_calls": llm_calls, "error": error, "retries": retries or [],
        "withheld_answer": withheld_answer,  # an answer the citation check blocked (not shown)
    }  # fmt: skip
    _log(
        {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"), "question": question,
            "tool_calls": [
                {"name": c["name"], "arguments": c["arguments"]} for c in out["tool_calls"]
            ],
            "answer_length": len(answer), "refused": refused, "verdict": verdict,
            "llm_calls": llm_calls, "error": error, "retries": retries or [],
            "withheld_answer": withheld_answer,
        }
    )  # fmt: skip
    return out


# ---- main entry point --------------------------------------------------------------------------


def ask(
    question: str, history: list[dict[str, str]] | None = None, client: ChatClient | None = None
) -> dict[str, Any]:
    """Answer one question from the database only; see the module docstring for the flow."""
    by_rule = guardrails.rule_refusal(question)
    if by_rule:
        return _reply(question, by_rule[1], refused=True, verdict=by_rule[0])
    named = guardrails.name_refusal(guardrails.check_player_names(question))
    if named:
        return _reply(question, named[1], refused=True, verdict=named[0])

    state: dict[str, Any] = {"llm_calls": 0, "retries": [], "last_call": None}

    def reply(q: str, text_: str, **kw: Any) -> dict[str, Any]:
        return _reply(q, text_, llm_calls=state["llm_calls"], retries=state["retries"], **kw)

    def on_retry(event: dict[str, Any]) -> None:
        state["retries"].append({"at": datetime.now(UTC).isoformat(timespec="seconds"), **event})
        if event.get("kind") == "429":
            state["llm_calls"] += 1  # a rejected request was still sent
        logger.warning("Groq pacing: {e}", e=event)

    def call_llm(llm: ChatClient, with_tools: bool) -> dict[str, Any]:
        if state["last_call"] is not None:
            gap = settings.groq_min_delay_seconds - (_clock() - state["last_call"])
            if gap > 0:
                _sleep(gap)
        tools = TOOLS if with_tools else None
        choice = "auto" if with_tools else None
        try:
            with_retry = getattr(llm, "chat_with_retry", None)
            if with_retry is not None:
                resp: dict[str, Any] = with_retry(
                    messages, tools=tools, tool_choice=choice, on_retry=on_retry
                )
            else:
                resp = llm.chat(messages, tools=tools, tool_choice=choice)
        finally:
            state["last_call"] = _clock()
        state["llm_calls"] += 1
        return resp

    try:
        llm = client or client_from_settings()
    except GroqError:
        return reply(question, UNAVAILABLE, refused=True, error="llm_unavailable")
    past = [
        {"role": m["role"], "content": m["content"]}
        for m in (history or [])[-MAX_HISTORY_MESSAGES:]
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt()}, *past, {"role": "user", "content": question}
    ]  # fmt: skip
    calls_made: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    evidence: list[Any] = [question]
    warnings: list[str] = []
    prefix: str | None = None
    used_similarity = False
    answer = ""
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        try:
            # the last round may only answer, so it does not carry the ~1.9k tokens of tool schemas
            resp = call_llm(llm, with_tools=round_no < MAX_TOOL_ROUNDS)
        except GroqRateLimitError as exc:
            logger.warning("Groq rate limit after retries: {exc}", exc=exc)
            return reply(
                question, UNAVAILABLE, refused=True, tool_calls=calls_made, sources=sources,
                error="rate_limited",
            )  # fmt: skip
        except GroqError as exc:
            logger.warning("Groq call failed: {exc}", exc=exc)
            return reply(
                question, UNAVAILABLE, refused=True, tool_calls=calls_made, sources=sources,
                 error="llm_unavailable",
            )  # fmt: skip
        try:
            msg = resp["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return reply(question, UNAVAILABLE, refused=True,
                          error="bad_llm_response")  # fmt: skip
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            answer = guardrails.normalize_answer(str(msg.get("content") or "")).strip()
            break
        if round_no == MAX_TOOL_ROUNDS:
            return reply(
                question, LOOP_GUARD, refused=True, verdict="LOOP_GUARD", tool_calls=calls_made,
                sources=sources, error="too_many_tool_rounds",
            )  # fmt: skip
        # the model's private "reasoning" field is not sent back
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": tool_calls})  # fmt: skip
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = str(fn.get("name", ""))
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError, ValueError):
                args = {}
            result = call_tool(name, args)
            calls_made.append({"name": name, "arguments": args, "ok": "error" not in result})
            if "error" in result:
                refusal = _error_refusal(name, args, result)
                if refusal is not None:
                    return reply(
                        question, refusal, refused=True, verdict=str(result["error"]).upper(),
                        tool_calls=calls_made, sources=sources,
                    )  # fmt: skip
            elif name in SIMILARITY_TOOLS:
                rule = guardrails.apply_similarity_rule(result)
                if rule["refusal"]:
                    return reply(
                        question, rule["refusal"], refused=True, verdict=rule["verdict"],
                        tool_calls=calls_made, sources=sources,
                    )  # fmt: skip
                result = {**rule["result"], "similarity_verdict": rule["verdict"]}
                prefix = prefix or rule["prefix"]
                used_similarity = True
            if "error" not in result:
                warnings += _engine_warnings(result)
                sources.append({"tool": name, "arguments": args, "fields": _key_fields(result)})
            evidence += [args, result]
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)})  # fmt: skip

    if not answer:
        return reply(question, guardrails.refusal_text("FALLBACK"), refused=True,
                      tool_calls=calls_made, sources=sources)  # fmt: skip
    missing = guardrails.unverified_numbers(answer, evidence)
    if missing:
        logger.warning("unverified numbers {m} in answer to {q!r}", m=missing, q=question)
        return reply(
            question, VERIFY_FAILED, refused=True, verdict="UNVERIFIED", tool_calls=calls_made,
            sources=sources, warnings=[f"Unverified numbers: {missing}"], withheld_answer=answer,
        )  # fmt: skip
    if prefix:
        answer = f"{prefix}\n\n{answer}"
        warnings.insert(0, prefix)
    if used_similarity:
        answer = f"{answer}\n\n{guardrails.STANDING_CAVEAT}"
    if warnings:
        unique = list(dict.fromkeys(w for w in warnings if w != prefix))
        if unique:
            answer += "\n\nCaveats from the engine:\n" + "\n".join(f"- {w}" for w in unique)
    return reply(
        question, answer, refused=False, tool_calls=calls_made, sources=sources,
        warnings=list(dict.fromkeys(warnings)),
    )  # fmt: skip


__all__ = ["LOOP_GUARD", "MAX_TOOL_ROUNDS", "UNAVAILABLE", "VERIFY_FAILED", "ask", "log_path"]
