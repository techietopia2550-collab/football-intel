"""Fonctions de niveau Terminale NSI, écrites POUR LE DOSSIER (pas copiées du projet).

NOTE : ces fonctions ne font PAS partie de Football Intel.
Ce sont des versions simplifiées, en Python « pur » (sans numpy ni pandas),
qui illustrent les notions du programme NSI avec les vraies données du projet
(saison 2015/16, chiffres tirés de data/football.db).
Le vrai code du projet est expliqué dans les autres fichiers de code_commenté/.

Lancer les tests intégrés (exemples dans les docstrings) :
    python -m doctest fonctions_nsi.py -v
"""

from math import sqrt

# ---------------------------------------------------------------------------
# Données d'exemple : vrais chiffres du projet (table player_season_stats_2015_16)
# Chaque joueur est un dictionnaire (clé -> valeur).
# xg_p90 = buts « attendus » par 90 minutes, pressions_p90 = pressings par 90 min.
# ---------------------------------------------------------------------------
JOUEURS = [
    {"nom": "Jamie Vardy", "equipe": "Leicester City", "buts": 24, "xg_p90": 0.65,
     "pressions_p90": 14.3, "tacles_p90": 1.22, "passes_cles_p90": 1.19},
    {"nom": "N'Golo Kanté", "equipe": "Leicester City", "buts": 1, "xg_p90": 0.02,
     "pressions_p90": 27.0, "tacles_p90": 4.88, "passes_cles_p90": 0.80},
    {"nom": "Sergio Busquets", "equipe": "Barcelona", "buts": 0, "xg_p90": 0.01,
     "pressions_p90": 18.1, "tacles_p90": 3.21, "passes_cles_p90": 0.56},
    {"nom": "Mesut Özil", "equipe": "Arsenal", "buts": 6, "xg_p90": 0.18,
     "pressions_p90": 14.0, "tacles_p90": 1.02, "passes_cles_p90": 4.26},
    {"nom": "Idrissa Gueye", "equipe": "Aston Villa", "buts": 0, "xg_p90": 0.03,
     "pressions_p90": 29.5, "tacles_p90": 4.51, "passes_cles_p90": 0.80},
    {"nom": "Luis Suárez", "equipe": "Barcelona", "buts": 40, "xg_p90": 0.79,
     "pressions_p90": 9.6, "tacles_p90": 0.74, "passes_cles_p90": 1.71},
    {"nom": "Harry Kane", "equipe": "Tottenham Hotspur", "buts": 25, "xg_p90": 0.58,
     "pressions_p90": 13.2, "tacles_p90": 0.86, "passes_cles_p90": 1.08},
]


# ===========================================================================
# 1. RÉCURSIVITÉ — recherche dans un arbre d'archétypes
# ===========================================================================
# Le projet range les joueurs dans 13 archétypes « à plat » (10 de champ + 3
# gardiens). Ici on les regroupe en ARBRE pour l'exercice. Ce regroupement
# (Défense / Milieu / Attaque) est inventé pour le dossier.
# Un nœud = un tuple (nom, liste_des_enfants). Une feuille a une liste vide.
ARBRE_ARCHETYPES = (
    "Tous les joueurs",
    [
        ("Gardiens", [
            ("Protected high-save keeper", []),
            ("Balanced keeper", []),
            ("Busy low-save keeper", []),
        ]),
        ("Joueurs de champ", [
            ("Défense", [
                ("Stopper centre-back", []),
                ("Ball-playing centre-back", []),
                ("Defensive full-back", []),
            ]),
            ("Milieu", [
                ("Destroyer", []),
                ("Deep playmaker", []),
                ("Pressing wide midfielder", []),
            ]),
            ("Attaque", [
                ("Dribbling winger", []),
                ("Chance creator", []),
                ("Target man", []),
                ("Poacher", []),
            ]),
        ]),
    ],
)


def chemin_vers(noeud: tuple, cible: str) -> list | None:
    """Renvoie le chemin (liste de noms) de la racine jusqu'au nœud `cible`,
    ou None si `cible` n'est pas dans l'arbre.

    >>> chemin_vers(ARBRE_ARCHETYPES, "Destroyer")
    ['Tous les joueurs', 'Joueurs de champ', 'Milieu', 'Destroyer']
    >>> chemin_vers(ARBRE_ARCHETYPES, "Libéro") is None
    True
    """
    nom, enfants = noeud                      # on « déballe » le tuple
    if nom == cible:                          # CAS DE BASE : trouvé
        return [nom]
    for enfant in enfants:                    # sinon on cherche dans chaque sous-arbre
        chemin = chemin_vers(enfant, cible)   # APPEL RÉCURSIF sur un arbre plus petit
        if chemin is not None:                # trouvé plus bas :
            return [nom] + chemin             # on ajoute le nœud courant devant
    return None                               # CAS DE BASE : feuille sans succès


def nombre_de_feuilles(noeud: tuple) -> int:
    """Compte les archétypes (les feuilles) d'un arbre.

    >>> nombre_de_feuilles(ARBRE_ARCHETYPES)
    13
    """
    nom, enfants = noeud
    if enfants == []:                         # une feuille compte pour 1
        return 1
    return sum(nombre_de_feuilles(e) for e in enfants)  # somme des sous-arbres


# ===========================================================================
# 2. TRI FUSION (diviser pour régner) — classer les joueurs
# ===========================================================================
def tri_fusion(liste: list, cle: str) -> list:
    """Renvoie une NOUVELLE liste de joueurs triée par `cle`, du plus grand au
    plus petit. Complexité : O(n log n).

    >>> [j["nom"] for j in tri_fusion(JOUEURS, "buts")][:3]
    ['Luis Suárez', 'Harry Kane', 'Jamie Vardy']
    """
    if len(liste) <= 1:                       # cas de base : déjà triée
        return liste[:]
    milieu = len(liste) // 2                  # DIVISER en deux moitiés
    gauche = tri_fusion(liste[:milieu], cle)  # RÉGNER : trier chaque moitié
    droite = tri_fusion(liste[milieu:], cle)
    return fusionner(gauche, droite, cle)     # COMBINER les deux moitiés triées


def fusionner(gauche: list, droite: list, cle: str) -> list:
    """Fusionne deux listes déjà triées (ordre décroissant) en une seule."""
    resultat = []
    i, j = 0, 0                               # un indice par liste
    while i < len(gauche) and j < len(droite):
        if gauche[i][cle] >= droite[j][cle]:  # on prend le plus grand des deux
            resultat.append(gauche[i])
            i += 1
        else:
            resultat.append(droite[j])
            j += 1
    resultat.extend(gauche[i:])               # ce qui reste (déjà trié)
    resultat.extend(droite[j:])
    return resultat


# ===========================================================================
# 3. RECHERCHE DICHOTOMIQUE — trouver un joueur dans une liste triée de noms
# ===========================================================================
def recherche_dichotomique(noms_tries: list, nom: str) -> int:
    """Renvoie l'indice de `nom` dans `noms_tries` (triée par ordre
    alphabétique), ou -1 s'il est absent. Complexité : O(log n).

    >>> noms = sorted(j["nom"] for j in JOUEURS)
    >>> noms[recherche_dichotomique(noms, "N'Golo Kanté")]
    "N'Golo Kanté"
    >>> recherche_dichotomique(noms, "Zinédine Zidane")
    -1
    """
    debut, fin = 0, len(noms_tries) - 1
    while debut <= fin:                       # tant que la zone de recherche n'est pas vide
        milieu = (debut + fin) // 2
        if noms_tries[milieu] == nom:
            return milieu                     # trouvé
        if noms_tries[milieu] < nom:          # comparaison alphabétique des chaînes
            debut = milieu + 1                # le nom est dans la moitié droite
        else:
            fin = milieu - 1                  # le nom est dans la moitié gauche
    return -1                                 # zone vide : absent


# ===========================================================================
# 4. SIMILARITÉ COSINUS (version simplifiée) + SEUIL DE CONFIANCE
# ===========================================================================
# Dans le projet, le cosinus est calculé APRÈS deux étapes (centrage-réduction
# et ACP, voir 07_ALGORITHMES_CLES.pdf). Ici on le calcule directement sur
# des statistiques centrées-réduites « à la main » pour garder l'idée.
STATS = ["xg_p90", "pressions_p90", "tacles_p90", "passes_cles_p90"]


def centrer_reduire(joueurs: list, stats: list) -> dict:
    """Pour chaque joueur : liste de ses écarts à la moyenne, divisés par
    l'écart-type (« z-scores »). Renvoie {nom: [z1, z2, ...]}."""
    vecteurs = {j["nom"]: [] for j in joueurs}
    for s in stats:
        valeurs = [j[s] for j in joueurs]
        moyenne = sum(valeurs) / len(valeurs)
        ecart_type = sqrt(sum((v - moyenne) ** 2 for v in valeurs) / len(valeurs))
        for j in joueurs:
            vecteurs[j["nom"]].append((j[s] - moyenne) / ecart_type)
    return vecteurs


def cosinus(u: list, v: list) -> float:
    """Similarité cosinus de deux vecteurs de même taille (entre -1 et 1).

    >>> cosinus([1, 0], [2, 0])        # même direction
    1.0
    >>> cosinus([1, 0], [0, 3])        # perpendiculaires
    0.0
    """
    produit_scalaire = sum(a * b for a, b in zip(u, v))
    norme_u = sqrt(sum(a * a for a in u))
    norme_v = sqrt(sum(b * b for b in v))
    if norme_u == 0 or norme_v == 0:          # vecteur nul : pas de direction
        return 0.0
    return produit_scalaire / (norme_u * norme_v)


SEUIL_FAIR = 0.60    # même valeur que FAIR_CUT dans src/chat/guardrails.py
SEUIL_REFUS = 0.45   # même valeur que REFUSE_CUT


def niveau(score: float) -> str:
    """Étiquette affichée à côté d'un score (règle du projet, simplifiée).

    >>> niveau(0.93), niveau(0.55), niveau(0.30)
    ('Fair', 'Weak', 'Refus')
    """
    if score >= SEUIL_FAIR:
        return "Fair"
    if score >= SEUIL_REFUS:
        return "Weak"
    return "Refus"


def plus_proches(nom: str, joueurs: list, n: int = 3) -> list:
    """Les `n` joueurs les plus proches de `nom` : liste de tuples
    (nom, score arrondi, niveau).

    >>> plus_proches("N'Golo Kanté", JOUEURS, 1)
    [('Idrissa Gueye', 0.98, 'Fair')]
    """
    vecteurs = centrer_reduire(joueurs, STATS)
    cible = vecteurs[nom]
    scores = [(autre, round(cosinus(cible, v), 2))
              for autre, v in vecteurs.items() if autre != nom]
    scores.sort(key=lambda t: t[1], reverse=True)   # du plus similaire au moins similaire
    return [(autre, s, niveau(s)) for autre, s in scores[:n]]


# ===========================================================================
# 5. DICTIONNAIRES ET LISTES — regrouper les joueurs par équipe
# ===========================================================================
def buts_par_equipe(joueurs: list) -> dict:
    """{équipe: total de buts} (comme un GROUP BY en SQL).

    >>> buts_par_equipe(JOUEURS)["Leicester City"]
    25
    """
    totaux = {}
    for j in joueurs:
        equipe = j["equipe"]
        if equipe not in totaux:              # première fois qu'on voit l'équipe
            totaux[equipe] = 0
        totaux[equipe] += j["buts"]
    return totaux


# ===========================================================================
# 6. PROGRAMMATION ORIENTÉE OBJET — classe Joueur
# ===========================================================================
class Joueur:
    """Un joueur et ses statistiques par 90 minutes.

    >>> v = Joueur("Jamie Vardy", "Leicester City", 3108, {"xg_p90": 0.65})
    >>> print(v)
    Jamie Vardy (Leicester City), 3108 min
    >>> v.est_qualifie()
    True
    """

    MINUTES_MIN = 900   # attribut de CLASSE : même seuil que le projet (900 minutes)

    def __init__(self, nom: str, equipe: str, minutes: float, stats: dict):
        self.nom = nom              # attributs d'INSTANCE (propres à chaque objet)
        self.equipe = equipe
        self.minutes = minutes
        self._stats = stats         # « _ » = à ne pas modifier de l'extérieur (encapsulation)

    def est_qualifie(self) -> bool:
        """Assez de minutes pour calculer des moyennes fiables ?"""
        return self.minutes >= Joueur.MINUTES_MIN

    def stat(self, nom_stat: str) -> float:
        """Accès en lecture à une statistique (interface publique)."""
        return self._stats[nom_stat]

    def __str__(self) -> str:       # appelée par print()
        return f"{self.nom} ({self.equipe}), {self.minutes} min"


class Gardien(Joueur):
    """HÉRITAGE : un Gardien EST un Joueur, avec une règle en plus.

    >>> g = Gardien("Hugo Lloris", "Tottenham Hotspur", 3330, {})
    >>> print(g)
    Hugo Lloris (Tottenham Hotspur), 3330 min [GB]
    """

    def __str__(self) -> str:       # POLYMORPHISME : même méthode, autre comportement
        return super().__str__() + " [GB]"


if __name__ == "__main__":
    import doctest
    print(doctest.testmod())
    print("Plus proches de Kanté :", plus_proches("N'Golo Kanté", JOUEURS))
    print("Plus proches de Vardy :", plus_proches("Jamie Vardy", JOUEURS))
