# Player heatmaps (data only)

`player_heatmaps` holds a 2D histogram of where each qualifying 2015/16 EPL player's events happened.
**Nothing is rendered**: rendering (Plotly) belongs to the UI session. Code: `src/analytics/heatmaps.py`; decisions:
`ANALYTICS_DECISIONS.md` section 10. Rebuild with `python -m src.analytics.heatmaps`.

## Format

| Column | Meaning |
|---|---|
| `player_id`, `player_name`, `season`, `league`, `source` | identity and provenance |
| `position_group_fine` | GK / CB / FB / DM / CM / AM / W / ST |
| `heatmap_type` | `all_actions`, `touches`, `defensive`, `passing`, `shots`, `all_actions_1h`, `all_actions_2h` |
| `minutes_total` | the player's season minutes (the divisor for per-90) |
| `event_count`, `event_count_p90` | grid sums: raw and per 90 minutes |
| `grid_json` | raw event counts, JSON `grid[row][col]`: **8 rows (y) x 12 columns (x)** |
| `grid_p90_json` | the same grid divided by `minutes_total` and multiplied by 90 (4 decimals) |

- **Cells** are 10 x 10 yards on StatsBomb's 120 x 80 pitch. Column 0 touches the player's own goal line; column 11 the
  opponent's (every team is recorded attacking towards x = 120). Row 0 is y 0-10 and **y increases toward the acting
  player's right touchline** (checked: the 15 most-played right-backs average y 66.5, left-backs 13.7). To draw it
  with the player's own goal on the left and his right side at the bottom, plot the array as it is.
- Coordinates just outside the pitch (x up to 120.9 in this data) are clamped into the edge cell.
- Every qualifying player has all 7 rows (323 x 7 = 2,261); a type with no events is an all-zero grid (26 goalkeepers
  and 2 defenders for `shots`).
- A heatmap shows where a player was involved, which reflects role, team style and possession as much as the player.

## Sanity check (Čech, Morgan, Mahrez), run before building on the table

Shares of each grid's events. "Box zone" is columns 0-1 by rows 2-5 (20 x 40 yards, a little larger than the 18-yard
box because cells are 10 yards wide).

| Player | Type | Events | Mean x | Own half | Box zone | Central lanes | Right-wide rows | Final third |
|---|---|---|---|---|---|---|---|---|
| Petr Čech (GK) | all_actions | 2474 | 9.8 | 100% | 90% | 96% | 1% | 0% |
| Petr Čech (GK) | touches | 823 | 9.7 | 100% | 91% | 97% | 1% | 0% |
| Petr Čech (GK) | defensive | 171 | 10.7 | 99% | 89% | 95% | 2% | 1% |
| Wes Morgan (CB) | all_actions | 2859 | 42.3 | 84% | 12% | 67% | 24% | 7% |
| Wes Morgan (CB) | touches | 1029 | 47.9 | 82% | 1% | 69% | 22% | 8% |
| Wes Morgan (CB) | defensive | 516 | 26.9 | 92% | 45% | 73% | 21% | 2% |
| Riyad Mahrez (CM) | all_actions | 5796 | 73.4 | 28% | 1% | 34% | 59% | 45% |
| Riyad Mahrez (CM) | touches | 2764 | 77.1 | 22% | 0% | 35% | 58% | 50% |
| Riyad Mahrez (CM) | defensive | 394 | 55.3 | 56% | 7% | 38% | 57% | 25% |

All three match what their roles predict: Čech (goalkeeper) sits almost entirely in his own box zone; Morgan (centre-back,
Leicester) stays in his own half, mostly central and slightly right (Right Center Back), with nearly half of his
defensive events in the box zone; Mahrez (Right Midfield in Leicester's 4-4-2, so a CM in the data) touches the ball
mostly on the right and in the final third.

Halves are not always level: Morgan has 1,700 first-half and 1,159 second-half events (his passes, receipts and carries
fall by about a third; clearances and duels do not; Leicester's totals per half are level). That is in the data.

## Sample grids

Raw counts, `grid[row][col]`, rows y 0-10 (top) to y 70-80 (bottom), columns from the player's own goal line (left) to
the opponent's (right).

**Petr Čech, all_actions** (2474 events, 2970 minutes, 74.97 per 90)

```
   3    2    5    2    2    0    1    0    0    0    0    0
   8   15   10    9    0    0    0    0    0    0    0    0
 130   81   34   11    0    0    0    0    0    0    0    0
 741  317   34    0    1    0    1    0    1    0    0    0
 621  200   46    1    1    0    0    0    0    0    0    0
  54   76   24    7    4    0    0    0    0    0    0    0
   8    1    5    1    0    0    0    0    0    0    0    0
   1   10    4    2    0    0    0    0    0    0    0    0
```

**Wes Morgan, defensive** (516 events, 3420 minutes, 13.58 per 90)

```
   3    2    2    0    2    0    1    2    0    1    0    1
   1    3    4    2    1    6    0    0    0    0    0    0
  10   11    8    6    2    1    1    0    0    0    0    0
  42   28    9    5    7    5    4    0    0    0    0    1
  53   49    9    9   11    7    1    0    0    0    1    1
  15   23   19   10   14    6    4    2    1    0    1    1
   5    9    5   12    9    9    5    5    0    0    0    0
   7    7    5   10    8    2    3    3    1    1    0    2
```

**Riyad Mahrez, touches** (2764 events, 2968 minutes, 83.82 per 90)

```
   0    0    3    3    2    3   12    7   14   19    9   12
   0    2    2    0   10   18   25   16   14   28    4    1
   0    0    0    3    9   11   13    9   26   20    8    7
   0    2    4    5    9   18   14   24   32   28   14    9
   0    0    4   13   18   27   23   22   50   51   29   18
   1    7    9   19   28   22   35   78   56   80   55   47
   5   12   15   20   26   59   61   95   89   90   66   22
   2   12   22   34   79   77  139  204  198  131  108   37
```

The stored JSON for one row (`Riyad Mahrez`, `shots`, raw `grid_json`):

```json
[[0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 2, 2], [0, 0, 0, 0, 0, 0, 0, 0, 0, 7, 7, 4], [0, 0, 0, 0, 0, 0, 0, 0, 1, 14, 13, 7], [0, 0, 0, 0, 0, 0, 0, 0, 0, 8, 8, 11], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]
```
