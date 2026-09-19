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

### What the foundation does (built 2026-09-19, one match ingested)

Modules in `src/ingest/statsbomb/`: `discovery.py` (competitions and matches), `fetch.py` (cached, validated
downloads), `parse.py` (per-player match metrics), `identity.py` (`statsbomb:<id>` players), `ingest.py`
(single-match orchestrator). New tables: `matches`, `player_match_stats`; new column `players.statsbomb_id`
(added by an idempotent migration in `init_db`).

Proof match: Manchester City 1-3 Leicester City (2016-02-06, id 3754290), 28 players. Verified against
independent facts: team minutes sum to 11 x match length, lineup minutes agree with all 6 `Substitution`
events, goals match the official score, and pass, shot and card totals equal the raw event counts.

Metrics computed: minutes, position played, passes (attempted, completed, %), progressive passes,
passes into final third and box, carries, progressive carries, carry distance, pressures (with mean
duration and counterpressures), tackles (and won), interceptions, blocks, clearances, ball recoveries,
dribbles, shots, goals, **xG (present in the data: `shot.statsbomb_xg` on every shot)**, fouls, cards.
Definitions and thresholds are documented at the top of `src/ingest/statsbomb/parse.py`.

### Known limitations of the foundation

- **Progressive thresholds are our own:** a pass or carry is progressive if it cuts the straight-line
  distance to the goal centre by at least 10 yards. This is not FBref's or StatsBomb's own definition, so
  do not compare the numbers with published ones.
- **Minutes are event-clock play time**, so a full match is about 97 minutes here, not 90.
- **Minutes and position come from the lineup file's position spells.** If a match's lineup lacks them for a
  player who has events, parsing raises `LineupError` rather than guessing. Only one match has been checked.
  Older or partial matches may lack spells; this must be checked across all 380 before trusting a season.
- **Temporary `Player Off`/`Player On` (injury) events are ignored.**
- **`position_played` is StatsBomb's position name** (e.g. "Left Center Midfield"). It is not mapped to
  GK/DEF/MID/FWD yet. StatsBomb players have no `position_group`.
- **`interceptions` counts attempts** whatever the outcome; `tackles` counts all Tackle duels.
- **Player names** use StatsBomb's nickname where present (e.g. "Pablo Zabaleta"). Not linked to Understat.
- **Team names** use `team_identity`, which only knows 2024/25 EPL clubs; 2015/16 clubs that are not in that
  table (Swansea City, Watford, Norwich City, Sunderland, ...) pass through with StatsBomb's spelling.
- Per-match rows are not aggregated to seasons yet, and no analytics exist.

## Next priority: the full 2015/16 EPL ingest (next session)

1. Loop `ingest_match` over the 380 matches from `list_available("EPL", 2015)`, resumable from the
   cache, collecting failures and failing loudly (do not silently skip a match). Expect about 3 MB per
   events file, roughly 1.1 GB of raw JSON on disk D (962 GB free) and about 25 to 30 minutes at the 2 s delay.
2. Before trusting the season, check that every match has lineup position spells and that per-match
   invariants hold (team minutes vs match length, goals vs official score, substitutions cross-check).
3. Add the 2015/16 EPL clubs missing from `team_identity`.
4. Map StatsBomb position names to GK/DEF/MID/FWD (as a reviewed, documented table, not a heuristic).
5. Only then aggregate to season level and start analytics; La Liga, Serie A and Ligue 1 follow the same path.
