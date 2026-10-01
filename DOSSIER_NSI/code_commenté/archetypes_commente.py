# =============================================================================
# EXTRAITS DU VRAI CODE — src/analytics/archetypes.py
# =============================================================================
# Lignes de code copiées SANS MODIFICATION. Seuls les commentaires « # >> »
# (en français) sont ajoutés pour le dossier. Non exécutable seul.
#
# Rôle : ranger chaque joueur dans un « archétype » (un style de jeu) d'après
# ce qu'il FAIT sur le terrain, pas d'après le poste écrit sur la feuille de match.
# Exemple : Özil (milieu offensif) et De Bruyne (ailier) peuvent tomber dans
# le même archétype « Chance creator ».
# Méthode : centrer-réduire -> ACP -> K-Means (regroupement en k paquets).
# Lien NSI : traitement de données en tables, dictionnaires, tuples,
#            classe (dataclass), algorithmique (algorithme itératif).
# =============================================================================


# --- Les 14 statistiques utilisées (lignes 74-81) -----------------------------
# >> Un TUPLE : liste NON modifiable. Elle a été fixée AVANT de voir les résultats
# >> (pour ne pas « tricher » en choisissant les colonnes qui donnent un joli résultat).
# >> « _p90 » = par 90 minutes, pour comparer des joueurs au temps de jeu différent.
OUTFIELD_FEATURES: tuple[str, ...] = (
    "tackles_p90", "interceptions_p90", "clearances_p90", "pressures_p90",
    "aerials_won_p90",
    "progressive_passes_p90", "pass_completion_pct", "passes_received_p90",
    "progressive_carries_p90", "dribbles_p90",
    "shots_p90", "xg_p90", "xa_p90", "key_passes_p90",
)  # fmt: skip


# --- k choisi et noms des archétypes (lignes 83-96) ---------------------------
# >> k = nombre de paquets. 10 pour les joueurs de champ, 3 pour les gardiens.
# >> Ce choix est un JUGEMENT HUMAIN noté dans ANALYTICS_DECISIONS.md (section 14),
# >> pas un calcul automatique.
CHOSEN_K: int | None = 10
CHOSEN_GK_K: int | None = 3
# >> DICTIONNAIRE : numéro de paquet -> nom lisible (choisi par l'auteur
# >> en regardant le profil moyen de chaque paquet).
OUTFIELD_LABELS: dict[int, str] = {
    0: "Stopper centre-back",
    1: "Ball-playing centre-back",
    2: "Defensive full-back",
    3: "Destroyer",
    4: "Deep playmaker",
    5: "Pressing wide midfielder",
    6: "Target man",
    7: "Poacher",
    8: "Dribbling winger",
    9: "Chance creator",
}


# --- Réduction + K-Means (lignes 160-162 et 197-215) -------------------------
def reduce(z: np.ndarray) -> tuple[PCA, np.ndarray]:
    # >> ACP : on garde assez d'axes pour 85 % de l'information
    pca = PCA(n_components=VARIANCE_TARGET, svd_solver="full").fit(z)
    return pca, pca.transform(z)


def fit_pool(
    members: pd.DataFrame,
    features: list[str] | tuple[str, ...],
    k: int,
    pool: str,
    id_offset: int = 0,
) -> ArchetypeModel:
    """Z-score, PCA and K-Means one pool; clusters numbered by centroid PC1 (ascending)."""
    members = members.reset_index(drop=True)
    z, kept, dropped = zscore_matrix(members, features)   # >> centrer-réduire
    pca, scores = reduce(z)                               # >> ACP
    # >> K-MEANS (bibliothèque scikit-learn) :
    # >>   n_init=50 -> on relance 50 fois avec des départs différents et on garde le meilleur
    # >>   random_state=SEED (=0) -> résultat REPRODUCTIBLE (même hasard à chaque fois)
    km = KMeans(n_clusters=k, n_init=N_INIT, random_state=SEED).fit(scores)
    # >> On renumérote les paquets dans un ordre fixe (selon le 1er axe de l'ACP)
    # >> pour que « 7 » veuille toujours dire « Poacher » d'une exécution à l'autre.
    order = np.argsort(km.cluster_centers_[:, 0])  # canonical numbering
    rank = np.empty(k, dtype=int)
    rank[order] = np.arange(k)
    return ArchetypeModel(
        pool, members, kept, dropped, z, pca, scores, k,
        rank[km.labels_], km.cluster_centers_[order], id_offset,
    )  # fmt: skip


# --- La « confiance » d'un joueur dans son archétype (lignes 254-260) --------
# >> C'est l'AUTRE mesure de confiance du projet (différente du seuil 0.60).
# >> d1 = distance au centre de SON paquet, d2 = distance au 2e centre le plus proche.
# >> confiance = (d2 - d1) / d2
# >>   -> 0 : le joueur est pile entre deux archétypes (classement fragile)
# >>   -> proche de 1 : il est bien au cœur de son archétype
# >> Ce n'est PAS une probabilité, c'est une marge géométrique.
def assignments(model: ArchetypeModel, labels: dict[int, str]) -> pd.DataFrame:
    """The `player_archetypes` rows for a fitted pool."""
    dist = model.distances()
    nearest = dist[np.arange(len(dist)), model.labels]          # >> d1
    other = np.where(np.arange(model.k)[None, :] == model.labels[:, None], np.inf, dist)
    second = other.min(axis=1)                                  # >> d2
    confidence = np.clip((second - nearest) / second, 0.0, 1.0)  # >> (d2 - d1) / d2
    # ... (suite : construction de la table player_archetypes)

# >> Chiffres réels (base de données) : 536 joueurs sur 1 337 ont une confiance
# >> d'archétype inférieure à 0.25, soit environ 40 %. Vardy : 0.07. Kanté : 0.24.
# >> Question ouverte pour la problématique : faut-il aussi un SEUIL ici ?
