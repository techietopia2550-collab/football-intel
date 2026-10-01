# =============================================================================
# EXTRAITS DU VRAI CODE — src/chat/conversation.py (le chatbot)
# =============================================================================
# Lignes de code copiées SANS MODIFICATION (sauf « ... » = passage coupé).
# Seuls les commentaires « # >> » sont ajoutés. Non exécutable seul.
#
# Rôle : répondre à une question en langage naturel (« Qui joue comme Kanté ? »)
# en utilisant UNIQUEMENT la base de données. Un modèle de langage (LLM,
# programme d'IA qui génère du texte, ici openai/gpt-oss-120b via le service Groq)
# rédige la réponse, mais c'est le CODE PYTHON qui contrôle tout autour.
# Lien NSI : modularité / API, gestion des erreurs (exceptions), boucles,
#            dictionnaires, interactions client-serveur (requêtes HTTP).
# =============================================================================

MAX_TOOL_ROUNDS = 2   # >> au plus 2 « tours » d'appels d'outils par question (ligne 37)


def ask(
    question: str, history: list[dict[str, str]] | None = None, client: ChatClient | None = None
) -> dict[str, Any]:
    """Answer one question from the database only; see the module docstring for the flow."""
    # >> ÉTAPE 1 — règles fixes, SANS IA : prédictions, hors-sujet, autre saison...
    # >> Si une règle se déclenche, on répond par une phrase de refus écrite à l'avance.
    by_rule = guardrails.rule_refusal(question)
    if by_rule:
        return _reply(question, by_rule[1], refused=True, verdict=by_rule[0])
    # >> ÉTAPE 2 — les noms de joueurs/équipes existent-ils dans la base ?
    # >> Inconnu -> refus « NOT_IN_DB » ; plusieurs possibles -> on demande lequel.
    named = guardrails.name_refusal(guardrails.check_player_names(question))
    if named:
        return _reply(question, named[1], refused=True, verdict=named[0])
    # ... (préparation des messages, gestion du débit d'appels au service)

    # >> ÉTAPE 3 — boucle : le LLM choisit un « outil » (une fonction Python du projet),
    # >> Python l'exécute, renvoie le résultat au LLM, etc.
    # ... (dans la boucle, pour chaque outil appelé :)
            result = call_tool(name, args)
            # ...
            elif name in SIMILARITY_TOOLS:
                # >> ÉTAPE 4 — LE SEUIL DE CONFIANCE est appliqué ICI (ligne 331),
                # >> AVANT que le LLM ne voie le résultat. Le LLM ne peut donc pas
                # >> « oublier » le seuil : il reçoit une liste déjà triée/coupée.
                rule = guardrails.apply_similarity_rule(result)
                if rule["refusal"]:
                    # >> refus : la question s'arrête là, le LLM n'écrit rien
                    return reply(
                        question, rule["refusal"], refused=True, verdict=rule["verdict"],
                        tool_calls=calls_made, sources=sources,
                    )  # fmt: skip
                result = {**rule["result"], "similarity_verdict": rule["verdict"]}
                prefix = prefix or rule["prefix"]   # >> avertissement à placer EN TÊTE
                used_similarity = True
    # ...

    # >> ÉTAPE 5 — VÉRIFICATION DES CHIFFRES (ligne 350) : chaque nombre de la réponse
    # >> doit se retrouver dans les résultats des outils. Sinon -> réponse RETENUE.
    # >> C'est la protection contre les « hallucinations » (chiffres inventés par l'IA).
    check = guardrails.check_citations(answer, evidence)
    missing = check["missing"]
    # ...
    if missing:
        logger.warning("unverified numbers {m} in answer to {q!r}", m=missing, q=question)
        return reply(
            question, VERIFY_FAILED, refused=True, verdict="UNVERIFIED", tool_calls=calls_made,
            sources=sources, warnings=[f"Unverified numbers: {missing}"], withheld_answer=answer,
        )  # fmt: skip
    # >> ÉTAPE 6 — on colle l'avertissement en tête et la mise en garde à la fin
    if prefix:
        answer = f"{prefix}\n\n{answer}"
        warnings.insert(0, prefix)
    if used_similarity:
        answer = f"{answer}\n\n{guardrails.STANDING_CAVEAT}"


# =============================================================================
# BONUS — une fonction RÉCURSIVE du projet : src/chat/guardrails.py, ligne 440
# =============================================================================
# >> Elle « aplatit » un résultat imbriqué (dictionnaires dans des listes dans
# >> des dictionnaires...) en une liste de (chemin, nombre). Elle sert à
# >> l'ÉTAPE 5 : récupérer TOUS les nombres présents dans les résultats.
def _flatten(obj: Any, path: str, out: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """(field path, number) pairs; a row with a "metric" key adds the metric name to its path."""
    # >> CAS DE BASE 1 : booléen ou rien -> on n'ajoute rien
    if isinstance(obj, bool) or obj is None:
        return out
    # >> CAS DE BASE 2 : un nombre -> on l'ajoute avec son chemin
    if isinstance(obj, int | float):
        out.append((path, float(obj)))
    # >> CAS DE BASE 3 : un texte -> on extrait les nombres écrits dedans
    elif isinstance(obj, str):
        out.extend((path, float(x)) for x in _NUMBER_IN_TEXT.findall(obj))
    # >> CAS RÉCURSIF : un dictionnaire -> on rappelle _flatten sur chaque valeur
    elif isinstance(obj, dict):
        base = f"{path}.{obj['metric']}" if isinstance(obj.get("metric"), str) else path
        for k, v in obj.items():
            _flatten(v, f"{base}.{str(k).lower()}", out)
    # >> CAS RÉCURSIF : une liste -> on rappelle _flatten sur chaque élément
    elif isinstance(obj, list | tuple):
        for v in obj:
            _flatten(v, path, out)
    return out
# >> Exemple : _flatten({"goals": 24, "similar": [{"similarity": 0.94}]}, "", [])
# >>   -> [(".goals", 24.0), (".similar.similarity", 0.94)]
# >> Autres fonctions récursives du projet : clean() (src/analytics/chatbot.py:60)
# >> et walk() dans _key_fields (src/chat/conversation.py:87).
