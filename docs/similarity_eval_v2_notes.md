## Reviewer notes

Written by hand after reading the lists above (2015/16 only). They are judgements about whether a list
looks sensible to someone who knows the season, not measurements. The model is not claimed to be
accurate, and nothing was tuned after seeing these results.

**Baseline check.** Running the v1 code on `data/football_backup_pre_sub_repair.db` (the database from
before the 6 matches were repaired) reproduces all ten stored v1 top scores exactly. So the gap between
the stored file and the "same data" column is entirely the repaired EPL data (374 -> 380 matches), not a
code change. Mahrez moves most (0.656 -> 0.783); why one group's PCA moved that much was not investigated.

**Özil: partly coherent, and the brief's neighbourhood does not appear.**
- Top 3 are sensible: David Silva (0.82), Ryad Boudebouz (0.79) and Mathieu Valbuena (0.78) are all
  creators who play between the lines. Ranks 4-6 (Hoolahan, Saponara, Barkley) are creative but looser.
  The tail still decays: rank 10 is 0.17, so beyond about #6 it is filler again, though 44 players in the
  group is a much less thin pool than 14 (v1 went negative from rank 4; v2's top-10 mean is 0.48).
- **The expected neighbours are not in the list because the engine never compares them with Özil.**
  Only Silva shares his group (AM). Mata and Eriksen are W in the data (51% and 54% of minutes), Cazorla
  DM (58%), and James Rodríguez, Pjanić and Hamšík are CM (49%, 57%, 100%). The group is whatever
  StatsBomb's lineup position strings say they mostly played (mapped through `FINE_POSITION_GROUPS`),
  and the same-group-only rule follows it. Pooling did not and could not change that. If the expected neighbours are the test, the test fails
  by construction; pooling widened the AM group from 14 to 44 but did not put these players in it.

**Goalkeepers (Čech): the list looks much better, with a caveat.** Ruffier, Cardinale, Ochoa, Navas,
Oblak, Trapp, Enyeama, Bravo, Buffon, Lloris: ten real first-choice keepers, and scores stay at
0.79-0.94 rather than falling to 0.25 as in v1. Pooled group is 98 keepers. The caveat from v1 stands:
goalkeeper similarity rests on save %, goals conceded and a goals-prevented proxy, all shaped by the
defence in front of him, so this partly finds keepers behind similar defences. The list being
"credible" does not mean the features separate keepers by style or quality.

**Full-backs: Fuchs improved, Walker did not.** Fuchs's top score rose 0.62 -> 0.80 (Pavlovic,
Frosinone) and the list now includes Baines, a like-for-like attacking left-back with set pieces, at #5;
the rest is a mix of full-backs and wing-backs. Walker's best match is 0.53 (Daniels), and the list
(Daniels, Rojo, Dubois, Kolarov, Clichy, Hutton, Clyne, Kelly, Mendy, Alba) is a generic full-back list
with no clear reading. FB still needs 13 components to keep 85%; a low top score means "nobody is really
like him".

**Where the verdict rule misleads.** It needs the top score and the top-10 mean to both move by 0.05.
- **Mahrez is "same" but the list is better.** Top score +0.03, top-10 mean 0.57 -> 0.75: the pooled list
  (Morales, Viera, Stanislas, Correa, Juanpi, Carrasco) is a clearer set of wide dribblers/creators
  than v1's. He is still filed as CM only because StatsBomb labels him "Right Midfield".
- **Vardy is "improved" partly because the pool got bigger.** A larger pool nearly always has a nearer
  neighbour. Gameiro (0.83) is #1, then Suárez, Kane, Bacca, Benzema; Defoe fell from #1 to #9. That reads better than v1, but the score rise is not proof.
- **Kane, Kanté, Morgan, Huth: same.** Agüero, Sturridge, Higuaín, Benzema for Kane; Gueye, Capoue for
  Kanté; the same non-carrying centre-backs (O'Shea, Olsson, Romagnoli, Squillaci) for Morgan and Huth.
  Pooling added Serie A and Ligue 1 names to the tails, not new ideas.
- **Cross-league matches are a feature, not an achievement.** 9 of Mahrez's 10 matches now come
  from La Liga, Serie A or Ligue 1, which a Premier League-only pool could never surface. Whether a
  Bordeaux winger really resembles a Leicester one depends on how comparable the leagues' styles and
  StatsBomb's tagging are. The pool treats them as one population; nobody has checked that they are.

**Things this evaluation cannot tell you**: whether a returned player would fit a role, whether the
ranking beats any alternative (there is no benchmark), or how the lists change with another season.
