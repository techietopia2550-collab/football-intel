# 07 — Les algorithmes clés

## Sommaire
1. Similarité cosinus
2. ACP (réduction du nombre de dimensions)
3. K-Means (regroupement en archétypes)
4. Correspondance floue des noms
5. Garde-fous du chatbot
6. Tableau récapitulatif des complexités

Notation : **n** = nombre de joueurs comparés, **d** = nombre de statistiques, **k** = nombre de groupes.

---

## 1. Similarité cosinus

**À quoi ça sert :** donner un score de ressemblance entre deux joueurs. C'est le score sur lequel porte le seuil de confiance.

**Explication simple :** chaque joueur est une flèche (un vecteur) dans un espace à d dimensions. On regarde **l'angle** entre deux flèches, pas leur longueur.
- même direction → cos(0°) = 1 ;
- perpendiculaires → cos(90°) = 0 ;
- opposées → cos(180°) = −1.

Comme les statistiques sont **centrées-réduites**, « même direction » veut dire « au-dessus de la moyenne sur les mêmes choses, en dessous sur les mêmes choses ». Kanté et Gueye sont tous les deux très au-dessus de la moyenne en tacles et en pressings, et en dessous en tirs : leurs flèches pointent dans la même direction.

**Formule** (vue en maths, géométrie) : cos(u, v) = (u · v) / (‖u‖ × ‖v‖), où u · v = Σ uᵢvᵢ est le produit scalaire et ‖u‖ = √(Σ uᵢ²) la longueur (norme).

**Pseudo-code :**
```
fonction cosinus(u, v)
    ps ← 0 ; nu ← 0 ; nv ← 0
    pour i de 0 à d-1
        ps ← ps + u[i] × v[i]
        nu ← nu + u[i]² ; nv ← nv + v[i]²
    si nu = 0 ou nv = 0 alors renvoyer 0
    renvoyer ps / (√nu × √nv)
```

**Implémentation :**
- version NSI avec une boucle : `code_commenté/fonctions_nsi.py` (`cosinus`) ;
- vraie version du projet, `src/analytics/similarity.py:185`, qui calcule tous les candidats en une ligne avec numpy (`matrix @ a / norms`) : `code_commenté/similarite_commente.py`.

**Complexité :** O(d) pour une paire ; O(n × d) pour comparer un joueur à n candidats ; plus O(n log n) pour trier. Pour Kanté : n = 175 joueurs du même archétype, d ≤ 8 axes.

**Pourquoi le cosinus et pas la distance euclidienne ?** La distance mesure l'écart **en valeur**. Le cosinus mesure la ressemblance **de profil**. Exemple : deux récupérateurs, l'un à 2 fois la moyenne des pressings, l'autre à 1,5 fois. Ils ont le même style, mais sont loin en distance. Le cosinus les juge proches. Inconvénient : un joueur « moyen partout » a un vecteur presque nul, et son cosinus devient instable.

**Lien avec la problématique :** le score est un **cosinus, pas une probabilité**. « 94 % » ne veut pas dire « 94 % de chances ». C'est la première façon de tromper l'utilisateur, et c'est pour ça que le projet ajoute un mot (*Fair*/*Weak*) à côté.

## 2. ACP (analyse en composantes principales)

**À quoi ça sert :** les 12 statistiques sont souvent liées entre elles. Un joueur qui tire beaucoup a aussi beaucoup d'xG, et compter ces deux colonnes revient à compter deux fois la même information. L'ACP fabrique de **nouveaux axes** qui combinent les colonnes et ne se répètent pas. On garde seulement les premiers axes, ceux qui portent **85 %** de l'information (la variance).

**Image simple :** l'ombre d'un objet en 3D sur un mur est en 2D. Si on choisit bien l'angle de la lampe, l'ombre garde l'essentiel de la forme.

**Pseudo-code (principe) :**
```
Z ← tableau centré-réduit (n joueurs × d statistiques)
calculer les axes principaux de Z (directions où les joueurs sont le plus « étalés »)
trier les axes par part de variance expliquée
garder les m premiers axes tels que somme des parts ≥ 0,85
nouvelles coordonnées ← projection de Z sur ces m axes   (n × m)
```

**Dans le projet :** `PCA(n_components=0.85, svd_solver="full").fit(z_matrix)` (`similarity.py:147`, fonction `fit_group`). C'est la bibliothèque scikit-learn qui fait le calcul (on **utilise une API**, ce qui est au programme). Résultats réels : Vardy 12 → 6 axes (86,7 %), Kanté 12 → 8 axes (87,7 %).

**Complexité :** environ O(n × d² + d³). Avec n ≈ 175 et d = 12, c'est instantané.

**Lien avec la problématique :** on perd 15 % de l'information. Deux joueurs peuvent différer sur ce qui a été jeté, et le score ne le verra pas.

## 3. K-Means (regroupement en archétypes)

**À quoi ça sert :** ranger les 1 337 joueurs en groupes de styles (10 pour les joueurs de champ, 3 pour les gardiens), sans utiliser le poste écrit sur la feuille de match.

**Explication simple :** on place k « centres » au hasard, puis on répète :
1. chaque joueur rejoint le centre le plus proche ;
2. chaque centre se déplace au milieu (moyenne) de ses joueurs.

On s'arrête quand plus rien ne bouge.

**Pseudo-code :**
```
choisir k centres au hasard
répéter
    pour chaque joueur j : groupe[j] ← indice du centre le plus proche de j
    pour chaque groupe g : centre[g] ← moyenne des joueurs du groupe g
jusqu'à ce que les groupes ne changent plus
```

**Dans le projet** (`src/analytics/archetypes.py:197`) :
- `KMeans(n_clusters=10, n_init=50, random_state=0)` : on relance 50 fois avec des départs différents et on garde le meilleur résultat. La graine (`random_state`) fixée rend le résultat **reproductible** ;
- le choix de **k = 10** est un **jugement humain**, guidé par un score appelé silhouette (`sweep_k`, ligne 165), et noté dans `ANALYTICS_DECISIONS.md` ;
- les **noms** (*Poacher*, *Destroyer*…) ont été donnés par l'auteur, en regardant le profil moyen de chaque groupe ;
- la **confiance** d'un joueur vaut (d2 − d1) / d2, où d1 est la distance à son centre et d2 la distance au 2ᵉ centre le plus proche (ligne 254).

**Complexité :** O(i × n × k × d) pour une exécution (i = nombre de tours), multipliée par 50 relances. Pour n = 1 239 joueurs de champ, k = 10 et au plus d = 14 statistiques (moins après l'ACP), ça reste rapide.

**Lien avec la problématique :** la recherche de similarité se fait **dans l'archétype**. Si l'archétype est mal choisi, on cherche au mauvais endroit. Vardy a une confiance de 0,07 : il est presque à la frontière, et le projet affiche alors un avertissement (`similarity.py:350`).

**Lien NSI :** K-Means ressemble aux **k plus proches voisins** de Première (calculs de distances), mais **sans étiquettes connues à l'avance**. C'est de l'apprentissage « non supervisé ».

## 4. Correspondance floue des noms

**À quoi ça sert :** retrouver « Kanté » quand on tape « kante », ou « Vardy » sans prénom.

> **Correction par rapport à la consigne :** le chatbot et la recherche **n'utilisent pas** `rapidfuzz`. Ils utilisent `difflib`, inclus dans Python. `rapidfuzz` sert ailleurs : rapprocher les noms StatsBomb et Transfermarkt pour les valeurs marchandes (`src/transform/market_values.py`).

**a) Recherche de joueurs** (`src/analytics/directory.py:142`). Une note par nom, du plus sûr au moins sûr :

| Cas | Note | Exemple (requête → nom) |
|---|---|---|
| nom exact | 4 | « jamie vardy » → Jamie Vardy |
| un mot entier du nom | 3,5 | « vardy » → Jamie Vardy |
| début d'un mot | 3 | « vard » → Jamie Vardy |
| contenu dans le nom | 2 | « ard » → Jamie Vardy |
| ressemblance (faute de frappe) ≥ 0,72 | 0,72 à 1 | « vardi » → Jamie Vardy |

Avant de comparer, `_fold` (`src/analytics/profile.py:112`) enlève les accents et les majuscules : « Kanté » devient « kante ».

**b) Garde-fou du chatbot** (`src/analytics/chatbot.py:137`, fonction `_resolve`) : **une correspondance seulement floue n'est jamais devinée**. Le chatbot répond « je n'ai pas trouvé » et propose des noms. Si plusieurs noms ont la même meilleure note, il demande lequel.

> **Lien direct avec la problématique :** c'est **un autre seuil de confiance**, sur les noms cette fois. Plutôt que de répondre sur le mauvais joueur, le programme préfère demander.

**c) rapidfuzz pour Transfermarkt :** `fuzz.ratio` donne un pourcentage de ressemblance entre deux chaînes (fondé sur la **distance d'édition**, le nombre de lettres à changer). Seuil : **0,85** (`FUZZY_MIN_RATIO`). Si deux candidats sont trop proches l'un de l'autre, le rapprochement est déclaré « ambigu » et **non fait**.

**Complexité :** comparer deux chaînes de longueurs a et b coûte O(a × b). Comparer une requête à N = 2 173 noms coûte O(N × a × b). C'est rapide, car les noms sont courts.

## 5. Garde-fous du chatbot

**À quoi ça sert :** empêcher le chatbot de répondre hors sujet, d'inventer, ou d'avoir l'air plus sûr qu'il ne l'est. Détails : `08_CHATBOT_ET_RAG.md`.

**Pseudo-code général** (`src/chat/conversation.py:198`) :
```
fonction répondre(question)
    si une règle détecte prédiction / hors-sujet / autre saison / trait non mesurable
        renvoyer la phrase de refus prévue                       # sans IA
    si un nom de joueur est inconnu ou ambigu
        renvoyer refus ou demande de précision                    # sans IA
    répéter au plus 2 fois
        l'IA choisit un outil ; Python l'exécute
        si l'outil est une recherche de similarité
            appliquer la règle du seuil (0,45 / 0,60)             # par le code
    l'IA rédige la réponse
    si un nombre de la réponse n'existe dans aucun résultat d'outil
        retenir la réponse                                       # anti-hallucination
    ajouter avertissement + mise en garde ; renvoyer
```

**Implémentation commentée :** `code_commenté/guardrails_commente.py` et `code_commenté/conversation_commente.py`.

**Complexité :** les règles utilisent des **expressions régulières** (des motifs de recherche dans un texte), en O(longueur de la question). La vérification des chiffres coûte environ O(nombres dans la réponse × nombres dans les résultats).

**Lien avec la problématique :** le seuil est appliqué **par le code, avant l'IA**. L'IA ne peut ni l'oublier ni le contourner.

## 6. Tableau récapitulatif des complexités

| Algorithme | Complexité | Taille réelle dans le projet |
|---|---|---|
| Cosinus (une paire) | O(d) | d ≤ 12 |
| Recherche des similaires | O(n × d + n log n) | n ≈ 44 à 196 (taille d'un archétype) |
| ACP | ≈ O(n d² + d³) | n ≈ 175, d = 12 |
| K-Means | O(i × n × k × d) × 50 relances | n = 1 239 joueurs de champ, k = 10 |
| Recherche de nom | O(N × a × b) | N = 2 173 noms |
| Tri (Timsort / fusion) | O(n log n) | – |
| Dichotomie (version NSI) | O(log n) | – |
