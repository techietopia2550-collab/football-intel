# ANALYTICS_DECISIONS

Every definition choice behind the 2015/16 EPL metrics, percentiles and profiles (StatsBomb Open Data).
These are analytics choices, not data corrections: they do not go in `statsbomb_corrections.csv`.
Code: `src/analytics/metrics.py` (definitions), `src/transform/normalize.py` (season aggregation),
`src/analytics/percentiles.py`, `src/analytics/profile.py`. Everything describes **2015/16 only**.

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

## 3. Season aggregation (`normalize.py`)

- **Sums, then ratios.** Minutes and counts are summed over matches. Per-90 = `count / minutes_total * 90`.
  A ratio (pass completion, tackle win %, ...) is `sum(numerator) / sum(denominator)`, never an average of
  per-match ratios. `pressure_duration_avg` is weighted by pressures.
- **Minimum minutes: 900** (configurable). Below it, or with no position group, the player goes to
  `insufficient_sample` with a `reason`, and is logged. That table has totals only, no per-90 rates:
  a rate over a few hundred minutes is not something to rank on. 323 qualify, 226 do not.
- **Position group = the group with the most minutes over the season**, ties broken alphabetically.
  `position_group_share` is that group's share of the player's minutes. Under 60% is a mixed role and
  `profile()` warns (21 players). The label is the plurality, not a certainty.
- **Mixed roles are not split:** all of a player's minutes count in his one group's rates.
- **Multi-team players** (11): the team with most minutes is `team`; the split is in `team_split` and
  `multi_team_players_2015_16.csv`. Their stats cover all clubs.
- **Position taxonomy** (from ingest): wing-backs and full-backs are DEF; defensive and all central and wide
  midfielders are MID, so 4-4-2 wide players such as Mahrez are MID; wingers, wide attacking mids and
  strikers are FWD; central attacking mids are MID. Özil and Mahrez are MID for this reason.
- **DEF pools centre-backs and full-backs** (60 / 58 by main position). Known limitation.

## 4. Metrics (39: 30 outfield, 9 goalkeeper)

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
stopped % (crosses faced not recorded), aerial duels won % (won aerials are not a complete event).
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
- **Group sizes:** GK 27, DEF 118, MID 102, FWD 76. None is under 20, so nothing is flagged, but 27 keepers is
  close to the line and the GK percentiles are coarse.
- **All ties share a rank.** `red_p90` is 0 for almost everyone, so every such player sits at about the same
  middling percentile. It carries no ranking signal.

## 6. Profiles (`profile.py`)

- **Strength = percentile >= 80; weakness = percentile <= 20** (the top and bottom 20%). Both are inclusive.
  For lower-is-better metrics the percentile is flipped first.
- **Only metrics relevant to the position are judged** (`RELEVANT_METRICS`). Deliberate omissions: creation
  metrics for DEF (so a centre-back's low key passes is not a weakness, at the cost of hiding full-back
  creation), pass completion for FWD (style, not skill), and all outfield metrics for GK.
- **Neutral = every other relevant metric,** including all descriptive ones. `unranked` lists relevant metrics
  with no percentile because of a sample-size guard.
- **Match by name,** ignoring accents and case; an exact name wins, otherwise the query must be a substring of
  exactly one name. An ambiguous query raises `ValueError` listing candidates (it never guesses): "Morgan" matches
  three players.
- **A player under 900 minutes** returns `qualified: False`, the reason, and no strengths or weaknesses.
- **`warnings`** carry: a small position group, a mixed role (under 60% of minutes), and a multi-team player.

## 7. Validation on known players (2026-09-20)

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
