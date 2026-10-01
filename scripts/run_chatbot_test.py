"""Run the 60-question chatbot test set (tests/fixtures/chatbot_test_set.json) against the live
chatbot and write docs/chatbot_test_results.json and docs/CHATBOT_ACCURACY.md.

Each question goes through `src.chat.conversation.ask` exactly as the chat page sends it. Between
two questions at least PACE_SECONDS pass. Every Groq request is counted (429 retries included)
and the run stops before MAX_API_CALLS, or when Groq reports a daily limit; results are saved
after every question, so a stopped run keeps what it has and `--resume` continues it.

Order: the refusal suite first, then the 50 in-scope questions round-robin over the categories,
so a partial run still covers every category.

    python scripts/run_chatbot_test.py              # full run (resumes nothing)
    python scripts/run_chatbot_test.py --resume     # skip questions already in the results
    python scripts/run_chatbot_test.py --summary    # rewrite CHATBOT_ACCURACY.md only
    python scripts/run_chatbot_test.py --replay     # re-judge saved answers offline (below)

`--replay` makes no API call. It re-applies the current deterministic code to the saved run:
the pre-LLM rules and name check on each question, and the citation check on each answer the
model wrote, against its recorded tool calls re-run on the local database. What the LLM would
say now (prompt or tool-schema changes) cannot be replayed; those questions keep their live
answer. The live outcome stays in the `live_*` fields.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import UTC, datetime
from itertools import zip_longest
from typing import Any

from loguru import logger

from src.chat import guardrails
from src.chat.conversation import SIMILARITY_TOOLS, VERIFY_FAILED, ask
from src.chat.dispatch import call_tool
from src.chat.llm_client import GroqRateLimitError, client_from_settings
from src.chat.prompt import system_prompt
from src.utils.config import PROJECT_ROOT, settings

TEST_SET = PROJECT_ROOT / "tests" / "fixtures" / "chatbot_test_set.json"
RESULTS = PROJECT_ROOT / "docs" / "chatbot_test_results.json"
SUMMARY = PROJECT_ROOT / "docs" / "CHATBOT_ACCURACY.md"
MANUAL_MARKER = "<!-- manual review: kept when this file is regenerated -->"
PACE_SECONDS = 30.0  # minimum gap between two questions
MAX_API_CALLS = 120  # every Groq request, retries included
RATE_LIMIT_BACKOFF = (90.0, 180.0)  # waits before re-asking a question that hit the rate limit
FLAGS = re.IGNORECASE | re.DOTALL
_DAILY = re.compile(r"per day|\bTPD\b|\bRPD\b|daily", re.IGNORECASE)
_JSON_ARGS = re.compile(r"\w+\(\s*\{")
_ORDINAL_SPACE = re.compile(r"\d\s+(?:st|nd|rd|th)\b")
# Typography the model uses that would hide a name or number from a pattern: no-break and thin
# spaces ("Idrissa Gueye") and non-breaking hyphens.
_ODD_SPACES = re.compile("[     ]")
_ODD_HYPHENS = re.compile("[‐‑]")
_ORG_ID = re.compile(r"org_[A-Za-z0-9]+")


def redact(text_: str) -> str:
    """Drop the Groq organization id from error text before it is saved."""
    return _ORG_ID.sub("org_<redacted>", text_)


def normalize(text_: str) -> str:
    return _ODD_HYPHENS.sub("-", _ODD_SPACES.sub(" ", text_))


class StopRun(Exception):
    """The API budget is spent or Groq's daily limit was hit."""


class CountingClient:
    """The real Groq client with every request counted and its token usage recorded."""

    def __init__(self, calls_so_far: int, tokens_so_far: int) -> None:
        self.client = client_from_settings()
        self._chat = self.client.chat
        self.client.chat = self._counted  # type: ignore[method-assign]
        self.calls, self.tokens, self.rate_limited = calls_so_far, tokens_so_far, 0
        self.question_calls: list[dict[str, Any]] = []

    def _counted(
        self, messages: Any, tools: Any = None, model: Any = None, tool_choice: Any = None
    ) -> dict[str, Any]:
        if self.calls >= MAX_API_CALLS:
            raise StopRun(f"API budget of {MAX_API_CALLS} calls spent")
        self.calls += 1
        try:
            resp: dict[str, Any] = self._chat(
                messages, tools=tools, model=model, tool_choice=tool_choice
            )
        except GroqRateLimitError as exc:
            self.rate_limited += 1
            detail = redact(exc.detail or "")
            self.question_calls.append({"status": 429, "detail": detail})
            if _DAILY.search(detail):
                raise StopRun(f"Groq daily limit: {detail}") from exc
            raise
        usage = resp.get("usage") or {}
        self.tokens += int(usage.get("total_tokens") or 0)
        self.question_calls.append(
            {
                "status": 200,
                "tools_sent": bool(tools),
                "prompt_tokens": usage.get("prompt_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        )
        return resp


# ---- scoring ------------------------------------------------------------------------------------


def score(entry: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    """Pass / fail of one answer against its test entry (pure: no network)."""
    answer = normalize(out["answer"] or "")
    withheld = normalize(out.get("withheld_answer") or "")
    called = [c["name"] for c in out["tool_calls"]]
    pattern_ok = re.search(entry["expected_answer_pattern"], answer, FLAGS) is not None
    must_not = entry.get("must_not_pattern")
    forbidden = bool(must_not) and re.search(must_not, answer, FLAGS) is not None
    expected_refusal = entry.get("expected_refusal")
    if expected_refusal:
        correct = pattern_ok  # the verbatim refusal text, whoever produced it
    else:
        correct = not out["refused"] and pattern_ok and not forbidden
    want = entry.get("expected_tool")
    wanted = [want] if isinstance(want, str) else list(want or [])
    tool_ok = (not called) if not wanted else any(t in called for t in wanted)
    reached_check = bool(out["tool_calls"]) and (
        not out["refused"] or out["verdict"] == "UNVERIFIED"
    )
    # format checks look at the text the model wrote, shown or withheld
    body = answer if not out["refused"] else withheld
    return {
        "correct": correct,
        "pattern_matched": pattern_ok,
        "withheld_would_match": (
            re.search(entry["expected_answer_pattern"], withheld, FLAGS) is not None
            if withheld
            else None
        ),
        "must_not_matched": forbidden,
        "tool_ok": tool_ok,
        "reached_citation_check": reached_check,
        "verified": out["verdict"] != "UNVERIFIED" if reached_check else None,
        "sources_block": "Sources:" in body if reached_check and body else None,
        "sources_json_args": bool(_JSON_ARGS.search(body)) if body else None,
        "ordinal_space": bool(_ORDINAL_SPACE.search(body)) if body else None,
    }


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


# ---- run ----------------------------------------------------------------------------------------


def run_order(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refusals = [e for e in entries if e["category"] == "refusal"]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        if e["category"] != "refusal":
            by_cat.setdefault(e["category"], []).append(e)
    mixed = [e for row in zip_longest(*by_cat.values()) for e in row if e is not None]
    return refusals + mixed


def load_results() -> dict[str, Any]:
    if RESULTS.exists():
        data: dict[str, Any] = json.loads(RESULTS.read_text(encoding="utf-8"))
        return data
    return {"meta": {}, "results": []}


def save_results(data: dict[str, Any]) -> None:
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(resume: bool) -> None:
    entries = json.loads(TEST_SET.read_text(encoding="utf-8"))
    data = load_results() if resume else {"meta": {}, "results": []}
    done = {r["id"] for r in data["results"]}
    meta = data["meta"]
    meta.update(
        model=settings.groq_model,
        test_set=str(TEST_SET.relative_to(PROJECT_ROOT)),
        questions_in_set=len(entries),
        prompt_characters=len(system_prompt()),
        pace_seconds=PACE_SECONDS,
        max_api_calls=MAX_API_CALLS,
        started=meta.get("started") or datetime.now(UTC).isoformat(timespec="seconds"),
    )
    counter = CountingClient(meta.get("api_calls", 0), meta.get("tokens", 0))
    last_end: float | None = None
    stop_reason = None
    for entry in run_order(entries):
        if entry["id"] in done:
            continue
        for attempt in range(len(RATE_LIMIT_BACKOFF) + 1):
            if last_end is not None:
                gap = PACE_SECONDS - (time.monotonic() - last_end)
                if gap > 0:
                    time.sleep(gap)
            counter.question_calls = []
            t0 = time.monotonic()
            try:
                out = ask(entry["question"], client=counter.client)
            except StopRun as exc:
                stop_reason = str(exc)
                break
            finally:
                last_end = time.monotonic()
            if out["error"] == "rate_limited" and attempt < len(RATE_LIMIT_BACKOFF):
                logger.warning("rate limited on {c}; backing off", c=entry["code"])
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            break
        if stop_reason:
            logger.warning("stopping before {c}: {r}", c=entry["code"], r=stop_reason)
            break
        record = {
            "id": entry["id"],
            "code": entry["code"],
            "category": entry["category"],
            "question": entry["question"],
            "answer": out["answer"],
            "withheld_answer": out["withheld_answer"],
            "tool_calls": out["tool_calls"],
            "refused": out["refused"],
            "verdict": out["verdict"],
            "error": out["error"],
            "llm_calls": out["llm_calls"],
            "requests": counter.question_calls,
            "seconds": round(last_end - t0, 1),
            **score(entry, out),
        }
        data["results"].append(record)
        meta.update(
            api_calls=counter.calls,
            tokens=counter.tokens,
            rate_limited_responses=meta.get("rate_limited_responses", 0)
            + sum(1 for c in counter.question_calls if c["status"] == 429),
        )
        save_results(data)
        logger.info(
            "{c} {ok} ({s}s, {n} calls)",
            c=entry["code"],
            ok="PASS" if record["correct"] else "FAIL",
            s=record["seconds"],
            n=record["llm_calls"],
        )
    meta.update(
        finished=datetime.now(UTC).isoformat(timespec="seconds"),
        stop_reason=stop_reason,
        completed=len(data["results"]),
    )
    save_results(data)
    write_summary(data, entries)


# ---- offline replay -----------------------------------------------------------------------------

_LIVE_FIELDS = ("answer", "withheld_answer", "refused", "verdict", "error", "tool_calls",
                "llm_calls", "correct")  # fmt: skip


def _model_text(record: dict[str, Any]) -> str | None:
    """What the model wrote, before the code added the similarity caveat and engine caveats."""
    if record["live_verdict"] == "UNVERIFIED":
        return str(record["live_withheld_answer"])
    if record["live_refused"] or not record["live_tool_calls"]:
        return None
    text_ = str(record["live_answer"])
    for marker in (f"\n\n{guardrails.STANDING_CAVEAT}", "\n\nCaveats from the engine:"):
        text_ = text_.split(marker)[0]
    return text_


def _evidence(record: dict[str, Any]) -> list[Any]:
    """The citation check's evidence as `conversation.ask` built it: the question, then each
    call's arguments and (similarity-rule-tagged) result, re-run on the local database."""
    evidence: list[Any] = [record["question"]]
    for call in record["live_tool_calls"]:
        result = call_tool(call["name"], call["arguments"])
        if "error" not in result and call["name"] in SIMILARITY_TOOLS:
            rule = guardrails.apply_similarity_rule(result)
            result = {**rule["result"], "similarity_verdict": rule["verdict"]}
        evidence += [call["arguments"], result]
    return evidence


def replay_record(record: dict[str, Any]) -> dict[str, Any]:
    """The record re-judged by the current deterministic code (see the module docstring)."""
    if "live_answer" not in record:  # first replay: keep what happened live
        record.update({f"live_{k}": record[k] for k in _LIVE_FIELDS})
    base = {k: record[f"live_{k}"] for k in _LIVE_FIELDS if k != "correct"}
    out: dict[str, Any] = {**base, "citation_status": None, "citation_warnings": [],
                           "replay_note": None}  # fmt: skip
    question = record["question"]
    pre = guardrails.rule_refusal(question) or guardrails.name_refusal(
        guardrails.check_player_names(question)
    )
    live_pre_llm = (
        record["live_refused"] and record["live_llm_calls"] == 0 and record["live_error"] is None
    )
    if pre:
        verdict, text_ = pre
        if not (live_pre_llm and record["live_verdict"] == verdict):
            out["replay_note"] = f"now refused before the LLM ({verdict})"
        out.update(answer=text_, withheld_answer=None, refused=True, verdict=verdict, error=None,
                   tool_calls=[], llm_calls=0)  # fmt: skip
        return out
    if live_pre_llm:
        out["replay_note"] = "no longer refused before the LLM: needs a live run"
        return out
    written = _model_text(record)
    if written is None:
        if record["live_error"]:
            out["replay_note"] = f"live error ({record['live_error']}): needs a live run"
        return out
    check = guardrails.check_citations(written, _evidence(record))
    if check["missing"]:
        out["citation_status"] = "failed"
        out["citation_warnings"] = [f"Unverified numbers: {check['missing']}"]
        out.update(answer=VERIFY_FAILED, withheld_answer=written, refused=True,
                   verdict="UNVERIFIED")  # fmt: skip
    else:
        mismatch = check["percentile_mismatch"]
        out["citation_status"] = "percentile_mismatch" if mismatch else "passed"
        if mismatch:
            out["citation_warnings"] = [guardrails.percentile_mismatch_warning(mismatch)]
        if record["live_verdict"] == "UNVERIFIED":  # now shown: the text the model wrote
            out.update(answer=written, withheld_answer=None, refused=False, verdict=None)
    if out["verdict"] != record["live_verdict"]:
        out["replay_note"] = f"citation check: {record['live_verdict'] or 'shown'} -> " + (
            out["verdict"] or "shown"
        )
    return out


def replay(entries: list[dict[str, Any]]) -> None:
    data = load_results()
    by_id = {e["id"]: e for e in entries}
    for r in data["results"]:
        r.update(replay_record(r))
        r.update(score(by_id[r["id"]], r))
    data["meta"]["replayed"] = datetime.now(UTC).isoformat(timespec="seconds")
    save_results(data)
    write_summary(data, entries)


# ---- summary ------------------------------------------------------------------------------------


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%})" if n else "0/0"


def write_summary(data: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    res = data["results"]
    meta = data["meta"]
    cats = list(dict.fromkeys(e["category"] for e in entries))
    lines = [
        "# Chatbot accuracy (60-question test set)",
        "",
        "Generated by `scripts/run_chatbot_test.py` from `tests/fixtures/chatbot_test_set.json`"
        " (docs/RAG_DESIGN.md section 6, Decision 4).",
        "Raw answers: `docs/chatbot_test_results.json`. Failure diagnosis:"
        " ANALYTICS_DECISIONS.md section 31.",
        "",
        f"- Model: `{meta.get('model')}`; system prompt {meta.get('prompt_characters')} characters",
        f"- Run: {meta.get('started')} to {meta.get('finished')}; {len(res)} of "
        f"{len(entries)} questions answered"
        + (f"; **stopped early: {meta['stop_reason']}**" if meta.get("stop_reason") else ""),
        f"- One run per question (the design asks for 3; the API budget allows 1). Pacing: at least"
        f" {meta.get('pace_seconds', PACE_SECONDS):.0f} s between questions.",
        "",
        "## Accuracy per category",
        "",
        "| Category | Correct | Wilson 95% | Expected tool called | Refused |",
        "|---|---|---|---|---|",
    ]
    for cat in cats:
        rows = [r for r in res if r["category"] == cat]
        n, k = len(rows), sum(r["correct"] for r in rows)
        lo, hi = wilson(k, n)
        tool_k = sum(r["tool_ok"] for r in rows)
        lines.append(
            f"| {cat} | {_pct(k, n)} | {lo:.0%}-{hi:.0%} | {_pct(tool_k, n)} | "
            f"{sum(r['refused'] for r in rows)} |"
        )
    in_scope = [r for r in res if r["category"] != "refusal"]
    n_all, k_all = len(res), sum(r["correct"] for r in res)
    k_in = sum(r["correct"] for r in in_scope)
    lo, hi = wilson(k_all, n_all)
    entry_by_id = {e["id"]: e for e in entries}
    false_ref = [
        r for r in in_scope if r["refused"] and not entry_by_id[r["id"]].get("expected_refusal")
    ]
    refusal_rows = [r for r in res if r["category"] == "refusal"]
    refusals_ok = sum(r["correct"] for r in refusal_rows)
    checked = [r for r in res if r["reached_citation_check"]]
    with_body = [r for r in checked if r["sources_block"] is not None]
    answered = [r for r in res if r["sources_json_args"] is not None]
    json_args = sum(bool(r["sources_json_args"]) for r in answered)
    withheld_ok = [r["code"] for r in res if r["verified"] is False and r["withheld_would_match"]]
    secs = [r["seconds"] for r in res]
    llm_secs = [r["seconds"] for r in res if r["llm_calls"]]
    lines += [
        f"| **all** | **{_pct(k_all, n_all)}** | {lo:.0%}-{hi:.0%} | "
        f"{_pct(sum(r['tool_ok'] for r in res), n_all)} | {sum(r['refused'] for r in res)} |",
        "",
        "At 6-10 questions per category the Wilson intervals are wide; a category score is a"
        " signal, not a measurement.",
        "",
        "## Headline numbers",
        "",
        f"- Overall accuracy: {_pct(k_all, n_all)}; in-scope (the 50): {_pct(k_in, len(in_scope))}",
        f"- Refusal accuracy (verbatim refusal given): {_pct(refusals_ok, len(refusal_rows))}",
        f"- False refusals (in-scope questions refused): {len(false_ref)}"
        + (f" ({', '.join(r['code'] for r in false_ref)})" if false_ref else ""),
        f"- Citation verification rate (answers that reached the number check and passed):"
        f" {_pct(sum(bool(r['verified']) for r in checked), len(checked))}",
        f"- Withheld by the citation check although the expected content was in the answer:"
        f" {len(withheld_ok)}" + (f" ({', '.join(withheld_ok)})" if withheld_ok else ""),
        f"- Sources block present (answers written after a tool call, shown or withheld):"
        f" {_pct(sum(bool(r['sources_block']) for r in with_body), len(with_body))}",
        f"- Sources written with JSON arguments: {json_args} of {len(answered)} written answers;"
        f' ordinal with a stray space ("98.9 th"):'
        f" {sum(bool(r['ordinal_space']) for r in answered)}",
        f"- Average response time: {sum(secs) / len(secs):.1f} s over all questions"
        + (
            f", {sum(llm_secs) / len(llm_secs):.1f} s over the {len(llm_secs)} that called the LLM"
            if llm_secs
            else ""
        )
        if secs
        else "- Average response time: n/a",
        f"- API calls: {meta.get('api_calls', 0)} (budget {meta.get('max_api_calls')}); tokens:"
        f" {meta.get('tokens', 0):,}; HTTP 429 responses: {meta.get('rate_limited_responses', 0)}",
    ]
    if meta.get("replayed"):
        status = [r.get("citation_status") for r in res]
        mism = [r["code"] for r in res if r.get("citation_status") == "percentile_mismatch"]
        strict = sum(
            r["correct"] and r.get("citation_status") != "percentile_mismatch" for r in res
        )
        # rows run live after the replay have no live_* copy: their score is the live score
        replayed = [r for r in res if "live_correct" in r]
        now_pass = [r["code"] for r in replayed if r["correct"] and not r["live_correct"]]
        now_fail = [r["code"] for r in replayed if not r["correct"] and r["live_correct"]]
        lines += [
            "",
            f"## Offline replay ({meta['replayed']})",
            "",
            "The scores above are the saved live answers re-judged by the current deterministic"
            " code, with no API call: the pre-LLM rules and name check on every question, and the"
            " citation check on every answer the model wrote, against its recorded tool calls"
            " re-run on the local database. Prompt and tool-description changes made since the"
            " live run are **not** reflected: that needs a live run.",
            "",
            f"- Applies to the {len(replayed)} questions run before the replay; the other"
            f" {n_all - len(replayed)} were run live with the replayed code.",
            f"- Live score: {_pct(sum(r.get('live_correct', r['correct']) for r in res), n_all)};"
            f" replayed: {_pct(k_all, n_all)}",
            f"- Now pass: {', '.join(now_pass) or 'none'};"
            f" now fail: {', '.join(now_fail) or 'none'}",
            f"- Citation check on the {sum(s is not None for s in status)} answers that reached it:"
            f" passed {status.count('passed')},"
            f" percentile_mismatch {status.count('percentile_mismatch')}"
            f" ({', '.join(mism) or 'none'}), failed {status.count('failed')}",
            f"- Strict score (a percentile_mismatch answer counted as wrong):"
            f" {_pct(strict, n_all)}",
        ]
    lines += [
        "",
        "## Every question",
        "",
        "| Code | Category | Question | Result | Live | Citation check | Tools called | Calls |"
        " Seconds | Note |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(res, key=lambda r: r["id"]):
        note = []
        if r["refused"]:
            note.append(f"refused ({r['verdict'] or r['error']})")
        if r["verified"] is False:
            note.append(
                "citation check failed"
                + (
                    " (withheld answer had the expected content)"
                    if r["withheld_would_match"]
                    else ""
                )
            )
        if not r["pattern_matched"]:
            note.append("expected content missing")
        if r["must_not_matched"]:
            note.append("forbidden content present")
        if not r["tool_ok"]:
            note.append("expected tool not called")
        if r.get("replay_note"):
            note.append(r["replay_note"])
        note += r.get("citation_warnings") or []
        tools = ", ".join(c["name"] for c in r["tool_calls"]) or "-"
        live = r.get("live_correct", r["correct"])
        lines.append(
            f"| {r['code']} | {r['category']} | {r['question'].replace('|', '/')} | "
            f"{'PASS' if r['correct'] else 'FAIL'} | {'PASS' if live else 'FAIL'} | "
            f"{r.get('citation_status') or '-'} | {tools} | {r['llm_calls']} | {r['seconds']} |"
            f" {'; '.join(note)} |"
        )
    missing = [e for e in entries if e["id"] not in {r["id"] for r in res}]
    if missing:
        lines += ["", "## Not run", "", ", ".join(e["code"] for e in missing)]
    # a hand-written review below MANUAL_MARKER survives regeneration
    old = SUMMARY.read_text(encoding="utf-8") if SUMMARY.exists() else ""
    if MANUAL_MARKER in old:
        lines += ["", MANUAL_MARKER + old.split(MANUAL_MARKER, 1)[1].rstrip("\n")]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote {p}", p=SUMMARY)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--resume", action="store_true", help="skip questions already run")
    parser.add_argument("--summary", action="store_true", help="only rewrite the summary")
    parser.add_argument(
        "--rescore", action="store_true", help="re-score saved answers (no API calls), then summary"
    )
    parser.add_argument(
        "--replay", action="store_true", help="re-judge saved answers with current code, offline"
    )
    args = parser.parse_args()
    entries = json.loads(TEST_SET.read_text(encoding="utf-8"))
    if args.replay:
        replay(entries)
    elif args.rescore:
        data = load_results()
        by_id = {e["id"]: e for e in entries}
        for r in data["results"]:
            r.update(score(by_id[r["id"]], r))
        save_results(data)
        write_summary(data, entries)
    elif args.summary:
        write_summary(load_results(), entries)
    else:
        run(resume=args.resume)


if __name__ == "__main__":
    main()
