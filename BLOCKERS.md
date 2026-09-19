# BLOCKERS

## 1. FBref ingest is blocked (Selenium) — OPEN

- **Blocked file:** `src/ingest/_blocked/fbref.py` (see `src/ingest/_blocked/BLOCKED.md`). Never executed.
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

### Replacement options

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

Superseded: see section 5. The earlier recommendation (StatsBomb as the primary source) does not
hold for current-season data, because StatsBomb Open Data has no 2024/25 coverage of any top-5 league,
and plain-requests FBref (Option 2) is blocked by Cloudflare and no longer has the advanced data.

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

## Next priority

Decide the defensive/passing stats approach (section 5). Apply a minutes threshold when using
position groups.

## 5. Player-level defensive and passing stats: source investigation (2026-09-19)

Goal: find a non-Selenium source for tackles, interceptions, blocks, pressures, progressive
passes/carries and pass completion. Investigation only; no ingest code was written. Each source was
hit only a handful of times.

### A. FBref via plain `requests`: NOT VIABLE

- **Request:** `GET https://fbref.com/en/comps/9/2024-2025/stats/2024-2025-Premier-League-Stats` with a
  Chrome User-Agent and Accept headers.
- **Response:** `403 Forbidden`, `server: cloudflare`, `cf-mitigated: challenge`, 5938-byte body starting
  `<!DOCTYPE html>...<title>Just a moment...</title>` (Cloudflare's JavaScript challenge). `requests`
  cannot run it.
- **Site-wide:** `/robots.txt` and `/en/` return the same 403 challenge, so no path is exempt.
- **CSV export:** FBref's "Get table as CSV" is a client-side JavaScript feature of the rendered page
  (per public write-ups; I could not inspect the page to confirm). There is no server-side CSV endpoint
  to call, and the `/en/share/<hash>/` links cannot be tried without the page.
- **Public workarounds** (browser-fingerprint TLS clients such as curl_cffi, cloudscraper, FlareSolverr,
  headless browsers) are Cloudflare-evasion tools. I did not try them: they are new dependencies, and
  evasion is at odds with the "no browser automation" rule and with FBref's terms. The one public
  Kaggle-feeding scraper I found (below) uses Playwright.
- **Second, independent problem: the advanced data no longer exists.** In January 2026 Opta (Stats
  Perform) terminated Sports Reference's data feed and required deletion; FBref removed all Opta advanced
  stats (announced ~20-23 Jan 2026) and says it keeps "basic, non-advanced" stats. The sources I could
  read do not list categories, but tackles, interceptions, pressures, progressive actions and pass
  completion were Opta-derived (my inference), so they are gone from the live site even if Cloudflare
  were bypassed. The Sports Reference announcement itself returned 403 to my fetcher; this rests on press
  coverage.
- **Verdict:** FBref is closed for defensive stats, live or scraped.

### B. StatsBomb Open Data: usable, but NOT current

Repo `statsbomb/open-data`: last push 2026-09-07, last data commit 2026-05-26 ("1647 new games").
License shows as `NOASSERTION`; the README requires crediting StatsBomb and using their logo on anything
published. Plain HTTP from `raw.githubusercontent.com`; no auth.

Coverage (men's; `data/competitions.json`, match counts from `data/matches/<comp>/<season>.json`):

| Competition | Seasons available | Coverage |
|---|---|---|
| Premier League | 2003/04 (38 matches), **2015/16 (380, full season)** | 2015/16 all 20 clubs |
| La Liga | 2004/05 to 2020/21 | **2015/16 = 380 matches (full)**; other seasons are Barcelona matches only (7-38 each) |
| Serie A | 1986/87 (1), **2015/16 (380, full)** | 2015/16 all clubs |
| Bundesliga | 2015/16 (34), 2023/24 (34) | Bayer Leverkusen matches only |
| Ligue 1 | **2015/16 (377)**, 2021/22 (26), 2022/23 (32) | 2015/16 near-full; 2021/22 and 2022/23 are PSG matches only |
| Champions League | 1970/71 to 2018/19 (18 seasons) | 2017/18 and 2018/19 = the final only (1 match each); others partial |

**2024/25 is not available for any of these.** The only 2024 entries in the whole dataset are Copa America
and UEFA Euro. No 2025/26 either.

Schema (checked on Bundesliga 2023/24 match 3895292, 3,843 events, 3.2 MB JSON): one event list per match.
Every event carries `type`, `player`, `team`, `position` (actual on-pitch role, e.g. "Center Attacking
Midfield"), `location`, `minute`/`second`, `under_pressure`, `possession`, `related_events`.
- **Pass:** `recipient`, `length`, `angle`, `height`, `end_location`, `body_part`, `type`, flags (`cross`,
  `through_ball`, `switch`, `shot_assist`, ...). `outcome` is absent when the pass completed. So pass
  completion and progressive passes can be derived from `location`/`end_location`.
- **Carry:** `end_location` (progressive carries derivable).
- **Pressure:** `location`, `duration`, `counterpress`.
- **Duel:** `duel.type` is "Tackle" or "Aerial Lost", with `outcome` (Won / Lost In Play / ...).
- **Interception**, **Block**, **Clearance**, **Ball Recovery**, **Dribbled Past** are their own event types.
- Lineups (`Starting XI`) and `Substitution` events allow minutes to be derived.

Verdict: everything needed exists, but **only for old seasons**: full-league coverage is 2015/16 alone
(PL, La Liga, Serie A, Ligue 1); the rest are single-club subsets. It cannot describe current players.

### C. Mirror datasets: the only route to 2024/25, unverified

Kaggle pages render client-side, so last-update date, license and columns could not be read without a
browser or Kaggle credentials. Marked unverified rather than guessed.

| Candidate | Covers (claimed) | Last update | License |
|---|---|---|---|
| kaggle.com/datasets/hubertsidorowicz/football-players-stats-2024-2025 | Big 5 leagues, 2024/25, FBref | unverified | unverified |
| kaggle.com/datasets/siddhrajthakor/fbref-premier-league-202425-player-stats-dataset | Premier League, 2024/25, FBref | unverified | unverified |
| kaggle.com/datasets/furkanark/premier-league-2024-2025-data | Premier League 2024/25, FBref match and stats pages | unverified | unverified |
| github.com/hubertsid/football-data-scraper (feeds the 2025-26 sibling of the first dataset) | Big 5, current season, FBref | last push 2026-03-17; **every weekly run since at least 2025-12-29 failed** | none declared |
| github.com/eddwebster/football_analytics | curated resource list, not a data dump | 2025-10-09 | none declared |

Facts from the scraper source: it collects Big-5 player tables `standard`, `shooting`, `passing`,
`passing_types`, `gca`, `defense`, `possession`, `playing_time`, `misc`, `keepers`, `keepers_adv`, so a
snapshot from it would contain the defensive/passing/possession tables. It uses Playwright (a headless
browser), which independently confirms plain HTTP does not work. Its runs failing since December 2025
suggests the current-season dataset is stale or empty.

Risks: (1) the 2024/25 snapshots are one-off, static season aggregates whose provenance is unaudited;
(2) the data is Opta-derived and Opta has demanded deletion, so redistribution licensing is doubtful and
the datasets may be pulled; (3) nothing verifies they predate the January 2026 removal (an automated
overwrite could have replaced advanced columns with blanks); (4) they carry no per-match data and must be
joined to Understat by fuzzy player name.

### Recommendation

There is no verified free, non-Selenium source of *current* (2024/25 or later) defensive stats today.
- **StatsBomb Open Data** is the only clean, legal, stable source and has everything needed, but only for
  2015/16 full leagues (~1,500 matches). Use it to design and validate defender and defensive-midfielder
  metrics, position groups and similarity, not to profile current players.
- **A Kaggle FBref 2024/25 snapshot** is the only candidate for current-season defensive numbers, pending a
  verification step: download one file (needs your approval) and confirm the `defense`, `passing` and
  `possession` columns are populated, then decide on the licensing risk.
- Do not spend effort on FBref directly.

An ingest module for the StatsBomb path would need to handle: `competitions.json` and per-season
`matches` discovery; one 2-4 MB events file per match (~380 files per full season) cached to `data/raw`,
with a courtesy delay; deriving minutes from lineups/substitutions; mapping StatsBomb player/team names
(different IDs from Understat) to our canonical names, which needs a player identity layer that does not
exist yet; computing progressive and completion metrics from coordinates in `/src/analytics`; and the
required StatsBomb attribution in the UI and reports.
