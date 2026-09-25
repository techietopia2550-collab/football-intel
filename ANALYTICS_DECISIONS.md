# ANALYTICS_DECISIONS

Every definition choice behind the 2015/16 EPL metrics, percentiles and profiles (StatsBomb Open Data).
These are analytics choices, not data corrections: they do not go in `statsbomb_corrections.csv`.
Code: `src/analytics/metrics.py` (definitions), `src/transform/normalize.py` (season aggregation),
`src/analytics/percentiles.py`, `src/analytics/profile.py`, `src/analytics/heatmaps.py`,
`src/analytics/similarity.py`. Everything describes **2015/16 only**.

**Section 13 (4 leagues pooled) supersedes every "EPL only" / "323 players" / per-league-season statement below.**

**Sections 8-12 were added when position groups went from 4 to 8 and the advanced metrics, heatmaps and
similarity were built. Where they contradict sections 3-7, they win:** sections 3-7 were written for the
4-group scheme (GK/DEF/MID/FWD) and are kept as history, with the superseded lines marked.

## 1. Scope and inputs

- **Season and league:** ENG-Premier League 2015/16. 374 of 380 matches. The 6 excluded matches are a
  StatsBomb lineup flaw (BLOCKERS.md section 8). Counts and minutes drop together, so per-90 rates are
  not biased by the gap, but `matches` and `minutes_total` can be up to 6 games short for some players.
- **Minutes are normalized to a 90-minute match** at ingest (BLOCKERS.md section 6), an approximation.
- **Coordinates:** StatsBomb 120x80 yards, every team attacking towards x=120.
- **Penalty shoot-outs** (period 5) are excluded from every count.

## 2. Event definitions (the counts behind the metrics; in `src/ingest/statsbomb/parse.py`)

| Count | Definition |
|---|---|
| shots, goals | `Shot` events; goal = outcome Goal. **Penalties are included.** Own goals are not shots. |
| xg | sum of StatsBomb `shot.statsbomb_xg`. **Pre-shot xG, penalties included** (no non-penalty xG yet). |
| shots_on_target | outcome Goal, Saved or Saved to Post. **Not** Blocked, Off T, Wayward, Post, Saved Off Target. |
| key_passes | `Pass` events with `assisted_shot_id` (StatsBomb's `shot_assist`, or `goal_assist` when it scored). |
| assists | `Pass` events flagged `goal_assist`. A subset of key passes. |
| xa | sum of the xG of the shot each key pass set up. |
| passes_into_box | completed pass ending in the box (x >= 102, 18 <= y <= 62) that started outside it. |
| passes_into_final_third | completed pass starting at x < 80 and ending at x >= 80. |
| progressive pass | **completed pass that cuts the straight-line distance to the opponent's goal centre by 10+ yards.** Our own threshold, not FBref's or StatsBomb's, so not comparable with published numbers. |
| progressive carry | carry that cuts the distance to goal centre by 10+ yards. Same caveat. |
| carry_distance | total straight-line length of all carries, yards. |
| tackles | every `Duel` of type Tackle, whatever the outcome. Won = Won, Success, Success In Play, Success Out. |
| interceptions | `Interception` events, **attempts whatever the outcome**. |
| ball_recoveries | `Ball Recovery` events not flagged `recovery_failure`. |
| blocks, clearances | `Block`, `Clearance` events. |
| pressures | `Pressure` events; counterpressures are those flagged `counterpress`. |
| dribbles | `Dribble` events (take-ons attempted); success = outcome Complete. |
| yellow / red | Yellow Card + Second Yellow / Red Card + Second Yellow, from fouls and Bad Behaviour. |
| gk_saves | `Goal Keeper` events Shot Saved, Shot Saved to Post, Penalty Saved. **Not** Shot Saved Off Target, and not bare `Save` events (they link to no shot). |
| gk_goals_conceded | `Goal Conceded` and `Penalty Conceded` events. |
| gk_xg_on_target_faced | pre-shot xG of the shots behind saves and goals conceded. **Not PSxG.** |
| gk_claims / punches / sweeper | `Collected` / `Punch` / `Keeper Sweeper` events. Style measures. |
| aerials, receiving, defensive location | added in section 9 (definitions, labels and limits there). |

## 3. Season aggregation (`normalize.py`)

- **Sums, then ratios.** Minutes and counts are summed over matches. Per-90 = `count / minutes_total * 90`.
  A ratio (pass completion, tackle win %, ...) is `sum(numerator) / sum(denominator)`, never an average of
  per-match ratios. `pressure_duration_avg` is weighted by pressures.
- **Minimum minutes: 900** (configurable). Below it, or with no position group, the player goes to
  `insufficient_sample` with a `reason`, and is logged. That table has totals only, no per-90 rates:
  a rate over a few hundred minutes is not something to rank on. 323 qualify, 226 do not.
- **Position group = the group with the most minutes over the season**, ties broken alphabetically.
  `position_group_share` is that group's share of the player's minutes. Under 60% is a mixed role and
  `profile()` warns (21 players on the coarse group; 75 on the fine group, section 8). The label is the
  plurality, not a certainty. **Ranking now uses `position_group_fine` (section 8).**
- **Mixed roles are not split:** all of a player's minutes count in his one group's rates.
- **Multi-team players** (11): the team with most minutes is `team`; the split is in `team_split` and
  `multi_team_players_2015_16.csv`. Their stats cover all clubs.
- **Coarse position taxonomy** (still stored as `position_group`, no longer used for ranking): wing-backs and
  full-backs are DEF; defensive and all central and wide midfielders are MID; wingers, wide attacking mids and
  strikers are FWD; central attacking mids are MID.
- ~~**DEF pools centre-backs and full-backs.**~~ **Resolved:** CB and FB are separate groups (section 8).

## 4. Metrics (49: 40 outfield, 9 goalkeeper; the 10 added in section 9)

Status: **direct** = event measure aggregated with no normalization; **derived** = per-90 or a ratio of
event counts (the counts themselves come straight from events); **unavailable** = not computable.
`guard` = a ratio is left empty (NaN) if its denominator is below this, so a small sample is never ranked.

| Metric | Group | Status | Guard | Direction |
|---|---|---|---|---|
| goals_p90, xg_p90, shots_p90 | ATTACKING | derived | | higher |
| shot_accuracy (on target / shots, %) | ATTACKING | derived | 15 shots | higher |
| xg_per_shot | ATTACKING | derived | 15 shots | higher |
| assists_p90, xa_p90, key_passes_p90, passes_into_box_p90 | CREATION | derived | | higher |
| pass_completion_pct | PASSING | derived | 200 passes | higher |
| progressive_passes_p90, passes_into_final_third_p90 | PASSING | derived | | higher |
| carries_p90, progressive_carries_p90, carry_distance_p90 | POSSESSION | derived | | higher |
| dribbles_p90 (**added**), dribble_success_pct (**added**) | POSSESSION | derived | 20 dribbles | higher |
| tackles_p90, interceptions_p90, blocks_p90, clearances_p90, ball_recoveries_p90 | DEFENDING | derived | | higher |
| tackle_win_pct | DEFENDING | derived | 20 tackles | higher |
| pressures_p90, counterpressures_p90 | PRESSING | derived | | higher |
| pressure_duration_avg (seconds) | PRESSING | **direct** | 100 pressures | descriptive |
| fouls_won_p90 | DISCIPLINE | derived | | higher |
| fouls_committed_p90, yellow_p90, red_p90 | DISCIPLINE | derived | | descriptive |
| gk_save_pct = saves / (saves + conceded) | GK | derived | 40 on target | higher |
| gk_goals_prevented_p90 (**proxy**) | GK | derived | | higher |
| gk_goals_conceded_p90 | GK | derived | | **lower** |
| gk_pass_completion_pct | GK | derived | 200 passes | higher |
| gk_shots_on_target_faced_p90, gk_saves_p90, gk_claims_p90, gk_punches_p90, gk_sweeper_actions_p90 | GK | derived | | descriptive |

Decisions behind the table:

- **Dribbling was added** (`dribbles_p90`, `dribble_success_pct`). It is not in the requested metric list, but the
  sanity check needs it to describe wide players such as Mahrez.
- **`key_passes_p90` is computable** (the "if computable" in the brief): every key pass carries `assisted_shot_id`.
- **Guards are our own round numbers,** chosen so a 900-minute player is not ranked on a handful of events.
  They are not statistically derived. On this data `shot_accuracy` and `xg_per_shot` are empty for 77 of 118 DEF,
  22 of 102 MID and 2 of 76 FWD.
- **Descriptive metrics are ranked but never called strengths or weaknesses.** That covers style measures
  (pressure duration, GK claims/punches/sweeping), team-dependent volume (GK saves and shots faced), and
  discipline counts. Discipline was not judged because a low foul count is not clearly a skill (it can mean not
  tackling) and a high one is not clearly a flaw. `fouls_won_p90` is judged (drawing fouls is a winger skill).
- **`gk_goals_conceded_p90` is lower-is-better** and is flipped in profiles. It is heavily shaped by the team's
  defence, so it is a weak signal about the keeper.
- **`gk_goals_prevented_p90` is a proxy and is negative for every keeper** (-0.4 to -1.1 per 90), because pre-shot
  xG averages over all shots and these are only on-target ones. Use it for ranking; never read its level.
- **GK and outfield metrics never mix.** Outfield metrics are empty for goalkeepers and the reverse; percentile
  pools are separate. `gk_pass_completion_pct` is the same arithmetic as `pass_completion_pct` but ranked among
  goalkeepers only.

**Unavailable in this dataset** (`metrics.UNAVAILABLE`): PSxG and goals minus PSxG (pre-shot xG only), crosses
stopped % (crosses faced not recorded), `last_line_events_p90` (no teammate positions at defensive actions) and
`is_captain` (no captain field). ~~aerial duels won %~~ **is computable and built** (section 9): won aerials are
the `aerial_won` flag on the winner's follow-up event, not a duel, which the earlier note missed.
**Computable but not built:** non-penalty xG, long-pass and launch % (pass length is in the events), touches.

## 5. Percentiles (`percentiles.py`)

- **Ranked only within position group, across the whole league-season, among qualifying players.** Never across
  positions.
- **Formula:** `(mid-rank - 0.5) / n * 100`, ties sharing the average rank. Reads as "share of the group this
  player is above, counting half of any ties". Range is about 0.5 to 99.5 rather than 0 to 100.
- **The stored percentile is always the rank of the value** (high number = high percentile). Lower-is-better
  metrics are flipped only when profiles are built.
- **Empty values** (a ratio under its guard) are stored with a NULL value and percentile, and not counted in
  `sample_size`.
- **Columns:** `sample_size` = players ranked on that metric in that group; `group_size` = all qualifying players
  in the group; `small_group` = `group_size` < 20.
- **Group sizes (4 groups, superseded):** GK 27, DEF 118, MID 102, FWD 76. **Now, by fine group:** GK 27, CB 59,
  FB 59, DM 40, CM 46, AM 14, W 41, ST 37. AM is under 20 and is flagged `small_group`; 27 keepers is close
  to the line and the GK percentiles are coarse. The table column is `position_group_fine`.
- **All ties share a rank.** `red_p90` is 0 for almost everyone, so every such player sits at about the same
  middling percentile. It carries no ranking signal.

## 6. Profiles (`profile.py`)

- **Strength = percentile >= 80; weakness = percentile <= 20** (the top and bottom 20%). Both are inclusive.
  For lower-is-better metrics the percentile is flipped first.
- **Only metrics relevant to the position are judged** (`RELEVANT_METRICS`, now keyed by the eight fine
  groups). Deliberate omissions: creation and shooting metrics for CB and DM (a centre-back's low key passes is
  not a weakness), pass completion for AM/W/ST (style, not skill), and all outfield metrics for GK. Splitting
  DEF means full-back creation is now judged (`FB` includes assists, xA, key passes, passes into the box).
- **Neutral = every other relevant metric,** including all descriptive ones. `unranked` lists relevant metrics
  with no percentile because of a sample-size guard.
- **Match by name,** ignoring accents and case; an exact name wins, otherwise the query must be a substring of
  exactly one name. An ambiguous query raises `ValueError` listing candidates (it never guesses): "Morgan" matches
  three players.
- **A player under 900 minutes** returns `qualified: False`, the reason, and no strengths or weaknesses.
- **`warnings`** carry: a small position group, a mixed role (under 60% of minutes), and a multi-team player.

## 7. Validation on known players (2026-09-20, under the 4-group scheme; re-run in section 12)

Checked before committing. Strengths and weaknesses are within-position percentiles.

- **Kanté (MID):** tackles, interceptions, ball recoveries all 99.5th; pressures 97.5th. Weak on xG and shots.
- **Mahrez (MID):** goals and xG 99.5th, dribbles 99.5th, xA 97.5th, fouls won 97.5th. Weak on pass completion
  (8th) and final-third passes.
- **Vardy (FWD):** xG 98th, xG per shot 98th, goals 96.7th, shots 87.5th. Weak on fouls won.
- **Özil (MID):** assists, key passes, passes into box all 99.5th; xA 98.5th. Weak on tackles, interceptions,
  pressures. Our data has 19 assists for him, the official 2015/16 figure.
- **Wes Morgan (DEF):** no strength at 80 or above; weak on progressive passes, carries and final-third passes
  (progressive passes and carries 1st, final-third passes 4th). Clearances 65th, blocks 66th. Re-ranked among
  centre-backs only, none of the ten passing/defending metrics I checked reaches 80 (blocks 78th is the highest),
  so the CB/FB pooling did not hide a strength. His reputation rests on aerial dominance, which is unavailable.

These are checks against reputation, not proof of accuracy. Nothing here is a claim about how well the
percentiles predict anything.

## 8. Position groups: 4 -> 8 (2026-09-20)

`position_group_fine` = GK, CB, FB, DM, CM, AM, W, ST. It is computed per match (the fine group with the most
minutes in that match, a tie going to the earliest spell) and per season (the fine group with the most minutes
over the season). The lineup position string is never used downstream: every position goes through
`FINE_POSITION_GROUPS` in `src/ingest/statsbomb/parse.py`. The coarse `position_group` (GK/DEF/MID/FWD) is kept
unchanged for reference; percentiles, profiles and similarity use the fine group only.

| Fine group | StatsBomb positions | Qualifying players (900+ min) | Players under 60% of minutes in it |
|---|---|---|---|
| GK | Goalkeeper | 27 | 0 |
| CB | Center Back, Left/Right Center Back | 59 | 4 |
| FB | Left/Right Back, Left/Right Wing Back | 59 | 6 |
| DM | Left/Right/Center Defensive Midfield | 40 | 10 |
| CM | Left/Right/Center Midfield, Left/Right Center Midfield | 46 | 26 |
| AM | Left/Right/Center Attacking Midfield | **14 (< 20: flagged)** | 9 |
| W | Left Wing, Right Wing | 41 | 15 |
| ST | Left/Right Center Forward, Center Forward, Secondary Striker | 37 | 5 |

- **AM has 14 players, below the 20 used to flag small groups.** Its percentiles are coarse (the best possible
  percentile is about 96.4) and similarity in that group is thin: Özil's list is meaningful only at #1 and #2.
- **The brief's mapping was followed literally.** Left/Right Midfield is CM (so Mahrez, 91% "Right Midfield" in
  Leicester's 4-4-2, is a CM), Left/Right Attacking Midfield is AM (not W), and only Left/Right Wing is W. StatsBomb
  has no plain "Striker" or "Left/Right Striker" position; the forward slots are Left/Right Center Forward and
  Center Forward, all ST, plus Secondary Striker. Wing-backs are FB with the full-backs.
- **Mixed roles get more common:** 75 of 323 players have under 60% of their minutes in their fine group (21 under
  the coarse grouping), 26 of them in CM. All of a player's minutes still count in his one group. Kanté is CM at 57%
  (the rest DM). `profile()` and `find_similar()` warn on these.
- **A NULL fine group is never accepted silently:** it is an invariant violation at ingest (`checks.py`) and a
  named exclusion (`no position_group_fine`) in `insufficient_sample`. There are 0.

## 9. Advanced metrics (2026-09-20)

What StatsBomb 2015/16 actually contains was checked in the cached raw events (380 matches), not assumed:

- **Aerials:** `Duel` has only two types, `Tackle` (15,445) and `Aerial Lost` (16,845). There is no `Aerial Won`
  duel; the winner is flagged with `aerial_won` on his own follow-up event, which exists only on `Pass`, `Clearance`,
  `Shot` and `Miscontrol` (10,519 + 4,847 + 909 + 570 = **16,845**). Wins equal losses exactly, so every contest
  has one loser event and one winner flag. The earlier claim in `metrics.UNAVAILABLE` ("won aerials are not a full
  event") was wrong and has been removed.
- **Captain:** no. Lineup player records have only `player_id, player_name, player_nickname, jersey_number,
  country, cards, positions` in all 13,678 records, and the string "captain" appears in none of the 380 lineup files,
  380 event files, the match files or `competitions.json`. `is_captain` is not added. (BLOCKERS.md section 11.)
- **Freeze frames:** only `Shot` events carry one. Teammate positions at a tackle or clearance are unknown, so
  `last_line_events_p90` cannot be computed and is not added.
- **Other event types present and not used:** `Dribbled Past` (8,771), `50/50` (559), `Miscontrol`, `Dispossessed`,
  `Shield`, `Error`, `Offside`, `Foul Won`. Not needed for anything asked.
- **Period 5 (shoot-out) is absent from this season**; coordinates run x 0.1-120.9, y 0.1-80.0.

Every new metric, its label and what it does NOT measure. **Fact** = the event count is exactly what StatsBomb
records. **Proxy** = a real count that stands in for something it is not. **Approximation** = a computation that
loses information.

| Metric | Definition | Label | Does NOT measure |
|---|---|---|---|
| `aerials_attempted_p90` | (aerials won + lost) per 90 | fact, descriptive | Skill; role and team style decide how many contests arise. |
| `aerials_won_p90` | events with `aerial_won` per 90 | fact | Ability alone: it is volume times success. Not compared with the opponent's aerial threat. |
| `aerial_win_pct` | won / (won + lost), %; empty under 20 contests | fact | The quality of the ball, the opponent, or whether a duel was contested at all. |
| `avg_defensive_action_x` | mean x (0 = own goal line, 120 = opponent's) of tackles, interceptions, clearances, blocks; empty under 50 events | **proxy**, descriptive | Positioning intelligence. It moves with the team's defensive line and possession as much as with the player. |
| `def_actions_in_own_box_p90` | those events inside the own penalty box (x <= 18, 18 <= y <= 62), per 90 | **proxy**, descriptive | Box defending quality. Teams that defend deep or face many crosses record more. |
| `cross_clearances_p90` | clearances whose `related_events` include a Pass flagged `cross`, per 90 | **proxy / approximation**, descriptive | Success against crosses. 17% of clearances have no related event, so it is a lower bound. |
| `cross_clearance_pct` | cross clearances / clearances, %; empty under 30 clearances | **proxy / approximation**, descriptive | How well he deals with crosses: only what share of his clearances were crosses. Crosses faced per player are not recorded. |
| `passes_received_p90` | completed passes whose `pass.recipient` is the player, per 90 | **proxy**, descriptive | Trust, leadership or importance. It reflects role, possession and style. |
| `passes_received_under_pressure_p90` | completed receipts (`Ball Receipt*` without an outcome) flagged `under_pressure`, per 90 | **proxy**, descriptive | Composure. The flag is StatsBomb's and its threshold is unknown. |
| `progressive_passes_received_p90` | received completed passes cutting distance to goal by 10+ yards, per 90 | **proxy**, descriptive | Anything about the player beyond how often teammates play forward to him. Our 10-yard threshold. |

- **The three receiving metrics are the brief's "trust proxies".** They are named for what they count. Nothing in
  this project claims any metric measures leadership, trust or positioning intelligence.
- **All ten are `higher_is_better=None` except `aerials_won_p90` and `aerial_win_pct`.** Descriptive metrics are
  ranked and used for similarity but never called a strength or weakness. The two aerial metrics are judged for the
  groups where `_AERIAL` is in `RELEVANT_METRICS` (CB, FB, DM, CM, ST).
- **Coverage on 296 qualifying outfield players:** every rate has a value for all 296; `aerial_win_pct` 291,
  `avg_defensive_action_x` 278 and `cross_clearance_pct` 171 (the guards; the last is mostly a defenders' metric).
- **Cross-checks that hold on the stored data (374 matches):** aerials won = aerials lost = 16,622;
  passes received = passes completed = 277,970; under-pressure receipts 27,632; cross clearances 3,837 of 21,345
  clearances (18%).
- **Passes received come from the pass's `recipient`, under-pressure from the receipt event.** The former is
  present on every completed pass; 5% of receipt events have no link to their pass, so counting progressive
  receipts through the receipt would undercount. The two counts differ by 2 events in the sample checked.

## 10. Heatmaps (`src/analytics/heatmaps.py`; data only, nothing is rendered)

- **Grid:** 12 columns (x) by 8 rows (y) = 96 cells of 10 x 10 yards on StatsBomb's 120 x 80 pitch, stored as JSON
  `grid[row][col]` (8 lists of 12). Ten-yard cells divide the pitch exactly and are coarse enough for a few
  hundred events to show a shape. Column 0 is at the player's own goal line (every team is recorded attacking
  towards x = 120). Row 0 is y 0-10; y increases towards the acting player's RIGHT touchline (verified: the 15
  most-played right-backs average y 66.5, left-backs 13.7). Coordinates just off the pitch (x up to 120.9) are
  clamped into the edge cell.
- **Table `player_heatmaps`:** one row per qualifying player per type: 323 players x 7 = 2,261 rows, all-zero grids
  included (the 28 all-zero rows are `shots` grids of 26 goalkeepers and 2 defenders). Columns include `grid_json` (raw counts), `grid_p90_json`
  (counts / season minutes x 90), `event_count`, `event_count_p90`, `minutes_total`, `position_group_fine`.
- **Types:** `all_actions` (every event with a player and location), `touches` (completed receipts, carries by
  start, dribbles, shots, as specified; passes played are not touches here), `defensive` (tackle duels,
  interceptions, blocks, clearances, non-failed recoveries), `passing` (passes by start location; key passes
  and box passes are subsets), `shots`, `all_actions_1h` / `all_actions_2h` (period 1 / 2; the file has no
  period 3+).
- **Sanity check, run before anything was built on it (all three pass):** Čech, 2,474 actions: 100% in his own half,
  90% in a box-sized zone (columns 0-1, y 20-60). Morgan, 2,859 actions: 84% in his own half, 67% central, 45% of his
  defensive events in the box-sized zone. Mahrez, 5,796 actions: 58% of touches in the two right-hand rows, 50% of
  touches in the final third.
- **Halves are not a bug when they differ:** Morgan has 1,700 first-half and 1,159 second-half events. Raw counts
  show his passes, receipts and carries fall (522 -> 327, 339 -> 182, 328 -> 172) while clearances and duels do not,
  and Leicester's total events are level (28,064 / 28,278). It is what the data says; the cause is not investigated.
- **A heatmap is where a player was involved, not how well he played.**

## 11. Similarity (`src/analytics/similarity.py`)

- **Method:** features for the target's fine group -> z-score within the group -> PCA keeping 85% of variance ->
  cosine similarity -> top N. Only players of the same fine group are compared. It is a **heuristic**; no accuracy
  is claimed and none is measured.
- **Features:** each group's `RELEVANT_METRICS` (per-90 rates and ratios, never raw totals), minus `red_p90` and
  `yellow_p90` (rare, chance-driven counts). That includes the descriptive proxies, so a match can come from
  team-context style rather than skill (see Vardy and Defoe in the evaluation). A feature empty for more than 30%
  of a group, or with no variance, is dropped for that group and reported; a few empty values are set to the
  group mean.
- **Explanations:** `shared_metrics` are the per-feature terms of the cosine numerator, which sum exactly to the
  similarity; both players being LOW on a metric counts as shared (the z-scores are shown). `differing_metrics`
  are the feature delta carried through the PCA loadings. The brief asked for "feature delta x PCA loading";
  that is the differences column, and the shared column is the additive decomposition of the score itself.
- **Filters:** `same_team_only`, `exclude_same_team`, `min_minutes` apply after the model is fitted, so scores do not
  change with the filter. `max_age` raises `AgeDataUnavailable`: StatsBomb's open data has no birth dates and no
  player has a `birth_year`. It is never silently ignored.
- **Evaluation:** `docs/similarity_eval.md`, ten fixed players, with written notes on what looks plausible
  (Kanté, Kane, Vardy's #2-#4, Mahrez, the centre-backs) and what does not (Özil beyond #2, both full-backs, goalkeepers
  beyond the top few). The algorithm was not tuned to any of them.

## 12. Re-profile of the five validation players under 8 groups (2026-09-20)

Compared with section 7 (4 groups). None of the five broke; the changes are what the finer grouping predicts.

- **Kanté:** DM/CM now CM (57% of minutes; the rest DM, flagged). Still 99th percentile on tackles, interceptions and
  ball recoveries. New weaknesses appear only because he is now ranked against all CMs: goals 18th, key passes 18th,
  xG 5th, shots 8th.
- **Mahrez:** CM (91% Right Midfield). Same strengths (goals, xG, xA, dribbles all 99th; shots and assists 97th).
  Only weakness left is pass completion (17th).
- **Vardy:** ST (100%). Same list, slightly lower percentiles (xG 96th, goals 93rd) because ST is narrower than FWD.
- **Özil:** AM (100%), a group of only 14, so his percentiles are coarse and flagged. Same creation strengths
  (assists, xA, key passes, box passes, progressive passes 96th).
- **Wes Morgan:** CB (100%). **Nothing changes in a way that helps him.** Strengths: none, before and after. The
  weaknesses stay ball progression (progressive passes 2nd percentile, carries 2nd, final-third passes 8th, carry
  distance 11th; ball recoveries 16th is new). The new metrics: aerials won 3.40 per 90 (35th percentile among 59
  CBs), aerial win rate 64.5% (**50th**), aerials attempted 5.26 (45th). His reputation for aerial dominance is not
  visible in these numbers: he is at the median among centre-backs. Descriptive only: mean defensive action at x = 23.7
  (33rd), 5.7 box-zone defensive actions per 90 (74th), cross-clearance share 25% (77th), passes received 13 per 90
  (2nd), progressive passes received 1.4 (8th). Those say Leicester's back line defended deep and did not build through
  him; they are not judged and are not strengths. No metric was added or changed to improve his profile.

## 13. Four leagues pooled: percentiles and similarity (2026-09-23)

Data: StatsBomb 2015/16, EPL + La Liga + Serie A + Ligue 1 (1,507 of 1,517 matches; BLOCKERS.md section 12).

**Decision: percentiles and similarity are POOLED across the four leagues, grouped by `position_group_fine`.**
Percentiles are not computed per league. (The v1 code was already league-agnostic; what made v1 EPL-only was the
data. `percentiles.run(league=None)` and `normalize.run(league=None)` pool by default; a league name restricts
the pool and is kept only to reproduce v1.)

Why per-league would be wrong for similarity:
- **A cosine between two players is only meaningful if both were z-scored against the same reference.** Per-league
  z-scores put every league on its own zero: a "+1.0 tackles" Serie A midfielder and a "+1.0" EPL midfielder would
  look identical while their raw rates differ, and the PCA axes would differ per league, so vectors from two
  leagues would not live in one space. Cross-league comparison would be impossible by construction.
- **The pool is too thin per league.** After the 900-minute cut a league has 8 to 27 players in the smallest groups
  (AM: 14, 11, 11, 8; GK 22-27). Pooled, no group is under 30 (AM 44).
- **The cost is stated, not hidden:** pooling assumes the leagues are one population (pace, tagging, and league
  strength are not modelled). A player's percentile now means "among these 4 leagues", not "in his league".

Rules kept: 900-minute minimum; multi-team players use the team (and league) with most minutes, and their stats
sum across both; ranking, z-scoring and PCA only within `position_group_fine`; z-scored per-90 rates only, no raw
volume. Nothing was tuned.

**Pooled season table:** 1,337 qualifying players (1,337 unique ids), 836 in `insufficient_sample`, no overlap. 74
qualifying players played for more than one club, 30 of them in more than one league.

| Group | EPL | La Liga | Serie A | Ligue 1 | Pooled |
|---|---|---|---|---|---|
| GK | 27 | 23 | 22 | 26 | 98 |
| CB | 58 | 62 | 68 | 66 | 254 |
| FB | 59 | 66 | 61 | 66 | 252 |
| DM | 41 | 53 | 46 | 48 | 188 |
| CM | 45 | 37 | 54 | 43 | 179 |
| AM | 14 | 11 | 8 | 11 | 44 |
| W | 41 | 46 | 26 | 38 | 151 |
| ST | 39 | 42 | 52 | 38 | 171 |

No pooled group is under 30; the smallest is AM (44), still well under the 98 to 254 of the others, so AM percentiles are
coarser. 239 of 1,337 players (18%) are mixed-role (under 60% of minutes in their group), against 23% before.

**The five validation players under the pooled percentiles** (compare section 12; none broke):
- Kanté: still CM (57%). Tackles, interceptions 99.7th, ball recoveries 99.2nd; dribble success 98.4th is new;
  pressures 85th. Weak on xG (3rd), shots (8th), fouls won (13th).
- Mahrez: CM (91%). Goals and xG 99.7th, dribbles 98th, xA 97.5th, shots 94th. **No weakness now** (pass completion
  17th before is no longer at or below 20th).
- Vardy: ST. xG 96th, goals 94th, shots 87th, xG per shot 85th. Weak on fouls won (7th), counterpressures (14th).
- Özil: AM (100%), group of 44. Assists, xA, key passes, box passes 98.9th; progressive passes 97th. Weak on tackles,
  pressures, shots.
- Wes Morgan: CB. **One strength that was not there before: blocks, 80.1st percentile** (right at the line of 80).
  Weaknesses unchanged in kind: progressive passes 0.6th, carries 1.0th, final-third passes 6.5th, ball recoveries 6.5th.
  That is a threshold effect of a bigger pool, not new evidence about him.

**Similarity v2** is evaluated in `docs/similarity_eval_v2.md` against v1 (Premier League only). v1 was recomputed
on today's data because the stored file predates the repair of 6 EPL matches; running the v1 code on the pre-repair
database reproduces the stored scores exactly. The verdict rule (top score and top-10 mean both move 0.05) was
fixed before the run; the reviewer notes there say where it misleads. A larger pool raises scores by itself, so a
score rise is weak evidence.

## 14. Playing archetypes (`src/analytics/archetypes.py`)

Why: `position_group_fine` comes from StatsBomb lineup position strings, so players who play alike land in
different groups (Özil AM; Mata and Eriksen W; Cazorla DM; Pjanić CM). An archetype groups players by what they DO.

- **Features:** 14 common outfield per-90 rates and ratios (`OUTFIELD_FEATURES`), fixed before any result was seen.
  Raw values, not within-position percentiles (percentiles would make an elite centre-back and an elite winger look
  alike). Position is never a feature.
- **Method:** z-score across the 1,239 outfield players, PCA to 85% variance (6 components), K-Means (seed 0, 50
  restarts). Goalkeepers (98) are clustered separately on the nine goalkeeper metrics, k = 3.
- **k = 10 is a judgement, not the arg-max.** Silhouette falls from 0.2055 (k = 8) to 0.1935 (k = 10); no k is
  well-separated, and k = 10 was accepted as the one that splits the roles worth telling apart (Chance creator, Deep
  playmaker, Destroyer, Target man, Poacher, ...). Stability across seeds: adjusted Rand 0.964 (outfield), 0.974
  (goalkeepers). k is accepted and is not revisited by the similarity work below.
- **Labels are descriptive names a person gave the clusters**, keyed by cluster id; they must be re-checked whenever
  the data or the feature list changes (`--describe`).
- **`cluster_confidence`** = (d2 - d1) / d2, the margin between the nearest and second-nearest centroid in PCA space.
  Not a probability. 0.25 is the "near the edge" warning line.
- **Structural checks (`sanity_check`):** Özil and Silva share an archetype; Kanté and Özil do not. Both hold
  (Chance creator; Destroyer).
- Sizes: Stopper CB 176, Ball-playing CB 105, Defensive FB 196, Destroyer 175, Deep playmaker 119, Pressing wide
  midfielder 129, Target man 44, Poacher 122, Dribbling winger 118, Chance creator 55; goalkeepers 28 / 43 / 27.
- Known misses (BLOCKERS.md section 14): Mata is a Deep playmaker, M'Vila a Defensive full-back.
- Exports: `python -m src.analytics.archetypes --export` writes `docs/archetypes/archetype_summary.csv`,
  `player_archetype_map.csv`, `archetype_scatter.png` (PC1 vs PC2 of the clustering space).

## 15. Archetype-weighted similarity: distinctive features (2026-09-23)

**Problem.** Inside an archetype, similarity used the union of every position group's metrics (34 usable of 38).
For a Chance creator that includes clearances, own-box defensive actions and aerial win %, which add noise: Eriksen
and Pjanić ranked 26th and 21st of 55 in Özil's list.

**Principle.** An archetype is defined by the metrics on which it differs most from everyone else, so only those
are compared inside it.
1. Z-score the usable features across the whole outfield pool (1,239 players). Four ratios empty for over 30% of the
   pool (`cross_clearance_pct`, `dribble_success_pct`, `shot_accuracy`, `xg_per_shot`) cannot be scored and are absent.
2. Archetype centroid = mean z per feature over the archetype's members. Global centroid = mean z over the pool (0
   by construction; empty ratios are imputed as 0).
3. Distinctiveness = |archetype centroid - global centroid|; **low counts as much as high** (a Stopper is defined by
   how little he carries as well as how much he clears).
4. Keep the top **N = 12**. Ranking and selection live in `archetypes.distinctive_table` / `distinctive_features`.
5. `find_similar(use_archetypes=True)` z-scores those 12 within the archetype's members, then PCA (85%) and cosine as
   before. `distinctive_n=None` restores the all-feature archetype behaviour; `use_archetypes=False` is unchanged.
   Goalkeepers keep their nine metrics (fewer than N, nothing to select).

**Why N = 12** (range allowed was 12 to 15; chosen before seeing any list): PCA at 85% then keeps 5 to 8 components,
enough for a 12-dimension space without leaving room for weak features. For the sharp archetypes (creators,
poachers, target men, stoppers) the 12th feature still has |Δz| of 1.0 to 1.2, so 12 loses little there. For the
weak archetypes (full-back, Destroyer, pressing wide midfielder) the 12th to 16th features all sit at 0.4 to 0.5 (a
plateau), so any cut-off in the range is arbitrary; a larger N would only add features at that same noise level.
One N for all archetypes keeps the rule simple and untuned.

**Selected features** (archetype mean z in brackets; a short reason after each group):

| Archetype | Features (in order) |
|---|---|
| 0 Stopper centre-back | cross_clearances_p90 (+1.63), clearances_p90 (+1.62), def_actions_in_own_box_p90 (+1.61): clears the box; avg_defensive_action_x (-1.20): defends deep; counterpressures_p90 (-1.17), pressures_p90 (-1.00): does not press; passes_received_under_pressure_p90 (-1.16), progressive_passes_received_p90 (-1.12), passes_received_p90 (-1.09), key_passes_p90 (-1.06), carries_p90 (-1.05): hardly involved in build-up or creation; aerial_win_pct (+1.06): wins headers |
| 1 Ball-playing centre-back | clearances_p90 (+1.32), def_actions_in_own_box_p90 (+1.30), cross_clearances_p90 (+1.29): still clears the box; pass_completion_pct (+1.27), progressive_passes_p90 (+1.22): the passing that separates him from a stopper; pressures_p90 (-1.20), counterpressures_p90 (-1.17): no pressing; avg_defensive_action_x (-1.14): deep; aerial_win_pct (+1.04); passes_received_under_pressure_p90 (-1.03), progressive_passes_received_p90 (-0.99), key_passes_p90 (-0.98): not a receiver or creator |
| 2 Defensive full-back | progressive_passes_p90 (+0.97): moves the ball forward by passing; shots_p90 (-0.75), xg_p90 (-0.69), goals_p90 (-0.62): little goal threat; progressive_passes_received_p90 (-0.75), passes_received_under_pressure_p90 (-0.67): not a forward outlet; interceptions_p90 (+0.68), blocks_p90 (+0.64), tackles_p90 (+0.53): defensive actions; avg_defensive_action_x (-0.64): deeper than the wide midfielders; pressure_duration_avg (+0.53); tackle_win_pct (-0.45) |
| 3 Destroyer | tackles_p90 (+1.28), ball_recoveries_p90 (+0.79), interceptions_p90 (+0.57), blocks_p90 (+0.61): wins the ball; pressures_p90 (+1.15), counterpressures_p90 (+0.93): presses; fouls_committed_p90 (+0.81): pays for it; pass_completion_pct (+0.56), passes_into_final_third_p90 (+0.50): keeps it simple; xa_p90 (-0.52), goals_p90 (-0.52), xg_p90 (-0.51): little attacking output |
| 4 Deep playmaker | passes_received_p90 (+1.56), carries_p90 (+1.50), carry_distance_p90 (+0.89): the ball runs through him; passes_into_final_third_p90 (+1.47), progressive_passes_p90 (+0.66), passes_into_box_p90 (+0.65), key_passes_p90 (+0.65): moves it forward; pass_completion_pct (+0.91); ball_recoveries_p90 (+0.81), counterpressures_p90 (+0.68): wins it back; aerials_attempted_p90 (-0.73), aerials_won_p90 (-0.67): stays out of the air |
| 5 Pressing wide midfielder | pressures_p90 (+0.75), counterpressures_p90 (+0.62), avg_defensive_action_x (+0.60): presses high; def_actions_in_own_box_p90 (-0.72), clearances_p90 (-0.68), cross_clearances_p90 (-0.65): never defends the box; aerial_win_pct (-0.73), aerials_won_p90 (-0.49): loses aerial duels; pass_completion_pct (-0.66), progressive_passes_p90 (-0.65): rough passer; key_passes_p90 (+0.51), passes_received_under_pressure_p90 (+0.50): receives in tight wide areas |
| 6 Target man | aerials_attempted_p90 (+3.33), aerials_won_p90 (+2.92): the aerial outlet; progressive_passes_received_p90 (+1.87), xg_p90 (+1.77), shots_p90 (+1.43), goals_p90 (+1.42): the forward end of moves and finishing; pass_completion_pct (-1.55), progressive_passes_p90 (-1.42), carry_distance_p90 (-1.28), carries_p90 (-1.24): does not pass or carry; interceptions_p90 (-1.27), ball_recoveries_p90 (-1.21): does not win the ball |
| 7 Poacher | xg_p90 (+1.82), goals_p90 (+1.62), shots_p90 (+1.35): finishing; progressive_passes_received_p90 (+1.32), passes_received_under_pressure_p90 (+1.14), avg_defensive_action_x (+0.99): plays on the last line; progressive_passes_p90 (-1.46), passes_into_final_third_p90 (-1.07): does not build; interceptions_p90 (-1.27), tackles_p90 (-1.23), blocks_p90 (-1.03), ball_recoveries_p90 (-1.00): does not defend |
| 8 Dribbling winger | dribbles_p90 (+1.88), progressive_carries_p90 (+1.67), carry_distance_p90 (+1.36): carries and beats his man; shots_p90 (+1.04), xa_p90 (+1.00), key_passes_p90 (+0.90): end product; fouls_won_p90 (+0.92), passes_received_under_pressure_p90 (+0.90): fouled while receiving in tight space; avg_defensive_action_x (+0.88): high; aerial_win_pct (-1.09), def_actions_in_own_box_p90 (-0.92), clearances_p90 (-0.90): not a defender |
| 9 Chance creator | key_passes_p90 (+2.63), xa_p90 (+2.62), passes_into_box_p90 (+2.58), assists_p90 (+2.21): the creation itself; carry_distance_p90 (+1.62), progressive_carries_p90 (+1.50), carries_p90 (+1.46), dribbles_p90 (+1.41): moves the ball into space; passes_received_p90 (+1.47), passes_received_under_pressure_p90 (+1.25), progressive_passes_received_p90 (+1.22): the ball comes to him between the lines; avg_defensive_action_x (+1.24): high up the pitch. No defensive feature: the first (low own-box actions) is 15th |
| GK (all three) | the nine `gk_*` metrics (`group_features(GK)`): fewer than N, so no selection |

**Outcome** (`docs/similarity_eval_v3.md`, 14 players plus Busquets; not a benchmark, no ground truth):
- Özil's top 5 are James Rodríguez, Barrada, Di María, De Bruyne, David Silva: all chance creators. Kanté's top 5 are
  ball-winning midfielders (Gueye, Illarramendi, Kankava, Poko, Rubén Pérez), better than the all-feature list,
  whose first match was a full-back.
- Diagnostic: Eriksen 26th -> 12th, Pjanić 21st -> 10th, Cazorla 13th -> 16th, Hamšík 11th -> 17th. Two better,
  two worse; the hoped-for top 10 was reached by one of four.
- Kyle Walker's list got worse (two centre-backs in the top 10). The weak archetypes have weak signatures (see
  the plateau above), so the principle helps sharply defined archetypes more than blurry ones. Not re-tuned.
- No list became nonsense.

**Limits:** the selection uses |difference|, so "low" features count; centroid distance is measured in z-units of the
whole outfield pool, so a broad archetype (full-back) always has smaller distances than a narrow one (Target man);
one N for all; 34 features are usable, not the 38 defined; one season. Nothing here was tuned to any player.

## 16. Streamlit UI and player photos (2026-09-23)

The UI (`src/app`) renders what `src/analytics` computes. New read-side code lives in `src/analytics/directory.py`
(directory, search, radar items, stats table, heatmap grids, archetype rosters and scatter); nothing is re-tuned.

- **Radar metrics** are a fixed list per position group (`RADAR_METRICS`), all ranked within the group. A metric
  where lower is better (goals conceded) is plotted flipped so outward always means better. Axes with no
  percentile (ratio under its sample guard) are dropped. "Raw values" mode draws each axis as a share of the
  group maximum; the hover shows the raw number.
- **Heatmaps** are drawn in StatsBomb yards (120 x 80, 10-yard cells) rather than 105 x 68 metres, because that is what
  the stored grid is; the team attacks left to right. All 1,337 players now have heatmaps (the first UI
  build only had the 323 EPL players; the job was rebuilt for the four leagues on 2026-09-23).
- **Search** ranks exact name, whole-word, prefix, substring, then a difflib fuzzy match (cutoff 0.72), accent-blind.
- **Pairwise similarity (Compare)** reuses `find_similar` in both modes; a pair outside one pool shows a dash.
- **Lineup corrections per player** are counted from `data/processed/statsbomb_corrections.csv` by the name written
  in the report, so a lineup spelling that differs from the canonical name is undercounted.
- **Theme:** `.streamlit/config.toml` repeats the four theme colours from `design.py` (Streamlit reads it before any
  Python runs); `tests/test_app_design.py` checks they match.
- **Player photos** (`src/app/player_images.py`): Wikipedia `pageimages` API, fetched by a background thread pool (the
  UI never waits; the silhouette shows until the file exists), cached at `data/player_images/<player_id>.jpg`
  (`:` replaced by `_`, illegal on Windows), attribution per image in `credits.json`. The page intro must call the
  subject a footballer, or the image is rejected (guards against namesakes and disambiguation pages); a miss is
  remembered. Third-party images are gitignored. **For local or portfolio use this is fine; commercial use needs a
  per-image licence check** (the credit line "Photo: Wikipedia / CC-BY-SA" is a generic label, not a verified
  licence for each file).

## 17. Team style vectors and team similarity (2026-09-23)

*(The request called this "section 16"; that number was already taken by the UI section, so it is 17.)*

Code: `src/analytics/team_style.py` (axes, table `team_style`), `team_similarity.py`, `team_profile.py` (scores,
headline facts, key players). Built from the cached StatsBomb events of the 1,507 stored matches; nothing new was
ingested. **These axes describe what a team's events look like. They do not measure tactics, instructions or
intent**, and every axis mixes a team's own choices with the opposition, game state and league.

**Season aggregation:** raw counts are summed over the team's matches, then the ratio is taken (never an average of
per-match ratios). `matches_used` is the number of matches with stored events (up to 3 fewer than the schedule for
teams hit by the ingest gaps in BLOCKERS.md section 12). Shoot-out events (period 5) are ignored. Team names go
through `normalize_team_name`. Coordinates are StatsBomb yards, the team attacking towards x = 120.

| Axis (column) | Exact definition | Measures | Does NOT measure | Kind |
|---|---|---|---|---|
| Possession % (`possession_pct`) | own passes / (own + opponent passes) x 100, all `Pass` events | share of passing volume | true time on the ball, carries, or possession quality | **proxy** (pass share, not ball time) |
| PPDA (`ppda`) | opponent passes that start at x <= 72 in their frame (their own 60%) / own tackles (`Duel` Tackle) + interceptions + fouls committed at x >= 48 in the team's frame. Lower = more aggressive | how few passes the opponent gets in its own 60% per defensive action | pressing itself: `Pressure` events are not used, so a press that forces errors without a tackle, interception or foul is invisible | **proxy** (the conventional PPDA form) |
| Direct speed (`direct_speed`) | mean of (end x - start x) over all passes, yards | how far the average pass moves the ball towards goal | speed in time; backward passes count negatively | **proxy** for directness |
| Build-up length (`buildup_length`) | own passes / possessions containing at least one own pass | passes per possession that had a pass | possession length in time; possessions made only of carries or a shot are excluded | **proxy** |
| Defensive line height (`def_line_height`) | mean x of own defensive actions: tackles, interceptions, blocks, clearances, non-failed ball recoveries (the `defensive` heatmap set) | where the team defends, in yards from its own goal | the offside line, or the shape when not defending: it only sees where an action happened | **proxy** |
| Width (`width`) | population standard deviation of the start y of all own passes | how spread across the pitch the passing is | wingers' positions, overlaps, or pass intent | **proxy** |
| Crossing volume (`crosses_p90`) | own `Pass` events flagged `cross`, excluding corners, per match | crosses attempted | success or quality; "per 90" is per match (league games have no extra time) | direct count |
| Progressive pass rate (`progressive_pass_rate`) | completed passes that cut the straight-line distance to the goal centre by 10+ yards / all passes | share of passes that move the ball goalwards | carries, or passes that break lines but stay level | **proxy**, our own 10-yard threshold as in section 2 |
| Shot quality (`xg_per_shot`) | sum of StatsBomb pre-shot xG / shots (penalties included, as in section 2) | average chance value | finishing, or chances not shot | direct measurement |
| Shot volume (`shots_pm`) | shots per match | how often the team shoots | shot value | direct count |
| Transition speed (`transition_secs`) | mean seconds from the first event of a possession to its first non-penalty shot, over own possessions that end in a shot and did not start from a corner, free kick or kick-off. **Lower = faster** | how quickly open-play possessions reach a shot | counter-attacks specifically (a slow passing move and a long ball are both just seconds), or possessions with no shot | **proxy**, approximate: event clock to the second, and possessions that reach no shot are not included, so it is noisy for teams with few shots |
| Set-piece reliance (`set_piece_xg_pct`) | xG of shots whose possession began from a corner or free kick, or that are corner / free-kick / penalty shots, / all xG x 100 | how much of the team's chance value is dead-ball | how good the set-piece routines are | **proxy**; penalties count as set pieces |

**Axes not computed:** none of the 12 requested was dropped. Every one is computable from event data, with the
proxy caveats above (BLOCKERS.md section 17 repeats them).

**Standings (position, points).** From StatsBomb's own cached match lists, not from `matches`, so they include the
few fixtures whose events were not ingested: 3 points a win, ranked by points, goal difference, goals for, then name.
Head-to-head tie-breaks are not applied. StatsBomb lists 377 of Ligue 1's 380 matches, so a Ligue 1 team can show 37
matches and fewer points than its real final table (the page says so). `league_position` is within league.

**Team similarity.** Each axis is z-scored across all 80 teams (population standard deviation; a constant axis becomes
0), and teams are compared by cosine similarity. **No PCA** (80 teams, 12 axes). Neighbours are ranked across all
four leagues. For each neighbour, "closest on" lists the axes where the two z-scores are largest with the same sign,
and "biggest gaps" the axes with the largest difference. The score is a heuristic, not validated; the page shows it as
a percentage (negative shows as 0%). Nothing was tuned to make known teams look right.

**Radar scores.** 0-100 rank among the 80 teams (average rank for ties, 0 lowest, 100 highest). PPDA and transition
seconds are flipped for display only so outward always means more pressing / faster to a shot; similarity uses the
raw axes (a sign flip of one axis for every team leaves cosine similarity unchanged). Headline facts are the up to
three axes at least 25 points from the median, worded as event patterns ("Deep defensive block" means the team's
defensive actions happen deep).

**Sanity check (reported, not tuned).** Leicester City is low on possession, high on direct speed, short on build-up
and fast to a shot, but its defensive line sits near the middle of the 80 teams, not deep. Its nearest neighbours
are not led by Atletico Madrid. Bayern Munich cannot be checked: the Bundesliga is not in this dataset (Big-4 only).
Exact neighbours are in the session report, not repeated here because they are a snapshot of a heuristic.

**Key players.** The five players with most minutes for the team (from `player_archetypes` and the season stats), with
the archetype mix of those five (goalkeepers show their own keeper archetype).

## 19. Match ratings: longest, best and worst game (`src/analytics/games.py`)

A player's matches are ranked against **each other** only (never against other players), using a
plain count for his season position group (`position_group_fine`). No weighting is fitted; the
numbers are what StatsBomb events give.

| Group | Rating = |
|---|---|
| ST, W, AM | goals + assists + xG + xA |
| CM, DM | progressive passes + tackles + key passes |
| CB, FB | tackles + interceptions + clearances |
| GK | saves + 3 x clean sheet + 2 x save % (save % = saves / (saves + goals conceded), 0 when no shot was faced) |

- Goals/assists are added to xG/xA on purpose: a goal is the outcome, xG the chance quality, so a
  game with both scores highest. Goalkeeper weights (3 for a clean sheet, 2 for save %) are judgement
  calls, not fitted.
- **Clean sheet** = the player's team conceded 0 in a match he played. The score comes from `matches`
  (StatsBomb); the football-data `match_results` table has no rows for these matches. A goalkeeper who
  played only part of a clean-sheet match still gets the credit.
- **Longest** = most minutes (ties: earlier date). **Best** = highest rating (ties: more minutes, then
  earlier). **Worst** = lowest rating among matches with **45+ minutes** (ties: more minutes).
- Known limits: the defender formula ignores attacking output, so a full-back known for assists
  (Jordi Alba) has a best game chosen for defending; ratings are counts, so a 90-minute game beats a
  short cameo; the numbers are not comparable between players or positions.
- Hero stats: games = distinct matches in `player_match_stats`; a tile is hidden when its value is
  unavailable. Per-match heatmaps are computed on demand from the cached raw events with the same
  12 x 8 grid and 7 types as section 10, cached in memory only, and show "Heatmap unavailable" if the
  match's events are not on disk.
- Numbering: there is no section 18 in this file; 19 and 20 follow the request that asked for them.

## 20. Wikipedia bio, team logos and licences (`src/app/player_images.py`)

- **Bio.** Birth date (P569, only when Wikidata knows the exact day) and first citizenship without an
  end date (P27, English label) are read from the Wikidata item behind the player's Wikipedia page,
  which must call him a footballer. Stored in `player_bio(player_id, birth_date, nationality,
  fetched_at)`, created idempotently by `init_db`. A row with NULLs means "looked, nothing found".
  It is fetched only for the profile hero, in the background, never for list avatars. Age is
  quoted on 2015-08-01. Nationality is Wikidata citizenship, which can differ from the sporting
  nationality; it is shown as text, not a flag emoji.
- **Team logos.** `fetch_team_logo` uses the club page's lead image only when its file name contains
  logo/crest/badge/emblem/shield and the intro says football club; otherwise the placeholder
  (`src/app/static/team_placeholder.png`) shows. Cached to `data/team_logos/<slug>.png`, attribution
  in `data/team_logos/credits.json`. Some dataset club names need a Wikipedia title map; unmapped
  ones may fall back to the placeholder.
- **Licences.** Player photos are CC-licensed per image; club logos are trademarks, usually fair-use
  files on Wikipedia. Fine for local/portfolio use; any commercial use needs a per-image licence
  check. Nothing is scraped from Transfermarkt or any other source.

## 21. Team logo cache and the match recap (2026-09-23)

- **Logos are cache-only in the UI.** `team_logo_path` / `team_logo_uri` only read
  `data/team_logos/<slug>.png` (or show the shield placeholder); they never start a fetch, so a page
  render never waits on Wikipedia. The encoded data URI is memoised per (file version, size).
- **Filling the cache.** `python -m src.app.preload_logos` walks every team in `team_style` (80),
  fetches each missing logo once with 3 s between teams, and is resumable (cached teams are skipped;
  teams recorded as `missing` in `credits.json` are skipped unless `--retry-missing`). The Team
  Fingerprint page has a small "Fetch N missing logos" button for the teams on screen.
- **Finding the crest.** The page's lead image when its file name says logo/crest/badge; otherwise the
  crest named in the club infobox (`image =` / `logo =`), because non-free crests such as Chelsea's are
  not returned by the page-image API. Only `.svg`/`.png` infobox files are accepted (a JPG there was a
  photo of a town hall for Guingamp), and the page intro must still describe a football club.
- **Match recap (`src/analytics/match_report.py`).** Timeline, lineups and team totals are parsed from
  the cached StatsBomb `events`/`lineups`/`matches` files. Goals carry the assister (`pass.goal_assist`
  via `shot.key_pass_id`), cards come from `Foul Committed`/`Bad Behaviour`, own goals from `Own Goal
  Against`, substitutions from `Substitution`. Period 5 (shoot-out) is ignored. Possession is each
  team's share of all passes (there is no possession clock in this data); pass accuracy counts passes
  without an outcome as completed; shots on target are Goal/Saved/Saved to Post as in the player
  metrics. Formation is the kick-off `tactics.formation`. Missing files give empty parts, and the page
  shows "not available" instead of a broken layout.

## 22. Similarity thresholds: measuring what a score means (planned, 2026-09-24)

Section 21 already holds the logo cache, so this is section 22. **Status: measured (2026-09-25),
see section 23.** This section is the methodology as fixed before any label was seen.

- **Why.** The UI shows cosine similarity as a match % (`design.fmt_match_pct`). The tier words
  (85%+ Strong, 70-84% Good, 55-69% Fair, <55% Weak) were chosen by judgement, and no function in
  the app applies them yet. This section measures the engine as-is so the tiers, when applied, carry
  a measured meaning. The similarity algorithm (section 15) is not tuned or changed.
- **Test set (`python -m src.analytics.build_test_pairs --n 100`).** Writes
  `data/manual/similarity_test_set.csv` (`settings.manual_dir`, kept in git) with the `label` column
  blank for hand-labelling as `similar` / `not similar`. 10 pairs from each of the 10 outfield
  archetypes; the goalkeeper pool is skipped. Both players of a pair are in the same archetype,
  because a score only exists inside one pool; the score is recomputed with the same model
  functions as `find_similar` (the test checks they agree to 4 decimals). Seed 0, so the file is
  reproducible. An existing file that holds labels is never overwritten without `--force`.
- **Stratified by score, one pair per band per archetype.** Bands `<0.45, 0.45-0.55, 0.55-0.60,
  0.60-0.65, 0.65-0.70, 0.70-0.75, 0.75-0.80, 0.80-0.85, 0.85-0.90, >=0.90` (an empty band would
  borrow from the nearest one; none did). A uniform sample would be useless: random pairs inside an
  archetype score near 0, so almost none would land near the thresholds under test. The price: the
  per-band precision is unbiased, but cumulative precision and recall describe this stratified mix,
  not all pairs in the database (a 0.90 pair is far rarer in reality than here). Each player appears
  in at most one pair (200 distinct players), and rows are shuffled so the file order does not
  reveal the score.
- **Labelling.** Hide `cosine_score` and `score_band` while labelling (the score anchors judgement).
  "Similar" means: a scout looking for player A would accept player B as a like-for-like option in
  role and style, whatever the quality gap.
- **Evaluation (`python -m src.analytics.evaluate_thresholds`).** For t in 0.50, 0.55, ..., 0.90:
  precision (share of pairs >= t labelled similar, with a Wilson 95% interval), recall, F1, number
  of pairs >= t, and **local precision** over pairs in [t, t + 0.10). Also precision per band.
  Writes `docs/similarity_thresholds.md` and `docs/similarity_thresholds.png`. With no labels it
  prints the "CSV ready for labeling" message and writes nothing; partly labelled files need
  `--allow-partial`.
- **Recommendation rule (fixed now, before any label).** Strong / Good / Fair = the lowest threshold
  where precision reaches 90% / 75% / 50% on both the cumulative and the local reading, each with at
  least 10 pairs. The local reading was added after a synthetic run showed cumulative precision
  alone can place a cut where pairs are mostly not similar, carried by the pairs far above it; a
  tier is shown on a pair scoring right at its cut, so precision there is what the word promises.
  Weak is kept only if pairs below the Fair cut are mostly labelled not similar. A tier that no
  threshold reaches is reported as not supported, not forced.
- **Then.** If the measured cuts differ from 85/70/55, the tier function in `design.py` is set to
  them and this section records the numbers. With 100 pairs the intervals are wide (about +/-15
  points at n = 20); the result is a first measurement, not a calibration.

## 23. Similarity thresholds: labels and the measured result (2026-09-25)

- **Labels.** Test set labels generated by LLM using position + archetype rules; reviewed and
  correctable by the author before the paper. Treat as a weak but honest baseline, not a
  human-expert ground truth. The rules: *similar* = same playing style and same role, whatever
  the quality gap (two poachers, two ball-playing CBs, two destroyers, two target men, ...); *not
  similar* = different roles even at the same position (destroyer vs deep playmaker, poacher vs
  target man, CB vs FB, winger vs central mid); when uncertain, *not similar*. Every pair shares an
  archetype by construction, so the archetype name alone would call all 100 similar. The labels
  therefore rest on the two positions and on the players' known 2015/16 roles; the real role
  overrides the scraped position where it is clearly wrong (e.g. Víctor Álvarez, listed W, was
  Espanyol's left-back). A pair is judged on the players, not on whether the archetype fits them
  (Sall and Škrtel are both stoppers inside the ball-playing pool, so *similar*). `cosine_score` and
  `score_band` were hidden while labelling. Result: **49 similar, 51 not similar**, no blanks.
- **Result (`docs/similarity_thresholds.md`, `.png`).** Precision is flat: 40-52% cumulative at
  every threshold 0.50-0.90, and 30-70% per band with no upward trend (the >=0.90 band is 4/10).
  Under the pre-registered rule: **Strong (90%) and Good (75%) are not supported** by any threshold;
  **Fair = 0.60** (precision 51%, 95% CI 40-63%, n = 70; local 50%, n = 20); **Weak = below 0.60**
  (43% of 30 pairs similar, so kept). The judgement cuts 85 / 70 / 55 are not confirmed.
- **Why it is flat (observation, not a fix).** 40 of the 100 pairs cross positions (CB with FB, CM
  with W, DM with FB); only 25% of those are labelled similar against 65% of same-position pairs,
  yet they score almost the same (mean cosine 0.630 vs 0.651). Inside one archetype pool the score
  does not see position, and most of what these labels call *not similar* is a position mismatch.
  Per archetype, share similar ranges from 0% (Deep playmaker) to 80% (Target man, Defensive
  full-back). The engine was not changed; whether to add a position term is a separate decision.
- **Applied.** `design.MATCH_TIERS` = Fair from 0.60, Weak below; no Strong / Good word is shown.
  The tier appears as a pill on the Similar Players cards and the closest-match line (player page)
  and as `72% · Fair` in the Compare page's archetype-weighted matrix. It is not shown on the
  position-group tab or on team similarity, which were not measured. The pill's tooltip and the
  captions say what the tier means. `tests/test_similarity_thresholds.py` fails if
  `MATCH_TIERS` stops matching what `evaluate_thresholds` recommends from the labelled CSV, so
  relabelling forces the tiers to be revisited.
- **Caveat.** A *Fair* tier on a 95% match reads oddly, but it is what the data says: here a high
  score does not make a same-role pair more likely than a middling one. With 100 LLM-labelled
  pairs the intervals are about +/-15 points; a human relabel should be run before these numbers
  go in the paper.

## 24. Team wage bills: methodology (`team_finance`, 2026-09-25)

Team context for the future Tactical Composer (budget fit). **Club-level annual wage bills only: there
are no player wages anywhere in the project, and none are to be derived** (dividing a bill by squad
size, say, would be invention).

- **Table `team_finance`** (`src/models/schema.py`), one row per (team_name, season, league), unique.
  `season` and `league` use the codes every other table uses (`"2015"`, `"ENG-Premier League"`), so it
  joins on them directly. The CSV and CLI also accept `2015-2016`, `2015/16`, `EPL`, `La Liga`, etc.
  Team names go through `team_identity`; an unknown name is stored as given and logged.
- **Created by `init_db`** (`create_all`, `checkfirst`), so the migration is idempotent like the rest.
- **Currency.** Every bill is stored in GBP (`wage_bill_annual_gbp`), with the source's own figure in
  `wage_bill_original_amount` and `wage_bill_currency_original`. EUR converts at a fixed
  **1 EUR = 0.75 GBP** for 2015/16 (`EXCHANGE_RATES_TO_GBP`), a round season average: EUR/GBP ran
  roughly 0.70-0.80 over July 2015 to June 2016, so a single rate can put a Continental bill a few percent
  off against an English one. `exchange_rate_used` records the rate applied (1.0 for GBP). When a row
  supplies both a GBP figure and a foreign original, the source's GBP figure is kept, no rate is recorded,
  and a warning is logged if it strays more than 2% from 0.75. A season with no rate cannot be imported.
- **Missing is NULL.** A blank amount stays NULL, is logged at import, and is left out of the ranking.
  Nothing is interpolated, averaged or estimated. Bad rows (unknown league, non-consecutive season,
  non-numeric or negative amount, an amount without a currency, a blank `source`, a `confidence`
  outside the four values) stop the import with the line number rather than being skipped.
- **Confidence** is one of `official` (club accounts, regulator filings), `aggregated` (a body such as
  UEFA or Deloitte compiling official figures), `estimate` (payroll trackers such as Capology, press)
  and `unknown`. It is shown next to every figure in the UI.
- **Rank** (`src/analytics/finance.py`) is 1 = highest bill across all four leagues of the season, over
  clubs with a known bill only. The UI says "rank N of M" and, while coverage is partial, "of M with
  data, 80 clubs", so a rank is never read against clubs we have no number for.
- **UI.** Player profile hero and Team Fingerprint hero: "Team wage bill: £XXXm/year (rank N of M,
  source: X — confidence)", with a "team context" badge and a tooltip saying it is not the player's
  wage. With no figure it reads "Team wage bill: not available".
- **Known comparability limits.** Accounts report the club's financial year and usually include all
  staff, not only players; payroll trackers estimate players only. Mixing them in one ranking mixes two
  quantities, so a row's `source` should say which it is, and the Composer should prefer rows of one kind.
- **Sources.** FBref wages pages and Capology are both behind a Cloudflare challenge (BLOCKERS.md
  section 19), so the table is filled from the hand-made `data/manual/team_finance.csv`.

## 25. Market values: Transfermarkt snapshot for 2015/16 (`player_market_values`, 2026-09-26)

Code: `src/transform/market_values.py` (snapshot, matching, table), `src/analytics/market_values.py`
(player value, club sum, coverage). Run: `python -m src.transform.market_values`.

- **Source.** The dcaribou *transfermarkt-datasets* dump, Kaggle `davidcariboo/player-scores`,
  downloaded by hand and read from `data/raw/transfermarkt/` (gitignored, never committed). Only local
  files are read: nothing is scraped or downloaded by code. Files used: `player_valuations.csv`
  (656,301 rows, 2000-01-20 to 2026-06-12), `players.csv` (50,149), `games.csv` and `appearances.csv`
  (for 2015/16 club membership). `clubs.csv` is not needed: `games.csv` names the 80 clubs of the four
  2015/16 leagues directly (`competition_id` GB1, ES1, IT1, FR1; Transfermarkt season code 2015).
- **License.** The dataset and pipeline are released CC0. CC0 covers the uploader's own work and cannot
  waive Transfermarkt's rights in its database (EU sui-generis database right). Treated as low-risk for
  non-commercial research, **not legally cleared** for commercial use (BLOCKERS.md sections 20 and 21).
- **What a value is.** Transfermarkt's community estimate of a transfer fee on a date. Not a wage, not a
  fee paid, not a model of ours.
- **Snapshot.** Per Transfermarkt player, the latest valuation dated 2015-10-01 to 2016-03-01, both
  inclusive (14,388 rows in the window). None in the window -> no value (NULL). On two valuations with the
  same date, the later row in the file wins. In practice each league's values come from one league-wide
  Transfermarkt update: Serie A 2016-01-04, Ligue 1 2016-02-04, EPL 2016-02-09, La Liga 2016-02-22 (a
  handful of players carry another date), so the leagues are compared at dates up to seven weeks apart,
  all after the January window opened. This replaces the "on or before 2016-01-01" proposal of BLOCKERS.md
  section 20.
- **Currency.** Stored in EUR (`market_value_eur`) and in GBP at the fixed 2015/16 rate of section 24
  (1 EUR = 0.75 GBP, `exchange_rate_used`). The UI shows EUR.
- **Match method.** `players.transfermarkt_id` was 0% populated and StatsBomb has no birth dates, so
  matching is by name, restricted to the player's club:
  1. *Candidates* are Transfermarkt players with an appearance for a club in a 2015/16 league game of the
     four leagues (`appearances` + `games`: all 1,520 games, 2,175 players). Club names are mapped to our
     canonical names through `team_identity` (18 Transfermarkt spellings added as aliases, e.g. "Arsenal FC",
     "Associazione Sportiva Roma"). A profiled player is compared only with candidates of his own club(s)
     (every club in `team_split` for winter movers). A name is never matched across a club conflict.
  2. *Names* are compared in every spelling Transfermarkt gives (`players.name`, first + last name, the
     appearance name) after `normalize_name`: casefold, accents stripped (plus ø, ł, đ, ß, æ, œ, ı),
     apostrophes and dots dropped, hyphens and other punctuation as spaces, suffixes Jr/Sr/II/III/IV
     dropped ("Junior" is kept: it is often a real name).
  3. *Stages*, the first that hits wins: **exact** (same name ignoring case), **normalized** (equal after
     normalization, or equal with spaces removed), **fuzzy** (one name's words all inside the other's with a
     4+ letter word, e.g. "José Giménez" / "José María Giménez"; same surname with one first name a 3+ letter
     prefix of the other, e.g. "Maxi" / "Maximiliano"; or a difflib ratio of at least 0.85, as written or
     with the words sorted, e.g. "Ki Sung-Yeung" / "Sung-yueng Ki"). `match_score` records the containment
     share, prefix share or ratio.
  4. *No guessing.* Two or more candidates at the best stage -> unmatched and noted as ambiguous (a fuzzy
     leader needs a 0.10 lead). A Transfermarkt player claimed by two profiles keeps the stronger match; the
     other is unmatched.
- **Result (real numbers, not tuned to a target).** 1,331 of 1,337 profiled players matched (**99.6%**);
  1,326 have a value in the window (**99.2%**). Matched / valued of profiled: EPL 323 / 323 of 324; La Liga
  335 / 333 of 340; Ligue 1 336 / 334 of 336; Serie A 337 / 336 of 337. By method: exact 1,212, normalized
  65, fuzzy 54, unmatched 6. All 54 fuzzy matches were read by eye and look right. The rate is high because
  the club restriction shrinks each search to about 25-30 names; it says nothing about how a club-free match
  would do.
- **Missing is NULL.** Unmatched players (6) and matched players with no valuation in the window (5) have
  every value field NULL, a `match_note` saying which, and show "no value available" in the UI, never 0.
  Unmatched players are listed by minutes in `data/processed/unmatched_market_values.csv`.
- **Club sum.** The Team Fingerprint shows "Profiled players' market value": the sum of the known values
  of the club's profiled players (900+ minutes), each counted at his main club only, with "N of M profiled
  players". It is **not a squad value**: fringe players are not profiled. Atlético Madrid: EUR 317m from 15
  of 15 profiled players, against EUR 387.5m for the 24 valued of the 25 Transfermarkt players with an
  Atlético league appearance. Below 80% of profiled players valued, the line adds "partial, coverage is thin".
- **`valuation_club`** stores Transfermarkt's club on the valuation date. It differs from our main club
  for 69 players: 37 are spellings only ("Bologna FC 1909", "Carpi FC 1909"), the rest moved in January, so
  the value was set at the new club (e.g. Khazri at Sunderland). The value is kept: it is still that
  player's 2015/16 valuation.
