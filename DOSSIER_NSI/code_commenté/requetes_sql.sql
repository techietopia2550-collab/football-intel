-- =============================================================================
-- 10 REQUÊTES SQL commentées, testées sur la vraie base data/football.db
-- (SQLite). Expliquées dans 05_SQL_ET_BASE_DE_DONNEES.md.
-- Les requêtes 8, 9 et 10 MODIFIENT la base : à tester sur une COPIE.
-- =============================================================================

-- 1. SELECT simple : nom, championnat, points et classement de 5 équipes 2015/16
SELECT team_name, league, points, league_position FROM team_style LIMIT 5;

-- 2. SELECT avec WHERE : les joueurs de Leicester qui ont marqué au moins 5 buts
SELECT player_name, goals, minutes_total
FROM player_season_stats_2015_16
WHERE team = 'Leicester City' AND goals >= 5;

-- 3. ORDER BY : les 5 meilleurs buteurs des 4 championnats
SELECT player_name, team, goals
FROM player_season_stats_2015_16
ORDER BY goals DESC
LIMIT 5;

-- 4. JOIN : chaque joueur de Leicester avec son archétype et sa confiance
SELECT s.player_name, a.archetype_label, a.cluster_confidence
FROM player_season_stats_2015_16 AS s
JOIN player_archetypes AS a ON a.player_id = s.player_id
WHERE s.team = 'Leicester City'
ORDER BY a.cluster_confidence;

-- 5. GROUP BY + COUNT : nombre de joueurs par archétype
SELECT archetype_label, COUNT(*) AS nb_joueurs
FROM player_archetypes
GROUP BY archetype_label
ORDER BY nb_joueurs DESC;

-- 6. AVG, MAX, MIN : statistiques sur la confiance d'archétype
SELECT ROUND(AVG(cluster_confidence), 3) AS moyenne,
       MAX(cluster_confidence) AS maxi,
       MIN(cluster_confidence) AS mini,
       COUNT(*) AS total
FROM player_archetypes;

-- 7. Sous-requête : joueurs qui pressent plus que la moyenne de TOUS les joueurs
SELECT player_name, team, ROUND(pressures_p90, 1) AS pressions_p90
FROM player_season_stats_2015_16
WHERE pressures_p90 > (SELECT AVG(pressures_p90) FROM player_season_stats_2015_16)
ORDER BY pressures_p90 DESC
LIMIT 5;

-- 8. CREATE TABLE : une table pour noter ses propres avis de scout
CREATE TABLE avis_scout (
    id INTEGER PRIMARY KEY,
    player_id VARCHAR NOT NULL REFERENCES players(player_id),
    note INTEGER CHECK (note BETWEEN 1 AND 10),
    commentaire VARCHAR
);

-- 9. INSERT : ajouter un avis sur Kanté
INSERT INTO avis_scout (player_id, note, commentaire)
SELECT player_id, 9, 'Récupérateur infatigable'
FROM players WHERE canonical_name = 'N''Golo Kanté';

-- 10. UPDATE : corriger la note
UPDATE avis_scout SET note = 10
WHERE player_id = (SELECT player_id FROM players WHERE canonical_name = 'N''Golo Kanté');
