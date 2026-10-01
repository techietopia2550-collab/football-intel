# 02 — La problématique

> **« Comment implémenter un seuil de confiance dans un algorithme de recherche par similarité pour éviter de tromper l'utilisateur ? »**

Ce document **ne répond pas** à la problématique. Il prépare la discussion avec ton professeur : il découpe la question, définit les mots, montre où elle se pose dans le code et liste les pistes possibles.

## Sommaire
1. Les mots de la problématique
2. Découpage en sous-questions
3. Où ça se passe dans le code
4. Ce que le projet a déjà mesuré (les faits)
5. Les éléments du projet qui aident à répondre
6. Questions ouvertes à discuter avec le professeur
7. Pistes possibles (sans trancher)

---

## 1. Les mots de la problématique

| Mot | Sens dans le projet | Attention |
|---|---|---|
| **Recherche par similarité** | Trouver les joueurs dont le vecteur de statistiques est le plus proche de celui d'un joueur donné. Proche de l'algorithme des k plus proches voisins vu en Première. | « Similaire » veut dire **statistiquement proche en 2015/16**. Ça ne veut pas dire « aussi bon » ni « même poste ». |
| **Similarité** (le score) | Un nombre entre -1 et 1 : la similarité cosinus (`01_RESUME_SIMPLE.md`, lexique). Affiché en % (0,85 → « 85 % »). | 85 % **n'est pas** une probabilité ! Ce n'est pas « 85 % de chances d'être similaire ». |
| **Confiance** | Le degré auquel on peut croire un résultat. | Le projet a **deux** mesures différentes : la confiance d'archétype (le joueur est-il bien au centre de son groupe ?) et l'étiquette Fair/Weak (que vaut ce score ?). |
| **Seuil** | Une valeur limite qui change la décision. Ici : 0,60 (Fair / Weak) et 0,45 (refus du chatbot). | Un seuil peut être **choisi au jugé** ou **mesuré**. Le projet a fait les deux, l'un après l'autre. |
| **Implémenter** | Écrire le code qui applique le seuil : où, quand, comment. | Il y a plusieurs endroits possibles (voir section 3). |
| **Tromper l'utilisateur** | Lui faire croire quelque chose de faux, même sans mentir. | Plusieurs formes : chiffre qui a l'air précis, liste toujours pleine, mot rassurant (« Strong »), chatbot qui invente. |

## 2. Découpage en sous-questions

1. **Que mesure vraiment le score ?** Est-ce qu'un score élevé veut dire « vraiment similaire » ?
2. **Comment savoir si un score est fiable ?** Il faut une « vérité » à comparer : qui décide qu'une paire est vraiment similaire ?
3. **Comment choisir la valeur du seuil ?** Au jugé, ou par une règle fixée à l'avance et une mesure ?
4. **Que faire sous le seuil ?** Cacher, avertir, refuser, ou montrer quand même avec une étiquette ?
5. **Où placer le seuil dans le programme ?** Dans l'algorithme, dans l'affichage ou dans le chatbot ?
6. **Comment le dire à l'utilisateur ?** Avec un mot, un pourcentage, une couleur, une phrase d'avertissement ?
7. **Comment vérifier que le seuil reste juste** si les données ou l'algorithme changent ?

## 3. Où ça se passe dans le code

```
find_similar()  ──────────►  renvoie TOUJOURS les n meilleurs, AUCUN seuil
src/analytics/similarity.py:291      (mais ajoute des avertissements, ligne 350)
        │
        ├──► Interface (Streamlit)
        │    src/app/design.py:225   MATCH_TIERS = Fair si ≥ 0.60, sinon Weak
        │    src/app/pages/2_player.py:458   badge affiché à côté de chaque joueur
        │
        └──► Chatbot
             src/chat/conversation.py:331   appelle la règle AVANT le LLM
             src/chat/guardrails.py:613     FAIR_CUT = 0.60, REFUSE_CUT = 0.45
             src/chat/guardrails.py:629     apply_similarity_rule()
```

**Le comportement du chatbot (code : `code_commenté/guardrails_commente.py`) :**

| Meilleur score | Verdict | Ce que voit l'utilisateur |
|---|---|---|
| aucun candidat | `NO_CANDIDATES` | Phrase de refus |
| < 0,45 | `REFUSE` | Refus, la liste est cachée |
| entre 0,45 et 0,60 | `WARN` | **Avertissement en premier**, puis la liste avec une étiquette par ligne |
| ≥ 0,60 | `FAIR` | La liste, plus une mise en garde fixe à la fin |

**Autres « confiances » dans le code :**
- `cluster_confidence` (`src/analytics/archetypes.py:254`) : (d2 − d1) / d2. En dessous de 0,25, `find_similar` ajoute un avertissement (`similarity.py:350`).
- Le seuil de **900 minutes** : en dessous, aucun score n'est calculé (pas assez de données).
- Les garde-fous de taille : moins de 3 joueurs → erreur (`fit_group`, `similarity.py:147`), statistique vide pour plus de 30 % des joueurs → abandonnée.

## 4. Ce que le projet a déjà mesuré (les faits)

Source : `docs/similarity_thresholds.md` et `ANALYTICS_DECISIONS.md`, sections 22-23.

**La méthode :**
- 100 paires de joueurs du même archétype, 10 par tranche de score (moins de 0,45, puis 0,45-0,55, …, 0,90 et plus).
- Chaque paire est étiquetée « similaire » ou « pas similaire » **sans voir le score**.
- **La règle de choix du seuil a été écrite AVANT de regarder les étiquettes**, pour ne pas choisir le seuil qui arrange. Un niveau est retenu au premier seuil où la précision atteint 90 % (Strong), 75 % (Good) ou 50 % (Fair), sur au moins 10 paires.

**Les résultats :**

| Seuil | Paires au-dessus | Précision | Intervalle de confiance à 95 % |
|---|---|---|---|
| 0,50 | 89 | 49 % | 39-60 % |
| **0,60** | **70** | **51 %** | **40-63 %** |
| 0,70 | 50 | 52 % | 39-65 % |
| 0,80 | 30 | 43 % | 27-61 % |
| 0,90 | 10 | 40 % | 17-69 % |

> **Le résultat clé : la précision est « plate », autour de 50 %, quel que soit le seuil.** Un score de 0,95 n'est pas plus fiable qu'un score de 0,65 sur ces données. Conséquence : les niveaux *Strong* (85 %) et *Good* (70 %), choisis au jugé au début, ont été **supprimés**. Il ne reste que *Fair* (≥ 0,60) et *Weak*.

**Pourquoi c'est plat (piste du projet) :** 40 paires sur 100 mélangent deux postes, par exemple un défenseur central et un latéral. Seulement 25 % d'entre elles sont étiquetées similaires, contre 65 % pour les paires de même poste, alors qu'elles ont presque le même score moyen (0,630 contre 0,651). **Le score ne voit pas le poste.**

**Mes vérifications sur la base (1er octobre 2026), en lançant `find_similar` sur tous les joueurs :**
- environ **10 joueurs sur ~1 320** ont un meilleur score sous 0,60, surtout des gardiens (Łukasz Fabiański : 0,55) ;
- **aucun** n'a un meilleur score sous 0,45. Sans filtre, le refus du chatbot ne se déclenche donc jamais ;
- 536 joueurs sur 1 337 (40 %) ont une confiance d'archétype sous 0,25. Vardy est à 0,07 : il est presque à la frontière entre deux archétypes.

**Les limites de la mesure (dites par le projet lui-même) :**
- les étiquettes « similaire / pas similaire » ont été **générées par une IA** à partir de règles (poste + archétype), pas par un expert ;
- 100 paires seulement, d'où des intervalles larges (environ ±15 points) ;
- les tranches sont équilibrées exprès : la précision « cumulée » ne représente donc pas la vraie population.

## 5. Les éléments du projet qui aident à répondre

| Élément | Fichier | Utile pour la sous-question |
|---|---|---|
| Algorithme de similarité | `src/analytics/similarity.py` | 1, 5 |
| Fabrication des paires de test | `src/analytics/build_test_pairs.py` | 2 |
| Calcul précision / rappel / intervalle de Wilson | `src/analytics/evaluate_thresholds.py` | 2, 3 |
| Rapport et courbe | `docs/similarity_thresholds.md`, `.png` | 3 |
| Étiquette affichée | `src/app/design.py:221-246` | 4, 6 |
| Règle du chatbot | `src/chat/guardrails.py:613-664` | 4, 5, 6 |
| Test qui casse si le seuil n'est plus celui mesuré | `tests/test_similarity_thresholds.py:136` | 7 |
| Explication du score (contribution de chaque statistique) | `similarity.py:193` (`pair_contributions`) | 6 |
| Journal des décisions | `ANALYTICS_DECISIONS.md` (sections 22-23) et `docs/RAG_DESIGN.md` (section 5) | toutes |

## 6. Questions ouvertes à discuter avec le professeur

1. **Le cadre :** dans quel cadre la problématique est-elle évaluée (projet, Grand oral, autre) ? Faut-il une démonstration ?
2. **Le niveau attendu :** la similarité cosinus, l'ACP et K-Means dépassent le programme. Faut-il les présenter en détail ou en « boîte noire » expliquée avec les mots du cours (vecteurs, k plus proches voisins) ?
3. **La vérité de référence :** est-ce acceptable que les étiquettes viennent d'une IA ? Faut-il refaire l'étiquetage à la main, même sur 30 paires ?
4. **Le résultat négatif :** la précision est plate. Est-ce un problème pour la problématique, ou au contraire un bon point de départ (« j'ai découvert que mon seuil ne voulait pas dire ce que je croyais ») ?
5. **Le refus qui ne sert jamais :** le seuil de 0,45 ne se déclenche jamais sans filtre. Faut-il le garder, le changer, ou l'expliquer ?
6. **Deux confiances :** faut-il parler aussi de la confiance d'archétype, ou rester sur le seuil de score ?
7. **Le périmètre :** traiter seulement l'interface, seulement le chatbot, ou les deux ?
8. **Le recentrage :** le titre est-il bien formulé ? Par exemple, « tromper » est-il le bon mot, plutôt qu'« induire en erreur » ?

## 7. Pistes possibles (sans trancher)

Ce sont des directions de réflexion, pas des réponses. Chacune a des avantages et des inconvénients à discuter.

| Piste | Idée | Question qu'elle soulève |
|---|---|---|
| A. Seuil fixe choisi au jugé | « Au-dessus de 70 %, c'est bon » | Sur quoi repose ce chiffre ? |
| B. Seuil mesuré | Choisir le seuil d'après la précision sur des paires étiquetées | Qui étiquette ? Combien de paires ? |
| C. Règle fixée avant la mesure | Écrire le critère avant de voir les résultats | Que faire si aucun seuil n'atteint l'objectif ? |
| D. Plusieurs niveaux de réponse | Montrer / avertir / refuser selon le score | Où mettre chaque frontière ? |
| E. Mots au lieu de pourcentages | « Fair » plutôt que « 94 % » | Un mot peut-il tromper autant qu'un nombre ? |
| F. Seuil appliqué par le code, pas par l'IA | Le chatbot reçoit une liste déjà filtrée | Comment le tester ? |
| G. Améliorer le score lui-même | Par exemple, ajouter le poste dans le calcul | Le seuil ne compense-t-il pas un algorithme imparfait ? |
| H. Expliquer le score | Montrer les 3 statistiques qui rapprochent ou éloignent les deux joueurs | L'utilisateur lit-il les explications ? |
| I. Tests automatiques | Un test échoue si le seuil ne correspond plus à la mesure | Que faire quand le test échoue ? |

Voir aussi : `07_ALGORITHMES_CLES.md` (comment le score est calculé), `08_CHATBOT_ET_RAG.md` (le seuil dans le chatbot), `09_QUESTIONS_PROF.md`.
