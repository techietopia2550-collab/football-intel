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


def _sim_pos(*rows: tuple[float, bool]) -> dict[str, Any]:
    return {"target_name": "Sergio Busquets", "warnings": [],
            "similar": [{"player_name": f"P{i}", "similarity": s, "position_match": m}
                        for i, (s, m) in enumerate(rows)]}  # fmt: skip


def test_similarity_splits_by_position_and_warns_on_a_cross_position_best() -> None:
    out = g.apply_similarity_rule(_sim_pos((0.81, False), (0.70, True), (0.50, True)))
    assert out["verdict"] == "FAIR" and out["position_warning"] == g.POSITION_SENTENCE
    assert out["prefix"] == g.POSITION_SENTENCE
    assert out["result"]["same_position_count"] == 2
    assert out["result"]["other_positions_count"] == 1
    assert [r["position_match"] for r in out["result"]["similar"]] == [False, True, True]


def test_similarity_same_position_best_adds_no_position_warning() -> None:
    out = g.apply_similarity_rule(_sim_pos((0.81, True), (0.70, False)))
    assert out["position_warning"] is None and out["prefix"] is None


def test_similarity_weak_and_cross_position_warnings_combine() -> None:
    out = g.apply_similarity_rule(_sim_pos((0.52, False)))
    assert out["verdict"] == "WARN"
    assert out["prefix"].startswith("Weak matches only")
    assert out["prefix"].endswith(g.POSITION_SENTENCE)


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


# The exact answer withheld live on 2026-09-28 (second Q3 run): 4.256, 5.845, 12.409 and 6.264 were
# flagged because in the Sources line "key_passes_p90=4.256, percentile=98.9" the word
# "percentile" tied 4.256 to percentile fields only.
WITHHELD_OZIL_SOURCES = (
    "Mesut Özil’s statistical strengths in 2015/16 were his creation and passing metrics, where he "
    "ranked at the very top of his position group.\n\n"
    "- Assists p90: 0.569 (98.9th percentile) – CREATION  \n"
    "- Expected assists p90 (xa p90): 0.413 (98.9th percentile) – CREATION  \n"
    "- Key passes p90: 4.256 (98.9th percentile) – CREATION  \n"
    "- Passes into box p90: 5.845 (98.9th percentile) – CREATION  \n"
    "- Progressive passes p90: 12.409 (96.6th percentile) – PASSING  \n"
    "- Passes into final third p90: 6.264 (92.0th percentile) – PASSING  \n"
    "- xG per shot: 0.122 (83.0th percentile) – ATTACKING  \n\n"
    "Sources:\n"
    '- profile(player_name="Mesut Özil") → assists_p90=0.569, percentile=98.9; xa_p90=0.413, '
    "percentile=98.9; key_passes_p90=4.256, percentile=98.9; passes_into_box_p90=5.845, "
    "percentile=98.9; progressive_passes_p90=12.409, percentile=96.6; "
    "passes_into_final_third_p90=6.264, percentile=92.0; xg_per_shot=0.122, percentile=83.0."
)


@needs_db
def test_live_ozil_answer_with_sources_passes() -> None:
    from src.chat.dispatch import call_tool

    profile = call_tool("profile", {"player_name": "Mesut Özil"})
    evidence = ["What are Özil's strengths?", {"player_name": "Mesut Özil"}, profile]
    assert g.unverified_numbers(WITHHELD_OZIL_SOURCES, evidence) == []
    # a wrong value next to its metric name is still caught, in the bullets and in the sources
    for good, bad in (("4.256 (", "4.556 ("), ("12.409,", "12.909,")):
        assert g.unverified_numbers(WITHHELD_OZIL_SOURCES.replace(good, bad), evidence)


def test_metric_label_ties_number_to_that_metric() -> None:
    results = [{"stats": [
        {"metric": "key_passes_p90", "value": 4.256, "percentile": 98.9},
        {"metric": "passes_into_box_p90", "value": 5.845, "percentile": 98.9},
        {"metric": "pass_completion_pct", "value": 90.69, "percentile": 40.0},
    ]}]  # fmt: skip
    # label then number, as prose, as "name=value", with "per 90 min", "%" and 0.5% rounding
    assert g.verify_citations("Key passes p90: 4.256", results)
    assert g.verify_citations("key_passes_p90=4.256, percentile=98.9", results)
    assert g.verify_citations("Passes into the box per 90 min: 5.85", results)
    assert g.verify_citations("Pass completion: 90.7%", results)
    # found but wrong: another metric's value next to this metric's name is blocked; its own
    # percentile quoted as the value is a percentile mismatch (a warning, not a block)
    assert g.unverified_numbers("Key passes p90: 5.845", results) == [5.845]
    assert g.check_citations("Key passes p90: 98.9", results) == {
        "missing": [], "percentile_mismatch": [("key_passes_p90", 98.9)]
    }  # fmt: skip
    # no metric name before the number: the old rule (any field) still applies
    assert g.verify_citations("He made 4.256 key passes per 90.", results)


# Withheld live on 2026-09-28 (third Q3 run): the Sources line used "<metric>_percentile=" labels,
# and 92.0 was read with the next pair's "xg_per_shot" and checked against xG fields only.
WITHHELD_OZIL_PERCENTILE_LABELS = (
    "Mesut Özil’s 2015/16 strengths were his elite creation and passing numbers, all ranking in "
    "the top percentiles of his position group.\n\n"
    "- assists p90 – 0.569 (98.9 percentile, CREATION)  \n"
    "- expected assists p90 – 0.413 (98.9 percentile, CREATION)  \n"
    "- key passes p90 – 4.256 (98.9 percentile, CREATION)  \n"
    "- passes into box p90 – 5.845 (98.9 percentile, CREATION)  \n"
    "- progressive passes p90 – 12.409 (96.6 percentile, PASSING)  \n"
    "- passes into final third p90 – 6.264 (92.0 percentile, PASSING)  \n"
    "- xG per shot – 0.122 (83.0 percentile, ATTACKING)  \n\n"
    "Sources:\n"
    '- profile(player_name="Mesut Özil") -> assists_p90=0.569, assists_p90_percentile=98.9, '
    "xa_p90=0.413, xa_p90_percentile=98.9, key_passes_p90=4.256, key_passes_p90_percentile=98.9, "
    "passes_into_box_p90=5.845, passes_into_box_p90_percentile=98.9, "
    "progressive_passes_p90=12.409, progressive_passes_p90_percentile=96.6, "
    "passes_into_final_third_p90=6.264, "
    "passes_into_final_third_p90_percentile=92.0, xg_per_shot=0.122, xg_per_shot_percentile=83.0"
)


@needs_db
def test_live_ozil_answer_with_percentile_labels_passes() -> None:
    from src.chat.dispatch import call_tool

    profile = call_tool("profile", {"player_name": "Mesut Özil"})
    evidence = ["What are Özil's strengths?", {"player_name": "Mesut Özil"}, profile]
    assert g.unverified_numbers(WITHHELD_OZIL_PERCENTILE_LABELS, evidence) == []
    wrong = WITHHELD_OZIL_PERCENTILE_LABELS.replace(
        "third_p90_percentile=92.0", "third_p90_percentile=93.0"
    )
    assert g.unverified_numbers(wrong, evidence) == [93.0]


def test_metric_percentile_label_ties_number_to_that_percentile() -> None:
    results = [{"stats": [
        {"metric": "key_passes_p90", "value": 4.256, "percentile": 98.9},
        {"metric": "xg_per_shot", "value": 0.122, "percentile": 83.0},
    ]}]  # fmt: skip
    assert g.verify_citations("key_passes_p90_percentile=98.9, xg_per_shot=0.122", results)
    assert g.verify_citations("Key passes percentile: 98.9", results)
    # the percentile of another metric, or the value, next to this metric's percentile label
    assert g.unverified_numbers("key_passes_p90_percentile=83.0", results) == [83.0]
    assert g.unverified_numbers("key_passes_p90_percentile=4.256", results) == [4.256]


# ---- citation check fixes from the 60-question run (2026-09-28) ---------------------------------

_PROFILE_ROWS = [{"strengths": [
    {"metric": "assists_p90", "value": 0.569, "percentile": 98.9, "score": 98.9},
    {"metric": "xg_per_shot", "value": 0.122, "percentile": 83.0, "score": 83.0},
], "weaknesses": [
    {"metric": "fouls_won_p90", "value": 0.984, "percentile": 6.7, "score": 6.7},
    {"metric": "counterpressures_p90", "value": 2.577, "percentile": 13.7, "score": 13.7},
]}]  # fmt: skip


def test_bug1_percentile_after_a_metric_name_is_checked_as_a_percentile() -> None:
    """Live P01: "assists p90 – 98.9th percentile" was read as assists_p90 = 98.9."""
    clean = {"missing": [], "percentile_mismatch": []}
    for text_ in (
        "- assists p90 – 98.9th percentile (CREATION)",
        "- xg_per_shot – 83.0 th percentile",
        "Assists p90: 98.9 percentile",
    ):
        assert g.check_citations(text_, _PROFILE_ROWS) == clean
    # a wrong percentile, or another metric's percentile, is still caught
    assert g.unverified_numbers("- assists p90 – 97.9th percentile", _PROFILE_ROWS) == [97.9]
    assert g.unverified_numbers("- assists p90 – 83.0th percentile", _PROFILE_ROWS) == [83.0]


def test_bug2_hyphen_and_space_variants_do_not_split_a_label() -> None:
    """Live E01: "Counter‑pressures p90: 5.891" matched pressures_p90."""
    results = [{"stats": [
        {"metric": "pressures_p90", "value": 815.0, "per90": 26.954, "percentile": 84.6},
        {"metric": "counterpressures_p90", "value": 198.0, "per90": 5.891, "percentile": 84.1},
        {"metric": "dribble_success_pct", "value": 58.7, "percentile": 62.2},
    ]}]  # fmt: skip
    for dash in ("‐", "‑", "–", "—", "-"):
        assert g.verify_citations(f"- Counter{dash}pressures p90: 5.891", results), dash
    assert g.verify_citations("Dribble‑success %: 58.7", results)
    assert g.verify_citations("Pressures p90: 26.954", results)
    assert g.unverified_numbers("- Counter‑pressures p90: 26.954", results) == [26.954]
    # the check reads hyphens as "-", but the answer shown to the user keeps its dashes
    assert g.normalize_answer("Stambouli – 93% · Fair") == "Stambouli – 93% · Fair"


def test_bug3_context_stays_in_its_own_phrase() -> None:
    """Live E02 / T01 / W02: a word from the next phrase pinned a number to the wrong fields."""
    shared = {"metric": "counterpressures_p90", "target_value": 5.946, "candidate_value": 6.308}
    sim = [{"similar": [{"player_name": "Benjamin Stambouli", "similarity": 0.9262,
                         "shared_metrics": [shared]}]}]  # fmt: skip
    assert g.verify_citations(
        "both produce high counter‑pressures p90, yielding a 93% cosine similarity", sim
    )
    scores = {"direct_speed": 94.9367088607595, "shots_pm": 78.481}
    team = [{"profile": {"values": {"width": 25.134, "direct_speed": 11.35}, "scores": scores}}]
    assert g.verify_citations(
        'team_fingerprint(team="Leicester City") -> scores.direct_speed=94.9367088607595, '
        "values.width=25.134",
        team,
    )
    # "rank)" closes 78.5's phrase: the "xG per shot" after it is not 78.5's context
    assert g.verify_citations("Shots per match (78.5 % rank) with xG per shot", team)
    rows = [{"players": [{"player_name": "Gerard Piqué", "metrics": {
        "pass_completion_pct": {"value": 91.683, "percentile": 99.0, "score": 99.0},
        "tackle_win_pct": {"value": 45.238, "percentile": 5.1, "score": 5.1}}}]},
        {"limit": 10}]  # fmt: skip
    line = "- Gerard Piqué – 99th percentile pass completion, 5th percentile tackle‑win"
    assert g.verify_citations(line, rows)
    assert g.verify_citations("find_players_by_profile(position_group=CB, limit=10)", rows)
    assert g.unverified_numbers(line.replace("5th", "25th"), rows) == [25.0]


# Live P02 answer, withheld on 2026-09-28: the bullets are right; the Sources line quotes the
# percentiles as the metrics' values (fouls_won_p90's value is 0.984, its percentile 6.7).
WITHHELD_VARDY = (
    "Vardy’s main weaknesses in 2015/16 were winning fouls and counter‑pressures.\n\n"
    "- fouls_won_p90 – 6.7 th percentile (very low)  \n"
    "- counterpressures_p90 – 13.7 th percentile (very low)\n\n"
    "Sources:\n"
    '- profile(player_name="Jamie Vardy") -> fouls_won_p90=6.7, counterpressures_p90=13.7'
)


def test_bug4_percentile_quoted_as_value_is_a_warning_not_a_block() -> None:
    assert g.check_citations(WITHHELD_VARDY, _PROFILE_ROWS) == {
        "missing": [],
        "percentile_mismatch": [("fouls_won_p90", 6.7), ("counterpressures_p90", 13.7)],
    }
    warning = g.percentile_mismatch_warning([("fouls_won_p90", 6.7)])
    assert "fouls_won_p90 = 6.7" in warning and "percentile" in warning
    # a number that is neither the value nor the percentile still blocks
    assert g.unverified_numbers(WITHHELD_VARDY.replace("=6.7", "=7.7"), _PROFILE_ROWS) == [7.7]


def test_player_pay_questions_get_no_data_before_the_llm() -> None:
    verdict, text_ = g.rule_refusal("What's Özil's weekly wage?") or ("", "")
    assert verdict == "NO_DATA" and text_.startswith("The database has no data for that:")
    assert g.rule_refusal("Kane salary")[0] == "NO_DATA"  # type: ignore[index]
    # a team's wage bill goes to the tools (team_fingerprint returns the wage context)
    assert g.rule_refusal("Leicester wage bill") is None
    assert g.rule_refusal("What was Arsenal's wage bill?") is None


def test_a_field_named_label_is_checked_against_that_field() -> None:
    """Live T05: "rank=2, team_name=Barcelona, value=66.204" read 2 as a market value."""
    result = {"teams": [{"rank": 1, "team_name": "Paris Saint-Germain", "value": 66.223},
                        {"rank": 2, "team_name": "Barcelona", "value": 66.204}]}  # fmt: skip
    text_ = (
        "Sources:\n- list_team_axis(axis=possession_pct) -> rank=2, team_name=Barcelona, "
        "value=66.204"
    )
    assert g.check_citations(text_, ["q", result])["missing"] == []
    assert g.check_citations("Barcelona 66.2 (rank 7)", ["q", result])["missing"] == [7.0]
