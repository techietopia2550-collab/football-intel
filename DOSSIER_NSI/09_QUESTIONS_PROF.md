# 09 — Questions probables du professeur

## Sommaire
- A. Questions générales sur le projet (1 à 6)
- B. Questions sur la problématique (7 à 12)
- C. Questions techniques NSI (13 à 18)
- D. Questions sur ta démarche (19 à 20)

Pour chaque question, tu trouveras des **éléments de réponse** (pas un texte à réciter) et **quoi montrer**. Ton professeur ne connaît pas le code : commence toujours par l'idée simple, puis montre le fichier.

---

## A. Questions générales sur le projet

**1. « Explique-moi ton projet en une minute. »**
- Une application web qui aide à recruter des joueurs : statistiques, style de jeu, joueurs similaires, chatbot.
- Données réelles : StatsBomb, saison 2015/16, 4 championnats, 1 337 joueurs analysables.
- Le point délicat : le score de ressemblance peut faire croire à une certitude, d'où ma problématique.
- *Montrer :* l'application (`streamlit run src/app/main.py`), page d'un joueur.

**2. « Pourquoi 2015/16 et pas cette saison ? »**
- Ce sont les seules données détaillées (chaque action du match) **gratuites et légales** pour 4 championnats complets.
- Conséquence : les résultats ne parlent pas des joueurs d'aujourd'hui. Le chatbot refuse d'ailleurs les questions sur d'autres saisons.

**3. « Comment est organisé ton code ? »**
- En couches : collecte → nettoyage → analyse → affichage. Chaque couche est un dossier de `src/`.
- Règle : l'affichage ne calcule rien, il appelle l'analyse.
- *Montrer :* l'arborescence de `src/` et le schéma de `01_RESUME_SIMPLE.md`, section 3.

**4. « Où sont stockées les données ? Pourquoi une base de données ? »**
- SQLite, un seul fichier (`data/football.db`), 18 tables.
- Une base permet des requêtes rapides (index), des liens entre tables (clés) et garantit qu'il n'y a pas de doublons (contraintes).
- *Montrer :* `05_SQL_ET_BASE_DE_DONNEES.md`, requête JOIN.

**5. « Comment tu sais que ton programme marche ? »**
- 663 tests automatiques (`pytest`), dont des tests qui vérifient exactement les phrases du seuil.
- Une mesure de qualité du score (100 paires) et un test du chatbot (60 questions).
- *Montrer :* `tests/test_guardrails.py` (`test_similarity_refuse_below_45`), lancer `pytest`.

**6. « Qu'est-ce qui ne marche pas encore ? »**
- La précision du score reste autour de 50 %, quel que soit le seuil.
- Le chatbot refuse à tort 13 questions sur 60.
- Pas d'âge des joueurs, une seule saison.
- Savoir dire ce qui ne marche pas est un **point fort** à l'oral, pas une faiblesse.

## B. Questions sur la problématique

**7. « Comment fonctionne ton algorithme de similarité ? »**
- Idée NSI : comme les k plus proches voisins de Première. Chaque joueur est un vecteur de statistiques, et on cherche les plus proches.
- Étapes : mêmes échelles (centrer-réduire) → résumé en quelques axes (ACP) → angle entre vecteurs (cosinus) → tri.
- *Montrer :* `07_ALGORITHMES_CLES.md` sections 1-2, `code_commenté/fonctions_nsi.py` (`plus_proches`).

**8. « Pourquoi le cosinus et pas la distance euclidienne ? »**
- Le cosinus compare le **profil** (sur quoi le joueur est fort ou faible), la distance compare les **valeurs**.
- Contrepartie : un joueur « moyen partout » a un vecteur presque nul, et son cosinus est instable.
- Question honnête à retourner : « le projet n'a pas comparé les deux par une mesure ; ce serait une piste ».

**9. « Comment tu as choisi ton seuil de 0,60 ? »**
- D'abord au jugé (85 / 70 / 55), puis **mesuré** : 100 paires étiquetées, avec une règle écrite **avant** de voir les résultats.
- 0,60 est le premier seuil où la précision atteint 50 % (51 %, fourchette 40-63 %).
- Limites : étiquettes faites par une IA, et seulement 100 paires.
- *Montrer :* `docs/similarity_thresholds.md`, et le tableau de `02_PROBLEMATIQUE.md`, section 4.

**10. « Que se passe-t-il si le seuil n'est pas atteint ? »**
- Dans l'interface : le badge passe de *Fair* à *Weak*. Rien n'est caché.
- Dans le chatbot : entre 0,45 et 0,60, un avertissement **en premier** ; sous 0,45, refus.
- *Montrer :* `code_commenté/guardrails_commente.py`, tableau final.

**11. « Ton score de 94 %, ça veut dire 94 % de chances ? »**
- Non. C'est un cosinus, pas une probabilité. C'est justement une façon de tromper l'utilisateur.
- Les mesures montrent qu'à 0,90 et plus, seulement 4 paires sur 10 étaient similaires.

**12. « Pourquoi ne pas simplement cacher les mauvais résultats ? »**
- Pistes à discuter (sans trancher, voir `02_PROBLEMATIQUE.md`, section 7) : cacher fait perdre de l'information, avertir risque que l'utilisateur ne lise pas l'avertissement.
- Le projet a choisi « répondre et avertir en premier », mais c'est **une** solution parmi d'autres.

## C. Questions techniques NSI

**13. « Quelle est la complexité de ton algorithme ? »**
- Similarité : O(n × d) pour les scores + O(n log n) pour le tri, avec n ≈ 175 et d ≤ 12 : instantané.
- *Montrer :* `07_ALGORITHMES_CLES.md`, section 6.

**14. « Où est-ce qu'il y a de la récursivité ? »**
- `_flatten` (`src/chat/guardrails.py:440`) : parcourt un résultat imbriqué (dictionnaires dans des listes…) pour récupérer tous les nombres.
- Version NSI : `chemin_vers` (arbre d'archétypes) et `tri_fusion`.
- *Montrer :* `code_commenté/conversation_commente.py` (bonus).

**15. « Est-ce que ta base de données est normalisée ? »**
- Les tables brutes oui (clés primaires, une clé étrangère, contraintes d'unicité).
- Les tables calculées non : les noms sont recopiés (redondance), et une clé étrangère manque dans `player_archetypes`. C'est un compromis pour la vitesse, appelé dénormalisation.
- *Montrer :* `05_SQL_ET_BASE_DE_DONNEES.md`, section 3.

**16. « Montre-moi une classe. Qu'est-ce qu'un attribut, une méthode ? »**
- `TokenWindow` : attributs `budget` et `_spent` (une **file**), méthodes `record()` et `used()`.
- *Montrer :* `06_POO_PYTHON.md`, section 2.1.

**17. « Comment tu évites les hallucinations du chatbot ? »**
- On ne fait pas confiance à l'IA : règles avant, noms vérifiés, seuil appliqué par le code, et **vérification de chaque nombre** après. Si un nombre est introuvable, la réponse est retenue.
- Limite : les phrases sans chiffre ne sont pas vérifiées.
- *Montrer :* `08_CHATBOT_ET_RAG.md`, section 5.

**18. « Pourquoi `>= cut - 1e-12` et pas `>= cut` ? »**
- Les nombres flottants sont approximatifs en binaire (0,1 + 0,2 ≠ 0,3, vu en Première). Un score qui « vaut » 0,6 peut être stocké comme 0,59999999… La petite marge évite de mal classer ce cas.
- *Montrer :* `src/app/design.py:238`.

## D. Questions sur ta démarche

**19. « Est-ce que tu as écrit tout ce code toi-même ? As-tu utilisé une IA ? »**
- **Réponds la vérité.** Dans l'historique Git du dépôt, tous les commits portent la mention « Co-Authored-By: Claude » (un assistant de programmation). Le professeur peut le voir avec `git log`.
- Ce qui compte alors : ce que **tu** as décidé (le sujet, les règles, le choix de mesurer le seuil, les compromis) et ta capacité à **expliquer** et **modifier** le code.
- Bonne attitude : « J'ai utilisé un assistant IA pour écrire une partie du code. Voici ce que j'ai décidé, voici ce que je sais expliquer, et voici ce que je suis en train d'apprendre. »
- Le programme de NSI prévoit d'ailleurs « utiliser des API ou des bibliothèques » : utiliser des outils n'est pas interdit. **Le cacher**, c'est le vrai risque.

**20. « Qu'est-ce que tu ferais si tu avais plus de temps ? »**
- Refaire l'étiquetage des paires **à la main**, ou avec un vrai connaisseur du football.
- Tester l'ajout du poste dans le calcul du score (piste du projet lui-même).
- Comparer le cosinus et la distance euclidienne par une mesure.
- Ajouter d'autres saisons.
- *Ne pas promettre trop :* choisis-en une ou deux.

---

**Conseil général :** si tu ne sais pas, dis « je ne sais pas, mais voici où je chercherais » et ouvre le fichier. C'est exactement l'esprit de ta problématique : ne pas faire croire à une certitude qu'on n'a pas.
