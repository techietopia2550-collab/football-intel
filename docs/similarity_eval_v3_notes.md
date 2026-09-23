## Reviewer notes and verdict

Written after reading the lists above. N = 12 and the feature rule were fixed before any list was seen;
no re-tuning followed. Similarity scores are not compared (see the top of this file).

**Verdict: the principle worked for the archetypes that have a sharp identity, and did nothing for, or
slightly hurt, the ones that do not. It is defensible, not validated.**

### The diagnostic the brief asked for

| Player | Rank in Özil's list, all-feature archetype | Rank in v3 |
|---|---|---|
| Eriksen | 26 | 12 |
| Pjanić | 21 | 10 |
| Cazorla | 13 | 16 |
| Hamšík | 11 | 17 |

Two of the four moved up (Eriksen, Pjanić), two moved down (Cazorla, Hamšík). None is in Özil's top 5 and
only Pjanić is at rank 10. **Against the brief's hope of Eriksen and Pjanić in the top 10: Pjanić
made it (10th), Eriksen did not (12th), and the other two got worse.** The pool is 55 players, so ranks 10 to 17 are all
within the same third of it; the movement is real but small. Read "Coherence of the top 10" below
before reading this as failure: the four are ranked 10 to 17 because 9 to 16 other players outrank them, and
the top 10 are all creators too.

### Coherence of the top 10

- **Özil (Chance creator):** top 5 are James Rodríguez, Barrada, Di María, De Bruyne, David Silva; 6 to 10 are
  Payet, Valbuena, Boudebouz, Messi, Pjanić. All chance creators; the top 5 are the right kind of player.
  Before (all features): Di María, James, Payet, Silva, Valbuena, Messi. The set is nearly the same (9 of 10
  shared), the order is tighter and De Bruyne enters at 4. Silva stays a top-5 match. Boudebouz, Valbuena,
  Hoolahan (all AM under v2) are the players that pooling by position used to give him; the archetype removes
  Hoolahan/Saponara/Barkley/Davis-type players from the top 10 and keeps the ones who create at Özil's rate.
- **Kanté (Destroyer):** v3 top 5 are Gueye, Illarramendi, Kankava, Poko, Rubén Pérez: ball-winning midfielders.
  This is clearly better than the all-feature list, whose #1 was André Poko as a *full-back* and which put
  Somália (FB), Ricardo Pereira (FB) in the top 10. v3 is 9 of 10 midfielders (8 of them DM in the data; only Poko is a full-back); Casemiro is at 7.
  Kanté's archetype confidence is low (0.24), so his pool is a soft description of him.
- **Čech (GK):** unchanged by construction (goalkeepers keep their nine metrics, so nothing was selected). The
  list is the same as the all-feature archetype list and differs from v2 only in the pool, as before.
- **Morgan (Stopper):** top 5 are all centre-backs of the same type. Not comparable to before in score terms
  (12 features give near-0.95 cosines for every stopper). Plausible, not sharper.
- **Vardy (Poacher):** Bastón, Torres, Gameiro, Cavani, Defoe: fine. Cluster confidence is 0.07, so Vardy sits on
  the edge of the Poacher archetype; the list does not contain a wide forward, which would be the danger.
- **Busquets (Deep playmaker):** Stambouli, Mario Suárez, Vecino, Lucas Silva, Rakitić: deep midfielders who
  circulate the ball; plausible. Overlaps v2 only in Motta, Vecino, Barry and Schweinsteiger, and is not obviously worse.
- **Mahrez (Dribbling winger):** Ntep, Townsend, Joaquín, Sarabia, Orellana: wide dribblers. Cluster
  confidence is 0.19 and Mahrez is a CM in the data; the list describes his dribbling, not his goal threat.
- **Kane (Poacher):** Ibrahimović, Agüero, Benzema, Higuaín, Floro Flores: a good top 4.

### Where v3 got worse or stayed weak (reported, not re-tuned)

- **Kyle Walker (Defensive full-back): degraded.** The archetype's distinctive features are weak (largest
  distance 0.97, the twelfth 0.45) and mostly defensive or low-attack (progressive passes, low shots and xG,
  interceptions, blocks, tackles), so Walker's pace and carrying are not features. v3 puts two centre-backs
  (Simon Francis, Geoff Cameron) in the top 10 and Cholevas, Ward first; the all-feature list (Dubois, Mendy,
  Clyne, Hutton, Rojo) was closer to "attacking full-back". Not nonsense, but worse.
- **Christian Fuchs (Defensive full-back): neither better nor worse.** Different names (Polomat, Letizia,
  Marín), full-backs throughout.
- **Cazorla and Hamšík** fell in Özil's list (see above). Their own v3 lists are creators
  (Cazorla: Mesloub, Pjanić, Banega, Willian; Hamšík: Silva, Insigne, Mesloub, Pjanić, Hazard), which is
  coherent; it is just further from Özil's profile than v2 or the all-feature list thought.
- **The distinctive features of weak archetypes are weak.** Defensive full-back, Destroyer and Pressing
  wide midfielder have a 12th feature with |Δz| 0.45 to 0.50, against 1.0 to 1.3 for the sharp archetypes:
  their selection cut-off is a plateau, so which features made it is partly arbitrary.
- **Low is distinctive too.** By the brief's rule (|difference|) a Stopper's set includes low key passes and
  low carries, and a Poacher's includes low tackles and interceptions. That means "does not do X" features
  are in the similarity; it is deliberate, and it is why a poacher's list is not polluted by wide players.
  It does not put defensive features in the Chance creator set: all 12 of its features are creative or
  positional, and its first defensive feature (low own-box defensive actions) is 15th.

### Limits that do not change

- One season of one source; 900+ minute players only; archetypes are descriptive names given to K-Means
  clusters, k = 10 chosen by judgement (silhouette prefers a smaller k), and a player on an archetype boundary
  (confidence under 0.25, flagged above) is only softly described by it.
- Mata is Deep playmaker, M'Vila Defensive full-back: the archetype misses recorded in BLOCKERS.md. Mata is
  therefore not in Özil's pool at all (his row in the neighbourhood table is "not in pool").
- No ground truth for "similar" exists; nothing here is scored for accuracy.
