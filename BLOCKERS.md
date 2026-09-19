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

Option 1, StatsBomb Open Data, as the primary style/similarity source, with Understat for
current-season xG and football-data.co.uk for team context. It is the only option that is free,
legal to use, stable and rich enough for style profiles. Its coverage gap is real and should be
weighed against the target leagues before committing. Option 2 is worth a time-boxed spike as a
supplement, not a foundation. Avoid Option 3.

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

Migrate the primary style/similarity source to StatsBomb Open Data before building similarity
(Prompt 3). Apply a minutes threshold when using position groups.
