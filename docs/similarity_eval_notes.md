## Reviewer notes

Written by hand after reading the output above (2015/16 only). These are judgements about whether a
list looks sensible to someone who knows the season. They are not measurements, and the model is not
claimed to be accurate.

**Looks plausible**

- **Kanté**: Gueye, Capoue, Behrami, Anita, M'Vila, Cork are all ball-winning midfielders; the shared
  metrics are tackles, interceptions and recoveries. Caveat: he is only 57% CM (the rest DM), so the
  search never looks at the DM group.
- **Vardy**: Kane, Agüero, Sturridge, Ighalo, Benteke, Lukaku share goals, xG and xG per shot (z about
  +1 to +2 for both). His #1, Defoe, is there for a different reason: both are *low* on the receiving
  proxies (passes received, under pressure, progressive; z -0.8 to -2.1), which describes how rarely
  teammates find them, not how they finish. The scoring profile is what matches Kane, Agüero and
  Sturridge at #2 to #4, so read #1 as a receiving-style match and #2 to #4 as a finishing match.
- **Kane**: Agüero and Sturridge on top, then Vardy, Defoe, Costa. Same reading as Vardy.
- **Mahrez**: Stanislas, Lens, Redmond, Watmore, Lanzini, Pugh, Sessègnon: wide dribblers and
  creators. He is grouped as CM only because StatsBomb labels 91% of his minutes "Right Midfield"
  (Leicester's 4-4-2), so his list mixes the CM group's wide players.
- **Morgan / Huth**: each other, Olsson, O'Shea, Lescott, Prödl, Bennett, Dann. Centre-backs who rarely
  carry or receive the ball. The shared metrics are mostly *low* carries and *low* passes received (see the z
  values): the match is "does not carry or receive much", not "is good at something".
- **Čech**: Lloris, Adrián, Courtois at the top. Goalkeeper similarity is dominated by save % and goals
  conceded, which depend on the team in front of the keeper, so this partly finds keepers behind
  similar defences.

**Weak or not to be trusted**

- **Özil**: only ranks 1 and 2 (David Silva 0.71, Hoolahan 0.24) are similar at all. From rank 4 down
  the cosine is *negative*: there are only 14 qualifying AM players, so after Silva there is nobody
  close. Silva at #1 is the sensible result; the rest of the list is filler.
- **Fuchs and Walker** (full-backs): the best match scores 0.61 and 0.47, and the lists look loose
  (Walker's includes Kolarov, Hutton, Clichy, Daniels; Clyne is 9th). The FB group pools full-backs and
  wing-backs from very different systems, the vector has 32 features and needs 12 components to keep
  85% of the variance, and `dribble_success_pct` was dropped (empty for 42% of the group). A low top
  score should be read as "nobody is really like him", not as a ranking to act on.
- **Goalkeepers**: 27 players and 4 components; scores fall from 0.87 to 0.25 within ten places, so only
  the top three or four are meaningful.
- `pressure_duration_avg` shows up often in the FB "differs" column: it is a noisy style ratio. It is
  left in because removing a feature after seeing which players it hurts would be tuning to the
  examples.

**Things this evaluation cannot tell you**: whether a returned player would fit a role, whether the
ranking beats any alternative (there is no benchmark), or how the lists change with another season.
The brief expected Kanté to be a DM and Özil and Mahrez to be CM; the data puts Kanté in CM (57%),
Özil in AM (100%) and Mahrez in CM (91%), and the engine follows the data.
