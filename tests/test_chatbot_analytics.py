"""Chatbot analytics (`src/analytics/chatbot.py`), name resolution, the tool registry, dispatch and
the Groq client.

The pure checks (argument validation, schemas, JSON safety, HTTP error mapping with a mocked
`requests.post`) always run. The rest read the real 2015/16 database and are skipped when it is not
built locally. No test calls Groq.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import numpy as np
import pytest
from sqlalchemy import create_engine, inspect

from src.analytics import chatbot
from src.chat import dispatch, llm_client, names
from src.chat.tools import TOOL_NAMES, TOOLS
from src.utils.config import settings


def _real_db_ready() -> bool:
    if not settings.db_path.exists():
        return False
    return inspect(create_engine(f"sqlite:///{settings.db_path}")).has_table(
        "player_season_stats_2015_16"
    )


needs_db = pytest.mark.skipif(not _real_db_ready(), reason="2015/16 database not built locally")


# ---- pure ---------------------------------------------------------------------------------------


def test_clean_makes_values_json_safe() -> None:
    raw = {"a": np.float64(1.5), "b": float("nan"), "c": (np.int64(2), date(2016, 2, 9))}
    out = chatbot.clean(raw)
    assert out == {"a": 1.5, "b": None, "c": [2, "2016-02-09"]}
    json.dumps(out)


def test_methodology_has_the_page_sections() -> None:
    m = chatbot.methodology()
    assert {"data_sources", "not_measured", "match_percent", "known_limitations"} <= set(m)
    assert all(isinstance(v, str) and v for v in m.values())
    assert "not measured" in m["not_measured"]
    m["data_sources"] = "changed"  # a copy: callers cannot edit the source text
    assert chatbot.methodology()["data_sources"] != "changed"


def test_unknown_metric_is_reported_with_suggestions() -> None:
    out = chatbot.find_players_by_profile("CB", [("aerials_won_pct", ">=", 80)])
    assert out["error"] == "unknown_metric"
    assert "aerial_win_pct" in out["suggestions"]


@pytest.mark.parametrize(
    "conditions",
    [[], [("tackles_p90", "=>", 80)], [("tackles_p90", ">=", 120)], [("tackles_p90",)]],
)
def test_bad_conditions_are_rejected(conditions: list[Any]) -> None:
    assert chatbot.find_players_by_profile("CB", conditions)["error"] == "bad_arguments"


def test_metric_not_judged_for_the_group_is_refused() -> None:
    out = chatbot.find_players_by_profile(
        "CB", [{"metric": "goals_p90", "op": ">=", "threshold": 80}]
    )
    assert out["error"] == "metric_not_judged"
    assert "CB" not in out["relevant_for_groups"]


def test_explain_metric_definition_without_a_player() -> None:
    out = chatbot.explain_metric("pass_completion_pct")
    assert out["category"] == "PASSING"
    assert out["higher_is_better"] is True
    assert out["formula"].startswith("passes_completed / passes_attempted x 100")
    assert "AM" not in out["relevant_for_groups"]  # style, not skill, for attackers
    assert chatbot.explain_metric("pressure_duration_avg")["direction"].startswith("descriptive")
    assert chatbot.explain_metric("nonsense_p90")["error"] == "unknown_metric"


def test_list_team_axis_rejects_bad_input() -> None:
    assert chatbot.list_team_axis("pace")["error"] == "unknown_axis"
    assert chatbot.list_team_axis("ppda", direction="middle")["error"] == "bad_arguments"


def test_tool_schemas_match_the_registry() -> None:
    assert len(TOOLS) == 16
    assert "resolve_player" not in TOOL_NAMES  # names are resolved in code
    assert TOOL_NAMES == list(dispatch.REGISTRY)
    for tool in TOOLS:
        fn = tool["function"]
        assert tool["type"] == "function" and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])
        json.dumps(tool)


def test_dispatch_rejects_unknown_tools_and_bad_arguments() -> None:
    assert dispatch.call_tool("predict_winner", {})["error"] == "unknown_tool"
    assert dispatch.call_tool("profile", {"wrong": 1})["error"] == "bad_arguments"
    assert dispatch.call_tool("profile", "not json")["error"] == "bad_arguments"
    assert dispatch.call_tool("methodology", '{"topic": "match_percent"}') == {
        "match_percent": chatbot.METHODOLOGY["match_percent"]
    }


class _Resp:
    def __init__(self, status: int, body: dict[str, Any] | None = None) -> None:
        self.status_code, self._body, self.text = status, body or {}, json.dumps(body or {})
        self.ok = 200 <= status < 300

    def json(self) -> dict[str, Any]:
        return self._body


@pytest.mark.parametrize(
    ("status", "exc", "message"),
    [
        (401, llm_client.GroqAuthError, llm_client.AUTH_MESSAGE),
        (429, llm_client.GroqRateLimitError, llm_client.RATE_LIMIT_MESSAGE),
        (500, llm_client.GroqError, "HTTP 500"),
    ],
)
def test_groq_http_errors_are_mapped(
    monkeypatch: pytest.MonkeyPatch, status: int, exc: type[Exception], message: str
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Resp:
        calls.append(kwargs)
        return _Resp(status)

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    with pytest.raises(exc, match=message):
        llm_client.GroqClient("k").chat([{"role": "user", "content": "hi"}])
    assert len(calls) == 1  # never retried


def test_groq_success_and_smoke_test(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> _Resp:
        sent.append(kwargs)
        return _Resp(200, body)

    monkeypatch.setattr(llm_client.requests, "post", fake_post)
    monkeypatch.setattr(llm_client.settings, "groq_api_key", "test-key")
    assert llm_client.smoke_test() == 0
    assert capsys.readouterr().out.strip() == "pong"
    assert sent[0]["headers"]["Authorization"] == "Bearer test-key"
    assert sent[0]["json"]["temperature"] == 0 and "tools" not in sent[0]["json"]


def test_smoke_test_without_a_key_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(llm_client.settings, "groq_api_key", "")
    assert llm_client.smoke_test() == 1
    assert llm_client.AUTH_MESSAGE in capsys.readouterr().out


# ---- real database ------------------------------------------------------------------------------


@needs_db
@pytest.mark.parametrize(
    ("query", "expected"),
    [("kante", "N'Golo Kanté"), ("KANTÉ", "N'Golo Kanté"), ("ozil", "Mesut Özil"),
     ("Luis Suarez", "Luis Suárez"), ("Vardy", "Jamie Vardy")],
)  # fmt: skip
def test_resolve_player_is_accent_and_case_safe(query: str, expected: str) -> None:
    hit = names.resolve_player(query)
    assert isinstance(hit, tuple) and hit[1] == expected and hit[0].startswith("statsbomb:")


@needs_db
def test_resolve_player_reports_ambiguity_and_unknowns() -> None:
    pogba = names.resolve_player("Pogba")
    assert isinstance(pogba, dict) and pogba["error"] == "ambiguous"
    assert {c["player_name"] for c in pogba["candidates"]} == {"Paul Pogba", "Florentin Pogba"}
    gueye = names.resolve_player("Gueye")  # Idrissa (qualified) and Babacar (under 900 minutes)
    assert isinstance(gueye, dict) and gueye["error"] == "ambiguous"
    haaland = names.resolve_player("Haaland")
    assert isinstance(haaland, dict) and haaland["error"] == "not_found"
    typo = names.resolve_player("Vardi")  # fuzzy only: suggested, never guessed
    assert isinstance(typo, dict) and typo["error"] == "not_found"
    assert "Jamie Vardy" in typo["suggestions"]


@needs_db
def test_resolve_team() -> None:
    assert names.resolve_team("leicester") == ("Leicester City", "Leicester City")
    assert names.resolve_team("atletico") == ("Atlético Madrid", "Atlético Madrid")
    madrid = names.resolve_team("Madrid")
    assert isinstance(madrid, dict) and madrid["error"] == "ambiguous"


@needs_db
def test_player_stats_real_player() -> None:
    s = chatbot.player_stats("Vardy", 2015)
    assert s["player"]["player_name"] == "Jamie Vardy"
    assert s["season_counts"]["goals"] == 24
    assert s["archetype"]["archetype_label"]
    assert s["market_value"]["market_value_eur"] > 0
    metrics = {r["metric"]: r for r in s["stats"]}
    assert "goals_p90" in metrics and 0 <= metrics["goals_p90"]["percentile"] <= 100
    assert s["strengths"] and all(i["score"] >= 80 for i in s["strengths"])
    json.dumps(s)


@needs_db
def test_player_stats_below_threshold_and_errors() -> None:
    w = chatbot.player_stats("Wilshere")
    assert w["player"]["qualified"] is False and w["stats"] == []
    assert w["season_counts"]["games"] >= 1 and w["warnings"]
    assert chatbot.player_stats("Nobody Anywhere")["error"] == "not_found"
    amb = chatbot.player_stats("Pogba")
    assert amb["error"] == "ambiguous" and len(amb["candidates"]) == 2


@needs_db
def test_compare_players_kante_gueye() -> None:
    assert chatbot.compare_players("Kanté", "Gueye")["error"] == "ambiguous"
    c = chatbot.compare_players("Kanté", "Idrissa Gueye")
    assert c["same_group"] is True and not c["notes"]
    assert len(c["metrics"]) >= 10
    for row in c["metrics"]:
        a, b = row["a"]["percentile"], row["b"]["percentile"]
        if a is not None and b is not None:
            assert math.isclose(row["percentile_delta"], round(a - b, 1))
    json.dumps(c)


@needs_db
def test_compare_players_across_groups_is_flagged() -> None:
    c = chatbot.compare_players("Özil", "Payet")  # AM vs W
    assert c["same_group"] is False and "Different position groups" in c["notes"][0]


@needs_db
def test_find_players_by_profile_weakness_query() -> None:
    conds = [("aerials_won_p90", ">=", 80), ("pass_completion_pct", "<=", 20)]
    out = chatbot.find_players_by_profile("CB", conds, limit=5)
    assert out["position_groups"] == ["CB"] and out["n_matching"] >= len(out["players"]) > 0
    for p in out["players"]:
        assert p["position_group_fine"] == "CB"
        assert p["metrics"]["aerials_won_p90"]["score"] >= 80
        assert p["metrics"]["pass_completion_pct"]["score"] <= 20
    margins = [p["margin"] for p in out["players"]]
    assert margins == sorted(margins, reverse=True)
    anywhere = chatbot.find_players_by_profile(None, conds)
    assert "CB" in anywhere["position_groups"] and "ST" not in anywhere["position_groups"]


@needs_db
def test_explain_metric_with_a_player() -> None:
    out = chatbot.explain_metric("pressures_p90", "Kanté")
    p = out["player"]
    assert p["player_name"] == "N'Golo Kanté" and p["judged_for_group"] is True
    assert 0 <= p["percentile"] <= 100 and p["sample_size"] > 0
    ozil = chatbot.explain_metric("pass_completion_pct", "Özil")["player"]
    assert ozil["judged_for_group"] is False  # not judged for AM
    assert chatbot.explain_metric("pressures_p90", "Pogba")["error"] == "ambiguous"


@needs_db
def test_list_team_axis_orders_by_display_score() -> None:
    top = chatbot.list_team_axis("ppda", "top", 5)
    assert len(top["teams"]) == 5 and top["teams_in_pool"] == 80
    scores = [t["score"] for t in top["teams"]]
    assert scores == sorted(scores, reverse=True)
    raw = [t["value"] for t in top["teams"]]
    assert raw == sorted(raw)  # PPDA is flipped: the hardest press has the lowest raw value
    bottom = chatbot.list_team_axis("possession_pct", "bottom", 3)
    assert [t["score"] for t in bottom["teams"]] == sorted(t["score"] for t in bottom["teams"])


_TOOL_CASES: dict[str, dict[str, Any]] = {
    "player_stats": {"player": "Vardy"},
    "profile": {"player_name": "Özil"},
    "find_similar": {"player_name": "Busquets", "n": 3},
    "compare_players": {"a": "Kanté", "b": "Idrissa Gueye"},
    "find_players_by_profile": {
        "conditions": [{"metric": "tackles_p90", "op": ">=", "threshold": 90}],
        "position_group": "DM",
        "limit": 3,
    },
    "explain_metric": {"metric": "pressures_p90", "player": "Kanté"},
    "team_fingerprint": {"team": "Leicester"},
    "list_teams": {"league": "ESP-La Liga"},
    "list_team_axis": {"axis": "possession_pct", "n": 3},
    "find_similar_teams": {"team": "Leicester", "n": 3},
    "build_squad": {"formation": "4-3-3", "budget_eur_m": 200, "style": "High-press direct"},
    "market_value": {"player": "Vardy"},
    "player_style_fit": {"player": "Kanté"},
    "archetypes": {"archetype": "Destroyer"},
    "player_games": {"player": "Vardy"},
    "methodology": {},
}


def test_every_tool_has_a_dispatch_case() -> None:
    assert list(_TOOL_CASES) == TOOL_NAMES


@needs_db
@pytest.mark.parametrize("name", TOOL_NAMES)
def test_dispatch_routes_every_tool(name: str) -> None:
    out = dispatch.call_tool(name, _TOOL_CASES[name])
    assert "error" not in out, out
    json.dumps(out)


@needs_db
def test_dispatch_tool_specific_results() -> None:
    fp = dispatch.call_tool("team_fingerprint", {"team": "leicester"})
    assert fp["profile"]["league_position"] == 1 and fp["profile"]["points"] == 81
    assert len(fp["key_players"]) == 5
    sq = dispatch.call_tool("build_squad", _TOOL_CASES["build_squad"])["squad"]
    assert len(sq["slots"]) == 11 and sq["total_value_eur"] <= 200e6
    sim = dispatch.call_tool("find_similar", {"player_name": "Busquets", "n": 3})
    assert sim["target_name"] == "Sergio Busquets" and len(sim["similar"]) == 3


@needs_db
@pytest.mark.parametrize(
    ("name", "args", "error"),
    [
        ("profile", {"player_name": "Pogba"}, "ambiguous"),
        ("player_stats", {"player": "Erling Haaland"}, "not_found"),
        ("find_similar", {"player_name": "Wilshere"}, "below_threshold"),
        ("team_fingerprint", {"team": "Bayern"}, "not_found"),
        ("build_squad", {"formation": "4-3-3", "budget_eur_m": 1, "style": "Balanced"},
         "infeasible_budget"),
        ("build_squad", {"formation": "4-3-3", "budget_eur_m": 100}, "bad_arguments"),
        ("archetypes", {"archetype": "Libero"}, "not_found"),
        ("methodology", {"topic": "tactics"}, "bad_arguments"),
    ],
)  # fmt: skip
def test_dispatch_errors_are_clean(name: str, args: dict[str, Any], error: str) -> None:
    assert dispatch.call_tool(name, args)["error"] == error
