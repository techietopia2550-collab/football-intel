"""Build tests/fixtures/chatbot_test_set.json: the 60-question chatbot test set of
docs/RAG_DESIGN.md section 6 (Decision 4), with expected values frozen from the database.

Every golden value comes from the same tool the chatbot calls (`src.chat.dispatch.call_tool`),
so the test checks the chatbot against the engines, not against numbers typed by hand.

Entry fields: id, code (design id, e.g. F01), category, question, expected_tool (str or list,
any of which counts), expected_answer_pattern (regex, searched with IGNORECASE | DOTALL), and
optionally expected_refusal (the verdict whose verbatim text must be given) and
must_not_pattern (regex that must not match).

    python scripts/build_chatbot_test_set.py
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from sqlalchemy import text

from src.chat.dispatch import call_tool
from src.chat.guardrails import REFUSALS, apply_similarity_rule
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON, season_table_name
from src.utils.config import PROJECT_ROOT

OUT = PROJECT_ROOT / "tests" / "fixtures" / "chatbot_test_set.json"
SEASON = DEFAULT_SEASON
S08_SCAN_LIMIT = 600  # players scanned for a target whose best match is Weak


# ---- regex helpers ------------------------------------------------------------------------------


def num_rx(value: float, decimals: tuple[int, ...] = (0, 1, 2, 3)) -> str:
    """The value as the model may print it: any of the given roundings (trailing zeros optional)."""
    forms: set[str] = set()
    for d in decimals:
        s = f"{value:.{d}f}"
        forms.add(s)
        if "." in s:
            forms.add(s.rstrip("0").rstrip("."))
        if d == 0 and abs(value) >= 1000:
            forms.add(f"{value:,.0f}")
    alts = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    return rf"(?<![\d.])(?:{alts})(?![\d])"


def name_rx(name: str) -> str:
    return re.escape(name).replace("'", "['’]")


_METRIC_ALIASES = {"xa": "(?:xa|expected assists)", "xg": "(?:xg|expected goals)"}


def metric_rx(metric: str) -> str:
    """A metric as a label or a code: 'fouls_won_p90' ~ 'fouls won' / 'fouls_won_p90'."""
    base = re.sub(r"_(?:p90|pct)$", "", metric)
    words = [_METRIC_ALIASES.get(w, re.escape(w)) for w in base.split("_")]
    return r"[ _-]?".join(words)


def all_of(*patterns: str) -> str:
    return "".join(f"(?=.*?(?:{p}))" for p in patterns)


def any_of(*patterns: str) -> str:
    return "(?:" + "|".join(patterns) + ")"


def in_order(*patterns: str) -> str:
    return ".*?".join(f"(?:{p})" for p in patterns)


def refusal_rx(verdict: str) -> str:
    """The fixed start of a verbatim refusal (up to its first {slot})."""
    head = REFUSALS[verdict].split("{")[0][:60]
    return re.escape(head).replace("'", "['’]").replace(r"\ ", r"\s+")


# ---- tool reads ---------------------------------------------------------------------------------


def tool(name: str, **args: Any) -> dict[str, Any]:
    res = call_tool(name, args)
    if "error" in res and name not in ("find_players_by_profile", "find_similar"):
        raise RuntimeError(f"{name}({args}) -> {res}")
    return res


def top_names(res: dict[str, Any], key: str = "player_name", n: int = 3) -> list[str]:
    return [r[key] for r in res.get("similar", [])[:n]]


def profile_metrics(profile: dict[str, Any], section: str) -> list[dict[str, Any]]:
    return list(profile.get(section) or [])


def weak_target() -> tuple[str, float]:
    """A qualified player whose best match is Weak (0.45-0.60), for S08."""
    with get_engine().connect() as conn:
        names = [
            str(r[0])
            for r in conn.execute(
                text(
                    f"SELECT player_name FROM {season_table_name(SEASON)} "
                    "ORDER BY minutes_total DESC"
                )
            )
        ]
    for name in names[:S08_SCAN_LIMIT]:
        res = call_tool("find_similar", {"player_name": name, "n": 3})
        if "error" in res or not res.get("similar"):
            continue
        rule = apply_similarity_rule(res)
        if rule["verdict"] == "WARN":
            return name, float(res["similar"][0]["similarity"])
    raise RuntimeError("no Weak-only similarity target found")


# ---- the set ------------------------------------------------------------------------------------


def build() -> list[dict[str, Any]]:
    q: list[dict[str, Any]] = []

    def add(
        code: str,
        category: str,
        question: str,
        expected_tool: str | list[str] | None,
        pattern: str,
        **extra: Any,
    ) -> None:
        re.compile(pattern)
        q.append(
            {
                "id": len(q) + 1,
                "code": code,
                "category": category,
                "question": question,
                "expected_tool": expected_tool,
                "expected_answer_pattern": pattern,
                **extra,
            }
        )

    # -- factual (9)
    vardy = tool("player_stats", player="Jamie Vardy")
    ozil = tool("player_stats", player="Mesut Özil")
    suarez = tool("player_stats", player="Luis Suárez")
    kane = tool("player_stats", player="Harry Kane")
    busq = tool("player_stats", player="Sergio Busquets")
    mahrez = tool("player_stats", player="Riyad Mahrez")
    lei = tool("team_fingerprint", team="Leicester City")
    psg = tool("team_fingerprint", team="Paris Saint-Germain")
    kane_xg = next(s for s in kane["stats"] if s["metric"] == "xg_p90")
    add(
        "F01",
        "factual",
        "How many goals did Vardy score in 2015/16?",
        "player_stats",
        num_rx(vardy["season_counts"]["goals"], (0,)),
    )
    add(
        "F02",
        "factual",
        "How many assists did Özil get?",
        "player_stats",
        num_rx(ozil["season_counts"]["assists"], (0,)),
    )
    add(
        "F03",
        "factual",
        "Luis Suárez goals and assists?",
        "player_stats",
        all_of(
            num_rx(suarez["season_counts"]["goals"], (0,)),
            num_rx(suarez["season_counts"]["assists"], (0,)),
        ),
    )
    add(
        "F04",
        "factual",
        "What was Harry Kane's xG per 90?",
        "player_stats",
        num_rx(float(kane_xg.get("per90") or kane_xg["value"]), (2, 3)),
    )
    add(
        "F05",
        "factual",
        "How many minutes did Busquets play?",
        "player_stats",
        num_rx(float(busq["player"]["minutes_total"]), (0, 1)),
    )
    add(
        "F06",
        "factual",
        "Where did Leicester finish?",
        "team_fingerprint",
        all_of(r"\b(?:1st|first)\b", num_rx(lei["profile"]["points"], (0,))),
    )
    add(
        "F07",
        "factual",
        "How many points did PSG get?",
        "team_fingerprint",
        num_rx(psg["profile"]["points"], (0,)),
    )
    add(
        "F08",
        "factual",
        "Mahrez's goals?",
        "player_stats",
        num_rx(mahrez["season_counts"]["goals"], (0,)),
    )
    add(
        "F09",
        "factual",
        "Jack Wilshere's stats?",
        "player_stats",
        r"\b143\b",
        expected_refusal="BELOW_THRESHOLD",
    )

    # -- similarity (8)
    s_busq = tool("find_similar", player_name="Sergio Busquets", n=5)
    s_kante = tool(
        "find_similar", player_name="N'Golo Kanté", n=5, filters={"exclude_same_team": True}
    )
    s_kante_all = tool("find_similar", player_name="N'Golo Kanté", n=10)
    kante_team = s_kante_all["target_team"]
    leicester_rivals = [r["player_name"] for r in s_kante_all["similar"] if r["team"] == kante_team]
    s_ozil = tool("find_similar", player_name="Mesut Özil")
    s_payet = tool("find_similar", player_name="Dimitri Payet", n=4)
    t_lei = tool("find_similar_teams", team="Leicester City", n=5)
    s_suarez = tool("find_similar", player_name="Luis Suárez", n=1)
    s_buffon = tool("find_similar", player_name="Gianluigi Buffon", n=3)
    weak_name, _ = weak_target()
    tier = r"\d{1,3}\s?%\s*·\s*(?:Fair|Weak)"
    add(
        "S01",
        "similarity",
        "Find me a player like Busquets",
        "find_similar",
        all_of(in_order(*(name_rx(n) for n in top_names(s_busq))), tier),
    )
    add(
        "S02",
        "similarity",
        "Who plays like Kanté but not at Leicester?",
        "find_similar",
        name_rx(top_names(s_kante, n=1)[0]),
        must_not_pattern=any_of(*(name_rx(n) for n in leicester_rivals))
        if leicester_rivals
        else None,
    )
    add(
        "S03",
        "similarity",
        "Players similar to Özil",
        "find_similar",
        all_of(re.escape(s_ozil["archetype_label"]), tier),
    )
    add(
        "S04",
        "similarity",
        "3 players like Payet",
        "find_similar",
        in_order(*(name_rx(n) for n in top_names(s_payet, n=3))),
        must_not_pattern=name_rx(top_names(s_payet, n=4)[3]),
    )
    add(
        "S05",
        "similarity",
        "Teams that played like Leicester",
        "find_similar_teams",
        in_order(*(name_rx(n) for n in top_names(t_lei, key="team_name", n=3))),
    )
    shared = [m["metric"] for m in s_suarez["similar"][0]["shared_metrics"]]
    add(
        "S06",
        "similarity",
        "Who is closest to Luis Suárez and why?",
        "find_similar",
        all_of(name_rx(top_names(s_suarez, n=1)[0]), any_of(*(metric_rx(m) for m in shared))),
    )
    add(
        "S07",
        "similarity",
        "A goalkeeper like the Juventus keeper",
        "find_similar",
        name_rx(top_names(s_buffon, n=1)[0]),
    )
    add(
        "S08",
        "similarity",
        f"Find players similar to {weak_name}",
        "find_similar",
        r"^Weak matches only",
    )

    # -- profile (7)
    p_ozil = tool("profile", player_name="Mesut Özil")
    p_vardy = tool("profile", player_name="Jamie Vardy")
    p_kante = tool("profile", player_name="N'Golo Kanté")
    p_mahrez = tool("profile", player_name="Riyad Mahrez")
    p_gueye = tool("profile", player_name="Idrissa Gueye")
    p_busq = tool("profile", player_name="Sergio Busquets")
    arche = {
        n: tool("player_stats", player=n)["archetype"]["archetype_label"]
        for n in ("N'Golo Kanté", "Riyad Mahrez")
    }
    top3 = [m["metric"] for m in profile_metrics(p_ozil, "strengths")[:3]]
    add(
        "P01",
        "profile",
        "What are Özil's strengths?",
        "profile",
        all_of(*(metric_rx(m) for m in top3), r"\d(?:\.\d)?\s?th\b"),
    )
    weak = [m["metric"] for m in profile_metrics(p_vardy, "weaknesses")[:2]]
    add(
        "P02",
        "profile",
        "What is Vardy weak at?",
        "profile",
        all_of(*(metric_rx(m) for m in weak)) if weak else r"no metric|none",
    )
    add(
        "P03",
        "profile",
        "Describe Kanté",
        "profile",
        all_of(re.escape(arche["N'Golo Kanté"]), re.escape(p_kante["position_group_fine"])),
    )
    add(
        "P04",
        "profile",
        "What kind of player is Mahrez?",
        "profile",
        all_of(re.escape(p_mahrez["position_group_fine"]), re.escape(arche["Riyad Mahrez"])),
    )
    aerial = [
        m
        for sec in ("strengths", "weaknesses", "neutral")
        for m in profile_metrics(p_gueye, sec)
        if m["metric"].startswith("aerial")
    ]
    add(
        "P05",
        "profile",
        "Is Gueye good in the air?",
        "profile",
        all_of("aerial", any_of(*(num_rx(m["percentile"], (0, 1)) for m in aerial)))
        if aerial
        else r"not judged",
    )
    add(
        "P06",
        "profile",
        "Busquets strengths",
        "profile",
        all_of(*(metric_rx(m["metric"]) for m in profile_metrics(p_busq, "strengths")[:2])),
        must_not_pattern=r"goals?[ _]p90|goals per 90",
    )
    add(
        "P07",
        "profile",
        "Is Özil a leader?",
        None,
        all_of(refusal_rx("UNMEASURABLE"), "leadership"),
        expected_refusal="UNMEASURABLE",
    )

    # -- team style (7)
    barca = tool("team_fingerprint", team="Barcelona")
    atleti = tool("team_fingerprint", team="Atlético Madrid")
    juve = tool("team_fingerprint", team="Juventus")
    poss = tool("list_team_axis", axis="possession_pct", n=5)
    ars = tool("team_fingerprint", team="Arsenal")
    add(
        "T01",
        "team_style",
        "How did Leicester play in 2015/16?",
        "team_fingerprint",
        all_of(r"\b(?:1st|first)\b", num_rx(lei["profile"]["points"], (0,))),
    )
    add(
        "T02",
        "team_style",
        "Did Barcelona press high?",
        "team_fingerprint",
        num_rx(barca["profile"]["scores"]["ppda"], (0, 1)),
    )
    add(
        "T03",
        "team_style",
        "How direct were Atlético?",
        "team_fingerprint",
        num_rx(atleti["profile"]["scores"]["direct_speed"], (0, 1)),
    )
    add(
        "T04",
        "team_style",
        "Juventus key players and types",
        "team_fingerprint",
        all_of(*(name_rx(p["player_name"]) for p in juve["key_players"][:5])),
    )
    add(
        "T05",
        "team_style",
        "Which teams had the most possession?",
        "list_team_axis",
        in_order(*(name_rx(t["team_name"]) for t in poss["teams"][:5])),
    )
    sv = ars["squad_value"]
    add(
        "T06",
        "team_style",
        "Arsenal squad market value",
        "team_fingerprint",
        all_of(
            any_of(num_rx(sv["total_eur"] / 1e6, (0, 1, 2)), num_rx(sv["total_eur"], (0,))),
            num_rx(sv["n_valued"], (0,)),
        ),
    )
    add(
        "T07",
        "team_style",
        "Leicester wage bill",
        "team_fingerprint",
        r"no (?:\w+ ){0,2}wage|wage[^.]{0,60}(?:not available|no data|missing|unavailable|isn['’]t)"
        r"|no data",
    )

    # -- weakness / filter (7)
    def cond(metric: str, op: str, threshold: float) -> dict[str, Any]:
        return {"metric": metric, "op": op, "threshold": threshold}

    filters = [
        (
            "W01",
            "Who wins aerials but doesn't pass well?",
            None,
            None,
            [cond("aerials_won_p90", ">=", 80), cond("pass_completion_pct", "<=", 20)],
        ),
        (
            "W02",
            "CBs who pass well but tackle poorly",
            "CB",
            None,
            [cond("pass_completion_pct", ">=", 80), cond("tackle_win_pct", "<=", 20)],
        ),
        (
            "W03",
            "Wingers who dribble a lot but rarely create",
            "W",
            None,
            [cond("dribbles_p90", ">=", 80), cond("key_passes_p90", "<=", 20)],
        ),
        (
            "W04",
            "Strikers with high xG but poor shot accuracy",
            "ST",
            None,
            [cond("xg_p90", ">=", 80), cond("shot_accuracy", "<=", 20)],
        ),
        (
            "W05",
            "Midfielders top 20% interceptions, bottom 20% progressive passes",
            ["DM", "CM"],
            None,
            [cond("interceptions_p90", ">=", 80), cond("progressive_passes_p90", "<=", 20)],
        ),
        (
            "W06",
            "Premier League full-backs with high xA",
            "FB",
            ["ENG-Premier League"],
            [cond("xa_p90", ">=", 80)],
        ),
    ]
    for code, question, group, leagues, conds in filters:
        args: dict[str, Any] = {"conditions": conds}
        if group:
            args["position_group"] = group
        if leagues:
            args["leagues"] = leagues
        res = call_tool("find_players_by_profile", args)
        rows = res.get("players") or []
        pattern = name_rx(rows[0]["player_name"]) if rows else r"no (?:players?|one)|none"
        add(code, "filter", question, "find_players_by_profile", pattern)
    add(
        "W07",
        "filter",
        "Centre-backs who score a lot",
        "find_players_by_profile",
        r"not (?:judged|relevant|ranked|assessed|considered|used)|no (?:players?|one)|none",
    )

    # -- comparison (6)
    c_ozil_payet = tool("compare_players", a="Mesut Özil", b="Dimitri Payet")
    press = {
        n: next(
            s for s in tool("player_stats", player=n)["stats"] if s["metric"] == "pressures_p90"
        )
        for n in ("Jamie Vardy", "Harry Kane")
    }
    messi = tool("player_stats", player="Lionel Messi")["season_counts"]["goals"]
    ronaldo = tool("player_stats", player="Cristiano Ronaldo")["season_counts"]["goals"]
    add(
        "C01",
        "comparison",
        "Kanté vs Gueye",
        "compare_players",
        all_of(name_rx("N'Golo Kanté"), name_rx("Idrissa Gueye"), "percentile"),
    )
    add(
        "C02",
        "comparison",
        "Compare Özil and Payet",
        "compare_players",
        all_of(
            name_rx("Mesut Özil"),
            name_rx("Dimitri Payet"),
            r"position group|different position|\bAM\b[^.]{0,40}\bW\b|\bW\b[^.]{0,40}\bAM\b",
        ),
    )
    assert c_ozil_payet  # the cross-position note is checked in the answer, not frozen
    add(
        "C03",
        "comparison",
        "Who presses more, Vardy or Kane?",
        ["compare_players", "player_stats"],
        all_of(*(num_rx(float(p.get("per90") or p["value"]), (1, 2, 3)) for p in press.values())),
    )
    add(
        "C04",
        "comparison",
        "Busquets or Pogba for ball progression?",
        None,
        all_of("matches several", "Paul Pogba", "Florentin Pogba"),
        expected_refusal="AMBIGUOUS",
    )
    add(
        "C05",
        "comparison",
        "Messi vs Ronaldo goals",
        ["compare_players", "player_stats"],
        all_of(num_rx(messi, (0,)), num_rx(ronaldo, (0,))),
    )
    add(
        "C06",
        "comparison",
        "Leicester vs Tottenham style",
        "team_fingerprint",
        all_of("Leicester", "Tottenham"),
    )

    # -- explanation (6)
    e_kante = tool("explain_metric", metric="pressures_p90", player="N'Golo Kanté")
    kp = e_kante["player"]
    shared_b = [m["metric"] for m in s_busq["similar"][0]["shared_metrics"]]
    e_ozil = tool("explain_metric", metric="pass_completion_pct", player="Mesut Özil")
    add(
        "E01",
        "explanation",
        "Why is Kanté ranked high on pressing?",
        "explain_metric",
        all_of(
            num_rx(float(kp.get("per90") or kp["value"]), (1, 2, 3)),
            num_rx(float(kp["percentile"]), (0, 1)),
        ),
    )
    add(
        "E02",
        "explanation",
        f"Why is {top_names(s_busq, n=1)[0]} similar to Busquets?",
        "find_similar",
        any_of(*(metric_rx(m) for m in shared_b)),
    )
    add(
        "E03",
        "explanation",
        'What does "progressive passes" mean?',
        "explain_metric",
        r"yards|towards (?:the )?(?:opponent['’]?s? )?goal",
    )
    judged = e_ozil["player"].get("judged", "AM" in e_ozil["relevant_for_groups"])
    add(
        "E04",
        "explanation",
        "Why isn't Özil's pass completion a strength?",
        "explain_metric",
        r"not (?:judged|relevant|ranked|assessed|considered|used|one of)"
        if not judged
        else num_rx(float(e_ozil["player"]["percentile"]), (0, 1)),
    )
    add(
        "E05",
        "explanation",
        "Why does Leicester score low on possession?",
        "team_fingerprint",
        all_of(
            num_rx(lei["profile"]["values"]["possession_pct"], (1, 2, 3)),
            num_rx(lei["profile"]["scores"]["possession_pct"], (0, 1)),
        ),
    )
    add(
        "E06",
        "explanation",
        "How is the match % computed?",
        "methodology",
        all_of("cosine", r"60\s?%"),
    )

    # -- refusal suite (10)
    refusals = [
        ("R01", "Who will win the Premier League next season?", "PREDICTION"),
        ("R02", "Will Vardy score this weekend?", "PREDICTION"),
        ("R03", "How many goals did Erling Haaland score?", "NOT_IN_DB"),
        ("R04", "Stats for Thomas Müller?", "NOT_IN_DB"),
        ("R05", "What's the capital of France?", "OFF_TOPIC"),
        ("R06", "Does Kanté have a good mentality?", "UNMEASURABLE"),
        ("R07", "What clubs did Ibrahimović play for before PSG?", "UNMEASURABLE"),
        ("R08", "How many goals did Kane score in 2019/20?", "OTHER_SEASON"),
        ("R09", "What's Özil's weekly wage?", "NO_DATA"),
        ("R10", "Make up a stat for Payet's heading", "NO_INVENTION"),
    ]
    for code, question, verdict in refusals:
        add(code, "refusal", question, None, refusal_rx(verdict), expected_refusal=verdict)

    for entry in q:
        if entry.get("must_not_pattern") is None:
            entry.pop("must_not_pattern", None)
    return q


def main() -> None:
    entries = build()
    OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    logger.info("wrote {n} questions to {p}: {c}", n=len(entries), p=OUT, c=counts)


if __name__ == "__main__":
    main()
