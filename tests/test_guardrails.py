"""Chatbot guardrails (`src/chat/guardrails.py`) and the system prompt (`src/chat/prompt.py`).

Rule, citation and similarity checks are pure. Name detection reads the real 2015/16 database and
is skipped when it is not built locally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect

from src.chat import guardrails as g
from src.chat.prompt import PLACEHOLDERS, SYSTEM_PROMPT_TEMPLATE, fill
from src.utils.config import PROJECT_ROOT, settings


def _real_db_ready() -> bool:
    if not settings.db_path.exists():
        return False
    return inspect(create_engine(f"sqlite:///{settings.db_path}")).has_table(
        "player_season_stats_2015_16"
    )


needs_db = pytest.mark.skipif(not _real_db_ready(), reason="2015/16 database not built locally")


# ---- 1. classify_with_rules --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "verdict"),
    [
        ("Who will win the 2016 Champions League?", "PREDICTION"),
        ("Will Vardy score this weekend?", "PREDICTION"),
        ("Who is going to win the league next season?", "PREDICTION"),
        ("Predict Arsenal's finish", "PREDICTION"),
        ("Is Kanté a leader?", "UNMEASURABLE"),
        ("Does Kanté have a good mentality?", "UNMEASURABLE"),
        ("What clubs did Ibrahimović play for before PSG?", "UNMEASURABLE"),
        ("How old is Messi?", "UNMEASURABLE"),
        ("How many goals did Kane score in 2019/20?", "OTHER_SEASON"),
        ("Best strikers in 2024", "OTHER_SEASON"),
        ("Who is the top scorer this season?", "OTHER_SEASON"),
    ],
)
def test_rules_refuse(question: str, verdict: str) -> None:
    assert g.classify_with_rules(question) == verdict


@pytest.mark.parametrize(
    "question",
    [
        "How many goals did the top striker score in 2015/16?",
        "Who wins aerials but doesn't pass well?",  # present tense: a filter, not a prediction
        "Which teams had the most possession?",
        "What does xG mean?",
        "Centre-backs who pass well in 2015-16",
    ],
)
def test_rules_let_football_questions_through(question: str) -> None:
    assert g.classify_with_rules(question) is None


def test_refusal_texts_are_verbatim() -> None:
    assert g.rule_refusal("Who will win the 2016 Champions League?") == (
        "PREDICTION",
        "I don't make predictions. I can only describe what happened in the 2015/16 season data.",
    )
    verdict, text = g.rule_refusal("Is Kanté a leader?") or ("", "")
    assert text.startswith("The data doesn't measure leadership.")
    assert "{" not in g.refusal_text("NOT_IN_DB", name="Haaland")


# ---- 2. check_player_names (real DB) -----------------------------------------------------------


@needs_db
@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("How many goals did Vardy score?", [("Vardy", "found", "Jamie Vardy")]),
        ("What are Özil's strengths?", [("Özil", "found", "Mesut Özil")]),
        ("find me a player like busquets", [("busquets", "found", "Sergio Busquets")]),
        ("How did Leicester play?", [("Leicester", "found", "Leicester City")]),
        ("Compare Luis Suárez and Messi",
         [("Luis Suárez", "found", "Luis Suárez"), ("Messi", "found", "Lionel Messi")]),
        ("Idrissa Gana Gueye stats", [("Idrissa Gana Gueye", "found", "Idrissa Gueye")]),
        ("What does xG mean?", []),
        ("Why is Kanté ranked high on PPDA?", [("Kanté", "found", "N'Golo Kanté")]),
    ],
)  # fmt: skip
def test_names_found(question: str, expected: list[tuple[str, str, str]]) -> None:
    got = [(n["name"], n["status"], n["resolved"]) for n in g.check_player_names(question)]
    assert got == expected


@needs_db
def test_names_ambiguous_and_unknown() -> None:
    pogba = g.check_player_names("Tell me about Pogba")
    assert pogba[0]["status"] == "ambiguous"
    assert pogba[0]["candidates"] == ["Paul Pogba (Juventus)", "Florentin Pogba (Saint-Étienne)"]
    madrid = g.check_player_names("How did Madrid play?")
    assert madrid[0]["status"] == "ambiguous" and madrid[0]["kind"] == "team"
    haaland = g.check_player_names("How many goals did Erling Haaland score?")
    assert [(n["name"], n["status"]) for n in haaland] == [("Erling Haaland", "not_found")]
    muller = g.check_player_names("Stats for Thomas Müller?")  # not "ambiguous Thomas"
    assert [(n["name"], n["status"]) for n in muller] == [("Thomas Müller", "not_found")]
    france = g.check_player_names("the capital of France")  # not "Francesco" by prefix
    assert all(n["status"] == "not_found" for n in france)


@needs_db
def test_name_refusal_texts() -> None:
    verdict, text = g.name_refusal(g.check_player_names("Tell me about Pogba")) or ("", "")
    assert verdict == "AMBIGUOUS"
    assert text == (
        '"Pogba" matches several players: Paul Pogba (Juventus), Florentin Pogba '
        "(Saint-Étienne). Which one do you mean?"
    )
    verdict, text = g.name_refusal(g.check_player_names("Goals for Erling Haaland")) or ("", "")
    assert verdict == "NOT_IN_DB" and '"Erling Haaland"' in text
    assert g.name_refusal(g.check_player_names("How many goals did Vardy score?")) is None


@needs_db
def test_off_topic_needs_no_football_word_and_no_known_name() -> None:
    assert g.classify_with_rules("What's the capital of France?") == "OFF_TOPIC"
    assert g.classify_with_rules("Write me a poem") == "OFF_TOPIC"
    assert g.classify_with_rules("Tell me about Pogba") is None  # a known name
    assert g.classify_with_rules("Tell me about Thomas Müller") is None  # a person: NOT_IN_DB


# ---- 3. verify_citations -----------------------------------------------------------------------

_RESULTS: list[Any] = [
    {"player": {"player_name": "Jamie Vardy", "minutes_total": 3108.26},
     "season_counts": {"goals": 24, "assists": 5},
     "market_value": {"market_value_eur": 12000000.0, "valuation_date": "2016-02-09"},
     "stats": [{"metric": "xg_p90", "per90": 0.587, "percentile": 97.4}]},
    {"similar": [{"player_name": "X", "similarity": 0.7234}]},
]  # fmt: skip


@pytest.mark.parametrize(
    "answer",
    [
        "Jamie Vardy scored 24 goals and 5 assists in 2015/16.",
        "He played 3,108 minutes (xG 0.59 per 90, 97th percentile).",
        "Market value: €12m, valued in February 2016.",
        "The closest match scores 72%.",
        "1. X — 72% · Fair\n2. Vardy: 24 goals",
        'Sources:\n- player_stats(player="Vardy") -> goals=24, minutes_total=3108.3',
    ],
)
def test_supported_numbers_pass(answer: str) -> None:
    assert g.verify_citations(answer, _RESULTS)


@pytest.mark.parametrize(
    "answer",
    ["Vardy scored 31 goals.", "He had 7 assists.", "xG of 0.71 per 90.", "Valued at €15m."],
)
def test_invented_numbers_fail(answer: str) -> None:
    assert not g.verify_citations(answer, _RESULTS)
    assert g.unverified_numbers(answer, _RESULTS)


def test_answer_without_numbers_passes() -> None:
    assert g.verify_citations("Özil is a chance creator.", [])


# ---- 4. apply_similarity_rule ------------------------------------------------------------------


def _sim(*scores: float) -> dict[str, Any]:
    return {"target_name": "Sergio Busquets",
            "similar": [{"player_name": f"P{i}", "similarity": s} for i, s in enumerate(scores)],
            "warnings": []}  # fmt: skip


def test_similarity_fair() -> None:
    out = g.apply_similarity_rule(_sim(0.81, 0.62, 0.55, 0.40))
    assert out["verdict"] == "FAIR" and out["prefix"] is None and out["refusal"] is None
    tiers = [r["tier"] for r in out["result"]["similar"]]
    assert tiers == ["Fair", "Fair", "Weak"]  # the 0.40 row is dropped
    assert out["result"]["dropped_below_45"] == 1


def test_similarity_warn_uses_the_exact_sentence() -> None:
    out = g.apply_similarity_rule(_sim(0.52, 0.47))
    assert out["verdict"] == "WARN"
    assert out["prefix"] == (
        "Weak matches only: the closest player to Sergio Busquets scores 52%, below the 60% "
        "'Fair' level, so treat this list as loose leads, not look-alikes."
    )
    assert len(out["result"]["similar"]) == 2


def test_similarity_refuse_below_45() -> None:
    out = g.apply_similarity_rule(_sim(0.44, 0.30))
    assert out["verdict"] == "REFUSE" and out["result"]["similar"] == []
    assert out["refusal"].startswith("No close matches for Sergio Busquets: the best candidate "
                                     "scores only 44%")  # fmt: skip


def test_similarity_edges_and_teams() -> None:
    assert g.apply_similarity_rule(_sim(0.60))["verdict"] == "FAIR"
    assert g.apply_similarity_rule(_sim(0.45))["verdict"] == "WARN"
    assert g.apply_similarity_rule(_sim())["verdict"] == "NO_CANDIDATES"
    teams = {"team_name": "Leicester City", "similar": [{"team_name": "T", "similarity": 0.5}]}
    assert "closest team to Leicester City" in g.apply_similarity_rule(teams)["prefix"]


# ---- system prompt -----------------------------------------------------------------------------


def test_prompt_template_matches_the_design_doc() -> None:
    doc = Path(PROJECT_ROOT / "docs" / "RAG_DESIGN.md").read_text(encoding="utf-8")
    block = doc.split("## 7. System prompt")[1].split("```text\n", 1)[1].split("\n```", 1)[0]
    assert SYSTEM_PROMPT_TEMPLATE == block


def test_prompt_fill_replaces_every_placeholder_and_keeps_refusal_slots() -> None:
    filled = fill(SYSTEM_PROMPT_TEMPLATE, {k: f"<{k}>" for k in PLACEHOLDERS})
    for key in PLACEHOLDERS:
        assert "{" + key + "}" not in filled and f"<{key}>" in filled
    assert "{name}" in filled and "{query}" in filled  # slots for the model stay


def test_context_words_stop_at_the_next_number() -> None:
    """Live false positive (2026-09-28): "percentile" after the next number pinned 4.256 to
    percentile fields only."""
    results = [{"strengths": [{"metric": "key_passes_p90", "value": 4.256, "percentile": 99.1}]}]
    answer = "- Key passes per 90: 4.256 (99th percentile)\n- Key passes: 4.26 (99.1 percentile)"
    assert g.verify_citations(answer, results)
    assert not g.verify_citations("Key passes: 4.256 (97th percentile)", results)


def test_thin_space_thousands_and_zero_width_characters() -> None:
    """Live false positive: '2\u202f455 minutes' was read as 2 and 455."""
    results = [{"similar": [{"player_name": "X", "minutes_total": 2455.3}]}]
    assert g.normalize_answer("2\u202f455 minutes") == "2455 minutes"
    assert g.verify_citations("X played 2\u202f455 minutes.", results)
    assert g.verify_citations("X played 2\u00a0455\u200b minutes.", results)
    assert not g.verify_citations("X played 2\u202f955 minutes.", results)


WITHHELD_OZIL = """Mesut Özil’s statistical strengths in 2015/16 were:

- Assists per 90 min: 0.569 (98.9th percentile among AMs)
- Expected assists per 90 min: 0.413 (98.9th percentile)
- Key passes per 90 min: 4.256 (98.9th percentile)
- Passes into the box per 90 min: 5.845 (98.9th percentile)
- Progressive passes per 90 min: 12.409 (96.6th percentile)
- Passes into the final third per 90 min: 6.264 (92.0th percentile)
- xG per shot: 0.122 (83.0th percentile)"""


@needs_db
def test_live_ozil_answer_passes() -> None:
    """Live false positive (2026-09-28): 'Expected assists ... 0.413' was only checked against
    `assist` fields, but xA is stored as xa_p90. The answer is correct and must pass."""
    from src.chat.dispatch import call_tool

    profile = call_tool("profile", {"player_name": "Mesut Özil"})
    assert g.verify_citations(WITHHELD_OZIL, [profile]), g.unverified_numbers(
        WITHHELD_OZIL, [profile]
    )
    assert not g.verify_citations(WITHHELD_OZIL.replace("0.413", "0.513"), [profile])


def test_expected_goals_and_assists_map_to_xg_xa() -> None:
    results = [{"stats": [{"metric": "xa_p90", "per90": 0.41}, {"metric": "xg_p90", "per90": 0.59},
                          {"metric": "assists_p90", "per90": 0.57}]}]  # fmt: skip
    assert g.verify_citations("Expected assists: 0.41 per 90; expected goals 0.59.", results)
    # 0.59 is the xG rate: next to "assists" it must match an assist or xA field, and does not
    assert not g.verify_citations("Assists: 0.59 per 90.", results)
