# 04 — Fonctions de niveau NSI appliquées au projet

## Sommaire
0. Avertissement
1. Récursivité : chercher dans un arbre d'archétypes
2. Tri fusion : classer les joueurs
3. Recherche dichotomique : trouver un joueur
4. Similarité cosinus simplifiée + seuil
5. Dictionnaires et listes : buts par équipe
6. Classe `Joueur` (POO)
7. Tester le tout

---

## 0. Avertissement

> **Ces 6 fonctions ont été écrites pour ce dossier. Elles ne font pas partie du projet.** Elles reprennent les idées du projet en Python « pur » (sans numpy ni pandas), au niveau du programme, avec de **vraies données** tirées de `data/football.db`.
> Code complet et testé : `code_commenté/fonctions_nsi.py`. Le **vrai** code du projet est dans les autres fichiers de `code_commenté/`.
> Si le professeur demande « c'est ton projet, ça ? », la bonne réponse est : « Non, c'est une version simplifiée que j'ai écrite pour expliquer l'idée. Le vrai code est ici. »

Les données d'exemple sont une liste de dictionnaires :

```python
JOUEURS = [
    {"nom": "Jamie Vardy", "equipe": "Leicester City", "buts": 24, "xg_p90": 0.65,
     "pressions_p90": 14.3, "tacles_p90": 1.22, "passes_cles_p90": 1.19},
    {"nom": "N'Golo Kanté", "equipe": "Leicester City", "buts": 1, "xg_p90": 0.02,
     "pressions_p90": 27.0, "tacles_p90": 4.88, "passes_cles_p90": 0.80},
    # … Busquets, Özil, Gueye, Suárez, Kane
]
```

## 1. Récursivité : chercher dans un arbre d'archétypes

**Idée :** le projet a 13 archétypes « à plat ». On les range en arbre pour l'exercice : Tous → Gardiens / Joueurs de champ → Défense / Milieu / Attaque → archétypes. Ce regroupement est inventé pour le dossier. Un nœud est un tuple `(nom, liste_des_enfants)`.

```python
def chemin_vers(noeud: tuple, cible: str) -> list | None:
    nom, enfants = noeud
    if nom == cible:                          # CAS DE BASE : trouvé
        return [nom]
    for enfant in enfants:
        chemin = chemin_vers(enfant, cible)   # APPEL RÉCURSIF sur un sous-arbre
        if chemin is not None:
            return [nom] + chemin
    return None                               # CAS DE BASE : rien trouvé ici
```

```python
>>> chemin_vers(ARBRE_ARCHETYPES, "Destroyer")
['Tous les joueurs', 'Joueurs de champ', 'Milieu', 'Destroyer']
>>> nombre_de_feuilles(ARBRE_ARCHETYPES)
13
```

- **Pourquoi ça s'arrête :** chaque appel porte sur un sous-arbre plus petit, et une feuille n'a pas d'enfant.
- **Complexité :** O(n), on visite au pire chaque nœud une fois. C'est un **parcours en profondeur**.
- **Lien avec le projet :** le vrai projet utilise la récursivité pour parcourir des résultats imbriqués (`_flatten`, voir `code_commenté/conversation_commente.py`).

## 2. Tri fusion : classer les joueurs

**Idée « diviser pour régner » :** couper la liste en deux, trier chaque moitié (récursivement), puis fusionner deux listes triées.

```python
def tri_fusion(liste, cle):
    if len(liste) <= 1:
        return liste[:]
    milieu = len(liste) // 2
    gauche = tri_fusion(liste[:milieu], cle)   # diviser + régner
    droite = tri_fusion(liste[milieu:], cle)
    return fusionner(gauche, droite, cle)      # combiner
```

```python
>>> [j["nom"] for j in tri_fusion(JOUEURS, "buts")][:3]
['Luis Suárez', 'Harry Kane', 'Jamie Vardy']      # 40, 25, 24 buts
```

- **Complexité :** O(n log n), contre O(n²) pour le tri par insertion.
- **Lien avec le projet :** `find_similar` trie les candidats par score avec `sorted(...)` (`src/analytics/similarity.py:377`). Python utilise **Timsort**, un mélange de tri fusion et de tri par insertion, lui aussi en O(n log n).

## 3. Recherche dichotomique : trouver un joueur

**Idée :** dans une liste **triée**, on regarde le milieu et on élimine une moitié à chaque étape.

```python
def recherche_dichotomique(noms_tries, nom):
    debut, fin = 0, len(noms_tries) - 1
    while debut <= fin:
        milieu = (debut + fin) // 2
        if noms_tries[milieu] == nom:
            return milieu
        if noms_tries[milieu] < nom:   # ordre alphabétique
            debut = milieu + 1
        else:
            fin = milieu - 1
    return -1
```

- **Complexité :** O(log n). Pour 2 173 joueurs, 12 comparaisons au plus, contre 2 173 en lecture séquentielle.
- **Lien avec le projet, et sa limite :** le vrai projet **ne fait pas** de dichotomie. Il veut aussi trouver « Kante » sans accent, « Vardy » sans le prénom, ou une faute de frappe. Il parcourt donc toute la liste et donne une note à chaque nom (`src/analytics/directory.py:142`). La dichotomie ne marche que pour une égalité exacte.

## 4. Similarité cosinus simplifiée + seuil

**Idée :** chaque joueur devient un vecteur de 4 statistiques **centrées-réduites**. On mesure l'angle entre deux vecteurs.

```python
def cosinus(u, v):
    produit_scalaire = sum(a * b for a, b in zip(u, v))
    norme_u = sqrt(sum(a * a for a in u))
    norme_v = sqrt(sum(b * b for b in v))
    if norme_u == 0 or norme_v == 0:
        return 0.0
    return produit_scalaire / (norme_u * norme_v)

SEUIL_FAIR, SEUIL_REFUS = 0.60, 0.45    # mêmes valeurs que le projet

def niveau(score):
    if score >= SEUIL_FAIR:
        return "Fair"
    if score >= SEUIL_REFUS:
        return "Weak"
    return "Refus"
```

```python
>>> plus_proches("N'Golo Kanté", JOUEURS, 3)
[('Idrissa Gueye', 0.98, 'Fair'), ('Sergio Busquets', 0.71, 'Fair'), ('Mesut Özil', -0.48, 'Refus')]
```

- **Complexité :** O(d) pour un cosinus (d = nombre de statistiques), O(n × d) pour comparer à n joueurs.
- **Différences avec le vrai projet :** le projet compare 12 statistiques (pas 4), parmi environ 175 joueurs du même archétype (pas 7), et passe par une ACP avant le cosinus. Le vrai score Kanté–Gueye est **0,85**, pas 0,98. **Le score dépend fortement des statistiques et des joueurs choisis.** C'est un argument pour la problématique : un même seuil n'a pas le même sens partout.

## 5. Dictionnaires et listes : buts par équipe

```python
def buts_par_equipe(joueurs):
    totaux = {}
    for j in joueurs:
        equipe = j["equipe"]
        if equipe not in totaux:
            totaux[equipe] = 0
        totaux[equipe] += j["buts"]
    return totaux
```

```python
>>> buts_par_equipe(JOUEURS)["Leicester City"]
25                                   # Vardy 24 + Kanté 1
```

- **Complexité :** O(n). L'accès `totaux[equipe]` est en O(1) en moyenne grâce au hachage.
- **Équivalent SQL :** `SELECT team, SUM(goals) FROM player_season_stats_2015_16 GROUP BY team;` (voir `05_SQL_ET_BASE_DE_DONNEES.md`).

## 6. Classe `Joueur` (POO)

```python
class Joueur:
    MINUTES_MIN = 900                     # attribut de classe

    def __init__(self, nom, equipe, minutes, stats):
        self.nom = nom                    # attributs d'instance
        self.equipe = equipe
        self.minutes = minutes
        self._stats = stats               # « privé » par convention

    def est_qualifie(self):
        return self.minutes >= Joueur.MINUTES_MIN

    def __str__(self):
        return f"{self.nom} ({self.equipe}), {self.minutes} min"


class Gardien(Joueur):                    # héritage
    def __str__(self):                    # redéfinition (polymorphisme)
        return super().__str__() + " [GB]"
```

```python
>>> print(Joueur("Jamie Vardy", "Leicester City", 3108, {}))
Jamie Vardy (Leicester City), 3108 min
>>> print(Gardien("Hugo Lloris", "Tottenham Hotspur", 3330, {}))
Hugo Lloris (Tottenham Hotspur), 3330 min [GB]
```

- **900 minutes** : c'est le vrai seuil du projet. En dessous, pas de score de similarité.
- L'héritage et le polymorphisme sont **hors programme** de Terminale (commentaire du BO), mais le projet les utilise. Voir `06_POO_PYTHON.md`.

## 7. Tester le tout

Depuis le dossier `DOSSIER_NSI/code_commenté/` :

```bash
python fonctions_nsi.py
# TestResults(failed=0, attempted=17)
```

Les exemples précédés de `>>>` dans les docstrings sont des **tests** (module `doctest`). C'est la version simple de ce que fait `pytest` dans le projet.
