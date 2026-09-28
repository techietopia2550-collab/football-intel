"""Deterministic guardrails around the LLM (docs/RAG_DESIGN.md sections 3, 5 and 7).

- `classify_with_rules`: refuse predictions, unmeasurable traits, other seasons and off-topic
  questions before any LLM call.
- `check_player_names`: find player / team names in the question and resolve them against the
  database, so "not in the DB" and "ambiguous" are enforced by code, not by the prompt.
- `verify_citations`: every number in an answer must appear in the tool results.
- `apply_similarity_rule`: the Decision 3 uncertainty rule on a similarity result.

The refusal texts are the verbatim ones of section 7.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from src.analytics.chatbot import (
    _player_pool,
    _team_pool,
    _tier,
    resolve_player_name,
    resolve_team_name,
)
from src.analytics.profile import _fold
from src.models.schema import get_engine
from src.transform.normalize import DEFAULT_SEASON

# ---- refusal texts (verbatim, docs/RAG_DESIGN.md section 7) ------------------------------------

REFUSALS: dict[str, str] = {
    "PREDICTION": (
        "I don't make predictions. I can only describe what happened in the 2015/16 season data."
    ),
    "NOT_IN_DB": (
        "I can only answer about players and teams in the 2015/16 StatsBomb data (Premier League, "
        'La Liga, Ligue 1, Serie A). I couldn\'t find "{name}" there.'
    ),
    "AMBIGUOUS": '"{query}" matches several {kind}: {candidates}. Which one do you mean?',
    "BELOW_THRESHOLD": (
        "{player} played {minutes} league minutes in 2015/16, below the 900-minute threshold, so "
        "there are no percentiles, profile or similarity scores for this player. Season totals "
        "are available."
    ),
    "OFF_TOPIC": "I can only answer football analytics questions about the 2015/16 StatsBomb data.",
    "UNMEASURABLE": (
        "The data doesn't measure {trait}. It contains on-pitch event data from 2015/16 only, so I "
        "can describe what a player did with the ball and without it, not {trait}."
    ),
    "OTHER_SEASON": "The data covers the 2015/16 season only, so I can't answer about {season}.",
    "NO_DATA": "The database has no data for that: {what_is_missing}.",
    "NO_INVENTION": "I only report numbers that are in the database, so I can't make one up.",
    "FALLBACK": "I can't answer that from the 2015/16 data.",
}


def refusal_text(verdict: str, **slots: Any) -> str:
    """The verbatim refusal for a verdict, with its {slots} filled."""
    return REFUSALS[verdict].format(**slots)


# ---- 1. rule-based pre-filter ------------------------------------------------------------------

_PREDICTION = re.compile(
    r"\bwill\s+(?:\w+\s+){0,3}?(?:win|score|finish|beat|lose|be relegated|get relegated|go down|"
    r"make it|succeed|become|improve|decline|sign|join|move|cost)\b"
    r"|\bgoing to (?:win|score|finish|beat|sign|join|move)\b"
    r"|\bnext (?:season|year|game|match|week|weekend|window)\b"
    r"|\b(?:predict\w*|forecast\w*|prediction|odds|bet|betting)\b"
    r"|\bchances? of (?:winning|scoring|being)\b|\bin the future\b"
    r"|\b(?:would|could) (?:\w+ ){0,2}(?:succeed|cope|adapt|thrive|flop) at\b",
    re.IGNORECASE,
)
_UNMEASURABLE: tuple[tuple[str, str], ...] = (
    (r"\bleader(?:ship)?\b|\bcaptain(?:cy)?\b", "leadership"),
    (r"\bmentality\b|\bmentally\b|\bmental\b|\bcomposure\b", "mentality"),
    (r"\bpersonality\b|\bcharacter\b|\battitude\b|\btemperament\b", "personality or character"),
    (r"\bwork ethic\b|\bprofessionalism\b|\bdedication\b", "work ethic"),
    (r"\binjur(?:y|ies|ed)\b|\bfitness record\b", "injuries"),
    (
        r"\bcareer\b|\btransfer history\b|\bclubs? (?:did|has|have) \w+(?: \w+)? play",
        "career history",
    ),
    (
        r"\bplay(?:ed)? for before\b|\bbefore (?:joining|signing)\b|\b(?:former|previous) clubs?\b",
        "career history",
    ),
    (r"\bhow old\b|\bage of\b|\bbirthday\b|\bborn\b", "age"),
    (r"\bintelligen(?:t|ce)\b|\bvision\b|\bdecision[- ]making\b", "intelligence or vision"),
)
_OTHER_SEASON_WORDS = re.compile(
    r"\b(?:this|current|last|previous|coming|upcoming) season\b|\bright now\b|\bnowadays\b"
    r"|\bcurrently\b|\btoday\b|\bthese days\b|\bat the moment\b",
    re.IGNORECASE,
)
_ALLOWED_SEASON = re.compile(r"\b(?:20)?15\s*[/-]\s*(?:20)?16\b")
_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")
_ALLOWED_YEARS = {"2015", "2016"}
# Words that make a question about football even with no name in it.
FOOTBALL_TERMS = frozenset(
    """
    football soccer player players team teams club clubs squad striker strikers forward forwards
    winger wingers midfielder midfielders defender defenders centre-back centre-backs fullback
    full-back full-backs goalkeeper goalkeepers keeper keepers goal goals assist assists xg xa
    shot shots pass passes passing tackle tackles pressing press pressure pressures aerial aerials
    dribble dribbles dribbling cross crosses crossing possession ppda league season match matches
    game games minutes percentile percentiles archetype archetypes metric metrics stat stats
    statistics similar similarity style formation budget market value transfer scout scouting
    premier liga ligue serie strengths weaknesses strength weakness profile compare comparison
    interceptions clearances recoveries carries progressive creator playmaker poacher defensive
    attacking composer fingerprint lineup xi statsbomb methodology
    """.split()
)


def _classify(question: str) -> tuple[str, dict[str, str]] | None:
    q = question.strip()
    if _PREDICTION.search(q):
        return "PREDICTION", {}
    for pattern, trait in _UNMEASURABLE:
        if re.search(pattern, q, re.IGNORECASE):
            return "UNMEASURABLE", {"trait": trait}
    stripped = _ALLOWED_SEASON.sub(" ", q)
    other_years = [y for y in _YEAR.findall(stripped) if y not in _ALLOWED_YEARS]
    if other_years:
        return "OTHER_SEASON", {"season": other_years[0]}
    word = _OTHER_SEASON_WORDS.search(q)
    if word:
        return "OTHER_SEASON", {"season": f"'{word.group(0)}'"}
    words = {w.strip("'?!.,:;()\"").casefold() for w in q.split()}
    if not words & FOOTBALL_TERMS and not _looks_like_football_names(question):
        return "OFF_TOPIC", {}
    return None


def _looks_like_football_names(question: str) -> bool:
    """A name the data knows (found or ambiguous), or an unknown multi-word name ("Thomas
    Müller"): the question is about people, so it is not off topic even with no football word."""
    return any(
        n["status"] != "not_found" or len(n["name"].split()) > 1
        for n in check_player_names(question)
    )


def classify_with_rules(question: str) -> str | None:
    """PREDICTION / OFF_TOPIC / OTHER_SEASON / UNMEASURABLE, or None to let the LLM handle it."""
    hit = _classify(question)
    return hit[0] if hit else None


def rule_refusal(question: str) -> tuple[str, str] | None:
    """(verdict, verbatim refusal text) when a rule fires, else None."""
    hit = _classify(question)
    return (hit[0], refusal_text(hit[0], **hit[1])) if hit else None


# ---- 2. name detection -------------------------------------------------------------------------

# Common English and football words that also occur inside player or team names: never read as a
# name on their own. Deliberately generous; a missed lowercase surname is still found by the LLM's
# tool call, which resolves names again.
STOPWORDS = (
    frozenset(
        """
    a about above after again against all also am an and any are as at be because been before
    being below best between both but by can could did do does doing down during each few find
    for from further get give good had has have having he her here hers him his how i if in into
    is it its just like me more most my no nor not now of off on once only or other our out over
    own same she should show so some such tell than that the their them then there these they
    this those through to too top under until up very vs versus was we were what when where which
    while who whom why will with would you your
    player players team teams club city united real sporting athletic olympique inter ac as fc
    league season goals goal young rose king best star black white green brown
    champions cup european europe world international national premier liga ligue serie
    strengths strength weaknesses weakness profile compare similar style stats played play plays
    good great high low much many more less better worse score scored scoring win won wins
    """.split()
    )
    | FOOTBALL_TERMS
)
STRONG_TIER = 3  # `chatbot._tier`: 3 = a whole name word, 4 = the full name
_TOKEN = re.compile(r"[^\W\d_](?:[\w'’.-]*[^\W_])?", re.UNICODE)


def _name_words(season: str = DEFAULT_SEASON) -> frozenset[str]:
    engine = get_engine()
    words: set[str] = set()
    for key in _player_pool(season, engine)["name_key"]:
        words.update(key.replace("'", " ").replace("-", " ").split())
    try:
        for key in _team_pool(season, engine)["name_key"]:
            words.update(key.replace("'", " ").replace("-", " ").split())
    except LookupError:
        pass
    return frozenset(w for w in words if len(w) >= 3 and w not in STOPWORDS)


_WORDS_CACHE: dict[str, frozenset[str]] = {}


def _known_words(season: str = DEFAULT_SEASON) -> frozenset[str]:
    if season not in _WORDS_CACHE:
        _WORDS_CACHE[season] = _name_words(season)
    return _WORDS_CACHE[season]


def _clean_token(tok: str) -> str:
    tok = re.sub(r"['’]s$", "", tok)  # possessive: Özil's -> Özil
    return tok.strip("'’.-")


def _is_candidate(tok: str, index: int, known: frozenset[str]) -> bool:
    folded = _fold(tok)
    if len(folded) < 3 or folded in STOPWORDS:
        return False
    parts = folded.replace("'", " ").replace("-", " ").split()
    if any(p in known for p in parts):
        return True
    # An unknown capitalised word mid-sentence is probably a name the data does not hold, unless it
    # is an acronym (PPDA, EPL).
    return index > 0 and tok[:1].isupper() and not (tok.isupper() and len(tok) <= 5)


def _candidate_spans(question: str, season: str = DEFAULT_SEASON) -> list[str]:
    """Runs of consecutive name-like tokens, e.g. ['Luis Suárez', 'Leicester City']."""
    known = _known_words(season)
    spans: list[str] = []
    current: list[str] = []
    for i, m in enumerate(_TOKEN.finditer(question)):
        tok = _clean_token(m.group(0))
        if tok and _is_candidate(tok, i, known):
            current.append(tok)
            continue
        if current:
            spans.append(" ".join(current))
            current = []
    if current:
        spans.append(" ".join(current))
    return spans


def _candidates_text(cands: Iterable[dict[str, Any]], name_key: str) -> list[str]:
    out = []
    for c in cands:
        extra = c.get("team") or c.get("league")
        out.append(f"{c[name_key]} ({extra})" if extra else str(c[name_key]))
    return out


def _strong(res: dict[str, Any], span: str, name_key: str) -> dict[str, Any]:
    """Keep a resolution only if the span matches a whole name word (or the full name): free text
    is not a search box, so "France" must not find "Francesco" by prefix."""
    if res.get("error") == "ambiguous":
        name = str(res["candidates"][0][name_key])
    elif "error" not in res:
        name = str(res[name_key])
    else:
        return res
    if _tier(_fold(name), _fold(span)) >= STRONG_TIER:
        return res
    return {"error": "not_found", "query": span, "suggestions": []}


def _resolve_span(span: str, season: str) -> dict[str, Any]:
    player = _strong(resolve_player_name(span, season), span, "player_name")
    team = _strong(resolve_team_name(span, season), span, "team_name")
    if "error" not in team and ("error" in player or _fold(span) == _fold(str(team["team_name"]))):
        return {"name": span, "status": "found", "kind": "team",
                "resolved": team["team_name"], "candidates": []}  # fmt: skip
    if "error" not in player:
        return {"name": span, "status": "found", "kind": "player",
                "resolved": player["player_name"], "candidates": []}  # fmt: skip
    if player["error"] == "ambiguous":
        return {"name": span, "status": "ambiguous", "kind": "player", "resolved": None,
                "candidates": _candidates_text(player["candidates"], "player_name")}  # fmt: skip
    if team.get("error") == "ambiguous":
        return {"name": span, "status": "ambiguous", "kind": "team", "resolved": None,
                "candidates": _candidates_text(team["candidates"], "team_name")}  # fmt: skip
    return {"name": span, "status": "not_found", "kind": None, "resolved": None,
            "candidates": player.get("suggestions", [])}  # fmt: skip


def check_player_names(question: str, season: str = DEFAULT_SEASON) -> list[dict[str, Any]]:
    """Every player or team name in the question, resolved: found / not_found / ambiguous."""
    out: list[dict[str, Any]] = []
    for span in _candidate_spans(question, season):
        res = _resolve_span(span, season)
        words = span.split()
        known = _known_words(season)
        kept = [w for w in words if any(p in known for p in _fold(w).replace("-", " ").split())]
        if res["status"] == "not_found" and 0 < len(kept) < len(words):
            # "Idrissa Gana Gueye": drop the words no name holds and retry; only a unique hit
            # counts, so "Thomas Müller" stays not found rather than "ambiguous Thomas".
            retry = _resolve_span(" ".join(kept), season)
            if retry["status"] == "found":
                res = {**retry, "name": span}
        out.append(res)
    return out


def name_refusal(names: list[dict[str, Any]]) -> tuple[str, str] | None:
    """NOT_IN_DB for the first unknown name, else AMBIGUOUS for the first ambiguous one."""
    for n in names:
        if n["status"] == "not_found":
            return "NOT_IN_DB", refusal_text("NOT_IN_DB", name=n["name"])
    for n in names:
        if n["status"] == "ambiguous":
            kind = "teams" if n["kind"] == "team" else "players"
            text_ = refusal_text(
                "AMBIGUOUS", query=n["name"], kind=kind, candidates=", ".join(n["candidates"])
            )
            return "AMBIGUOUS", text_
    return None


# ---- 3. citation check -------------------------------------------------------------------------

# Numbers that belong to the data's framing, not to any result: the season years, "per 90", the
# minutes threshold and the top of the percentile scale.
FRAMING_NUMBERS = frozenset({2015.0, 2016.0, 90.0, 900.0, 100.0})
_FRAMING_PATTERNS = re.compile(
    r"\b(?:20)?15\s*[/-]\s*(?:20)?16\b|\bper[- ]?90\b|\bp90\b|/\s?90\b|\b0\s*[-–]\s*100\b"
    r"|\b(?:top|bottom)\s+20\s*%|\b60\s*%\s*\+?\s*(?:\(?'?fair'?\)?)"
    r"|\b900[- ]minute",
    re.IGNORECASE,
)
_LIST_MARKER = re.compile(r"(?m)^\s*(?:\d+[.)]|#+)\s+")
# A number, with optional thousands commas, decimals, a money suffix (m / k / bn) or an ordinal.
_NUMBER = re.compile(
    r"(?<![\w.])(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(m|k|bn|st|nd|rd|th)?(?![\w])",
    re.IGNORECASE,
)
_NUMBER_IN_TEXT = re.compile(r"-?\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z_]+")
_CLAUSE_END = re.compile(r"[;!?\n]|\.(?!\d)")
_ZERO_WIDTH = re.compile("[​‌‍⁠﻿]")
_SPACED_THOUSANDS = re.compile(r"(?<![\d.])\d{1,3}(?:[   ]\d{3})+(?![\d])")
CONTEXT_WORDS = 3  # words on each side of a number that can tie it to a field
# A word next to a number (by prefix) -> substrings of the tool-result field paths it must come
# from. A number near one of these words is only checked against those fields, so "31 goals"
# cannot pass on some unrelated percentile of 31.
DOMAIN_KEYS: dict[str, tuple[str, ...]] = {
    # "expected goals / assists" are stored as xg / xa, so those words also allow xg / xa fields
    "goal": ("goal", "xg"), "assist": ("assist", "xa"), "expected": ("xg", "xa"),
    "minute": ("minute",),
    "game": ("games", "matches"), "appearance": ("games", "matches"),
    "point": ("points",), "percentile": ("percentile", "score"),
    "xg": ("xg",), "xa": ("xa",), "shot": ("shot",), "tackle": ("tackle",),
    "interception": ("interception",), "pressure": ("pressure",), "aerial": ("aerial",),
    "dribble": ("dribble",), "save": ("save",), "clean": ("clean_sheet",),
    "possession": ("possession",), "valued": ("value", "total_eur", "budget"),
    "value": ("value", "total_eur", "budget"), "worth": ("value", "total_eur", "budget"),
    "million": ("value", "total_eur", "budget"), "budget": ("budget", "value"),
}  # fmt: skip


def normalize_answer(answer: str) -> str:
    """Undo typography that splits numbers: zero-width characters, and thin / no-break spaces used
    as thousands separators ("2 455" -> "2455")."""
    text_ = _ZERO_WIDTH.sub("", answer)
    return _SPACED_THOUSANDS.sub(lambda m: re.sub(r"[    ]", "", m.group(0)), text_)


def _answer_numbers(answer: str) -> list[tuple[float, int, list[str]]]:
    """(value, decimals shown, nearby words) for every number in the answer, framing removed.
    A number's words stop at the neighbouring numbers and at clause ends, so in "key passes 4.26
    (99th percentile)" the word "percentile" belongs to 99, not to 4.26."""
    text_ = _LIST_MARKER.sub(" ", _FRAMING_PATTERNS.sub(" ", normalize_answer(answer)))
    matches = list(_NUMBER.finditer(text_))
    out = []
    for i, m in enumerate(matches):
        raw = m.group(1).replace(",", "")
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        value = float(raw)
        suffix = (m.group(2) or "").lower()
        if suffix == "bn":
            value, decimals = value * 1000, max(decimals - 3, 0)  # compared in millions
        left = matches[i - 1].end() if i else 0
        right = matches[i + 1].start() if i + 1 < len(matches) else len(text_)
        before_text = _CLAUSE_END.split(text_[left : m.start()])[-1]
        after_text = _CLAUSE_END.split(text_[m.end() : right])[0]
        before = _WORD.findall(before_text.lower())[-CONTEXT_WORDS:]
        after = _WORD.findall(after_text.lower())[:CONTEXT_WORDS]
        words = before + after + (["million"] if suffix in ("m", "bn") else [])
        out.append((value, decimals, words))
    return out


def _flatten(obj: Any, path: str, out: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """(field path, number) pairs; a row with a "metric" key adds the metric name to its path."""
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, int | float):
        out.append((path, float(obj)))
    elif isinstance(obj, str):
        out.extend((path, float(x)) for x in _NUMBER_IN_TEXT.findall(obj))
    elif isinstance(obj, dict):
        base = f"{path}.{obj['metric']}" if isinstance(obj.get("metric"), str) else path
        for k, v in obj.items():
            _flatten(v, f"{base}.{str(k).lower()}", out)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _flatten(v, path, out)
    return out


def _matches(value: float, decimals: int, pool: Iterable[float]) -> bool:
    tol = 0.5 * 10**-decimals + 1e-9
    for v in pool:
        # as stored, as a percentage of a 0-1 share, and as euros in millions / thousands
        for c in (v, v * 100, v / 1e6, v / 1e3):
            if abs(abs(c) - abs(value)) <= tol:
                return True
    return False


def _field_keys(words: list[str]) -> set[str]:
    keys: set[str] = set()
    for w in words:
        for stem, fields in DOMAIN_KEYS.items():
            if w.startswith(stem):
                keys.update(fields)
    return keys


def unverified_numbers(answer_text: str, tool_results: Any) -> list[float]:
    """The numbers in the answer that no tool result supports. A number next to a domain word
    ("goals", "percentile", "value"...) must match a field of that kind."""
    flat = _flatten(tool_results, "", [])
    everything = {v for _, v in flat}
    missing = []
    for value, decimals, words in _answer_numbers(answer_text):
        if value in FRAMING_NUMBERS:
            continue
        keys = _field_keys(words)
        pool = {v for p, v in flat if any(k in p for k in keys)} if keys else everything
        if not _matches(value, decimals, pool):
            missing.append(value)
    return missing


def verify_citations(answer_text: str, tool_results: Any) -> bool:
    """True when every number in the answer appears in the tool results (or is framing)."""
    return not unverified_numbers(answer_text, tool_results)


# ---- 4. similarity uncertainty rule (Decision 3) -----------------------------------------------

FAIR_CUT, REFUSE_CUT = 0.60, 0.45
WARN_SENTENCE = (
    "Weak matches only: the closest {kind} to {target} scores {score:.0%}, below the 60% 'Fair' "
    "level, so treat this list as loose leads, not look-alikes."
)
REFUSE_SENTENCE = (
    "No close matches for {target}: the best candidate scores only {score:.0%}, which is too low "
    "to call similar. Ask 'show the nearest anyway' to see them."
)
NO_CANDIDATES_SENTENCE = "No {kind}s are left to compare after the filters you asked for."
STANDING_CAVEAT = (
    "Similarity is a heuristic on 2015/16 event data: in testing, about half of Fair matches "
    "shared a role and style. It says nothing about ability, age, price or fit."
)


def apply_similarity_rule(similarity_result: dict[str, Any]) -> dict[str, Any]:
    """Tag, trim and judge a find_similar / find_similar_teams result (Decision 3)."""
    is_team = "team_name" in similarity_result and "target_name" not in similarity_result
    kind = "team" if is_team else "player"
    target = similarity_result.get("target_name") or similarity_result.get("team_name")
    rows = list(similarity_result.get("similar") or [])
    out: dict[str, Any] = {"verdict": None, "prefix": None, "refusal": None,
                           "caveat": STANDING_CAVEAT, "best_score": None,
                           "result": dict(similarity_result)}  # fmt: skip
    if not rows:
        out["verdict"] = "NO_CANDIDATES"
        out["refusal"] = NO_CANDIDATES_SENTENCE.format(kind=kind)
        return out
    best = max(float(r["similarity"]) for r in rows)
    out["best_score"] = best
    if best < REFUSE_CUT:
        out["verdict"] = "REFUSE"
        out["refusal"] = REFUSE_SENTENCE.format(target=target, score=best)
        out["result"]["similar"] = []
        return out
    kept = [
        {**r, "tier": "Fair" if float(r["similarity"]) >= FAIR_CUT else "Weak"}
        for r in rows
        if float(r["similarity"]) >= REFUSE_CUT
    ]
    out["result"]["similar"] = kept
    out["result"]["dropped_below_45"] = len(rows) - len(kept)
    if best < FAIR_CUT:
        out["verdict"] = "WARN"
        out["prefix"] = WARN_SENTENCE.format(kind=kind, target=target, score=best)
    else:
        out["verdict"] = "FAIR"
    return out


__all__ = [
    "REFUSALS", "STANDING_CAVEAT", "apply_similarity_rule", "check_player_names",
    "classify_with_rules", "name_refusal", "refusal_text", "rule_refusal", "unverified_numbers",
    "verify_citations",
]  # fmt: skip
