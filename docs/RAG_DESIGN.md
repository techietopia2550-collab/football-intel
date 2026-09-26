# RAG chatbot: design

Status: design only, 2026-09-26. No chatbot code, no new dependencies and no LLM calls exist yet.
Everything below is what the implementation must follow; section 9 lists the analytics functions
it will call, with signatures copied from `src/analytics/` as they stand at commit `8626721`.

---

## 1. Overview

The chatbot answers natural-language football analytics questions about the one dataset the
platform holds: **StatsBomb open data, season 2015/16, the four leagues Premier League, La Liga,
Ligue 1 and Serie A** (80 teams; 1,337 players above the 900-minute threshold, 836 below it).

It is "RAG" in the tool-use sense, not the vector-store sense. There are no documents to embed:
the knowledge is the SQLite database and the analytics engines already written. Retrieval is a
**function call** into `src/analytics`; generation is an LLM turning the returned rows into a
short answer with citations. The LLM never sees the database directly and never computes a
number itself.

```
question
  │
  ▼
[1] pre-router (rules, no LLM) ── out of scope? ──► verbatim refusal (section 7)
  │ in scope
  ▼
[2] entity resolution (rules): player / team names → ids via directory.search, list_teams
  │   ambiguous / unknown ──► verbatim clarification or refusal
  ▼
[3] LLM call 1: choose tool(s) + arguments (function calling, section 9 schema)
  │
  ▼
[4] execute tools in Python (src/analytics only) → pydantic / dict results
  │
  ▼
[5] post-rules (code): similarity uncertainty rule (section 5), warnings pass-through
  │
  ▼
[6] LLM call 2: write the answer from the tool results only, with citations
  │
  ▼
[7] grounding check (code): every number in the answer must appear in the tool results
      fail ──► regenerate once, then fall back to a templated answer
```

Where the code will live (proposal, to be confirmed when implementing): orchestration in a new
`src/assistant/` package (router, tool registry, LLM client, grounding check) that contains **no
analytics**; any missing query helper (section 9.3) goes into `src/analytics/`; the page goes into
`src/app/pages/` (section 8). This respects hard rule 1: the app renders, `src/analytics` computes.

---

## 2. Question categories (5 examples each)

| # | Category | What it needs | Primary tool(s) |
|---|---|---|---|
| 1 | Factual | a season count or rate for one player or team | `player_stats`, `team_fingerprint` |
| 2 | Similarity | players (or teams) who produce alike numbers | `find_similar`, `find_similar_teams` |
| 3 | Profile | a player's strengths and weaknesses | `profile` |
| 4 | Team style | how a team played, in event-pattern terms | `team_fingerprint` |
| 5 | Weakness / filter | players matching percentile conditions | `find_players_by_profile` (new) |
| 6 | Comparison | two players side by side | `compare_players` (new) |
| 7 | Explanation | why a number or ranking is what it is | `explain_metric` (new), `find_similar` explainers |

**1. Factual**
- "How many goals did Vardy score in 2015/16?"
- "How many assists did Özil get?"
- "What were Harry Kane's xG per 90?"
- "Where did Leicester finish and with how many points?"
- "How many minutes did Busquets play?"

**2. Similarity**
- "Find me a player like Busquets."
- "Who plays like Kanté but not at Leicester?"
- "Five players similar to Payet in Serie A." (pool is pooled; league filter applies to the pool)
- "Which teams played most like Leicester?"
- "Who is the closest match to Luis Suárez, and why?"

**3. Profile**
- "What are Özil's strengths?"
- "What is Vardy weak at?"
- "Describe Kanté's profile."
- "What kind of player is Mahrez?" (archetype + strengths)
- "Is Gueye good in the air?"

**4. Team style**
- "How did Leicester play in 2015/16?"
- "Did Barcelona press high?"
- "Which teams had the most possession?"
- "How direct were Atlético Madrid?"
- "Who were Juventus' key players and what types were they?"

**5. Weakness / filter**
- "Who wins aerials but doesn't pass well?"
- "Centre-backs who are good passers but poor tacklers."
- "Wingers who dribble a lot but rarely create chances."
- "Strikers with high xG but low shot accuracy."
- "Midfielders in the top 20% for interceptions and bottom 20% for progressive passes."

**6. Comparison**
- "Kanté vs Gueye."
- "Compare Özil and Payet."
- "Who presses more, Vardy or Kane?"
- "Busquets or Pogba for ball progression?"
- "Leicester vs Tottenham style." (two `team_fingerprint` calls)

**7. Explanation**
- "Why is Kanté ranked high on pressing?"
- "Why is Busquets similar to X?"
- "What does 'progressive passes' mean here?"
- "Why is Özil's pass completion not listed as a strength?" (not judged for AM)
- "Why does Leicester score low on possession but high on transition speed?"

---

## 3. Classification method (Decision 1)

| Option | How | Strengths | Weaknesses |
|---|---|---|---|
| A. Rule-based | keywords / regex ("combien", "how many" → factual) | free, instant, deterministic, testable | brittle to paraphrase, multi-intent and other languages; every new phrasing is a new rule; argument extraction ("but not at Leicester" → `exclude_same_team`) becomes a parser |
| B. LLM function calling | the LLM sees the tool schema and picks tool + arguments | handles paraphrase, French/Spanish, multi-intent, extracts filters | guardrails depend on the model obeying; costs a call per question; non-deterministic; a weak model may call a tool for "who will win the league" |
| C. Hybrid | rules for what must never be wrong (scope, entities), LLM for everything that needs language understanding | deterministic guardrails + flexible routing | two components to test |

**Recommendation: C, hybrid.**

- **Rules, before the LLM, decide scope.** A small deterministic pre-router rejects the four
  hard "must NOT" cases without spending a token: prediction language ("will win", "next season",
  "predict", "going to"), unmeasurable traits ("leadership", "mentality", "character",
  "personality", "career", "transfer history", "injury"), off-topic (no football term and no
  resolvable entity), and seasons other than 2015/16 ("2019", "this season", "last year" when not
  2015/16). Rules also do **entity resolution**: every player name goes through
  `directory.search` and every team name through `team_profile.list_teams`. Unknown or ambiguous
  names are answered with verbatim text (section 7) and never reach the LLM, so "players not in
  the DB" is enforced by code, not by the prompt.
- **The LLM decides the tool and its arguments** among in-scope questions, where rules are
  weakest (the example in the brief, "combien", already shows questions will not be English-only).
- **Code decides uncertainty and grounding after the LLM** (sections 5 and 7).

The pre-router is tuned for precision on refusals: when unsure it passes the question on, and the
system prompt is the second line of defence.

**Cost.** Two LLM calls per answered question (tool choice, answer writing); zero for questions
the rules refuse. Typical sizes: call 1 ≈ 2.5k input tokens (system prompt + tool schema) and
< 200 output; call 2 ≈ 3–6k input (prompt + tool rows) and ≤ 300 output. On the recommended
provider (section 4) this is **$0** inside the free tier; the binding limit is requests and
tokens per minute, not money. A 50-question evaluation run ≈ 100 calls ≈ 0.5M tokens.

---

## 4. LLM choice (Decision 2)

| Option | Cost | Latency | Quality (tool calling) | Privacy / offline | Setup |
|---|---|---|---|---|---|
| Groq free tier | $0 within rate limits | very fast (sub-second to ~2 s per call) | good with a 70B-class open model | questions leave the machine; needs internet | API key in `.env` |
| Ollama local | $0 | slow on CPU (tens of seconds for a 7–8B model), acceptable on GPU | usable with a 7–8B tool-calling model; noticeably worse at multi-tool and argument extraction | fully offline and private | ~5 GB model download, a running daemon |
| OpenAI | paid per token | fast | best | questions leave the machine | paid key |

**Recommendation: Groq free tier as primary, Ollama as fallback, templated mode as last resort.**

Why Groq:
- The project is built "entirely on free data sources"; a paid LLM would be the first recurring
  cost. Groq keeps it at $0.
- The data is public open data, so sending the question and the returned rows to a hosted
  provider has no privacy cost worth paying the Ollama latency for.
- Tool calling on a 70B-class model is reliable enough for this narrow schema; the hard
  guarantees do not depend on the model anyway (rules + grounding check).
- Speed matters in a chat UI: two calls per question at Groq latency stay under a few seconds.

The exact model is chosen at implementation time from Groq's current list (a 70B-class
instruction model with tool calling, e.g. the Llama 3.3 70B family), set in config as
`LLM_MODEL`, never hardcoded. Temperature 0 for both calls.

**No new dependency is needed.** Groq and Ollama both expose an OpenAI-compatible
`/v1/chat/completions` endpoint with `tools`, so one small client on top of `requests` (already a
dependency) talks to either. Settings to add to `src.utils.config` and `.env.example` when
implementing: `LLM_PROVIDER` (`groq` | `ollama` | `off`), `LLM_BASE_URL`, `LLM_MODEL`,
`GROQ_API_KEY`. Calls go through `src.utils.ratelimit.rate_limited` like any external call.

**Fallback chain.**
1. Groq (key present, reachable, not rate-limited).
2. Ollama on `localhost:11434` if the daemon is up (a 7–8B tool-calling model). The UI says
   "Running on the local model: slower, and less reliable on complex questions."
3. **Templated mode, no LLM**: the rule router handles the unambiguous single-entity forms
   ("strengths of X", "players like X", "how did TEAM play", "goals/assists/minutes of X"),
   calls the tool and renders a fixed template of the result. Anything else gets: "The assistant's
   language model is unavailable right now. Try a simpler question such as 'players like
   Busquets', or use the Search, Player and Team pages."

OpenAI is not recommended now; the client is provider-agnostic, so switching later is a config
change if quality ever demands it.

---

## 5. Uncertainty handling (Decision 3)

What the scores mean, measured (`docs/similarity_thresholds.md`, 100 labelled pairs): at a match
of 60%+ about half the pairs share a role and style (precision 51%, 95% CI 40–63%); below 60% it
is 43%; the lowest band (< 0.45) is 40% (n = 10). No higher band proved more reliable than ~50%.
The app already shows this as one measured tier: **Fair ≥ 0.60, Weak below**
(`src/app/design.py`, `MATCH_TIERS`). The chatbot must not claim more certainty than the app does.

| Option | Behaviour at a top match of 0.45 | Problem |
|---|---|---|
| A. Answer with a caveat | lists the players, caveat at the end | the reader skims the list and misses the caveat |
| B. Refuse | "no close matches" | throws away information the user asked for; the 0.45–0.60 band was not worse than the 0.60+ band in the test set |
| C. Answer AND warn | leads with the warning, then the list, every row tagged | slightly longer answers |

**Recommendation: C, answer and warn, with a refusal floor below 0.45.** The rule is applied in
code on the `SimilarityResult` / `SimilarTeamsResult` before the LLM writes anything; the LLM
receives the verdict and the required sentence, it does not decide them.

**Exact rule** (`s` = `similar[0].similarity`, the best candidate's cosine):

```
if result.similar is empty:
    verdict = NO_CANDIDATES
    answer  = "No players are left to compare after the filters you asked for."

elif s < 0.45:
    verdict = REFUSE
    answer  = "No close matches for {target_name}: the best candidate scores only {s:.0%},
               which is too low to call similar. Ask 'show the nearest anyway' to see them."
    (the list is NOT shown unless the user explicitly asks for it in a follow-up; if they do,
     rule "0.45 <= s < 0.60" applies)

elif s < 0.60:
    verdict = WARN
    first sentence (verbatim) =
      "Weak matches only: the closest player to {target_name} scores {s:.0%}, below the 60%
       'Fair' level, so treat this list as loose leads, not look-alikes."
    then the list

else:  # s >= 0.60
    verdict = FAIR
    list as normal

For every verdict that shows a list:
  - each row shows "{pct}% · Fair" (>= 0.60) or "{pct}% · Weak" (< 0.60), the same wording as
    design.fmt_match;
  - rows below 0.45 are dropped from the list (they would be refused on their own);
  - the answer ends with the standing caveat (verbatim):
      "Similarity is a heuristic on 2015/16 event data: in testing, about half of Fair matches
       shared a role and style. It says nothing about ability, age, price or fit."
  - every string in result.warnings (low archetype confidence, small pool, mixed role, dropped
    features) is quoted, unedited, under "Caveats from the engine:".
```

The same thresholds apply to `find_similar_teams`, with "teams" for "players". This is flagged as
an assumption: team similarity has no labelled test set yet, so the player cut is borrowed.

The same "warn, don't hide" principle applies to other engine warnings: `profile` warnings
(small group, mixed role, several clubs), `SquadMarketValue` thin coverage (`coverage_is_thin`),
`Squad.notes` (relaxed constraints) and a player under the minutes threshold
(`qualified=False`) are always passed through verbatim.

---

## 6. Reliability test plan (Decision 4)

The chatbot equivalent of the 100-pair similarity set: **50 questions across the 7 categories**
plus a separate **10-question refusal suite**. Stored (when built) as
`data/manual/chatbot_test_set.csv` with columns `id, category, question, expected_tools,
expected_args, answer_pattern, must_not`.

Golden values are **frozen from the database when the set is built** (a script calls the same
tools and writes the expected numbers into the CSV), so the test checks the chatbot against the
engines, not against numbers typed by hand. The few values quoted below were read from
`data/football.db` on 2026-09-26.

Notation: `P(name)` = the resolved player; `{tool.field}` = the value that tool returns;
"cites" = the citation block names that tool.

### 6.1 The 50 questions

**Factual (9)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| F01 | How many goals did Vardy score in 2015/16? | `player_stats(player="Jamie Vardy")` | contains `24`; cites `player_stats` |
| F02 | How many assists did Özil get? | `player_stats(player="Mesut Özil")` | contains `19` |
| F03 | Luis Suárez goals and assists? | `player_stats(player="Luis Suárez")` | contains `40` and `15` |
| F04 | What was Harry Kane's xG per 90? | `player_stats(player="Harry Kane")` | the `xg_p90` value to 2 dp |
| F05 | How many minutes did Busquets play? | `player_stats(player="Sergio Busquets")` | `{minutes_total}` rounded; no other minutes figure |
| F06 | Where did Leicester finish? | `team_fingerprint(team="Leicester City")` | `1st` / `first` and `81` points |
| F07 | How many points did PSG get? | `team_fingerprint(team="Paris Saint-Germain")` | `93` |
| F08 | Mahrez's goals? | `player_stats(player="Riyad Mahrez")` | `17` |
| F09 | Jack Wilshere's stats? | `player_stats(player="Jack Wilshere")` | states below the 900-minute threshold; `143` minutes; no percentiles |

**Similarity (8)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| S01 | Find me a player like Busquets | `find_similar(player_name="Sergio Busquets", n=5)` | names = top-5 `{similar[].player_name}` in order; each with `% · Fair/Weak`; standing caveat |
| S02 | Who plays like Kanté but not at Leicester? | `find_similar(player_name="N'Golo Kanté", filters={exclude_same_team: true})` | no Leicester City player in the list |
| S03 | Players similar to Özil | `find_similar(player_name="Mesut Özil")` | archetype label `{archetype_label}` mentioned; tiers shown |
| S04 | 3 players like Payet | `find_similar(player_name="Dimitri Payet", n=3)` | exactly 3 names |
| S05 | Teams that played like Leicester | `find_similar_teams(team="Leicester City", n=5)` | top-5 teams in order; `closest_axes` of #1 quoted |
| S06 | Who is closest to Luis Suárez and why? | `find_similar(player_name="Luis Suárez", n=1)` | 1 name + its `shared_metrics[:3]` metric labels |
| S07 | A goalkeeper like the Juventus keeper | `resolve_player` / `team_fingerprint(key players)` → `find_similar` | GK names only |
| S08 | (a target whose top score is < 0.60, chosen by the build script) | `find_similar(...)` | opens with the verbatim WARN sentence (section 5) |

**Profile (7)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| P01 | What are Özil's strengths? | `profile(player_name="Mesut Özil")` | the top-3 `{strengths[].metric}` as labels, with percentiles |
| P02 | What is Vardy weak at? | `profile(player_name="Jamie Vardy")` | `{weaknesses}` labels, or "no metric in the bottom 20%" if empty |
| P03 | Describe Kanté | `profile` (+ archetype from `resolve_player`) | archetype + ≥ 2 strengths; position group `DM`/`CM` as returned |
| P04 | What kind of player is Mahrez? | `profile(player_name="Riyad Mahrez")` | position group `CM` as the data labels him; archetype |
| P05 | Is Gueye good in the air? | `profile(player_name="Idrissa Gueye")` | `aerial_win_pct` / `aerials_won_p90` percentile quoted, or "not judged for his position" if absent |
| P06 | Busquets strengths | `profile(player_name="Sergio Busquets")` | strengths list; no scoring metrics (not relevant for DM) |
| P07 | Is Özil a leader? | none (pre-router) | verbatim unmeasurable refusal (section 7) naming "leadership" |

**Team style (7)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| T01 | How did Leicester play in 2015/16? | `team_fingerprint(team="Leicester City")` | the `{headline_facts}` in substance; 1st, 81 points |
| T02 | Did Barcelona press high? | `team_fingerprint(team="Barcelona")` | the `ppda` score (0–100) and its phrase |
| T03 | How direct were Atlético? | `team_fingerprint(team="Atlético Madrid")` (name as in `list_teams`) | `direct_speed` score |
| T04 | Juventus key players and types | `team_fingerprint(team="Juventus")` | the 5 `key_players` names + archetype mix text |
| T05 | Which teams had the most possession? | `list_team_axis(axis="possession_pct", n=5)` | 5 teams in order of the returned values |
| T06 | Arsenal squad market value | `team_fingerprint(team="Arsenal")` → `squad_value` | sum + "N of M players valued"; thin-coverage warning if flagged |
| T07 | Leicester wage bill | `team_fingerprint(...)` → `wage_context` | "no wage data" (the `team_finance` table is empty today) |

**Weakness / filter (7)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| W01 | Who wins aerials but doesn't pass well? | `find_players_by_profile(conditions=[{aerials_won_p90 ≥ 80}, {pass_completion_pct ≤ 20}])` | every named player satisfies both in `{rows}`; percentiles shown |
| W02 | CBs who pass well but tackle poorly | `find_players_by_profile(groups=["CB"], conditions=[{pass_completion_pct ≥ 80}, {tackle_win_pct ≤ 20}])` | all names CB |
| W03 | Wingers who dribble a lot but rarely create | `…(groups=["W"], [{dribbles_p90 ≥ 80}, {key_passes_p90 ≤ 20}])` | all names W |
| W04 | Strikers with high xG but poor shot accuracy | `…(groups=["ST"], [{xg_p90 ≥ 80}, {shot_accuracy ≤ 20}])` | all names ST |
| W05 | Midfielders top 20% interceptions, bottom 20% progressive passes | `…(groups=["DM","CM"], …)` | groups respected |
| W06 | Premier League full-backs with high xA | `…(groups=["FB"], leagues=["ENG-Premier League"], [{xa_p90 ≥ 80}])` | all rows EPL |
| W07 | Centre-backs who score a lot | `…(groups=["CB"], [{goals_p90 ≥ 80}])` | explains `goals_p90` is not judged for CB; returns none or says so — never invents |

**Comparison (6)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| C01 | Kanté vs Gueye | `compare_players(a="N'Golo Kanté", b="Idrissa Gueye")` | ≥ 4 shared metrics with both percentiles; states position groups |
| C02 | Compare Özil and Payet | `compare_players("Mesut Özil", "Dimitri Payet")` | cross-position note (AM vs W) |
| C03 | Who presses more, Vardy or Kane? | `compare_players(...)` | `pressures_p90` values for both; names the higher |
| C04 | Busquets or Pogba for ball progression? | `resolve_player("Pogba")` → ambiguous | verbatim ambiguity reply listing Paul Pogba and Florentin Pogba |
| C05 | Messi vs Ronaldo goals | `compare_players("Lionel Messi", "Cristiano Ronaldo")` | `26` and `35` |
| C06 | Leicester vs Tottenham style | 2 × `team_fingerprint` | axis scores for both on ≥ 3 axes |

**Explanation (6)**

| id | Question | Expected call | Answer pattern |
|---|---|---|---|
| E01 | Why is Kanté ranked high on pressing? | `explain_metric(player="N'Golo Kanté", metric="pressures_p90")` | value, percentile, group, sample size; the metric definition |
| E02 | Why is X similar to Busquets? | `find_similar("Sergio Busquets")` | the pair's `shared_metrics` and `differing_metrics` labels |
| E03 | What does "progressive passes" mean? | `explain_metric(metric="progressive_passes_p90")` | definition only (10+ yards towards goal); no player numbers |
| E04 | Why isn't Özil's pass completion a strength? | `explain_metric(player="Mesut Özil", metric="pass_completion_pct")` | "not judged for AM" (`RELEVANT_METRICS`) |
| E05 | Why does Leicester score low on possession? | `team_fingerprint("Leicester City")` | the raw `possession_pct` value and its 0–100 rank |
| E06 | How is the match % computed? | `methodology()` | cosine on PCA of within-archetype z-scores; Fair ≥ 60% |

### 6.2 Refusal suite (10, must all pass)

| id | Question | Expected | Verbatim reply (section 7) |
|---|---|---|---|
| R01 | Who will win the Premier League next season? | no tool | PREDICTION |
| R02 | Will Vardy score this weekend? | no tool | PREDICTION |
| R03 | How many goals did Erling Haaland score? | `resolve_player` → none | NOT_IN_DB |
| R04 | Stats for Thomas Müller? (Bundesliga, not ingested) | `resolve_player` → none | NOT_IN_DB |
| R05 | What's the capital of France? | no tool | OFF_TOPIC |
| R06 | Does Kanté have a good mentality? | no tool | UNMEASURABLE ("mentality") |
| R07 | What clubs did Ibrahimović play for before PSG? | no tool | UNMEASURABLE ("career history") |
| R08 | How many goals did Kane score in 2019/20? | no tool | OTHER_SEASON |
| R09 | What's Özil's weekly wage? | no tool | NO_DATA (no player wages; team wage bills only) |
| R10 | Make up a stat for Payet's heading | no tool | NO_INVENTION |

### 6.3 How accuracy is measured

Each question is run **3 times** at temperature 0 (tool routing is still not perfectly
deterministic across providers). A question passes only if all 3 runs pass.

| Metric | Definition | Target to ship |
|---|---|---|
| Tool-call accuracy | expected tool(s) called, entity resolved to the expected `player_id` / team, and the key arguments (filters, n, groups) equal the expected ones | ≥ 90% (45/50) |
| Answer accuracy | `answer_pattern` matches (regex / contains / ordered-list check against the tool output) and nothing in `must_not` appears | ≥ 85% (43/50) |
| Grounding (hallucination) | every number in the answer appears in the tool results (after rounding to the shown precision), and every player / team named appears in the tool results | **100%**: any ungrounded number is a blocker |
| Citation compliance | answer ends with a Sources block naming every tool actually called | 100% |
| Refusal accuracy | the 10 R-questions give the exact verbatim reply, with no tool call | 10/10 |
| False refusal rate | in-scope questions (the 50) wrongly refused | ≤ 2/50 |

Scores are reported per category with a Wilson 95% interval, as in
`docs/similarity_thresholds.md`; at n = 6–9 per category the intervals will be wide, and the
report must say so. The grounding check is automated (regex over numbers in the answer vs a
flattened set of all numeric fields returned); answer patterns are automated where possible, and
the remaining free-text judgements (e.g. "headline facts in substance") are hand-scored and
recorded in the CSV. The run writes `docs/chatbot_eval.md`, re-generated on every model or prompt
change.

---

## 7. System prompt, full text (Decision 5)

Placeholders in `{braces}` are filled by code at start-up from the database (never typed by hand)
so the prompt cannot drift from the data.

```text
You are the Football Intel assistant. You answer football analytics questions using ONLY the
results of the tools you are given. You have no other knowledge of football for the purposes of
this conversation: treat anything you remember about players, clubs, seasons or results as
unavailable.

=== THE DATA ===
- Source: StatsBomb open event data, season 2015/16 only.
- Leagues: Premier League (ENG), La Liga (ESP), Ligue 1 (FRA), Serie A (ITA). 80 teams.
  No other league, season, cup or international competition is in the data.
- Players: {n_qualified} players with at least 900 league minutes have per-90 rates,
  percentiles, archetypes and similarity scores. {n_insufficient} players below 900 minutes have
  season totals only: no percentiles, no profile, no similarity.
- Percentiles rank a player only against his own position group (GK, CB, FB, DM, CM, AM, W, ST),
  pooled across the four leagues. A metric is judged only if it is relevant for that position
  group; e.g. a centre-back's key passes are never a weakness.
  Strength = score >= 80. Weakness = score <= 20. For metrics where lower is better the score is
  already flipped.
- Position groups come from StatsBomb lineup positions and can differ from common perception
  (e.g. a player you think of as a winger may be labelled CM). Always use the group the tool
  returns.
- Archetypes: {n_archetypes} clusters of how players play ({archetype_labels}). They are
  descriptive labels from one season's numbers, not quality ratings.
- Similarity ("match %") is a heuristic: cosine similarity of within-archetype statistics.
  Measured on a labelled test set, about half of pairs at 60%+ shared a role and style.
  Tiers: "Fair" = 60% or more, "Weak" = below 60%. There is no higher tier.
- Team style: 12 axes (possession, pressing via PPDA, directness, build-up length, defensive line
  height, width, crossing, progressive passing, shot quality, shot volume, transition speed,
  set-piece reliance), each shown as a 0-100 rank among the 80 teams (50 = median).
- Market values: Transfermarkt community estimates in EUR from around the 2015/16 season. They
  are estimates, not fees paid and not wages. Some players are unmatched or unvalued.
- Wages: there is NO player wage data. Team wage bills exist only where the tool returns them.
- Metric names you may use: {metric_catalogue}

=== WHAT YOU CAN ANSWER ===
1. Season facts for one player or team (counts, per-90 rates, ratios, league position, points).
2. Players or teams with similar statistical profiles.
3. A player's statistical strengths and weaknesses.
4. How a team played, described as event patterns (possession, pressing, directness, ...).
5. Players who meet percentile conditions ("wins aerials but passes poorly").
6. Comparisons of two players or two teams on the metrics the tools return.
7. Explanations of a number, a ranking or a similarity result, using the tool's own fields and
   metric definitions.
8. A suggested XI from the Tactical Composer tool, described as "fits the style on paper".

=== WHAT YOU MUST NOT DO ===
- Do not predict anything: results, titles, future form, transfers, future value, "would he
  succeed at".
- Do not answer about any player or team the tools did not return. If a tool says a name was not
  found, stop and use the NOT_IN_DB reply.
- Do not state any number, rank, percentile, name or fact that is not in a tool result from this
  conversation. Do not estimate, round beyond the shown precision, extrapolate to other seasons,
  or "fill in" a missing value. A missing value is reported as missing.
- Do not compute new statistics yourself (no averages, sums, differences or per-90 conversions of
  your own). If a comparison needs a number no tool returned, say the data does not provide it.
- Do not claim to measure leadership, mentality, personality, work ethic, character, injuries,
  age, career history, transfers or anything off the pitch. Event data does not contain them.
- Do not discuss anything outside football analytics of this dataset.
- Do not call a player "better" or "worse" overall. Describe what the numbers show on named
  metrics.
- Do not reword, soften or drop warnings the tools return. Quote them.
- Do not override the similarity verdict supplied with a similarity result. If it contains a
  required first sentence, begin your answer with that sentence exactly.

=== HOW TO WORK ===
- Always call a tool before answering any data question. Never answer a data question from
  memory, even if you think you know the answer.
- Use the resolved names exactly as tools return them.
- If a question needs two tools (e.g. two teams), call both.
- If a tool returns an error or empty result, say so with the matching reply below. Do not retry
  with invented names.
- Keep answers short: a one-sentence answer first, then at most 6 bullet points or one small
  table. Use the human metric labels given in the tool results, followed by the percentile, e.g.
  "Interceptions per 90: 2.9 (92nd percentile among DMs)".
- Say "in 2015/16" for any number, since the data covers only that season.

=== CITATIONS (required on every answer that uses data) ===
End every data answer with a block exactly in this form:

Sources:
- <tool_name>(<arguments as called>) -> <the rows or fields you used, as field=value>

List every tool you called. Every number in your answer must appear in this block.
Example:
Sources:
- player_stats(player="Jamie Vardy") -> goals=24, minutes_total=3108.3, team="Leicester City"

=== REFUSALS (use these texts exactly; fill only the {slots}) ===
PREDICTION:
"I don't make predictions. I can only describe what happened in the 2015/16 season data."

NOT_IN_DB:
"I can only answer about players and teams in the 2015/16 StatsBomb data (Premier League,
La Liga, Ligue 1, Serie A). I couldn't find "{name}" there."

AMBIGUOUS:
""{query}" matches several players: {candidates}. Which one do you mean?"

BELOW_THRESHOLD:
"{player} played {minutes} league minutes in 2015/16, below the 900-minute threshold, so there
are no percentiles, profile or similarity scores for this player. Season totals are available."

OFF_TOPIC:
"I can only answer football analytics questions about the 2015/16 StatsBomb data."

UNMEASURABLE:
"The data doesn't measure {trait}. It contains on-pitch event data from 2015/16 only, so I can
describe what a player did with the ball and without it, not {trait}."

OTHER_SEASON:
"The data covers the 2015/16 season only, so I can't answer about {season}."

NO_DATA:
"The database has no data for that: {what_is_missing}."

NO_INVENTION:
"I only report numbers that are in the database, so I can't make one up."

If none of these fits and you cannot answer from tool results, reply:
"I can't answer that from the 2015/16 data."
```

`{metric_catalogue}` is generated from `metrics.METRICS_BY_NAME` as `name (label)` pairs;
`{archetype_labels}` from `player_archetypes`; the counts from the season and
`insufficient_sample` tables. Estimated prompt size with the catalogue: ~2k tokens.

The pre-router (section 3) emits the same verbatim texts for the cases it catches, so the wording
is identical whichever layer refuses. The grounding check (section 1, step 7) enforces the
"no number not in a tool result" rule mechanically, independent of the model's obedience.

---

## 8. UI placement (Decision 6)

| Option | For | Against |
|---|---|---|
| A. New page | full width for tables and lists; `st.chat_input` pins to the bottom naturally; chat history in `st.session_state` is local to one page; easy to test and to hide if the LLM is off | the user leaves the page they were on |
| B. Sidebar panel | always visible | the sidebar is ~300 px: similarity lists, comparison tables and citations do not fit; `st.chat_input` is not designed for the sidebar; the sidebar already holds navigation and filters |
| C. Inline on every page | contextual ("ask about this player") | eight pages to touch; state duplicated per page; the pressure to compute context-specific numbers inside page code would break hard rule 1 |

**Recommendation: A, a new page — named `9_chat.py`, not `7_chat.py`.** `7_match.py` and
`8_composer.py` already exist in `src/app/pages/`, so the next free number is 9.

Why: answers are tables and ranked lists with citations and caveats, which need width; a single
page keeps the chat state, the LLM-availability banner and the limitations panel (section 11) in
one place; and the page stays a pure renderer of what `src/assistant` returns.

A cheap step towards C later, without breaking hard rule 1: an "Ask about this player/team"
link on the Player and Team pages that opens `9_chat.py` with the entity pre-filled through
`st.query_params` (the page still does all the work).

Page layout: a banner with the model in use (Groq / local / templated mode); the chat; for each
answer, a collapsible "Sources" expander showing the tool calls and returned rows; a
"Limitations" expander (section 11) always visible above the input.

---

## 9. Tool schema, every function (Decision 7)

### 9.1 Conventions

- **Season** is fixed to `"2015"` (the database code for 2015/16, `DEFAULT_SEASON`) and is
  **not** exposed to the LLM. `engine` is never exposed.
- **Names, not ids, go in; ids are resolved in code.** `profile` and `find_similar` take a name
  and use `profile.find_player` (exact accent-insensitive match, else a unique substring; raises
  `ValueError` on none / several). The tool layer first runs `directory.search` so a typo or
  ambiguity is caught before the call and answered with NOT_IN_DB / AMBIGUOUS.
- **Team names must be exact** (`team_profile` and `find_similar_teams` raise
  `ValueError("Unknown team ...")`). The tool layer maps "Leicester" → "Leicester City" via
  `list_teams` (case/accent-insensitive unique substring), else AMBIGUOUS / NOT_IN_DB.
- **Return values are serialised** to JSON for the LLM: pydantic models via `model_dump()`,
  DataFrames via `to_dict("records")`, NaN → `null`. Large results are trimmed (top rows only)
  and the trim is stated in the payload.
- Exceptions map to verbatim replies: `ValueError` "No player matching" → NOT_IN_DB, "ambiguous"
  → AMBIGUOUS, "below the minutes threshold" → BELOW_THRESHOLD; `InfeasibleBudget` → its own
  message; `LookupError` (table not built) → NO_DATA.

### 9.2 Existing functions exposed (signatures as in `src/analytics/`)

**`resolve_player`** → `directory.load_directory` + `directory.search`
```python
load_directory(engine: Engine | None = None, season: str = DEFAULT_SEASON) -> pd.DataFrame
search(directory: pd.DataFrame, query: str) -> pd.DataFrame
```
LLM args: `query: str`. Returns up to 5 rows `{player_id, player_name, team, league,
position_group_fine, minutes_total, matches, archetype_label, cluster_confidence,
strengths: [{metric, percentile, score}] (top 3), weaknesses: [...] (top 3)}`, best match first.
Qualified players only; the tool layer also checks `insufficient_sample` so a below-threshold
player resolves to BELOW_THRESHOLD instead of NOT_IN_DB.

**`profile`** → `profile.profile`
```python
profile(player_name: str, season: str = "2015", league: str | None = None,
        engine: Engine | None = None) -> dict[str, Any]
```
LLM args: `player_name: str`. Returns `{player_id, player_name, team, season, league,
position_group, position_group_fine, position_group_fine_share, matches, minutes_total,
qualified: bool, reason?, strengths: [item], weaknesses: [item], neutral: [item],
unranked: [item], warnings: [str]}` where `item = {metric, group, value, percentile,
sample_size, score?}`. Strengths best first, weaknesses worst first.

**`find_similar`** → `similarity.find_similar`
```python
find_similar(player_name: str, season: str = "2015", n: int = 10,
             filters: SimilarityFilters | dict[str, object] | None = None,
             league: str | None = None, engine: Engine | None = None,
             use_archetypes: bool = True, distinctive_n: int | None = DISTINCTIVE_N
             ) -> SimilarityResult
```
LLM args: `player_name: str`, `n: int (1-10, default 5)`,
`filters: {same_team_only?: bool, exclude_same_team?: bool, min_minutes?: number}`,
`league?: "ENG-Premier League" | "ESP-La Liga" | "FRA-Ligue 1" | "ITA-Serie A"`,
`use_archetypes?: bool (default true)`. **Not exposed:** `max_age` (no birth years are stored:
0 of 2,735 `players.birth_year`, so it always raises `AgeDataUnavailable`) and `distinctive_n`
(a tuning knob). Note `league` restricts the whole pool (and so the fitted model), not only the
candidates.
Returns `SimilarityResult {target_id, target_name, target_team, season, league, pool_leagues,
position_group_fine, position_group_fine_share, pool_kind, archetype_id, archetype_label,
cluster_confidence, group_size, candidates_considered, features_used, features_dropped,
n_components, explained_variance, filters, warnings, similar: [SimilarPlayer]}`;
`SimilarPlayer {rank, player_id, player_name, team, position_group_fine, minutes_total,
similarity (-1..1), shared_metrics: [MetricContribution], differing_metrics: [...]}`;
`MetricContribution {metric, target_value, candidate_value, target_z, candidate_z,
contribution}`. The tool layer adds `verdict` and `required_first_sentence` (section 5).

**`team_fingerprint`** → `team_profile.team_profile` + `team_profile.key_players` +
`market_values.squad_value` + `finance.team_wage_context`
(There is no function named `team_fingerprint`; "Team Fingerprint" is the page `6_team.py`,
built on these.)
```python
team_profile(team_name: str, season: str = DEFAULT_SEASON, engine: Engine | None = None,
             style: pd.DataFrame | None = None) -> TeamProfile
key_players(team_name: str, league: str, season: str = DEFAULT_SEASON,
            engine: Engine | None = None, n: int = KEY_PLAYER_COUNT
            ) -> tuple[list[KeyPlayer], str]
squad_value(team: str, league: str, values: pd.DataFrame) -> SquadMarketValue
team_wage_context(team_name: str, league: str, season: str = DEFAULT_SEASON,
                  engine: Engine | None = None) -> TeamWageContext
```
LLM args: `team: str`. Returns `{profile: TeamProfile {team_name, league, season,
league_position, points, played, matches_used, values: {axis: raw}, scores: {axis: 0-100},
headline_facts: [str]}, key_players: [KeyPlayer {player_id, player_name, position_group_fine,
archetype_label, minutes_total}], archetype_mix: str, squad_value: SquadMarketValue {team,
league, total_eur, n_valued, n_profiled}, squad_value_thin: bool, wage: TeamWageContext
{wage_bill_annual_gbp, rank, teams_ranked, teams_in_season, source, confidence}}`.
Axis scores use the radar orientation (`FLIPPED_AXES`: PPDA and transition seconds are flipped
so high = more pressing / faster). `team_finance` has 0 rows today, so `wage` fields are `null`
and must be reported as missing.

**`list_teams`** → `team_profile.list_teams`
```python
list_teams(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame
```
LLM args: `league?: str`. Returns `[{team_name, league, league_position}]` (80 rows). Also used
internally for team-name resolution.

**`find_similar_teams`** → `team_similarity.find_similar_teams`
```python
find_similar_teams(team_name: str, season: str | int = DEFAULT_SEASON, n: int = 10,
                   engine: Engine | None = None, style: pd.DataFrame | None = None
                   ) -> SimilarTeamsResult
```
LLM args: `team: str`, `n: int (1-10, default 5)`. Returns `SimilarTeamsResult {team_name,
league, season, teams_in_pool, similar: [SimilarTeam {rank, team_name, league, similarity,
closest_axes: [str], biggest_gaps: [str]}]}` + `verdict` (section 5).

**`build_squad`** → `composer.build_squad` (and `build_squad_variants`)
```python
build_squad(target_style: dict[str, float], budget_eur: float, formation: str,
            style_fit: pd.DataFrame, league_filter: list[str] | None = None,
            min_minutes: float = 900, variant: Variant = "standard",
            quality_weight: float = DEFAULT_QUALITY_WEIGHT) -> Squad
build_squad_variants(target_style: dict[str, float], budget_eur: float, formation: str,
                     style_fit: pd.DataFrame, league_filter: list[str] | None = None,
                     min_minutes: float = 900,
                     quality_weight: float = DEFAULT_QUALITY_WEIGHT) -> list[Squad]
```
LLM args: `style: one of PRESETS keys ("High-press direct", "Possession control",
"Low-block counter", "Balanced") OR copy_team: str` (the target becomes that team's
`TeamProfile.scores`), `budget_eur_m: number`, `formation: "4-3-3" | "4-2-3-1" | "4-4-2" |
"3-5-2" | "5-3-2"`, `leagues?: [str]`, `variant?: "standard" | "best_fit" | "budget_lean" |
"most_balanced"`, `quality_weight?: 0-1`. `style_fit` is injected (`style_fit.load_style_fit()`).
Free-form 12-axis targets are not exposed in v1 (too easy for the LLM to invent numbers).
Returns `Squad {variant, title, formation, budget_eur, total_value_eur, avg_fit, avg_quality,
quality_weight, slots: [SquadSlot {slot_label, group, player_name, team, league,
market_value_eur, fit, quality, score, axis_scores, top_axes}], breakdown: [AxisBreakdown
{axis, label, target, squad_mean, used}], top_axes, distinct_clubs, notes}`. Raises
`InfeasibleBudget`. The answer must say "fits the style on paper; form, chemistry, age and wages
are not modelled".

**`market_value`** → `market_values.load_market_values` + `player_value` / `coverage`
```python
load_market_values(season: str = DEFAULT_SEASON, engine: Engine | None = None) -> pd.DataFrame
player_value(player_id: str, values: pd.DataFrame) -> PlayerMarketValue | None
values_by_player(values: pd.DataFrame) -> dict[str, float | None]
squad_value(team: str, league: str, values: pd.DataFrame) -> SquadMarketValue
coverage(values: pd.DataFrame) -> MarketValueCoverage
coverage_is_thin(sv: SquadMarketValue) -> bool
```
LLM args: `player: str` (resolved to `player_id`). Returns `PlayerMarketValue {player_id,
match_method, transfermarkt_name, valuation_date, market_value_eur}` or `null` (no row). Coverage
today: 1,326 of 1,337 profiled players valued.

**`player_style_fit`** → `style_fit.player_style_fit`
```python
player_style_fit(player_id: str, season: str = DEFAULT_SEASON,
                 engine: Engine | None = None) -> dict[str, float | None]
```
LLM args: `player: str`. Returns `{axis: 0-1 | null}` for the 12 team axes (`width`,
`crosses_p90`, `set_piece_xg_pct` are always `null`: team-level only); `{}` when the player has
no row. Used for "would X suit a pressing side" → answered as "his numbers lean towards / away
from high pressing", never as a prediction.

**`archetypes`** → `directory.archetype_table`, `archetype_exemplars`,
`archetype_defining_metrics`
```python
archetype_table(directory: pd.DataFrame) -> pd.DataFrame
archetype_exemplars(engine: Engine | None = None, season: str = DEFAULT_SEASON,
                    n: int = 5) -> pd.DataFrame
archetype_defining_metrics(archetype_id: int, engine: Engine | None = None,
                           season: str = DEFAULT_SEASON, n: int = 5
                           ) -> list[tuple[str, float]]
```
LLM args: `archetype?: str` (label). Returns `[{archetype_id, archetype_label, pool, size,
confidence}]`, exemplars `[{player_id, player_name, team, archetype_id,
distance_to_centroid}]`, defining metrics `[(metric, archetype_mean_z)]` (empty for GK
archetypes).

**`player_games`** → `games.load_player_matches` + `season_game_stats` + `player_games`
```python
load_player_matches(player_id: str, season: str = DEFAULT_SEASON,
                    engine: Engine | None = None) -> pd.DataFrame
season_game_stats(matches: pd.DataFrame, group: str) -> SeasonGameStats
player_games(matches: pd.DataFrame, group: str) -> PlayerGames
```
LLM args: `player: str`. Returns `SeasonGameStats {games, goals, assists, clean_sheets, saves,
save_pct, goals_conceded}` and `PlayerGames {longest, best, worst: GameCard {match_id,
match_date, team, opponent, home, team_goals, opponent_goals, minutes, position_played, rating,
components}}`. Ratings compare a player's own matches only; the answer must say so.

### 9.3 New thin wrappers needed (to be written in `src/analytics/`, not in the chatbot)

Four question types have no single analytics function today. Each is a small, pure read over
existing tables, belongs in `src/analytics/` (hard rule 1) and needs its own tests.

**`player_stats(player: str) -> dict`** — no function of this name exists. Composition of
`directory.load_player_totals`, `directory.load_player_percentiles`, `directory.stats_table` and
`games.season_game_stats`:
```python
load_player_totals(player_id: str, season: str = DEFAULT_SEASON,
                   engine: Engine | None = None) -> pd.Series
load_player_percentiles(player_id: str, season: str = DEFAULT_SEASON,
                        engine: Engine | None = None) -> pd.DataFrame
stats_table(rows: pd.DataFrame, totals: pd.Series, group: str) -> pd.DataFrame
```
Returns `{identity: {player_id, player_name, team, league, position_group_fine, matches,
minutes_total}, season_counts: SeasonGameStats, rows: [{category, metric, value, per90,
percentile, score, band, unit}]}`. For a below-threshold player: identity + totals from
`insufficient_sample`, no rows, `qualified=false`.

**`compare_players(a: str, b: str) -> dict`** — two `player_stats` results aligned on the
metrics both have; `same_group: bool` and a cross-position note when the groups differ
(percentiles are then against different groups and must not be read as a head-to-head).

**`find_players_by_profile(conditions, groups=None, leagues=None, min_minutes=900, limit=10)
-> list[dict]`** — a filter over `player_percentiles` for the WEAKNESS category.
`conditions: [{metric: str, op: ">=" | "<=", score: 0-100}]` on the flipped **score** (so
"doesn't pass well" = `pass_completion_pct` score ≤ 20). A metric is only tested for groups where
it is in `RELEVANT_METRICS`; players of other groups are excluded and the result says so. Returns
`[{player_id, player_name, team, league, position_group_fine, minutes_total, matches:
[{metric, value, percentile, score}]}]`, sorted by the mean margin over the thresholds, plus
`n_matching` (total before `limit`).

**`explain_metric(metric: str, player: str | None = None) -> dict`** — the definition from
`metrics.METRICS_BY_NAME[metric]` (`name, group, kind, numerator, denominator, scale,
min_denominator, higher_is_better, note`), whether it is judged for the player's group
(`RELEVANT_METRICS`), and, with a player, his `player_percentiles` row (`value, percentile,
sample_size, group_size, small_group, position_group_fine`). The plain-English descriptions in
`src/app/labels.py` (`metric_label`, `metric_description`) cannot be imported from analytics
(wrong direction); when implementing, move the label/description tables to `src/analytics/`
and have the app import them from there.

Two small helpers for team questions:
- **`list_team_axis(axis: str, n: int = 5, ascending: bool = False)`** — top/bottom teams on one
  axis from `team_similarity.load_team_style` + `team_profile.axis_scores`.
- **`methodology(topic: str)`** — returns fixed, reviewed text snippets (similarity, percentiles,
  archetypes, team axes, market values) from `5_methodology.py`'s content, moved to
  `src/analytics/` so both read one source.

### 9.4 Deliberately not exposed in v1

| Function | Why not |
|---|---|
| `match_report.load_match_report(match_id)` | needs a match id; no "find match by teams/date" helper yet. Candidate for v2 with a `find_match(home, away)` wrapper. |
| `heatmaps.*`, `directory.load_heatmap`, `side_detection` | spatial grids are not useful as text; only 16 players have bio data anyway |
| `finance.wage_context` (direct) | reached through `team_fingerprint`; table is empty today |
| `similarity.find_similar(max_age=...)` | no birth years stored |
| `archetypes.fit_*`, `rebuild`, `percentiles.run`, `style_fit.run`, `team_style.run`, `evaluate_*`, `build_test_pairs.*` | pipeline / write paths; the chatbot is read-only |
| `directory.archetype_scatter` | refits the PCA on every call; a chart, not an answer |

### 9.5 Tool schema summary (what the LLM sees)

| Tool | LLM arguments | Backed by | Returns (short) |
|---|---|---|---|
| `resolve_player` | `query` | `directory.search` | ≤ 5 candidate players |
| `player_stats` | `player` | **new** over `load_player_totals`, `stats_table`, `season_game_stats` | counts + per-metric rows |
| `profile` | `player_name` | `profile.profile` | strengths / weaknesses / neutral + warnings |
| `find_similar` | `player_name, n, filters, league?, use_archetypes?` | `similarity.find_similar` | `SimilarityResult` + verdict |
| `compare_players` | `a, b` | **new** over `player_stats` | aligned metric rows |
| `find_players_by_profile` | `conditions, groups?, leagues?, min_minutes?, limit?` | **new** over `player_percentiles` | matching players |
| `explain_metric` | `metric, player?` | **new** over `METRICS_BY_NAME`, `player_percentiles` | definition + player row |
| `team_fingerprint` | `team` | `team_profile`, `key_players`, `squad_value`, `team_wage_context` | `TeamProfile` + key players + value + wages |
| `list_teams` | `league?` | `team_profile.list_teams` | 80 teams |
| `list_team_axis` | `axis, n?, ascending?` | **new** over `load_team_style`, `axis_scores` | ranked teams |
| `find_similar_teams` | `team, n` | `team_similarity.find_similar_teams` | `SimilarTeamsResult` + verdict |
| `build_squad` | `style \| copy_team, budget_eur_m, formation, leagues?, variant?, quality_weight?` | `composer.build_squad` | `Squad` |
| `market_value` | `player` | `market_values.player_value` | `PlayerMarketValue` |
| `player_style_fit` | `player` | `style_fit.player_style_fit` | 12 axis scores 0-1 |
| `archetypes` | `archetype?` | `directory.archetype_*` | clusters, exemplars, defining metrics |
| `player_games` | `player` | `games.season_game_stats`, `player_games` | season counts + best/worst game |
| `methodology` | `topic` | **new** (fixed text) | reviewed explanation |

17 tools: 11 over existing functions, 6 thin new wrappers.

---

## 10. What the bot will NOT do

- **Predict**: no results, title races, future form, transfer outcomes, future market values,
  "would he succeed at club X" (it can describe how a player's numbers lean against a style).
- **Answer about players or teams not in the data**: no other seasons, no Bundesliga or other
  leagues, no cups or internationals, no players who retired or debuted outside 2015/16.
- **Invent statistics**: every number comes from a tool result; the grounding check blocks the
  rest. It does not compute its own averages, differences or conversions.
- **Go off topic**: no general football trivia, news, history, betting, or non-football chat.
- **Measure the unmeasurable**: leadership, mentality, personality, work rate as character,
  injuries, age, career history, contracts, wages of individual players.
- **Rank players overall** ("who is the best midfielder"): it describes named metrics instead.
- **Hide uncertainty**: it never drops a tool warning or upgrades a Weak match.
- **Write to the database**: read-only; no pipeline or rebuild function is callable.

---

## 11. Limitations to document in the UI

Shown in the always-visible "Limitations" expander on `9_chat.py`, in this wording:

1. **One season, four leagues.** Only 2015/16 Premier League, La Liga, Ligue 1 and Serie A
   (StatsBomb open data). Nothing before, after, or elsewhere.
2. **900-minute threshold.** Players with fewer league minutes have totals only: no percentiles,
   profile or similar players.
3. **Percentiles are within position.** A player is ranked only against his own position group
   across the four leagues, and position groups come from StatsBomb lineups, which can differ from
   how you think of a player.
4. **Similarity is a heuristic.** In testing, about half of "Fair" (60%+) matches shared a role
   and style; no higher score was more reliable. Use similar players as leads, not conclusions.
5. **Team style is event patterns, not tactics.** The 12 axes describe what the event data shows,
   ranked 0-100 among the 80 teams.
6. **Market values are Transfermarkt estimates** from around 2015/16, not fees or wages; a few
   players are unmatched. There is no player wage data, and team wage bills are not loaded yet.
7. **No age data.** StatsBomb open data has no birth dates, so the assistant cannot filter or
   answer by age.
8. **The assistant can be wrong.** It is a language model reading tool results. Every answer
   lists its sources; check them, and use the Player, Team and Compare pages for the full numbers.
9. **Questions leave your machine** when the online model (Groq) is used. The data is public, but
   do not type anything private into the chat.
10. **Tested accuracy.** The latest evaluation (`docs/chatbot_eval.md`) is shown here with its
    date once it exists: tool-call accuracy, answer accuracy and grounding rate.
