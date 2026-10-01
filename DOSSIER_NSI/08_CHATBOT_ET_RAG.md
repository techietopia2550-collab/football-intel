# 08 — Le chatbot et le RAG

## Sommaire
1. Qu'est-ce que le RAG ?
2. Le trajet d'une question, étape par étape
3. Comment les questions sont classées
4. Comment le seuil 0,60 est appliqué
5. Comment les hallucinations sont évitées
6. Résultats mesurés
7. Lien direct avec la problématique

Code commenté : `code_commenté/conversation_commente.py` et `code_commenté/guardrails_commente.py`. Conception complète : `docs/RAG_DESIGN.md`.

---

## 1. Qu'est-ce que le RAG ?

**RAG** veut dire *Retrieval-Augmented Generation*, en français « génération augmentée par la recherche ». L'idée : **un LLM seul répond de mémoire**. Il a lu Internet, mais peut se tromper ou inventer. Avec le RAG, on l'oblige à **aller chercher l'information dans une source fiable**, puis à rédiger à partir de ce qu'il a trouvé.

| | LLM seul | RAG |
|---|---|---|
| D'où vient l'information ? | De sa « mémoire » (son entraînement) | D'une source fournie au moment de la question |
| Risque d'invention | Élevé | Plus faible, et **vérifiable** |

**Dans Football Intel**, il n'y a pas de documents à fouiller : la « source fiable » est la base de données. Le LLM ne lit jamais la base directement. Il choisit parmi **16 outils** (des fonctions Python de `src/analytics/`, comme `find_similar`, `player_stats` ou `compare_players`), et c'est **Python** qui les exécute. Le projet l'appelle « RAG au sens de l'appel d'outils » (`docs/RAG_DESIGN.md`, section 1).

- **LLM utilisé :** `openai/gpt-oss-120b`, via le service en ligne **Groq** (requêtes HTTPS, clé secrète dans `.env`).
- **Lien NSI :** client/serveur, API, modularité.

## 2. Le trajet d'une question, étape par étape

Exemple : **« Qui joue comme Kanté ? »**

| Étape | Qui agit | Ce qui se passe | Fichier |
|---|---|---|---|
| 1. Règles | Python | Prédiction ? Hors sujet ? Autre saison ? → non, on continue | `guardrails.py:122` |
| 2. Noms | Python | « Kanté » → N'Golo Kanté, trouvé une seule fois → on continue | `guardrails.py:291` |
| 3. Choix d'outil | LLM | Demande `find_similar(player="Kanté")` | `conversation.py`, `tools.py` |
| 4. Exécution | Python | Calcule : Gueye 0,85, Illarramendi 0,77, … | `dispatch.py` → `similarity.py` |
| 5. Seuil | Python | Meilleur score 0,85 ≥ 0,60 → verdict **FAIR**, chaque ligne étiquetée | `guardrails.py:629` |
| 6. Rédaction | LLM | Écrit la réponse à partir du résultat seulement | – |
| 7. Vérification | Python | Chaque nombre de la réponse existe-t-il dans le résultat ? | `guardrails.py:543` |
| 8. Ajouts | Python | Mise en garde permanente + avertissements du moteur | `conversation.py:361-369` |

Le LLM n'intervient qu'aux étapes 3 et 6. **Tout le contrôle est fait par du code Python déterministe**, c'est-à-dire qui donne toujours le même résultat pour la même entrée.

## 3. Comment les questions sont classées

**Avant l'IA, par des règles** (`src/chat/guardrails.py:122`, fonction `_classify`). Ce sont des expressions régulières, c'est-à-dire des motifs de recherche dans le texte :

| Code | Déclencheur (exemples) | Réponse |
|---|---|---|
| `PREDICTION` | « will … win », « next season », « predict » | « Je ne fais pas de prédictions… » |
| `NO_DATA` | « salary », « wages » | « La base n'a pas cette donnée… » |
| `UNMEASURABLE` | « leadership », « mentality », « injuries », « age » | « Les données ne mesurent pas … » |
| `OTHER_SEASON` | une année autre que 2015/2016, « this season », « currently » | « Les données couvrent seulement 2015/16 » |
| `OFF_TOPIC` | aucun mot de football et aucun nom connu | « Je réponds seulement aux questions de football… » |

**Puis sur les noms** (`check_player_names`, `name_refusal`) :

| Code | Cas |
|---|---|
| `NOT_IN_DB` | le nom n'existe pas dans la base (ex. « Erling Haaland », absent en 2015/16) |
| `AMBIGUOUS` | plusieurs joueurs correspondent → le chatbot demande lequel |
| `BELOW_THRESHOLD` | le joueur a moins de 900 minutes → seulement ses totaux |

**Les phrases de refus sont écrites à l'avance** (dictionnaire `REFUSALS`, `guardrails.py:32`). L'IA ne les invente pas.

**La conception prévoyait 7 catégories de questions** (factuelle, similarité, profil, style d'équipe, filtre, comparaison, explication), plus une catégorie « refus » utilisée dans les tests (`docs/RAG_DESIGN.md`, section 2).

## 4. Comment le seuil 0,60 est appliqué

```python
FAIR_CUT, REFUSE_CUT = 0.60, 0.45          # src/chat/guardrails.py:613
```

| Meilleur score | Verdict | Ce que voit l'utilisateur |
|---|---|---|
| aucun candidat | `NO_CANDIDATES` | « Aucun joueur ne reste après tes filtres. » |
| < 0,45 | `REFUSE` | « Pas de correspondance proche… » La liste est cachée. On peut demander à la voir. |
| 0,45 à 0,60 | `WARN` | **Phrase d'avertissement en premier** (« Weak matches only… treat this list as loose leads »), puis la liste |
| ≥ 0,60 | `FAIR` | La liste, chaque ligne marquée Fair ou Weak |

Dans **tous** les cas, la réponse se termine par la mise en garde fixe : *« La similarité est une heuristique (une méthode approximative) sur les données 2015/16 : lors des tests, environ la moitié des correspondances Fair partageaient un rôle et un style. Elle ne dit rien du niveau, de l'âge, du prix ni de l'adaptation. »*

**Pourquoi « avertir » plutôt que « refuser » entre 0,45 et 0,60 ?** Le document de conception compare trois options (`docs/RAG_DESIGN.md`, section 5) :

| Option | Problème noté par le projet |
|---|---|
| A. Répondre avec une mise en garde à la fin | L'utilisateur lit la liste et ne voit pas la mise en garde |
| B. Refuser | On jette une information demandée, alors que la tranche 0,45-0,60 n'était pas moins bonne que 0,60+ dans les tests |
| **C. Répondre ET avertir en premier** (choisie) | Réponses un peu plus longues |

**Point essentiel :** la règle est appliquée **avant** que l'IA ne voie le résultat (`conversation.py:331`). L'IA reçoit une liste déjà triée, coupée et étiquetée, avec la phrase qu'elle **doit** placer en premier.

## 5. Comment les hallucinations sont évitées

Une **hallucination**, c'est quand l'IA invente un fait ou un chiffre avec assurance. Le projet empile plusieurs protections :

1. **Les instructions** (le *prompt* système, `src/chat/prompt.py`) disent : « réponds seulement avec les résultats des outils, ne prédis pas, n'écris jamais un nombre absent des résultats ». Mais on ne fait **pas confiance** à ces instructions seules.
2. **Les refus par règles** : pour les questions dangereuses, l'IA n'est **pas appelée du tout**.
3. **Les noms vérifiés par le code** : impossible de parler d'un joueur absent de la base.
4. **Le seuil appliqué par le code** (section 4).
5. **La vérification des chiffres** (`check_citations`, `guardrails.py:543`) : tous les nombres de la réponse sont extraits et comparés à **tous** les nombres des résultats d'outils (récupérés par la fonction **récursive** `_flatten`). Une tolérance de 0,5 % est acceptée pour les arrondis. **Si un seul nombre est introuvable, la réponse est retenue** et l'utilisateur voit : « Je n'ai pas pu vérifier les nombres de ma réponse. »
6. **Limite de boucle** : au plus 2 tours d'outils (`MAX_TOOL_ROUNDS = 2`), pour éviter que l'IA tourne en rond.
7. **Journal** : chaque question est enregistrée dans `data/processed/chat_logs.jsonl`, pour pouvoir vérifier après coup.

> **Limite honnête :** la vérification contrôle les **nombres**, pas les **phrases**. Une affirmation fausse sans chiffre (« c'est un excellent passeur ») peut passer.

## 6. Résultats mesurés

Source : `docs/CHATBOT_ACCURACY.md`, test de 60 questions entre le 28 septembre et le 1er octobre 2026.

| Mesure | Résultat |
|---|---|
| Bonnes réponses, toutes questions | 37/60 (**62 %**), intervalle 49-73 % |
| Questions de similarité | 5/8 (62 %) |
| Refus corrects (questions qui devaient être refusées) | **10/10 (100 %)** |
| Refus à tort (questions valables refusées) | 13 |
| Réponses dont les chiffres ont passé la vérification | 34/36 (94 %) |
| Temps moyen de réponse | 26,9 s |

**À retenir :** le système refuse **trop souvent** (13 refus à tort) plutôt que d'inventer. C'est un choix : mieux vaut « je ne sais pas » qu'une réponse fausse. Mais il a un coût pour l'utilisateur.

## 7. Lien direct avec la problématique

Le chatbot pose la problématique **deux fois** :
1. **Le seuil sur le score de similarité** : où le placer, et quoi faire en dessous (section 4) ?
2. **La « confiance » dans l'IA elle-même** : on ne peut pas savoir à l'avance si un LLM dira vrai. Le projet ne lui fait donc pas confiance : le code vérifie avant et après.

Questions à discuter :
- Faut-il le même seuil dans l'interface (Fair/Weak, sans refus) et dans le chatbot (avec un refus sous 0,45) ?
- Le refus sous 0,45 ne se déclenche jamais sans filtre (voir `02_PROBLEMATIQUE.md`, section 4) : est-il utile ?
- 13 refus à tort : à partir de quand la prudence devient-elle elle-même une façon de « tromper » l'utilisateur, en lui faisant croire qu'une information n'existe pas ?
