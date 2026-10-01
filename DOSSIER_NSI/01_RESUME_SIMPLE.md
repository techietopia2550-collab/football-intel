# 01 — Football Intel en résumé

## Sommaire
1. Qu'est-ce que Football Intel ?
2. Quel problème ça résout ?
3. Comment ça marche (vue d'ensemble)
4. Les technologies
5. Ce qui est fait, ce qui reste
6. Petit lexique (les mots qu'on retrouve partout)

---

## 1. Qu'est-ce que Football Intel ?

Football Intel est une **application web d'analyse de joueurs de football**, pensée pour le recrutement (le « scouting »). On tape le nom d'un joueur et l'application :

- affiche ses statistiques de la saison ;
- décrit son **style de jeu** (son « archétype », par exemple *Destroyer* pour un récupérateur comme Kanté) ;
- propose des **joueurs qui jouent de la même façon** ;
- compare deux joueurs, ou décrit le style d'une équipe ;
- répond à des questions en langage courant grâce à un **chatbot** (« Qui joue comme Busquets ? »).

Les données couvrent **une seule saison, 2015/16**, dans **4 championnats** : Premier League (Angleterre), Liga (Espagne), Ligue 1 (France) et Serie A (Italie). Elles viennent de **StatsBomb**, une entreprise qui publie gratuitement une partie de ses données. Chaque action d'un match y est enregistrée (passe, tacle, tir…), ce qui représente des millions d'événements.

| Chiffre | Valeur |
|---|---|
| Équipes | 80 |
| Matchs enregistrés | 1 507 |
| Lignes « un joueur dans un match » | 41 816 |
| Joueurs ayant joué au moins 900 minutes (analysables) | 1 337 |
| Joueurs sous 900 minutes (stats brutes seulement) | 836 |

## 2. Quel problème ça résout ?

Un recruteur cherche souvent **un remplaçant** : « Notre milieu récupérateur part, qui joue comme lui ? » Regarder des centaines de matchs prend un temps énorme. Football Intel propose une **première liste de pistes** à partir des chiffres.

> **Le piège, et donc la problématique :** l'application donne toujours une liste, même quand aucun joueur n'est vraiment proche. Si elle affiche « 94 % de ressemblance », l'utilisateur croit que c'est sûr. **Comment éviter de le tromper ?** C'est le sujet de `02_PROBLEMATIQUE.md`.

## 3. Comment ça marche (vue d'ensemble)

Le projet est découpé en **couches**. Chaque couche est un dossier de `src/` et ne fait qu'une seule chose.

```
 StatsBomb (fichiers JSON sur Internet)
        │  1. COLLECTE        src/ingest/      télécharge et garde une copie locale
        ▼
 data/raw/  (fichiers bruts, jamais modifiés)
        │  2. NETTOYAGE       src/transform/   additionne les matchs -> stats de saison
        ▼
 data/football.db  (base de données SQLite, 18 tables)
        │  3. ANALYSE         src/analytics/   percentiles, archétypes, similarité…
        ▼
 4. AFFICHAGE   src/app/   (Streamlit, 9 pages)      5. CHATBOT   src/chat/
```

**Exemple concret, Kanté :**
1. On récupère ses 37 matchs avec chaque action.
2. On additionne tout et on ramène les chiffres « par 90 minutes », pour comparer avec un joueur qui a moins joué. Kanté fait 27,0 pressings (actions de pression sur un adversaire) et 4,88 tacles par 90 minutes.
3. Un algorithme range Kanté dans l'archétype *Destroyer*. Un autre trouve le joueur au style le plus proche : **Idrissa Gueye** (Aston Villa), avec un score de **0,85** (le score va de -1 à 1).
4. La page affiche « 85 % · Fair ». *Fair* (« correct ») est l'étiquette de confiance, expliquée dans `02_PROBLEMATIQUE.md`.

## 4. Les technologies

| Outil | À quoi il sert | Lien NSI |
|---|---|---|
| **Python 3** | Tout le projet | Langage du programme |
| **SQLite** | Base de données dans un seul fichier, `data/football.db` | Bases de données, SQL |
| **SQLAlchemy** | Bibliothèque qui décrit les tables comme des classes Python | POO, modularité |
| **pandas / numpy** | Calcul sur de grands tableaux de nombres | Données en tables |
| **scikit-learn** | Algorithmes d'apprentissage automatique tout faits (ACP, K-Means) | Utiliser une bibliothèque (API) |
| **Streamlit** | Fabrique une page web à partir de code Python | Interactions Web |
| **pydantic** | Vérifie que les données ont la bonne forme (types) | Typage, mise au point |
| **pytest** | Lance les 663 tests automatiques | Tests, mise au point |
| **Groq** | Service en ligne qui fait tourner le modèle de langage du chatbot | Client/serveur, HTTP |

## 5. Ce qui est fait, ce qui reste

**Fait (d'après le dépôt Git, du 19 septembre au 1er octobre 2026) :**
- collecte des données StatsBomb pour les 4 championnats ;
- 49 statistiques par joueur, percentiles (rang en % parmi les joueurs du même poste) ;
- 13 archétypes (10 pour les joueurs de champ, 3 pour les gardiens) ;
- recherche de joueurs et d'équipes similaires, avec explication du score ;
- **mesure de ce que vaut un score** (100 paires testées) et étiquette *Fair* / *Weak* ;
- chatbot avec garde-fous, testé sur 60 questions (62 % de bonnes réponses).

**Ce qui reste / limites connues :**
- une seule saison (2015/16) : rien sur les joueurs actuels ;
- pas d'âge des joueurs (StatsBomb ne le donne pas) ;
- la mesure de qualité du score repose sur des étiquettes **générées par une IA**, pas par un expert humain. Le projet le dit lui-même (`ANALYTICS_DECISIONS.md`, section 23) ;
- la précision mesurée reste autour de 50 % quel que soit le seuil. C'est le résultat le plus important pour la problématique ;
- le dossier `src/reports` (export PDF/CSV) est encore vide.

## 6. Petit lexique

Ces mots reviennent dans tout le dossier. Ils sont expliqués ici une fois pour toutes.

| Mot | Explication simple |
|---|---|
| **Par 90 (p90)** | Statistique divisée par les minutes jouées, puis multipliée par 90. Permet de comparer un titulaire et un remplaçant. |
| **xG** (*expected goals*, buts attendus) | Probabilité qu'un tir devienne un but, calculée d'après sa position. 0,65 xG par 90 minutes = Vardy se crée des occasions qui « valent » 0,65 but par match. |
| **Vecteur** | Liste de nombres qui décrit un joueur : `[xG, pressings, tacles, …]`. |
| **Centrer-réduire (z-score)** | Transformer chaque statistique en « nombre d'écarts-types au-dessus ou en dessous de la moyenne », pour mettre toutes les statistiques sur la même échelle. |
| **Similarité cosinus** | Mesure si deux vecteurs « pointent dans la même direction ». 1 = même profil, 0 = sans rapport, -1 = profils opposés. Détails : `07_ALGORITHMES_CLES.md`. |
| **ACP** (analyse en composantes principales) | Méthode qui résume beaucoup de colonnes en quelques « axes » qui gardent l'essentiel de l'information (ici 85 %). |
| **K-Means** | Algorithme qui range des points en k groupes de points proches. |
| **Archétype** | Un des 13 groupes trouvés par K-Means, auquel l'auteur a donné un nom (*Poacher*, *Destroyer*…). |
| **Seuil** | Valeur limite au-dessus ou en dessous de laquelle on change de décision (ici 0,60 et 0,45). |
| **Précision** (au sens des tests) | Parmi les paires que l'algorithme déclare similaires, part qui l'est vraiment. |
| **LLM** (grand modèle de langage) | Programme d'IA qui génère du texte, comme ChatGPT. |
| **Hallucination** | Quand un LLM invente un fait ou un chiffre faux avec assurance. |
| **RAG** | Technique où l'IA va d'abord chercher les informations dans une source fiable (ici la base de données) avant de répondre. Détails : `08_CHATBOT_ET_RAG.md`. |
| **Garde-fou** (*guardrail*) | Règle écrite en Python qui contrôle ou bloque la réponse du chatbot. |
