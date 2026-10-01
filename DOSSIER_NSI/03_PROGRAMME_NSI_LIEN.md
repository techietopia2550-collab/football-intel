# 03 — Le projet et le programme NSI

## Sommaire
1. Comment lire ce tableau
2. Terminale : rubrique par rubrique
3. Première : notions mobilisées
4. Ce que le projet n'illustre pas
5. Ce qui dépasse le programme

Texte officiel et sources : `ressources/programme_nsi_2025.md`.

---

## 1. Comment lire ce tableau

- **Élément du programme** : intitulé du BO.
- **Où dans le projet** : ce qui l'illustre, expliqué simplement.
- **Fichier / ligne** : à ouvrir pour montrer. Les numéros de ligne correspondent au commit `7e94ddb` du 1er octobre 2026.
- ✅ = illustré directement par le vrai code ; 📝 = illustré par une fonction écrite **pour le dossier** (`code_commenté/fonctions_nsi.py`), pas par le projet.

## 2. Terminale : rubrique par rubrique

### Structures de données

| Élément du programme NSI | Où c'est dans le projet | Fichier / ligne |
|---|---|---|
| ✅ Interface et implémentation | `ChatClient` décrit seulement l'**interface** (« il faut une méthode `chat()` »). `GroqClient` en est une implémentation, et les tests en utilisent une fausse, sans Internet. | `src/chat/conversation.py:70`, `src/chat/llm_client.py:139` |
| ✅ Vocabulaire objet : classe, attribut, méthode | `TokenWindow` : attributs `budget`, `window`, méthodes `used()`, `wait_for_room()`, `record()`. | `src/chat/llm_client.py:91-136` |
| ✅ Classe comme modèle de table | Chaque table SQL est une classe : `Player`, `PlayerArchetype`… | `src/models/schema.py:39`, `:254` |
| ✅ Liste | Résultat de similarité = liste de joueurs triée. | `src/models/similarity.py` (`similar: list[SimilarPlayer]`) |
| ✅ File (FIFO, premier entré premier sorti) | `deque` : on retire les plus anciens appels au chatbot quand ils sortent de la fenêtre d'une minute (`popleft`). | `src/chat/llm_client.py:106-108` (`_trim`) |
| ✅ Dictionnaire, clé | `REFUSALS` (code → phrase de refus), `OUTFIELD_LABELS` (numéro → nom d'archétype). | `src/chat/guardrails.py:32`, `src/analytics/archetypes.py:85` |
| ✅ Recherche dans une liste vs dans un dictionnaire | Les noms de joueurs sont recherchés dans une liste parcourue en entier (O(n)). Les archétypes sont retrouvés par clé dans un dictionnaire (O(1) en moyenne). | `src/analytics/directory.py:142` vs `archetypes.py:85` |
| ✅ Ensemble, tuple | `frozenset` des statistiques exclues ; tuple des 14 statistiques des archétypes. | `similarity.py:84`, `archetypes.py:74` |
| 📝 Arbre | Arbre d'archétypes (regroupement inventé pour l'exercice). | `fonctions_nsi.py` (`chemin_vers`) |
| Graphes | Non illustré (voir section 4). | – |

### Bases de données

| Élément du programme NSI | Où c'est dans le projet | Fichier / ligne |
|---|---|---|
| ✅ Modèle relationnel : relation, attribut, domaine | 18 tables. Chaque colonne a un type (`VARCHAR`, `INTEGER`, `FLOAT`, `DATE`). | `src/models/schema.py` |
| ✅ Clé primaire | `players.player_id`, `matches.id`… | `schema.py:44` |
| ✅ Clé étrangère | `player_match_stats.player_id → players.player_id` | `schema.py:178` |
| ✅ Contrainte d'unicité | Un même joueur ne peut pas avoir deux lignes pour le même match. | `schema.py:171` |
| ✅ Distinguer structure et contenu | `init_db()` crée la structure (vide) ; l'ingestion remplit le contenu. | `schema.py:428` |
| ✅ Repérer des anomalies du schéma | Redondance des noms de joueurs, absence de clé étrangère dans `player_archetypes`, tables d'une autre saison laissées dans la base. | `05_SQL_ET_BASE_DE_DONNEES.md`, section 3 |
| ✅ SGBD : persistance, efficacité | SQLite garde les données sur disque, avec des index pour aller plus vite (`index=True`). | `schema.py:46` |
| ✅ SQL : SELECT, WHERE, JOIN | Requêtes de l'algorithme de similarité. | `similarity.py:112`, `:245-252` |
| ✅ SQL : INSERT, UPDATE | Écriture des tables calculées ; ajout de colonnes par `ALTER TABLE`. | `src/transform/normalize.py`, `schema.py:422` |

### Langages et programmation

| Élément du programme NSI | Où c'est dans le projet | Fichier / ligne |
|---|---|---|
| ✅ Récursivité | `_flatten` parcourt un résultat imbriqué ; `clean` convertit un résultat en JSON ; `walk` construit le panneau « Sources ». | `src/chat/guardrails.py:440`, `src/analytics/chatbot.py:60`, `src/chat/conversation.py:87` |
| ✅ Modularité, API, bibliothèques | Découpage en 7 dossiers ; utilisation documentée de scikit-learn, pandas, SQLAlchemy ; API web de Groq. | `src/`, `CLAUDE.md` |
| ✅ Créer des modules simples et les documenter | Chaque fichier commence par une description (*docstring*) ; chaque fonction est typée. | ex. `similarity.py:1-55` |
| ✅ Paradigme impératif | Boucles et conditions de `fit_group`. | `similarity.py:147` |
| ✅ Paradigme fonctionnel | Fonctions « pures » (même entrée → même sortie, sans rien modifier d'autre), fonctions anonymes `lambda`, listes en compréhension. | `similarity.py:377`, `guardrails.py:649` |
| ✅ Paradigme objet | Classes de résultats, d'erreurs, de client. | `src/models/`, `llm_client.py` |
| ✅ Gestion des bugs : flottants | Comparaison `similarity >= cut - 1e-12` (marge d'erreur). | `src/app/design.py:238` |
| ✅ Gestion des bugs : typage | pydantic refuse une donnée de mauvais type ; `mypy` vérifie les types. | `src/models/similarity.py` |
| ✅ Gestion des bugs : condition non exhaustive | `apply_similarity_rule` traite les 4 cas, y compris « aucun candidat ». | `guardrails.py:629` |
| ✅ Tests | 663 tests `pytest`. | `tests/` |
| Calculabilité, problème de l'arrêt | Non illustré directement. Lien possible : on ne peut pas prouver automatiquement qu'un LLM ne mentira jamais, d'où les vérifications par le code. | – |

### Algorithmique

| Élément du programme NSI | Où c'est dans le projet | Fichier / ligne |
|---|---|---|
| ✅ / 📝 Diviser pour régner, tri | Le projet trie avec `sorted()` (Timsort, un dérivé du tri fusion). Version NSI : `tri_fusion`. | `similarity.py:377` ; `fonctions_nsi.py` |
| 📝 Arbres : taille, parcours | `nombre_de_feuilles`, `chemin_vers` (parcours en profondeur). | `fonctions_nsi.py` |
| Graphes, programmation dynamique, Boyer-Moore | Non illustrés. | – |

### Architectures, systèmes, réseaux

| Élément du programme NSI | Où c'est dans le projet | Fichier / ligne |
|---|---|---|
| ✅ Sécurisation des communications (partiel) | Le chatbot appelle Groq en **HTTPS** ; la clé secrète est dans `.env`, jamais dans le code. | `src/chat/llm_client.py`, `.env.example` |
| ✅ Processus et ressources (partiel) | Limitation du débit : attendre avant d'envoyer une requête pour ne pas dépasser le quota. Ce n'est pas de l'ordonnancement au sens strict. | `llm_client.py:114` |
| ✅ Stockage (lien avec la gestion des ressources) | `disk_bootstrap.py` force tous les fichiers temporaires sur le disque D:. | `src/utils/disk_bootstrap.py` |

### Histoire de l'informatique

| Élément | Lien possible |
|---|---|
| Évolution logiciel / matériel | Les algorithmes utilisés sont anciens : ACP (Pearson, 1901), K-Means (Lloyd, 1957, publié en 1982). Ils n'ont été appliqués au football qu'avec la puissance de calcul et les données récentes. Les LLM datent de la fin des années 2010. |

## 3. Première : notions mobilisées

| Notion de Première | Où dans le projet |
|---|---|
| **k plus proches voisins** | La recherche de similarité, c'est trouver les k joueurs les plus proches (k = 10 par défaut). |
| **Nombres flottants** | Le `- 1e-12` de `design.py:238`, et l'arrondi des scores à 4 décimales (`similarity.py`). |
| Traitement de données en tables | Agrégation de 41 816 lignes en 1 337 ; pandas = tables en Python. |
| Recherche dichotomique, tris | `fonctions_nsi.py` ; tri des scores. |
| Web : client/serveur, HTTP | Streamlit (serveur local), Groq (serveur distant). |
| Spécification, tests | Docstrings et tests. |

## 4. Ce que le projet n'illustre pas

Les **graphes**, la **programmation dynamique**, **Boyer-Moore**, les **protocoles de routage** et le **problème de l'arrêt**. C'est normal : un projet n'a pas à couvrir tout le programme. Il vaut mieux le dire franchement que forcer un lien.

## 5. Ce qui dépasse le programme

| Notion | Niveau habituel | Comment en parler en NSI |
|---|---|---|
| Centrer-réduire (z-score) | Statistiques, Terminale maths ou supérieur | « Mettre toutes les colonnes sur la même échelle » |
| Similarité cosinus | Post-bac | « Produit scalaire divisé par le produit des longueurs », vu en maths (géométrie) |
| ACP | Post-bac | « Résumer 12 colonnes en 6, en gardant 85 % de l'information » |
| K-Means | Post-bac | « Former k groupes de points proches, comme les k plus proches voisins, mais sans étiquettes » |
| Intervalle de Wilson | Post-bac | « Fourchette dans laquelle se trouve probablement la vraie précision » |
| LLM / RAG | Post-bac | « Une IA qui rédige, et du code Python qui contrôle » |

Voir `07_ALGORITHMES_CLES.md` pour l'explication de chacun.
