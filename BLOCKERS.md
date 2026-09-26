# BLOCKERS

## 1. FBref: DEAD (definitively, not just blocked)

FBref is not coming back as a source. Three independent reasons, each sufficient: (1) `soccerdata.FBref`
needs Selenium, which the project forbids; (2) plain HTTP is stopped by a site-wide Cloudflare JavaScript
challenge (403 on every path, including `robots.txt`); (3) in January 2026 FBref lost its Opta advanced
data, so the defensive and passing tables no longer exist. Evidence is in section 5. Do not revisit.

- **Quarantined file:** `src/ingest/_blocked/fbref.py` (see `src/ingest/_blocked/BLOCKED.md`). Never executed.
- **What:** `soccerdata` 1.9.1 `FBref.__mro__` is `[FBref, BaseSeleniumReader, BaseReader, ...]`.
  Constructing it launches undetected Chrome via `seleniumbase` (`uc=True`), and its CAPTCHA
  handler can drive the desktop with pyautogui (`uc_gui_handle_captcha`) when `headless=False`.
  `fbref.py` has no `import selenium` line, so an import grep does not catch it: the
  dependency is inherited.
- **Why blocked:** project rule, no Selenium-based ingest. (Default `headless=True` would not
  trigger the GUI path, and `pyautogui` is not installed, but the browser launch alone violates the rule.)
- **Impact:** no FBref data. Understat has xG/xA/shots/key passes only: no progressive passing,
  pressures, tackles, carries or other defensive/possession detail, so style profiles and
  similarity are limited for non-attacking roles.
- **Also affects:** `soccerdata.WhoScored` and `soccerdata.Sofascore` (seleniumbase-based per the README).
  Verify with `__mro__` before use.

### Replacement options (historical: the choice was made in section 5)

1. **StatsBomb Open Data** — free JSON on GitHub (`statsbomb/open-data`), plain HTTP, no scraping.
   Event-level data supports rich style features (pressures, progressive actions, carries).
   Limited to selected competitions/seasons, so it is not a full current-season league feed.
2. **Custom `requests` scraper for FBref** — plain HTTP + lxml through `rate_limited`, raw HTML
   cached to `data/raw`. FBref sits behind Cloudflare and its ToS restrict automated access;
   success is not guaranteed and it needs a feasibility spike first.
3. **Selenium with pyautogui patched out** — keep `soccerdata.FBref` but disable the `uc_gui_*`
   calls. Still launches a browser, conflicts with the project rule, and depends on patching
   a third-party package. Least preferred.

### Recommendation

Superseded by section 5: StatsBomb Open Data was chosen, on 2015/16 only. Option 2 (plain-requests
FBref) is ruled out (Cloudflare 403, and the advanced data is gone); Option 3 is rejected by the project rule.

## 2. `tls_requests` native DLL failure — RESOLVED (transient, watch for recurrence)

- **What happened:** the first Understat run failed with
  `[WinError 193] %1 is not a valid Win32 application` loading
  `tls_requests/bin/tls-client-windows-64-1.13.1.dll`. Python is 64-bit AMD64, so it was not an
  architecture mismatch. `wrapper-tls-requests` 1.2.5 downloads its DLL from GitHub on first use;
  that download was evidently corrupt or truncated, and the library deleted it on load failure,
  leaving `bin/` empty. (Cause inferred, not directly observed: the bad file was already deleted.)
- **Fix:** retried the same command once. The library re-downloaded
  `tls-client-xgo-1.13.1-windows-amd64.dll` (24.9 MB, healthy) and Understat ingested 562 rows.
  No manual DLL placement was needed.
- **If it recurs:** check that `site-packages/tls_requests/bin/*.dll` is larger than ~5 MB
  (healthy is ~25 MB). If not, delete it and rerun, or download the windows-amd64 asset for
  tag v1.13.1 from `github.com/bogdanfinn/tls-client/releases` into that folder. Antivirus or a
  proxy interfering with the GitHub download is the likely trigger.
- **Risk:** anything routed through `soccerdata`'s `BaseRequestsReader` depends on this download
  succeeding on first use. `football_data_csv` does not.

## 3. Understat position grouping — RESOLVED (per-match method; limitations below)

- **Problem:** Understat's season-level `position` ("D F M S") is an alphabetical letter list, not
  priority-ordered, so a first-letter rule mislabelled 53 attackers as DEF (Mbeumo, Madueke, ...).
  The letter rule has been removed; `understat.py` no longer sets `position_group`.
- **Method (`src/ingest/understat_positions.py`):** Understat's per-match rosters
  (`soccerdata.Understat.read_player_match_stats`, plain HTTP, no Selenium) carry a real position code
  and minutes for every appearance. For each player, starter minutes are summed per position group and
  the group with the most minutes wins. Bench appearances (`Sub`) carry no role and are ignored. No
  priority heuristic. Raw match pages are cached one file per match in
  `data/raw/understat/<season>/<league>_match_players/`.
- **Code map:** `GK`; `DC/DL/DR` -> DEF; `DMC/DML/DMR/MC/ML/MR/AMC` -> MID; `AML/AMR/FW/FWL/FWR` -> FWD.
- **Result, EPL 2024/25:** DEF 172, MID 153, FWD 125, GK 42, NULL 70 (562 players). All 42 goalkeepers
  match Understat's own `GK` letters. Salah FWD, Van Dijk DEF, Rodri MID, Alisson GK, Mbeumo FWD.
- **NULLs:** 70 players, all substitute-only (no starts, so no position exists in the data). Reasons are
  logged in `data/processed/position_nulls.csv` (gitignored). Exclude NULL from position-based analysis.
- **Limitations:**
  - **DEF 172 is every player who ever started at DC/DL/DR.** By starter minutes it is 134 at >=450, 108
    at >=900, 78 at >=1500. Position-based analysis should apply a minutes threshold.
  - **Taxonomy choices drive edge cases.** Wide attacking mids (`AML/AMR`) are FWD, so Understat "M"
    wingers such as Grealish and Sancho come out FWD. Wing-backs coded `ML/MR` in back-fives come out MID
    (Dalot, Semedo), so DEF undercounts them. 39 of 492 grouped players disagree with Understat's own
    season letters, mostly for these two reasons.
  - **Mixed roles:** 19 players win with under 60% of their starter minutes (e.g. Wan-Bissaka DEF
    1778/MID 1350; McGinn MID 1107/FWD 1028). The label is the plurality, not a certainty. A tie gives NULL.
  - **Thin evidence:** Rodri has one 20-minute start in Understat's data (injury season), so his MID
    label rests on very little.
  - **Requires ~380 page fetches per league-season** (~25 min at the 3 s rate limit; resumable from cache).
    It must be re-run after the season ingest for each new league/season.

## 4. Other known defects (not blockers)

- **Team names are normalized for the EPL 2024/25 only** (`src/ingest/team_identity.py`). Other
  leagues, and promoted/relegated clubs in other seasons, pass through unchanged until added.
  There is no player identity resolution across sources and no `identity.py`.
- **`src/ingest/_blocked/fbref.py` is stale:** it calls `build_team_id(source=...)`, whose
  signature changed. It stays quarantined and unmodified.
- **Fetch and parse are fused** with the DB layer in the ingest modules; only `_to_records` /
  `_upsert_records` are testable offline. The network fetch functions are untested.
- **Only `ENG-Premier League` 2024 has been ingested live.**

## 5. Defensive and passing stats: DECIDED, StatsBomb Open Data on 2015/16 (2026-09-19)

**Decision:** use StatsBomb Open Data for player-level defensive and passing metrics, on the
2015/16 season. Do not use the Kaggle FBref mirrors (legal risk) and do not spend more effort on FBref.

**The trade-off, stated plainly: no free source gives current-season (2024/25 or later) defensive or
advanced passing data.** With StatsBomb 2015/16 we can design, build and validate profiles,
similarity and squad-needs logic for defenders and midfielders, but only for 2015/16 players. Current
players can only be described by Understat's attacking numbers (xG, xA, shots, key passes). Anything
built on this data must say which season it describes. Getting current defensive data would need a paid
provider (Opta/StatsBomb commercial, Wyscout, etc.); that is out of scope while the project is free-data only.

### Why the alternatives were ruled out (evidence from the investigation)

- **FBref: dead.** A plain `requests` GET of the EPL 2024/25 stats page returned `403 Forbidden`,
  `cf-mitigated: challenge` (Cloudflare "Just a moment..." JavaScript challenge); `/robots.txt` and `/en/`
  returned the same, so no path is exempt. `soccerdata.FBref` needs Selenium, which the project forbids.
  Independently, in January 2026 Opta (Stats Perform) terminated Sports Reference's feed and FBref removed
  all Opta advanced stats, keeping only basic stats (press coverage; the announcement itself returned 403 to
  my fetcher). The Opta-derived tables (defense, passing, possession) are gone even if Cloudflare were passed.
  Cloudflare-evasion tools (curl_cffi, cloudscraper, FlareSolverr, headless browsers) were not tried: they are
  new dependencies and at odds with the no-browser-automation rule and FBref's terms.
- **Kaggle FBref mirrors: rejected on legal grounds.** The data is Opta-derived and Opta demanded its
  deletion, so redistribution is doubtful. Nothing was downloaded or inspected. The one public scraper feeding
  them uses Playwright and its scheduled runs have failed since at least 2025-12-29.
- **WhoScored / Sofascore:** Selenium-based in `soccerdata`, blocked by the same rule.

### StatsBomb coverage (men's; from `competitions.json` and `matches/<comp>/<season>.json`)

Competition and season ids are read from `competitions.json` at run time. Confirmed for 2015/16:
EPL `(2, 27)`, La Liga `(11, 27)`, Serie A `(12, 27)`, Ligue 1 `(7, 27)`.

| Competition | Available seasons | Coverage |
|---|---|---|
| Premier League | 2003/04 (38), **2015/16 (380, full)** | 2015/16 all 20 clubs |
| La Liga | 2004/05 to 2020/21 | **2015/16 = 380 (full)**; other seasons are Barcelona only |
| Serie A | 1986/87 (1), **2015/16 (380, full)** | 2015/16 all clubs |
| Bundesliga | 2015/16 (34), 2023/24 (34) | Bayer Leverkusen only: **not usable** for league-wide work |
| Ligue 1 | **2015/16 (377)**, 2021/22, 2022/23 | 2015/16 near-full; later seasons PSG only |
| Champions League | 1970/71 to 2018/19 | one final or partial seasons |

2024/25 is not available for any of these (the only 2024 entries in the dataset are Copa America and the
Euros). Full-league 2015/16 is roughly 1,500 matches across four leagues. The Bundesliga has no full season.
The repo shows license `NOASSERTION`; its README requires crediting StatsBomb and using their logo
(done: see the README attribution and `src/app/static/statsbomb-logo.png`).

### What the foundation does (built 2026-09-19; full 2015/16 EPL ingested 2026-09-19)

Modules in `src/ingest/statsbomb/`: `discovery.py` (competitions and matches), `fetch.py` (cached, validated
downloads), `parse.py` (per-player match metrics), `checks.py` (per-match invariants), `identity.py`
(`statsbomb:<id>` players), `ingest.py` (`ingest_match` for one match, `ingest_season` for a whole
league-season, resumable and loud). New tables: `matches`, `player_match_stats`; new column
`players.statsbomb_id` (added by an idempotent migration in `init_db`).

Proof match: Manchester City 1-3 Leicester City (2016-02-06, id 3754290), 28 players. Verified against
independent facts: team minutes sum to 11 x match length, lineup minutes agree with all 6 `Substitution`
events, goals match the official score, and pass, shot and card totals equal the raw event counts.

**Full season run (2026-09-19):** all 380 EPL 2015/16 matches discovered; 374 stored, 6 failed (below the
20-failure stop threshold, so the run continued to completion). 10,302 `player_match_stats` rows, 0 with
a NULL `position_group`, all 20 clubs normalize to the expected EPL 2015/16 set. 58 lineup corrections
(all dismissal spell-cuts, the pattern from section 7), 1 invariant violation (section 7, item 4). Pre-flight
lineup scan across all 380 matches found 24 unique StatsBomb position names in real data, all already
mapped in `POSITION_GROUPS`; the 8 positions listed as unverified below are now confirmed (only
"Secondary Striker" never appeared this season). Raw JSON cache: 1.1 GB in `data/raw/statsbomb/`, on disk D.

Metrics computed: minutes, position played, passes (attempted, completed, %), progressive passes,
passes into final third and box, carries, progressive carries, carry distance, pressures (with mean
duration and counterpressures), tackles (and won), interceptions, blocks, clearances, ball recoveries,
dribbles, shots, goals, **xG (present in the data: `shot.statsbomb_xg` on every shot)**, fouls, cards.
Definitions and thresholds are documented at the top of `src/ingest/statsbomb/parse.py`.

### Known limitations of the foundation

- **Progressive thresholds are our own:** a pass or carry is progressive if it cuts the straight-line
  distance to the goal centre by at least 10 yards. This is not FBref's or StatsBomb's own definition, so
  do not compare the numbers with published ones.
- **Minutes are event-clock play time**, so a full match is about 97 minutes here, not 90 (see section 6
  for the 90-minute normalization applied on top of this).
- **Minutes and position come from the lineup file's position spells.** If a match's lineup lacks them for a
  player who has events, parsing raises `LineupError` rather than guessing; none of the 374 stored matches
  hit this.
- **Temporary `Player Off`/`Player On` (injury treatment) events are subtracted from expected team minutes**
  (`checks.temporary_absences`). A *permanent* unreplaced injury exit (no matching `Player On`, no card, all
  subs used) is not handled the same way and surfaces as an invariant violation instead — see section 7,
  item 4.
- **`position_played` is StatsBomb's position name** (e.g. "Left Center Midfield"), mapped to
  GK/DEF/MID/FWD via `POSITION_GROUPS`. All 24 names observed in the full 2015/16 EPL season are mapped;
  see section 7 pre-flight results.
- **`interceptions` counts attempts** whatever the outcome; `tackles` counts all Tackle duels.
- **Player names** use StatsBomb's nickname where present (e.g. "Pablo Zabaleta"). Not linked to Understat.
- **Team names** use `team_identity`, which now covers both 2024/25 and 2015/16 EPL clubs (26 total); all
  20 clubs in the 2015/16 season normalize correctly.
- Per-match rows are aggregated to seasons and profiled in section 10 (added 2026-09-20).

## 6. StatsBomb minutes are normalized to a 90-minute match (decided 2026-09-19)

StatsBomb reports event-clock minutes (~97 for a full match). We normalize to 90 so per-90 metrics are
comparable to published sources. This is an approximation.

- **Mechanism:** setting `MINUTES_NORMALIZE` (default True). Each match's minutes are multiplied by
  `90 / match length`, where match length is the sum of the two periods' event clocks. The factor used is stored
  per match in `matches.minutes_scale_factor` (0.92 to 0.94 in the first five matches), so raw
  event-clock minutes are recoverable as `minutes / factor`. With `MINUTES_NORMALIZE=False` the factor is
  stored as 1.0 and minutes stay in event-clock time.
- **Why it is only approximate:** it spreads stoppage time evenly over the whole match, treats every match's
  real length as 90, and scales a sent-off player's shortened match by the same factor. Counting metrics
  (tackles, passes, ...) are not scaled, only minutes, so a per-90 figure is `count / minutes * 90`.
- **Full-match players come out at 90.0.** A test checks that after ingest they are within 0.5 of 90.

## 7. StatsBomb data quirks found across the full 2015/16 EPL season (smoke test 2026-09-19, full run 2026-09-19)

All were caught by the per-match invariant checks; none was silently patched.

- **A sent-off player's lineup spell can stay open.** First seen in Chelsea v Swansea (3754078): Courtois
  is sent off at 51:37, but the lineup lists him playing "Center Attacking Midfield" until the final
  whistle, 42.9 phantom minutes. Handling: `player_spells` cuts spells at the dismissal event (`Red Card`
  or `Second Yellow`), and every cut larger than 0.1 min is written to
  `data/processed/statsbomb_corrections.csv` and logged. The correction rests on the dismissal event being
  right. **58 corrections of this kind across the full season** (see the CSV for the full list; every one
  is a dismissal spell-cut, the same mechanism as Courtois).
- **Treatment breaks are real gaps.** Delaney (Crystal Palace v Norwich, 3754138) is off from 64:02 to 64:20
  (`Player Off`/`Player On`), so a team's minutes can be a fraction short of 11 x match length. The
  team-minutes invariant subtracts these events. A gap in the lineup with no matching event is still
  reported as a violation.
- **`Own Goal For` is credited to the beneficiary team**, which the goals invariant relies on. It held
  across the season: 0 goal-count violations in 374 stored matches.
- **Position names are full names, not codes**, and there is no "Sub" position: a substitute's spell carries
  the position he took. **Pre-flight scan (2026-09-19), all 380 lineup files:** 24 unique position names
  observed, all 24 already mapped in `POSITION_GROUPS`. This confirms the 8 positions previously listed as
  unverified (Center Back, Center Midfield, Left/Right Attacking Midfield, Left Defensive Midfield,
  Left/Right Wing Back). Only "Secondary Striker" (mapped, never observed) did not appear this season.
- **An unused substitute with events fails the match loudly** (`LineupError`) instead of being guessed at.
  Did not occur in the full run.
- **A player's lineup spell can end early with no matching `Player On`, no card, and no substitute
  available.** New in the full run, distinct from the two cases above. Stoke City 2-0 Manchester City
  (3754135, 2015-12-05): Fernandinho's last event is `Player Off` at 72:41 (apparent injury); City had
  already used all three substitutions (Fernando off 55:54, Bony off 56:09, Silva on 61:10), so no
  replacement is possible and he never returns. `temporary_absences` only subtracts a gap when it finds a
  matching `Player On` for the same player, so this is not accounted for: the match is stored, but flagged
  as a violation (Man City minutes 20.43 short of expected) rather than silently absorbed.
  **Only 1 occurrence in 374 matches.** Not fixed this session; a future pass could treat an unmatched
  `Player Off` (no card, no return) the same way a dismissal is treated.
- **Lineup minutes can disagree with the `Substitution` event itself**, not just with a dismissal or
  treatment break: a player's lineup spell can run to end-of-match (96-103 min) while the actual
  `Substitution` event that took him off fires 10-50 minutes earlier. `check_against_substitutions` catches
  this before parsing and the whole match is skipped (not partially stored), because the mismatch calls the
  reliability of that match's lineup spells into question generally, not just for one player.
  **6 of 380 matches failed this way** (3754099, 3754148, 3754217, 3754274, 3754336, 3754339 — see
  `data/processed/statsbomb_failures.csv` for exact player ids and times). This looks like the same class
  of StatsBomb lineup defect as the dismissal case in reverse (a spell not closed at the right event), but
  was not investigated or fixed this session — the run was configured to log, skip and continue rather than
  stop, per the 20-failure threshold (6 is well under it).

## Full 2015/16 EPL ingest: DONE (2026-09-19)

380 matches discovered, 374 stored, 6 failed (item above), 1 invariant violation (item above), 58 lineup
corrections. 10,302 `player_match_stats` rows, 0 with a NULL `position_group`, all 20 clubs normalize to
the expected EPL 2015/16 set. Ran in 1,376 s (~23 min) for events (lineups were pre-fetched in a ~980 s
pre-flight pass). 1.1 GB raw JSON cached on disk D.

## 8. The 6 failed 2015/16 EPL matches: StatsBomb data flaw, left out (investigated 2026-09-20)

**Verdict: StatsBomb lineup-file flaw, one shared root cause, not a parser bug. The 6 matches stay out.**

Matches 3754099, 3754148, 3754217, 3754274, 3754336, 3754339. Inspected the raw lineup and event JSON
for all 6 (not just one). In each, a player is substituted off and the `Substitution` event is correct:
his last event in the match *is* the substitution and nothing follows it. But the lineup file also gives him
one extra trailing position spell that starts at the substitution instant (`start_reason` `Player On` in 5
of the 6, `Tactical Shift` in the other) and stays open until the final whistle. Examples:
3754099 Berahino is subbed off at half-time (2nd period, 45:00) yet the lineup has him on the left wing
for 96 minutes; 3754339 Shaqiri has a `Player Off` (52:02) / `Player On` (54:02) treatment break and is
then subbed off at half-time, but the lineup runs him to 103 minutes.

Why not a parser bug: the parser reads the lineup as given, and `check_against_substitutions` correctly
refuses to trust a lineup that contradicts the substitution events. Why not auto-fixed: the lineup is wrong
in a way that needs a guess about which spell to drop (the same class as the dismissal spell-cut in section 7,
which we did correct, but that one had an unambiguous event to cut at). Per the session rule, no per-match
patching: the 6 matches are excluded, not repaired.

- **Impact:** 374 of 380 matches (98.4%) are used. The excluded matches drop every player's minutes *and*
  counts together for those games, so per-90 rates are unbiased in expectation but every player's season
  `matches`/`minutes_total` is up to ~6 games short. Affected clubs: West Brom, Crystal Palace, Tottenham,
  Arsenal, Stoke (x2), Everton and their opponents (see `statsbomb_failures.csv` and the match ids).
- **If revisited:** a possible fix is to close a player's last spell at his `Substitution` event when
  the lineup contradicts it (5 of 6 have a `Player On` at that instant). That is a data correction and
  would be logged in `statsbomb_corrections.csv`, like the dismissals.

## 9. Man City minutes gap in match 3754135: accepted, not a bug (2026-09-20)

One 2015/16 match has a team playing ~20 min short due to unreplaced injury. Correct behavior, not a bug.
(Stoke 2-0 Man City, 3754135: Fernandinho leaves injured at 72:41 after City used all three subs. The
invariant flags it; the data is right; nothing is auto-corrected.)

## 10. Metrics + percentiles layer, 2015/16 EPL: BUILT (2026-09-20)

Code: `src/analytics/metrics.py`, `src/transform/normalize.py`, `src/analytics/percentiles.py`,
`src/analytics/profile.py`. Every definition choice is in `ANALYTICS_DECISIONS.md`.

- **Season table:** `player_season_stats_2015_16` has 323 players at or above 900 minutes; `insufficient_sample`
  has the other 226 (549 players total, none dropped silently, each with a reason).
- **Multi-team players (11), team with most minutes used, split in `data/processed/multi_team_players_2015_16.csv`:**
  Pritchard (West Brom 69 / Spurs 8), Murray (Bournemouth 627 / Palace 123), Bamford (Norwich 245 / Palace 139),
  Lescott (Villa 2626 / West Brom 180), Townsend (Newcastle 1075 / Spurs 42), Grabban (Bournemouth 595 /
  Norwich 251), Dyer (Leicester 252 / Swansea 22), Shelvey (Swansea 1202 / Newcastle 1052), Naismith
  (Norwich 851 / Everton 374), Caulker (Southampton 152 / Liverpool 15), Jarvis (Norwich 1017 / West Ham 85).
  Each is a player's stats over *both* clubs under one team label. Shelvey is the near-even split.
- **Parser extended** (no network, re-run from the raw cache): `player_match_stats` gained `shots_on_target`,
  `assists`, `key_passes`, `xa` and six goalkeeper columns. Nothing else about ingest changed; the 6 failures,
  the 1 violation and the 58 corrections are identical to before.
- ~~**DEF pools centre-backs and full-backs.**~~ **Resolved in section 11:** 8 fine position groups.
- **21 of 323 players have under 60% of their minutes in their (coarse) position group** (e.g. Antonio, De Bruyne, Mata,
  Lallana, Coutinho). `profile()` warns on these. Their percentiles are against the group they played most.
- **`gk_goals_prevented_p90` is a proxy and is negative for every keeper** (about -0.4 to -1.1 per 90): pre-shot xG
  averages over all shots, so it understates the danger of on-target ones. Use it to rank keepers, never read its level.
- ~~**Aerial duels ... unavailable**~~ **Wrong for aerials, corrected in section 11:** they are computable. PSxG and
  crosses-stopped remain unavailable (see `metrics.UNAVAILABLE`).

## 11. 8 position groups, advanced metrics, heatmaps, similarity: BUILT (2026-09-20)

Code: `src/ingest/statsbomb/parse.py` (fine groups and new counts), `src/analytics/heatmaps.py`,
`src/analytics/similarity.py`, `src/analytics/evaluate_similarity.py`, `src/utils/disk_bootstrap.py`. Decisions and
labels are in `ANALYTICS_DECISIONS.md` sections 8-12. The raw cache was re-parsed with no network: 374/380 matches,
the same 6 failures, the same 1 violation (Man City, section 9).

What could NOT be done, and why (nothing was faked):

- **`is_captain`: not in the data.** Lineup player records carry `player_id, player_name, player_nickname,
  jersey_number, country, cards, positions` (all 13,678 of them). The string "captain" occurs in none of the 380
  lineup files, 380 event files, the match files or `competitions.json`. Not added; listed in `metrics.UNAVAILABLE`.
- **`last_line_events_p90`: not computable.** It needs teammate positions at each defensive action; the open data has
  a freeze frame only on `Shot` events. Not added.
- **Player age is unavailable, so `find_similar(max_age=...)` cannot work.** StatsBomb's open lineups have no birth
  dates; `players.birth_year` is empty for all 1,111 players. The filter raises `AgeDataUnavailable` rather than
  being ignored. It needs another source (none is ingested).
- **Position group AM has only 14 qualifying players** (< 20). Percentiles and similarity there are coarse; Özil's
  similar-player list is only meaningful at #1 and #2.

Corrections to earlier statements:

- **Aerials ARE computable.** The earlier "won aerials are not a full event" was wrong: the winner is flagged
  `aerial_won` on his follow-up Pass/Clearance/Shot/Miscontrol, and wins equal losses (16,845 = 16,845 over all 380
  cached matches). Aerial metrics are built (section 9 of `ANALYTICS_DECISIONS.md`).

Caveats to carry forward:

- **75 of 323 players are mixed-role** (under 60% of minutes in their fine group), 26 of them CM, against 21 under
  the coarse groups. Each is ranked and matched only within his main group.
- **The similarity model has no benchmark.** `docs/similarity_eval.md` has ten fixed players and written notes;
  full-back and goalkeeper lists are weak (top scores 0.47 to 0.61 for the two full-backs).
- **Three mypy errors remain and were there before this session:** `parse.py` carry-distance float/int,
  `understat_positions.py` name redefinition, and the quarantined `_blocked/fbref.py`.

Disk (C: writes): see `docs/DISK.md`.

## 12. Four-league 2015/16 dataset: INGESTED (2026-09-22)

| League | Matches stored | Failed | Violations |
|---|---|---|---|
| EPL | 380 / 380 | 0 | 0 |
| La Liga | 372 / 380 | 8 | 0 |
| Serie A | 379 / 380 | 1 | 3 |
| Ligue 1 | 376 / 377 | 1 | 3 |
| **Total** | **1,507 / 1,517** | **10** | **6** |

The 6 EPL matches of section 8 are no longer excluded: the substitution-spell defect below was repaired.
`player_match_stats` covers all 1,507 matches (41,816 rows, 2,173 players). Position groups use the 8-group
schema in `position_group_fine`.

StatsBomb lineup clock: three distinct flaw patterns across the 4-league dataset — (1) substitution spell left
open until final whistle (repaired), (2) player with events but no lineup position and no substitution event
(10 matches, unfixable), (3) short Player Off/Player On absences counted inconsistently at half-time boundary or
overlapping spells (6 matches, 0.6–3.5 min each, benign). Total impact: 16 of 1,517 matches (1.05%). Documented,
not hidden.

## 13. Coverage that is NOT in the dataset, and known labelling limits

- **Bundesliga: not ingested.** StatsBomb's only Bundesliga 2015/16 matches are 34 Bayer Leverkusen games; a
  league-wide pool cannot be built from one club.
- **Eredivisie: unavailable.** It does not exist in any free event-data source we can use.
- **FBref: dead** (section 1). **WhoScored / Sofascore:** blocked (Selenium). **Kaggle FBref mirrors:** rejected on
  legal grounds (section 5).
- **Kanté is CM, not DM, in the data.** Position group comes only from StatsBomb's lineup position strings, taken
  by minutes: Kanté has 57% of his minutes in slots mapped to CM and the rest DM, so he is a CM (flagged mixed
  role). The same limitation puts Mata and Eriksen in W, Cazorla in DM, and James Rodríguez, Pjanić and Hamšík
  in CM, so the attacking-midfield neighbourhood one would expect around Özil (AM, 44 players pooled) is never
  compared with him (`docs/similarity_eval_v2.md`). No role model beyond the position string exists.
- **A cross-league player is one row.** 30 of the 1,337 qualifying players played in more than one league in
  2015/16 (winter moves): their stats are summed across leagues, `league` and `team` are the main one (most
  minutes) and the split is in `team_split` / `data/processed/multi_team_players_2015_16.csv`.
- **Leagues are treated as one population.** Whether a Ligue 1 winger's numbers are comparable with a La Liga
  winger's (pace of play, StatsBomb tagging by competition) has not been checked.

## 14. Archetype edge cases (2026-09-23)

Archetypes (k = 10, ANALYTICS_DECISIONS.md section 14) are descriptive clusters; two expected assignments miss.
Neither is fixed: k is accepted and the clusters are not re-run.

- **Juan Mata (Manchester United): Deep playmaker (confidence 0.21), not Chance creator.** His metrics put him with
  the deep-lying, ball-circulating group, so he is not in Özil's Chance creator pool of 55 and never appears in
  Özil's list. Eriksen, Pjanić, Cazorla and Hamšík (the other creators the brief names) are in it.
- **Yann M'Vila (Sunderland): Defensive full-back (confidence 0.27), not Destroyer.** A ball-winning midfielder in
  the position data (CM) whose numbers look more like a full-back's; he sits in the Defensive full-back pool, not
  Kanté's Destroyer pool (v2's position-group list had him 7th for Kanté).
- **Low-confidence assignments** (confidence under 0.25: Kanté 0.24, Vardy 0.07, Mahrez 0.19, Pjanić 0.18, Cazorla
  0.12, Hamšík 0.22, Busquets 0.18) sit near an archetype boundary: the pool is a soft description of them and
  `find_similar` warns.
- **Weak archetypes have weak signatures.** Defensive full-back, Destroyer and Pressing wide midfielder have a 12th
  distinctive feature at |Δz| 0.45 to 0.50 (sharp archetypes 1.0 to 1.3), so their similarity lists are less
  reliable; Kyle Walker's v3 list got worse (`docs/similarity_eval_v3.md`).
- **Hard-coded label map.** `OUTFIELD_LABELS` is keyed by cluster id (numbered by centroid PC1); re-check it after any
  data or feature change (`python -m src.analytics.archetypes --describe`).

## Next priority (future session)

1. UI (player profile page, radar/heatmap rendering, search, comparison). Nothing in `src/app` uses the
   analytics yet.
2. An age source, if `max_age` is wanted (StatsBomb has none).
3. Decide whether a role model beyond StatsBomb's position string is needed (Kanté/Özil neighbours above).

## 17. Team fingerprint: axes and known limits (2026-09-23)

- **Axes not computed: none.** All 12 requested axes are computed. Nine are proxies, not direct measurements (possession
  % is pass share, PPDA excludes `Pressure` events, transition speed is seconds to a shot, etc.); the exact
  definitions and what each does not measure are in ANALYTICS_DECISIONS.md section 17.
- **Bayern Munich is not in the dataset.** Only the Big-4 (EPL, La Liga, Serie A, Ligue 1) is ingested; the cached
  Bundesliga file holds 34 matches and is not parsed. "Bayern clusters with PSG, Barcelona, Real" cannot be tested;
  Barcelona, Real Madrid and PSG can be compared with each other instead.
- **Standings can be short.** StatsBomb lists 377 of Ligue 1's 380 matches (and 37 for PSG), so a Ligue 1 table can differ
  from the real one. The team page shows a note when a team has fewer than 38 fixtures.
- **Set-piece and transition axes are noisy proxies** (penalties count as set pieces; transition speed only sees
  possessions that end in a shot).

## 18. Trophies, career history and personal achievements are NOT available

No loaded source provides them (StatsBomb Open Data has match events only; Transfermarkt is out of
scope). The profile shows none and invents none; the "Career context" card stays a placeholder. Bio
is limited to birth date and nationality from Wikipedia/Wikidata, and is absent for players whose
page is missing or ambiguous.

## Similarity score is statistical, not categorical

The 100-pair threshold evaluation (commit ee44078) shows precision is flat (~40-52%) across all
thresholds. The score measures statistical closeness of profiles, not whether two players fill the
same role. Cross-position pairs inside the same archetype score high even when a human would call
them different roles. The UI now shows position context on similar-player cards so users can judge
for themselves. The similarity engine is NOT tuned to chase the labels — see ANALYTICS_DECISIONS.md
section 23.

## 19. Team wage bills: Capology unreachable, manual CSV required (2026-09-25)

**Status: no automated source for 2015/16 club wage bills.** The `team_finance` table, importer and UI
lines are built (ANALYTICS_DECISIONS.md section 24) but the table is empty until the manual CSV is filled.

- **FBref wages pages: blocked.** `GET https://fbref.com/robots.txt` and
  `GET https://fbref.com/en/comps/9/2015-2016/wages/` (plain `requests`, 3 s apart) both returned
  `403`, `cf-mitigated: challenge`, "Just a moment..." (Cloudflare JavaScript challenge). Same block as
  section 1. The other three leagues were not requested, since the whole domain is challenged.
- **Capology: blocked.** `GET https://www.capology.com/robots.txt` (plain `requests`, User-Agent
  `FootballIntel/0.1 (football research project; plain requests, rate-limited)`) returned `403`,
  `server: cloudflare`, `cf-mitigated: challenge`, `cType: 'managed'`: a managed challenge page with
  "Enable JavaScript and cookies to continue". Because robots.txt itself was challenged, no wage page was
  requested: the rules could not be read, and the brief was to stop at that point. So whether Capology
  has archived 2015/16 payroll pages, and what they look like, is **unknown**, not "no".
- **Not tried, on purpose:** Selenium, headless browsers, cookies, cf-clearance tokens, curl_cffi,
  cloudscraper, FlareSolverr, or any other challenge workaround.
- **Code:** `src.ingest.finance.fetch_capology` checks robots.txt, raises `SourceBlocked` on a challenge
  (tested offline) and has **no page parser**, because no page structure has ever been seen.
  `python -m src.ingest.finance --source capology ...` exits with code 2 and this explanation.

**Next step: fill `data/manual/team_finance.csv` by hand**, one row per club (80 for 2015/16), every
figure from a citable document (`source`, `source_url`, `confidence`), then
`python -m src.ingest.finance --import-csv data/manual/team_finance.csv`. Suggested sources, best first:
club annual reports / Companies House filings (EPL wages are public in the accounts: `official`), the
Deloitte Football Money League and UEFA Club Licensing Benchmarking Report (`official`/`aggregated`),
and published press compilations (`estimate`). No value is to be filled in from memory. Clubs with no
citable figure get a row with blank amounts and `confidence = unknown` (stored as NULL).

Note that club accounts cover the financial year (often July to June, but not for every club) and
usually include staff other than players, so they are not the same thing as a player payroll. Record
which one a row uses in `source`.

## 20. Market values for 2015/16: source audit (2026-09-25, nothing downloaded or scraped)

**Scope.** Transfermarkt publishes *market values* (its community's transfer-fee estimates), not wages.
This audit looks for a free, legal source of 2015/16 market values for the 1,337 profiled players, as
the budget metric for the future Tactical Composer. No scraper was built and no dataset was downloaded.
The profile page shows a "Transfer value: not yet ingested" placeholder until this is resolved.

### A. Existing data
`players.transfermarkt_id` is populated for **0 of 2,735** rows, so **0 of the 1,337** profiled
players (0%). Any source keyed on Transfermarkt ids needs an identity-matching step first
(name + club + birth date; `player_bio` has birth dates for the players Wikidata knows).

### B. Kaggle (metadata read through Kaggle's public `api/v1/datasets/view` endpoint; no files fetched)

| Dataset | Coverage | License | Access | Last updated |
|---|---|---|---|---|
| **davidcariboo/player-scores** "Football Data from Transfermarkt" (source code: GitHub `dcaribou/transfermarkt-datasets`) | Player-level. `player_valuations.csv` (39.6 MB, 650k+ rows: `player_id, date, market_value_in_eur, current_club_id, ...`) is built from Transfermarkt's per-player market-value history, so it holds each player's full valuation history. The pipeline's `config.yml` lists seasons 2012-2025 for GB1, ES1, IT1, FR1 (our Big-4). Also `players`, `transfers`, `appearances`. | CC0 (dataset and repo) | Kaggle API / web download (914 MB total); also direct CSV and DuckDB links from the repo | Version 679, 2026-09-05. **Updates are paused** (data current to 2026-07-06), which doesn't matter for 2015/16 |
| mexwell/football-data-from-transfermarkt | An older (2023) re-upload of the same dcaribou data, 400k valuations | CC0 | Kaggle | 2023-10-02 |
| mexwell/football-transfer-dataset | Transfers 2009-2021, 7 leagues, with `market_val_amnt` **at the time of each transfer** only. Players who didn't move in 2015/16 get no value | "Other (in description)" | Kaggle | 2024-03-27 |
| kanchana1990/football-transfer-value-intelligence-2024 ("...2026") | 508 current top-5-league players, with career value histories (9,779 points). Few 2015/16 squad players | CC BY-SA 4.0 | Kaggle | 2026-02-21 |
| kberkek00/transfermarkt-datas | Current squads with current market value (player_bio); no dated history found in the description | Apache 2.0 | Kaggle | 2026-04-15 |

**The data is Transfermarkt's, whatever the uploader's license says.** CC0 covers the uploader's
own work. It can't waive Transfermarkt's rights in its database (Transfermarkt is German, so the EU
sui-generis database right applies). This is the same question that ruled out the Kaggle FBref mirrors
(section 5), with one difference: there Opta had demanded deletion. We know of no Transfermarkt takedown
of the dcaribou dataset, which has been public since about 2020 (97k downloads). That makes it a low-risk
option for **non-commercial research**, but not a cleared one. Get a legal check before any commercial use.

### C. Transfermarkt internal API (one request)
`GET https://www.transfermarkt.com/ceapi/marketValueDevelopment/graph/28003` (Messi's well-known id;
plain `requests`, project User-Agent, 3 s delay): **HTTP 405**, `server: CloudFront`,
`x-cache: Error from cloudfront`, body titled "Human Verification" (an **AWS WAF** challenge, not
Cloudflare). Blocked. No second request was sent and no workaround was tried. Whether it carries data
back to 2015/16 is therefore unverified from here. The dcaribou pipeline reads the same history,
so it very likely does.

### D. Wayback Machine (CDX index queries to web.archive.org only; no snapshot pages were fetched)
Unique 200-status snapshots between 2015-07 and 2016-06 (`collapse=digest`):

| Page | URL pattern | Snapshots |
|---|---|---|
| EPL league page, season 2015 | `transfermarkt.co.uk/premier-league/startseite/wettbewerb/GB1/saison_id/2015` | 17 (182 for any GB1 league-page variant) |
| Man Utd squad | `transfermarkt.co.uk/manchester-united/kader/verein/985` | 49 |
| Atlético squad / club page | `transfermarkt.com/atletico-madrid/kader/verein/13` / `.../startseite/verein/13` | 21 / 89 |
| Juventus club page | `transfermarkt.com/juventus-turin/startseite/verein/506` | 27 |
| PSG club page | `transfermarkt.com/fc-paris-saint-germain/startseite/verein/583` | 17 |
| Eibar / Gazélec Ajaccio club page | `.../sd-eibar/startseite/verein/1533` / `.../gfc-ajaccio/startseite/verein/3558` | 22 / 17 |
| Watford squad / any Watford page | `transfermarkt.co.uk/fc-watford/kader/verein/1010` / `.../fc-watford/` | 9 / 216 |
| Carpi club page (slug not confirmed) | `transfermarkt.com/carpi-fc-1909/startseite/verein/1066` | 0 |
| Messi profile | `transfermarkt.com/lionel-messi/profil/spieler/28003` | 43 |

Club squad (`kader`) and club (`startseite`) pages list every player's market value, so one snapshot
per club, about 80 in all, would cover the whole dataset. Coverage is good for big clubs and thinner for
small ones (Watford has 9 squad snapshots; the Carpi query returned 0, possibly because of the URL slug).
**Scrapeable?** Technically yes: archive.org serves plain HTML with no challenge. But automated
extraction of Transfermarkt content through the archive is still scraping Transfermarkt's data, so we
treat it like the live site. Manual lookup, with the snapshot URL recorded per row, is acceptable.

### E. GitHub projects
- **dcaribou/transfermarkt-datasets**: the pipeline behind the Kaggle dataset. CC0, 510 stars, pushed
  2026-09-05. No key needed. Recommended (B).
- **dcaribou/transfermarkt-scraper**: the scraper itself. No license file. Not for us (it scrapes).
- **felipeall/transfermarkt-api**: MIT, 453 stars, a self-hosted FastAPI service that scrapes
  Transfermarkt live. No key, but it is a scraper and would hit the same AWS WAF. Rejected.
- **salimt/football-datasets**: 93k players, 901k market-value rows with a `season` column, mirrored
  on Kaggle. **No LICENSE file** (all rights reserved by default). Rejected unless a license is added.
- **ewenme/transfers**: transfers since 1992/93 with fees, not market values. No license. Not suitable.
None needs a paid key.

### F. Recommendation
- **Primary: dcaribou "Football Data from Transfermarkt"** (`player_valuations.csv` + `players.csv`),
  downloaded once from Kaggle in a later session, for non-commercial research use with attribution to
  Transfermarkt and the dataset. Use the latest valuation on or before a fixed reference date
  (proposal: 2016-01-01, mid-season) as the "2015/16 market value". Match players to Transfermarkt ids by
  name, club and birth date, and leave unmatched players NULL (logged, never guessed). Before building:
  confirm on the downloaded file that 2015/16 valuations exist for the Big-4 (the pipeline config says
  they should), and record the legal caveat above in ANALYTICS_DECISIONS.md.
- **Fallback: manual CSV from Wayback Machine snapshots** of each club's 2015/16 squad page (about 80
  pages), one row per player, with the snapshot URL and date recorded on every row.
- **Rejected:** the live Transfermarkt site and ceapi (ToS, AWS WAF), live-scraping wrappers
  (felipeall/transfermarkt-api, transfermarkt-scraper), and unlicensed mirrors (salimt).

## 21. Market values ingested from the dcaribou Transfermarkt dataset (2026-09-26)

**Status: done.** Section 20's primary recommendation is built (ANALYTICS_DECISIONS.md section 25).
The Kaggle dataset `davidcariboo/player-scores` is in `data/raw/transfermarkt/` (12 CSVs, about 915 MB,
gitignored). It was first placed in `football csv files/` at the repo root, which was **not** gitignored,
and was moved to `data/raw/transfermarkt/` before anything was committed. An earlier attempt had the GitHub
code repo instead (`transfermarkt-datasets-master/`: no data, only DVC pointers) and was stopped.
`player_valuations.csv`: 656,301 rows, 2000-01-20 to 2026-06-12, 14,388 rows in 2015-10-01..2016-03-01.

- **Match rate, as measured:** 1,331 of 1,337 profiled players matched (99.6%), 1,326 valued (99.2%).
  Matched: EPL 323/324, La Liga 335/340, Ligue 1 336/336, Serie A 337/337. Not adjusted to any target.
- **Unmatched (6), left NULL on purpose:**
  - *Nicknames the rules do not bridge* (the probable Transfermarkt player is at the club, but only a
    person should confirm it): Robert Brady (Norwich; TM "Robbie Brady", 128229), Jonny Castro (Celta; TM
    "Jonny Otto", 175446), Kolo (Sevilla; TM "Timothée Kolodziejczak", 84085). A reviewed manual-override
    file could add them; none exists, and they are not matched in code.
  - *No 2015/16 league appearance in the Transfermarkt data*, so no club evidence: Markel Susaeta (Athletic;
    exists as TM 54245), Pichu (Sporting Gijón; TM "Pichu Atienza", 59337), Juan Carlos (Málaga; 14
    Transfermarkt players are called "Juan Carlos", so he could not be disambiguated by name either). The
    appearances table misses some players' games; name-only matching is not allowed.
- **Matched but no valuation in the window (5):** Matthieu Saunier (Troyes), Milan Biševac (Lazio), Alexis
  Ruano (Getafe), Abdoul Camara (Angers), Ángel Lafita (Getafe). Shown as "no value available".
- **Weaker matches worth knowing.** 54 fuzzy matches, all read by eye. The lowest-scoring are full legal
  names against short names (João Miranda de Souza Filho / "Miranda", Alex Rodrigo Dias da Costa / "Alex",
  Evaeverson Lemos da Silva Brandão / "Brandão", Maximiliano Moralez / "Maxi Moralez"), accepted only because
  each was the one such name at the club. The two "Juanfran"s (Atlético: Torres; Deportivo: Moreno) were kept
  apart by the club restriction. No match was ambiguous or double-claimed.
- **Snapshot dates differ by league** (Serie A 4 Jan, Ligue 1 4 Feb, EPL 9 Feb, La Liga 22 Feb 2016): up to
  seven weeks apart.
- **Club totals are partial by design:** only profiled (900+ minute) players are summed. Atlético EUR 317m
  (15/15 profiled) vs EUR 387.5m over its 24 valued Transfermarkt appearance-makers.
- **Licensing:** CC0 from the uploader, but the data is Transfermarkt's. Low-risk for non-commercial
  research, **not legally cleared**; get a legal check before any commercial use (section 20.B).
- **Not done:** `players.transfermarkt_id` is still empty (the id lives in `player_market_values` only).
  `rapidfuzz` is declared in `pyproject.toml` but not installed in `.venv`; stdlib `difflib` is used, so no
  dependency was added or installed.

## 22. Team wage bills: pipeline ready, table empty (2026-09-26)

**Status: working but empty, by design.** The importer, the CSV contract (ANALYTICS_DECISIONS.md section
26), the reject report and the UI states are built and tested. `data/manual/team_finance.csv` is
header-only, so `team_finance` has **0 of 80** clubs and every page shows "Wage bill: not available".
The figures are to be filled in by hand from citable documents; none will be generated or estimated in
code.

- **Coverage so far:** none. No row has been imported.
- **Blocked (automated):** FBref wages pages and Capology, both behind a Cloudflare challenge (sections 1
  and 19). Not worked around. `fetch_capology` stays a scaffold that stops on the challenge.
- **Viable (manual, cited):**
  - Club annual reports and regulator filings (`official`). EPL clubs file accounts at Companies House;
    wages are a line of the accounts. Spanish clubs publish annual reports (LaLiga also publishes squad
    cost limits, which are a different measure). Italian and French clubs' accounts are less uniformly
    public. Accounts cover the club's financial year, which is not always July-June, and usually include
    all staff.
  - Deloitte Annual Review of Football Finance / Football Money League and the UEFA Club Licensing
    Benchmarking Report (`aggregated`): wage totals for the biggest clubs, often not all 80.
  - Press payroll compilations (`estimate`), with the article cited.
  - Wayback Machine snapshots of payroll trackers, read and cited by hand (not scraped), as `estimate`.
- **Still missing:** every figure; a cited 2015/16 USD rate (USD rows are rejected until one is added);
  a decision on mixing accounts (all staff, financial year) with payroll estimates (players only) in one
  ranking. The ranking currently mixes whatever is imported, with each row's confidence shown beside it.

## 23. Market values: the 11 profiled players with no value (2026-09-26)

**Status: accepted, left NULL.** 1,326 of 1,337 profiled players have a 2015/16 market value; 11 do not
(ANALYTICS_DECISIONS.md sections 25 and 27). Nothing is estimated for them; the UI says "no value
available". Minutes are 2015/16 totals.

- **Unmatched (6)**, `match_confidence` NULL, listed in `data/processed/unmatched_market_values.csv`:
  | Player | Club | Minutes | Why |
  |---|---|---|---|
  | Robert Brady | Norwich City | 2,969 | Nickname: TM "Robbie Brady" (128229) |
  | Jonny Castro | Celta Vigo | 2,932 | Nickname: TM "Jonny Otto" (175446) |
  | Pichu | Sporting Gijón | 2,880 | No 2015/16 TM appearance; TM "Pichu Atienza" (59337) |
  | Kolo | Sevilla | 2,329 | Nickname: TM "Timothée Kolodziejczak" (84085) |
  | Markel Susaeta | Athletic Club | 1,807 | No 2015/16 TM appearance; exists as TM 54245 |
  | Juan Carlos | Málaga | 1,177 | No 2015/16 TM appearance; 14 TM players share the name |
- **Matched, no valuation between 2015-10-01 and 2016-03-01 (5):** Matthieu Saunier (Troyes, 2,315),
  Abdoul Camara (Angers, 1,445), Milan Biševac (Lazio, 1,363), Alexis Ruano (Getafe, 1,350), Ángel Lafita
  (Getafe, 999). Their Transfermarkt ids are stored; only the value is NULL.
- **Unblock path:** a reviewed manual-override file (`match_confidence = manual`) could add Brady, Jonny,
  Kolo, Pichu and Susaeta, whose Transfermarkt ids are known above. It does not exist yet and they are not
  matched in code. The 5 unvalued players would need a valuation outside the window, which the snapshot rule
  does not allow.
- **Dependency:** `rapidfuzz` (declared in `pyproject.toml`) is now installed in `.venv` and used (section
  21's "not installed" note no longer applies).

## 24. Tactical Composer: team-style axes with no player-level measure (2026-09-26)

**Status: built without them.** Three of the 12 team-style axes cannot be mirrored by stored player
metrics, so the Composer shows their sliders disabled and never uses them (ANALYTICS_DECISIONS.md
section 28). Nothing was re-ingested or re-parsed for this.

- **Crossing volume.** Player crosses are not extracted; `player_match_stats` has only
  `cross_clearances` (a defensive count). Unblock: count `pass.cross` (non-corner) per player in
  `src/ingest/statsbomb/parse.py` from the cached event files, as `team_style` already does per team.
- **Width.** No wide-touch or mean-y measure per player. Progressive carries (the brief's stand-in) do
  not measure width. Unblock: mean |y - 40| of a player's passes or touches from the cached events (the
  heatmap grids in `player_heatmaps` could give a coarse version without a re-parse).
- **Set-piece reliance.** A team's share of xG from set pieces. A player-level share of set-piece
  involvement could be derived (set-piece shots and deliveries), but it would describe a role, not the
  team pattern. Left team-level.
- **Partial:** Direct speed has only progressive carries; the briefed `direct_pass_rate` (forward yards
  per pass) is not stored. Unblock: sum pass dx per player during parsing.
- **Known weaknesses of the result** (section 29): the first version ignored quality and came in far
  under budget. Since the style + quality objective (2026-09-26) the budget is used, but log-scaled
  market value separates players only weakly, so high-volume players still win most slots (Kanté, fit
  47%, is excluded by the 50-60% fit floors). A possible league effect in pooled percentiles is unchecked.
- **Mapping inverts for ball-winners in deep teams** (found 2026-09-26, section 29): per-90 pressing and
  passing volume measure a player's workload, which for a defensive midfielder in a low block runs
  opposite to the team's PPDA and possession. Kanté fits Leicester's own style vector at 0.21 (151st of
  178 CMs). Unblock: pressing location (share of pressures in the opponent half) or volumes relative to
  the team's totals, from the cached events. Not done; no re-parse in this session.
- **Stale text, left on purpose:** the Composer page's "Why these players" caption still says quality is
  "market value on a log scale"; it is now square-root scaled (the brief allowed no UI change).
- **Stale text, left on purpose:** the Methodology page's Composer section still says suggestions "often
  come in far under budget". That was true before the revision; it was not edited because the brief
  allowed changes to the Composer page only. The three unmeasurable axes above are unchanged.
