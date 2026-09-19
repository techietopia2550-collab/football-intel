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
- Per-match rows are not aggregated to seasons yet, and no analytics exist.

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

## Next priority (future session)

1. Investigate and, if fixable without guessing, fix the 6 lineup/substitution-mismatch failures above
   (or document them as permanently unrecoverable for those match ids).
2. Decide whether to fix the unmatched-`Player Off` gap case (1 occurrence) the same way dismissals are
   handled, or leave it as a reported violation.
3. La Liga, Serie A and Ligue 1 2015/16 follow the same path (`ingest_season` already supports them via
   `--league LALIGA/SERIEA/LIGUE1`); each needs its own team-name and position pre-flight pass.
4. Aggregate `player_match_stats` to season level and start analytics (out of scope this session).
