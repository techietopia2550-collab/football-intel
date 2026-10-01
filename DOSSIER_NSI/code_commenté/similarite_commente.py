# =============================================================================
# EXTRAITS DU VRAI CODE — src/analytics/similarity.py
# =============================================================================
# Les lignes de code ci-dessous sont copiées SANS MODIFICATION depuis le projet.
# Seuls les commentaires « # >> » (en français) ont été ajoutés pour le dossier.
# Ce fichier n'est pas exécutable seul (il manque les imports du module).
#
# Rôle du module : trouver les joueurs qui JOUENT COMME un joueur donné.
# Étapes (voir 07_ALGORITHMES_CLES.pdf) :
#   1. choisir le « groupe » de comparaison (l'archétype du joueur)
#   2. choisir les statistiques (les 12 plus typiques de l'archétype)
#   3. centrer-réduire chaque statistique (z-score)
#   4. ACP : résumer les 12 colonnes en quelques « axes » (85 % de l'information)
#   5. similarité cosinus entre le joueur et chaque candidat, puis classement
# Lien NSI : traitement de données en tables, modularité (fonctions courtes),
#            algorithmique (tri), langages (typage, exceptions).
# =============================================================================


# --- Constantes (src/analytics/similarity.py, lignes 76-84) ------------------
# >> Des CONSTANTES en majuscules : on les change à un seul endroit.
# >> LOW_CONFIDENCE : en dessous, le joueur est « au bord » de son archétype.
LOW_CONFIDENCE = 0.25  # below this margin a player sits near the edge of his archetype
# >> On garde assez d'axes de l'ACP pour conserver 85 % de la variance.
VARIANCE_TARGET = 0.85
# >> Une statistique vide pour plus de 30 % des joueurs est abandonnée.
MAX_MISSING_SHARE = 0.30
TOP_EXPLAINERS = 3
# >> Nombre de statistiques « typiques » utilisées dans un archétype.
DISTINCTIVE_N = 12  # archetype-distinctive features used inside an archetype
MIXED_ROLE_SHARE = 0.6
# Yellow and red cards are rare, chance-driven counts: they add noise, not style.
# >> frozenset = ENSEMBLE non modifiable. Les cartons ne décrivent pas un style de jeu.
EXCLUDED_FEATURES: frozenset[str] = frozenset({"red_p90", "yellow_p90"})


# --- Similarité cosinus (lignes 185-190) -------------------------------------
# >> C'est LE calcul central. a = vecteur du joueur cible,
# >> matrix = un tableau où chaque LIGNE est le vecteur d'un candidat.
def cosine(a: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Cosine similarity of vector `a` with each row of `matrix` (0 for a zero-length vector)."""
    # >> norme (longueur) de chaque ligne × norme de a  -> dénominateur
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(a)
    # >> on désactive l'avertissement « division par zéro » le temps du calcul
    with np.errstate(invalid="ignore", divide="ignore"):
        # >> « @ » = produit matriciel : un produit scalaire par candidat, d'un coup
        sims = matrix @ a / norms
    # >> si une norme vaut 0 (vecteur nul), on renvoie 0 au lieu de « nan »
    return np.where(norms > 0, sims, 0.0)
# >> Équivalent NSI (boucle) : voir cosinus() dans fonctions_nsi.py.
# >> Complexité : O(n × d), n candidats, d dimensions (ici d ≤ 12).


# --- Préparation : centrer-réduire puis ACP (lignes 147-182) -----------------
def fit_group(members: pd.DataFrame, group: str, features: list[str] | None = None) -> GroupModel:
    """Z-score `members` (one pool's qualifying players) and fit the PCA.

    `group` names the pool in messages and picks its features unless `features` is given."""
    # >> GARDE-FOU n°1 : moins de 3 joueurs, la comparaison n'a pas de sens -> exception
    if len(members) < 3:
        raise ValueError(f"Only {len(members)} {group} players: too few to compare")
    kept: list[str] = []            # >> statistiques gardées
    dropped: dict[str, str] = {}    # >> statistiques écartées -> raison (dictionnaire)
    columns: list[np.ndarray] = []
    for name in features if features is not None else group_features(group):
        values = members[name].to_numpy(dtype=float)
        # >> part de valeurs manquantes (nan) dans la colonne
        missing = float(np.isnan(values).mean())
        # >> GARDE-FOU n°2 : trop de trous -> on n'utilise pas cette statistique
        if missing > MAX_MISSING_SHARE:
            dropped[name] = f"empty for {missing:.0%} of {group} players"
            continue
        mean, std = np.nanmean(values), np.nanstd(values)
        # >> GARDE-FOU n°3 : tout le monde a la même valeur -> statistique inutile
        if not np.isfinite(std) or std == 0:
            dropped[name] = "no variance in the group"
            continue
        # >> CENTRER-RÉDUIRE : (valeur - moyenne) / écart-type
        # >> -> toutes les statistiques sont sur la même échelle (sans unité)
        z = (values - mean) / std
        # >> une valeur manquante devient 0 = « dans la moyenne »
        columns.append(np.where(np.isnan(z), 0.0, z))
        kept.append(name)
    if not kept:
        raise ValueError(f"No usable features for {group}")

    z_matrix = np.column_stack(columns)   # >> tableau joueurs × statistiques
    # >> ACP (bibliothèque scikit-learn) : garde le nombre d'axes nécessaire pour 85 %
    pca = PCA(n_components=VARIANCE_TARGET, svd_solver="full").fit(z_matrix)
    return GroupModel(
        members=members.reset_index(drop=True),
        features=kept,
        dropped=dropped,
        z=z_matrix,
        pca=pca,
        scores=pca.transform(z_matrix),   # >> coordonnées de chaque joueur sur les axes
        explained_variance=float(pca.explained_variance_ratio_.sum()),
    )


# --- Extrait de find_similar() (lignes 350-377) : avertissements + classement
# >> Ici, le projet NE BLOQUE PAS : il AJOUTE DES AVERTISSEMENTS à la réponse.
# >> C'est une première forme de « confiance » : on dit à l'utilisateur quand
# >> le résultat est fragile.
        if confidence < LOW_CONFIDENCE:
            warnings.append(
                f"Low archetype confidence ({confidence:.2f}): near the edge of '{arch_label}', "
                "so the pool is a soft description of him."
            )
# >> Exemple réel : Jamie Vardy a une confiance d'archétype de 0.07 (« Poacher »),
# >> donc cet avertissement est affiché pour lui.

    if len(members) < MIN_GROUP_SIZE:
        warnings.append(
            f"Only {len(members)} qualifying {pool_name} players (< {MIN_GROUP_SIZE}): the PCA "
            "and the similarity scores are coarse."
        )

    pool = members[members["player_id"] != target["player_id"]]   # >> on retire la cible
    pool = _apply_filters(pool, target, flt, season, engine, warnings)
    # >> un score cosinus pour CHAQUE joueur de l'archétype
    sims = cosine(model.scores[t_idx], model.scores)
    # >> TRI décroissant par score, puis on garde les n premiers
    # >> (sorted = Timsort, O(n log n) ; voir tri_fusion() pour la version NSI)
    order = sorted(pool.index, key=lambda i: -sims[i])[:n]

# >> REMARQUE pour la problématique : à ce stade, AUCUN seuil n'est appliqué.
# >> find_similar renvoie toujours les n meilleurs, même si leur score est bas.
# >> Les seuils 0.60 / 0.45 sont appliqués APRÈS, dans deux autres fichiers :
# >>   - src/app/design.py (badge « Fair » / « Weak » dans l'interface)
# >>   - src/chat/guardrails.py (règle du chatbot) -> voir guardrails_commente.py
