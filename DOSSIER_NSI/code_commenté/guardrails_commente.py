# =============================================================================
# EXTRAITS DU VRAI CODE — le SEUIL DE CONFIANCE (cœur de la problématique)
#   1) src/app/design.py, lignes 221-240   (interface Streamlit)
#   2) src/chat/guardrails.py, lignes 613-664 (chatbot)
# =============================================================================
# Lignes de code copiées SANS MODIFICATION. Seuls les commentaires « # >> »
# sont ajoutés. Non exécutable seul.
# Lien NSI : structures conditionnelles, tuples, dictionnaires, listes en
#            compréhension, spécification d'une fonction, tests.
# =============================================================================


# ---------------------------------------------------------------------------
# 1) INTERFACE — src/app/design.py
# ---------------------------------------------------------------------------
# >> Historique (ANALYTICS_DECISIONS.md, sections 22-23) :
# >>   - au départ, 4 niveaux choisis « au jugé » : Strong 85 %, Good 70 %, Fair 55 %, Weak
# >>   - puis MESURE sur 100 paires de joueurs étiquetées « similaire » / « pas similaire »
# >>   - résultat : seul « Fair à partir de 0.60 » est justifié par les mesures
# >>     -> Strong et Good ont été SUPPRIMÉS (aucun seuil n'atteignait la précision voulue)

# Match-% tiers, measured on the labelled test set (docs/similarity_thresholds.md,
# ANALYTICS_DECISIONS.md section 23): (word, lowest cosine, tone), highest first. Strong (90%
# precision) and Good (75%) are absent because no threshold reached them; the old judgement cuts
# were 85 / 70 / 55. Everything under the last cut is MATCH_TIER_BELOW.
# >> Un TUPLE de TUPLES (mot, seuil, couleur). Il n'en reste qu'un seul.
MATCH_TIERS: tuple[tuple[str, float, str], ...] = (("Fair", 0.60, "neutral"),)
MATCH_TIER_BELOW: tuple[str, str] = ("Weak", "negative")
# >> Texte de l'infobulle : il dit HONNÊTEMENT ce que « Fair » veut dire (environ 1 chance sur 2)
MATCH_TIER_NOTE = (
    "Measured tier: at 60%+ about half of labelled test pairs shared a role and style; "
    "below 60%, fewer than half. No higher score band proved more reliable than that."
)


def match_tier(similarity: float | None) -> tuple[str, str] | None:
    """(tier word, tone) for a cosine similarity, from the measured MATCH_TIERS; None if missing."""
    # >> pas de score -> pas d'étiquette (on n'invente rien)
    if similarity is None or np.isnan(similarity):
        return None
    # >> on parcourt les niveaux du plus haut au plus bas
    for word, cut, tone in MATCH_TIERS:
        # >> « - 1e-12 » : petite marge pour les erreurs d'arrondi des nombres flottants
        # >> (0.6 n'est pas représentable exactement en binaire !)
        if similarity >= cut - 1e-12:
            return word, tone
    return MATCH_TIER_BELOW


# ---------------------------------------------------------------------------
# 2) CHATBOT — src/chat/guardrails.py
# ---------------------------------------------------------------------------
# >> Deux seuils : 0.60 (Fair) et 0.45 (refus).
FAIR_CUT, REFUSE_CUT = 0.60, 0.45
# >> Phrases imposées : le modèle de langage ne les écrit pas, le CODE les impose.
WARN_SENTENCE = (
    "Weak matches only: the closest {kind} to {target} scores {score:.0%}, below the 60% 'Fair' "
    "level, so treat this list as loose leads, not look-alikes."
)
REFUSE_SENTENCE = (
    "No close matches for {target}: the best candidate scores only {score:.0%}, which is too low "
    "to call similar. Ask 'show the nearest anyway' to see them."
)
NO_CANDIDATES_SENTENCE = "No {kind}s are left to compare after the filters you asked for."
# >> Mise en garde ajoutée à TOUTE réponse de similarité, même bonne.
STANDING_CAVEAT = (
    "Similarity is a heuristic on 2015/16 event data: in testing, about half of Fair matches "
    "shared a role and style. It says nothing about ability, age, price or fit."
)


def apply_similarity_rule(similarity_result: dict[str, Any]) -> dict[str, Any]:
    """Tag, trim and judge a find_similar / find_similar_teams result (Decision 3)."""
    is_team = "team_name" in similarity_result and "target_name" not in similarity_result
    kind = "team" if is_team else "player"
    target = similarity_result.get("target_name") or similarity_result.get("team_name")
    rows = list(similarity_result.get("similar") or [])
    # >> Dictionnaire résultat : verdict + éventuel préfixe/refus + mise en garde
    out: dict[str, Any] = {"verdict": None, "prefix": None, "refusal": None,
                           "caveat": STANDING_CAVEAT, "best_score": None,
                           "result": dict(similarity_result)}  # fmt: skip
    # >> CAS 1 : plus aucun candidat (les filtres ont tout retiré)
    if not rows:
        out["verdict"] = "NO_CANDIDATES"
        out["refusal"] = NO_CANDIDATES_SENTENCE.format(kind=kind)
        return out
    # >> On regarde le MEILLEUR score (pas la moyenne) : c'est lui qui décide
    best = max(float(r["similarity"]) for r in rows)
    out["best_score"] = best
    # >> CAS 2 : même le meilleur est sous 0.45 -> REFUS, la liste est vidée
    if best < REFUSE_CUT:
        out["verdict"] = "REFUSE"
        out["refusal"] = REFUSE_SENTENCE.format(target=target, score=best)
        out["result"]["similar"] = []
        return out
    # >> Sinon : on étiquette chaque ligne Fair/Weak et on RETIRE celles sous 0.45
    # >> (liste en compréhension + {**r, ...} = copie du dictionnaire avec une clé en plus)
    kept = [
        {**r, "tier": "Fair" if float(r["similarity"]) >= FAIR_CUT else "Weak"}
        for r in rows
        if float(r["similarity"]) >= REFUSE_CUT
    ]
    out["result"]["similar"] = kept
    out["result"]["dropped_below_45"] = len(rows) - len(kept)
    # >> CAS 3 : meilleur entre 0.45 et 0.60 -> on RÉPOND, mais on PRÉVIENT d'abord
    if best < FAIR_CUT:
        out["verdict"] = "WARN"
        out["prefix"] = WARN_SENTENCE.format(kind=kind, target=target, score=best)
    # >> CAS 4 : meilleur ≥ 0.60 -> réponse normale (avec la mise en garde permanente)
    else:
        out["verdict"] = "FAIR"
    return out

# >> RÉSUMÉ :
# >>   meilleur score     | verdict        | ce que voit l'utilisateur
# >>   (aucun candidat)   | NO_CANDIDATES  | phrase de refus
# >>   < 0.45             | REFUSE         | phrase de refus, liste cachée
# >>   0.45 ≤ s < 0.60    | WARN           | avertissement EN PREMIER, puis la liste
# >>   ≥ 0.60             | FAIR           | la liste + mise en garde permanente
#
# >> Mesure faite en lançant le moteur sur tous les joueurs (sans filtre, 1er octobre 2026) :
# >>   - environ 10 joueurs sur ~1 320 ont un meilleur score < 0.60 (surtout des gardiens,
# >>     ex. Łukasz Fabiański : 0.55)
# >>   - AUCUN n'a un meilleur score < 0.45 -> le refus ne se déclenche presque jamais
# >>     sans filtre. Question ouverte à discuter (voir 02_PROBLEMATIQUE.pdf).
#
# >> Tests automatiques : tests/test_guardrails.py (test_similarity_fair,
# >> test_similarity_warn_uses_the_exact_sentence, test_similarity_refuse_below_45,
# >> test_similarity_edges_and_teams) et tests/test_similarity_thresholds.py.
