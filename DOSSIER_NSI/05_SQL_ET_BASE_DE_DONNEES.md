# 05 — SQL et base de données

## Sommaire
1. La base en un coup d'œil
2. Le schéma et les relations
3. Est-ce normalisé ? (anomalies du schéma)
4. Dix requêtes SQL commentées (testées)
5. Du SQL au chatbot
6. Le SGBD SQLite : ce qu'il apporte

Fichiers liés : `code_commenté/bd_schema_commente.sql` (les CREATE TABLE réels), `code_commenté/requetes_sql.sql` (les 10 requêtes).

---

## 1. La base en un coup d'œil

- **Fichier :** `data/football.db`, une base **SQLite** (tout tient dans un seul fichier, sans serveur).
- **Création :** les tables sont décrites comme des **classes Python** dans `src/models/schema.py`. La bibliothèque SQLAlchemy traduit ces classes en `CREATE TABLE` (`init_db()`, ligne 428).
- **18 tables.** Les principales :

| Table | Une ligne = | Lignes |
|---|---|---|
| `players` | un joueur (identité) | 2 735 |
| `matches` | un match | 1 507 |
| `player_match_stats` | un joueur dans un match | 41 816 |
| `player_season_stats_2015_16` | un joueur qualifié (≥ 900 min) sur la saison | 1 337 |
| `insufficient_sample` | un joueur sous 900 min | 836 |
| `player_archetypes` | l'archétype d'un joueur | 1 337 |
| `player_percentiles` | un joueur × une statistique (rang en %) | 50 442 |
| `team_style` | une équipe (style de jeu) | 80 |
| `player_market_values` | valeur Transfermarkt d'un joueur | 1 337 |

## 2. Le schéma et les relations

Notation du cours (# = clé primaire, → = clé étrangère) :

```
players            (#player_id, canonical_name, birth_year, nationality, statsbomb_id, …)
matches            (#id, match_id, season, league, match_date, home_team, away_team, …)
player_match_stats (#id, match_id, player_id → players, team, minutes, tackles, xg, …)
player_season_stats_2015_16 (player_id, player_name, team, league, minutes_total, goals, …_p90)
player_archetypes  (#id, player_id, season, archetype_id, archetype_label, cluster_confidence)
```

```
            players
         #player_id ◄──────────────┐ clé étrangère (déclarée)
                                    │
  matches                 player_match_stats ──(addition des matchs)──► player_season_stats_2015_16
  match_id ◄ ─ ─ ─ ─ ─ ─ ─  match_id                                       player_id
  (lien NON déclaré)                                                         │
                                                                             ▼ (même player_id,
                                                                    player_archetypes   lien non déclaré)
```

- **Clé primaire :** identifie une ligne de façon unique (`players.player_id`, par exemple `statsbomb:3961` pour Kanté).
- **Clé étrangère :** `player_match_stats.player_id` doit exister dans `players`. C'est déclaré dans le schéma (`FOREIGN KEY`).
- **Contraintes d'unicité :** `UNIQUE (source, match_id, player_id)` empêche d'enregistrer deux fois le même joueur dans le même match. Si on relance la collecte, pas de doublons.
- **Tables calculées :** `player_season_stats_2015_16` est **reconstruite** à partir de `player_match_stats` (`src/transform/normalize.py`). Elle n'est jamais modifiée à la main.

## 3. Est-ce normalisé ? (anomalies du schéma)

Le programme demande de « repérer des anomalies dans le schéma ». Voici ce que j'ai repéré, pour en discuter honnêtement.

| Observation | Pourquoi c'est une anomalie | Pourquoi le projet le fait quand même |
|---|---|---|
| `player_name` et `team` sont recopiés dans `player_match_stats`, `player_archetypes`, `player_market_values`… | **Redondance** : si un nom change, il faut le changer partout, sinon la base devient **incohérente**. | Lecture plus simple et plus rapide (pas de jointure pour afficher un nom). Ces tables sont recalculées à chaque fois. |
| `player_archetypes.player_id` n'a **pas** de `FOREIGN KEY` | Le SGBD ne garantit pas que le joueur existe. | Le lien existe dans les données : j'ai vérifié, 0 joueur « orphelin ». Mais rien ne l'impose. |
| `matches.match_id` n'est pas une clé étrangère dans `player_match_stats` | Même problème. | `match_id` seul n'est pas unique (il faut aussi `source`). |
| Une table **par saison** (`player_season_stats_2015_16`) | Une colonne `season` suffirait ; ajouter une saison crée une nouvelle table. | Choix du projet. La colonne `season` existe quand même. |
| `teams` (20 lignes) et `match_results` (380) contiennent la saison **2024/25** | Données d'une autre saison, inutilisées par l'analyse. | Restes des premiers essais (Understat / football-data.co.uk). |

**Conclusion à discuter :** la base n'est **pas entièrement normalisée**. Les données brutes (`players`, `matches`, `player_match_stats`) sont propres. Les tables **calculées** sont volontairement « à plat », pour aller plus vite. C'est un compromis courant, appelé **dénormalisation**.

## 4. Dix requêtes SQL commentées (testées)

Toutes ont été exécutées sur la vraie base le 1er octobre 2026 (les requêtes 8 à 10 sur une **copie**, car elles modifient la base).

**1. SELECT simple**
```sql
SELECT team_name, league, points, league_position FROM team_style LIMIT 5;
```
Affiche 4 colonnes des 5 premières équipes. → `('Arsenal', 'ENG-Premier League', 71, 2)`, …

**2. SELECT avec WHERE**
```sql
SELECT player_name, goals, minutes_total
FROM player_season_stats_2015_16
WHERE team = 'Leicester City' AND goals >= 5;
```
Garde seulement les lignes qui respectent **les deux** conditions (`AND`). → Mahrez 17, Vardy 24, Okazaki 5, Ulloa 6.

**3. ORDER BY**
```sql
SELECT player_name, team, goals FROM player_season_stats_2015_16
ORDER BY goals DESC LIMIT 5;
```
Trie du plus grand au plus petit (`DESC`). → Suárez 40, Higuaín 36, Ibrahimović 36, Ronaldo 35, Messi 26.

**4. JOIN**
```sql
SELECT s.player_name, a.archetype_label, a.cluster_confidence
FROM player_season_stats_2015_16 AS s
JOIN player_archetypes AS a ON a.player_id = s.player_id
WHERE s.team = 'Leicester City'
ORDER BY a.cluster_confidence;
```
Associe chaque ligne de stats à la ligne d'archétype qui a le **même** `player_id`. → Drinkwater *Destroyer* 0,044 ; **Vardy *Poacher* 0,073** ; … ; Kanté *Destroyer* 0,24 ; … ; Huth *Stopper* 0,44. Lien avec la problématique : 7 joueurs de Leicester sur 14 ont une confiance sous 0,25, donc sont à la limite de leur archétype.

**5. GROUP BY + COUNT**
```sql
SELECT archetype_label, COUNT(*) AS nb_joueurs
FROM player_archetypes GROUP BY archetype_label ORDER BY nb_joueurs DESC;
```
Regroupe les lignes qui ont le même archétype et les compte. → *Defensive full-back* 196, *Stopper* 176, *Destroyer* 175, … *Target man* 44.

**6. AVG, MAX, MIN**
```sql
SELECT ROUND(AVG(cluster_confidence), 3) AS moyenne, MAX(cluster_confidence) AS maxi,
       MIN(cluster_confidence) AS mini, COUNT(*) AS total
FROM player_archetypes;
```
Fonctions d'**agrégation** : un seul résultat pour toute la table. → moyenne 0,32 ; max 0,85 ; min 0,002 ; 1 337 joueurs.

**7. Sous-requête**
```sql
SELECT player_name, team, ROUND(pressures_p90, 1) AS pressions_p90
FROM player_season_stats_2015_16
WHERE pressures_p90 > (SELECT AVG(pressures_p90) FROM player_season_stats_2015_16)
ORDER BY pressures_p90 DESC LIMIT 5;
```
La requête entre parenthèses est calculée **d'abord** (la moyenne), puis sert de seuil. → Crimi (Carpi) 42,2 ; Lollo (Carpi) 40,5 ; …

**8. CREATE TABLE**
```sql
CREATE TABLE avis_scout (
    id INTEGER PRIMARY KEY,
    player_id VARCHAR NOT NULL REFERENCES players(player_id),
    note INTEGER CHECK (note BETWEEN 1 AND 10),
    commentaire VARCHAR
);
```
Crée une table avec clé primaire, clé étrangère et contrainte `CHECK` (la note doit être entre 1 et 10).

**9. INSERT**
```sql
INSERT INTO avis_scout (player_id, note, commentaire)
SELECT player_id, 9, 'Récupérateur infatigable'
FROM players WHERE canonical_name = 'N''Golo Kanté';
```
Ajoute une ligne. Le `''` sert à écrire une apostrophe dans une chaîne SQL. → 1 ligne insérée.

**10. UPDATE**
```sql
UPDATE avis_scout SET note = 10
WHERE player_id = (SELECT player_id FROM players WHERE canonical_name = 'N''Golo Kanté');
```
Modifie la note. **Sans `WHERE`, toutes les lignes seraient modifiées.** → `(1, 'statsbomb:3961', 10, 'Récupérateur infatigable')`.

## 5. Du SQL au chatbot

Le chatbot **n'écrit jamais de SQL lui-même**. C'est voulu : une IA qui écrit du SQL pourrait se tromper, ou effacer des données.

```
Question : « Qui joue comme Kanté ? »
   │
   ▼  le LLM choisit l'outil find_similar("Kanté")        (src/chat/tools.py)
   ▼  Python exécute src/analytics/similarity.py, qui lance des requêtes écrites à l'avance :
        SELECT * FROM player_season_stats_2015_16 WHERE season = :s
        SELECT player_id, pool, archetype_id, archetype_label, cluster_confidence
          FROM player_archetypes WHERE season = :s
   ▼  calcul du score, puis règle du seuil (guardrails)
   ▼  le LLM rédige la réponse à partir de CE résultat seulement
   ▼  vérification : chaque nombre de la réponse doit venir du résultat
```

- `:s` est un **paramètre** : la valeur est envoyée séparément de la requête. Cela empêche l'**injection SQL**, une attaque où quelqu'un glisse du code SQL dans un champ de saisie (`src/analytics/similarity.py:112-119`).
- Le seuil de 900 minutes est appliqué **avant**, au moment de construire les tables. C'est déjà un premier « filtre de confiance » : pas assez de données, pas de score.

## 6. Le SGBD SQLite : ce qu'il apporte

| Service d'un SGBD (programme) | Dans le projet |
|---|---|
| Persistance | Les données restent sur le disque entre deux lancements. |
| Efficacité | Des **index** sur `player_id`, `season`, `league` accélèrent les recherches. |
| Accès concurrents | SQLite gère un seul écrivain à la fois. Ça suffit ici : l'application est utilisée par une seule personne. |
| Sécurisation | Pas de comptes utilisateurs dans SQLite. La base est locale et ignorée par Git (`.gitignore`). |
| Évolution du schéma | `_migrate()` ajoute les colonnes manquantes (`ALTER TABLE … ADD COLUMN`) sans perdre les données (`schema.py:415`). |
