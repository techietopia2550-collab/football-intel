# ANALYTICS_DECISIONS

Every definition choice behind the 2015/16 EPL metrics, percentiles and profiles (StatsBomb Open Data).
These are analytics choices, not data corrections: they do not go in `statsbomb_corrections.csv`.
Code: `src/analytics/metrics.py` (definitions), `src/transform/normalize.py` (season aggregation),
`src/analytics/percentiles.py`, `src/analytics/profile.py`, `src/analytics/heatmaps.py`,
`src/analytics/similarity.py`. Everything describes **2015/16 only**.

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
