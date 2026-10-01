-- =============================================================================
-- SCHÉMA DE LA BASE — extrait de data/football.db (SQLite)
-- =============================================================================
-- Les instructions CREATE TABLE ci-dessous sont celles que SQLite a réellement
-- enregistrées (requête : SELECT sql FROM sqlite_master WHERE name = '...').
-- Elles sont générées automatiquement par Python (SQLAlchemy) à partir des
-- classes de src/models/schema.py. Seuls les commentaires « -- >> » sont ajoutés.
-- Certaines tables très longues sont abrégées (« ... »).
-- Lien NSI : modèle relationnel, clé primaire, clé étrangère, contraintes.
-- =============================================================================

-- >> TABLE players : l'identité d'un joueur (une ligne = un joueur). 2 735 lignes.
-- >> Classe Python correspondante : Player (src/models/schema.py, ligne 39).
CREATE TABLE players (
	player_id VARCHAR NOT NULL,           -- >> ex. 'statsbomb:3961' (= Kanté)
	canonical_name VARCHAR NOT NULL,      -- >> nom « officiel » choisi par le projet
	fbref_id VARCHAR,                     -- >> identifiants dans les AUTRES sources :
	understat_id VARCHAR,                 -- >> permettent de relier les sources entre elles
	transfermarkt_id VARCHAR,
	birth_year INTEGER,                   -- >> vide pour StatsBomb (pas de date de naissance)
	nationality VARCHAR,
	position_group VARCHAR, statsbomb_id VARCHAR,
	PRIMARY KEY (player_id)               -- >> CLÉ PRIMAIRE : identifie la ligne de façon unique
);

-- >> TABLE matches : un match (StatsBomb). 1 507 lignes.
CREATE TABLE matches (
	id INTEGER NOT NULL,                  -- >> clé « technique » auto-incrémentée
	source VARCHAR NOT NULL,
	match_id INTEGER NOT NULL,            -- >> identifiant du match CHEZ StatsBomb
	season VARCHAR NOT NULL,
	league VARCHAR NOT NULL,
	match_date DATE NOT NULL,
	home_team VARCHAR NOT NULL,
	away_team VARCHAR NOT NULL,
	home_score INTEGER,
	away_score INTEGER,
	statsbomb_competition_id INTEGER,
	statsbomb_season_id INTEGER, minutes_scale_factor FLOAT,
	PRIMARY KEY (id),
	-- >> contrainte d'UNICITÉ : un même match ne peut pas être inséré deux fois
	CONSTRAINT uq_match_source_id UNIQUE (source, match_id)
);

-- >> TABLE player_match_stats : ce qu'a fait UN joueur dans UN match. 41 816 lignes.
-- >> C'est la table « brute » d'où sortent toutes les statistiques de saison.
CREATE TABLE player_match_stats (
	id INTEGER NOT NULL,
	source VARCHAR NOT NULL,
	match_id INTEGER NOT NULL,
	player_id VARCHAR NOT NULL,
	player_name VARCHAR NOT NULL,         -- >> REDONDANT avec players.canonical_name
	team VARCHAR NOT NULL,                -- >> (voir « normalisation » dans 05_SQL...md)
	opponent VARCHAR NOT NULL,
	season VARCHAR NOT NULL,
	league VARCHAR NOT NULL,
	position_played VARCHAR,
	minutes FLOAT NOT NULL,
	passes_attempted INTEGER NOT NULL,
	passes_completed INTEGER NOT NULL,
	-- ... (une quarantaine d'autres colonnes : tacles, pressings, tirs, xG, etc.)
	PRIMARY KEY (id),
	CONSTRAINT uq_player_match_source UNIQUE (source, match_id, player_id),
	-- >> CLÉ ÉTRANGÈRE : player_id doit exister dans la table players
	FOREIGN KEY(player_id) REFERENCES players (player_id)
);

-- >> TABLE player_season_stats_2015_16 : UNE ligne par joueur qualifié (≥ 900 min),
-- >> 1 337 lignes, 111 colonnes (totaux + moyennes par 90 min).
-- >> Elle est CALCULÉE (src/transform/normalize.py) à partir de player_match_stats :
-- >> c'est une table « résultat », reconstruite à chaque fois.
-- >> Les joueurs sous 900 minutes vont dans la table insufficient_sample (836 lignes).

-- >> TABLE player_archetypes : l'archétype de chaque joueur qualifié. 1 337 lignes.
-- >> Remplie par src/analytics/archetypes.py --rebuild.
CREATE TABLE player_archetypes (
	id INTEGER NOT NULL,
	player_id VARCHAR NOT NULL,
	player_name VARCHAR NOT NULL,
	season VARCHAR NOT NULL,
	league VARCHAR NOT NULL,
	team VARCHAR NOT NULL,
	source VARCHAR NOT NULL,
	position_group_fine VARCHAR,
	pool VARCHAR NOT NULL,                -- >> 'outfield' (champ) ou 'gk' (gardien)
	archetype_id INTEGER NOT NULL,        -- >> 0 à 9 (champ), 100 à 102 (gardiens)
	archetype_label VARCHAR NOT NULL,     -- >> ex. 'Destroyer'
	cluster_confidence FLOAT NOT NULL,    -- >> (d2 - d1) / d2, entre 0 et 1
	distance_to_centroid FLOAT NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_player_archetype UNIQUE (source, player_id, season)
	-- >> REMARQUE : pas de FOREIGN KEY vers players ici (contrairement à
	-- >> player_match_stats). Le lien existe dans les données (vérifié : 0 orphelin),
	-- >> mais la base ne l'impose pas. Bon sujet de discussion.
);

-- >> AUTRES TABLES (non détaillées) :
-- >>   player_percentiles (50 442) : rang en % de chaque joueur sur chaque statistique
-- >>   team_style (80)             : profil de jeu des 80 équipes (possession, pressing...)
-- >>   player_market_values (1 337): valeur Transfermarkt (rapprochement de noms flou)
-- >>   player_heatmaps, player_press_location, player_style_fit, player_bio, ingest_log
-- >>   teams (20) et match_results (380) : saison 2024/25, restes des premiers
-- >>     essais (Understat / football-data) — non utilisés par l'analyse 2015/16.
-- >>   team_finance : vide (0 ligne).

-- >> SCHÉMA RELATIONNEL (notation du cours) :
-- >>   players (#player_id, canonical_name, birth_year, nationality, ...)
-- >>   matches (#id, match_id, season, league, match_date, home_team, away_team, ...)
-- >>   player_match_stats (#id, match_id, player_id→players, minutes, ...)
-- >>   player_archetypes (#id, player_id, season, archetype_id, archetype_label, ...)
-- >> (# = clé primaire, → = clé étrangère)
