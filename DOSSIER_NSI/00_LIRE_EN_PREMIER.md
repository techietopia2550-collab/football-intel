# 00 — Lire en premier

**Dossier NSI — projet Football Intel**
Problématique : *« Comment implémenter un seuil de confiance dans un algorithme de recherche par similarité pour éviter de tromper l'utilisateur ? »*
Préparé le 1er octobre 2026, pour la réunion de discussion avec le professeur. Les chiffres et les numéros de ligne correspondent au commit `7e94ddb`.

---

## Par où commencer (ce soir, environ 2 h)

1. **`01_RESUME_SIMPLE.md`** (15 min) : le projet en clair, et le **lexique** des mots techniques.
2. **`02_PROBLEMATIQUE.md`** (30 min) : **le plus important pour demain.** Les sous-questions, les faits mesurés et les questions à poser au prof.
3. **`code_commenté/guardrails_commente.py`** (15 min) : le code du seuil, ligne par ligne.
4. **`09_QUESTIONS_PROF.md`** (30 min) : les questions générales qu'il pourrait poser.
5. Le reste plus tard, selon les questions du professeur.

## Ce que contient chaque fichier

| Fichier | Contenu | Pour demain ? |
|---|---|---|
| `00_LIRE_EN_PREMIER.md` | Ce guide | ✔ |
| `01_RESUME_SIMPLE.md` | Le projet expliqué simplement + lexique | ✔ |
| `02_PROBLEMATIQUE.md` | Découpage de la problématique, faits, questions ouvertes, pistes | ✔✔ |
| `03_PROGRAMME_NSI_LIEN.md` | Tableau programme NSI ↔ projet ↔ fichier/ligne | ✔ (à montrer) |
| `04_FONCTIONS_NSI.md` | 6 fonctions de niveau NSI (récursivité, tri, dichotomie, cosinus, dictionnaires, classe) | plus tard |
| `05_SQL_ET_BASE_DE_DONNEES.md` | Schéma, normalisation, 10 requêtes testées | plus tard |
| `06_POO_PYTHON.md` | Les classes du projet et les notions de POO | plus tard |
| `07_ALGORITHMES_CLES.md` | Cosinus, ACP, K-Means, noms flous, garde-fous + complexités | si question technique |
| `08_CHATBOT_ET_RAG.md` | Le chatbot, le RAG, le seuil, les hallucinations | si question chatbot |
| `09_QUESTIONS_PROF.md` | 20 questions probables + éléments de réponse | ✔ |
| `10_PLAN_ORAL.md` | Brouillon du plan de l'oral final | plus tard, avec le prof |
| `code_commenté/` | Extraits du **vrai** code, commentés en français | ✔ (guardrails) |
| `ressources/programme_nsi_2025.md` | Programme officiel et textes de l'épreuve, avec les sources | si besoin |
| `pdf/` | Les mêmes documents en PDF | pour imprimer |

## Comment utiliser ce dossier demain

- **Ton professeur n'a vu le projet que de loin.** Commence par l'idée (`01`, section 2), puis la problématique (`02`), et ensuite seulement le code.
- **Imprime ou ouvre `02_PROBLEMATIQUE.md`**. La section 6 contient les questions à **lui** poser : c'est une discussion, pas un examen.
- **Montre `03_PROGRAMME_NSI_LIEN.md`** s'il demande « qu'est-ce que ça a à voir avec le programme ? ».
- Note ses réponses : elles décideront de la suite (périmètre, niveau de détail, format de l'oral).

## Ce qui vient du projet / ce qui a été écrit pour le dossier

C'est important pour rester honnête devant le professeur.

| Source | Où |
|---|---|
| **Vrai code du projet** (copié sans modification, seuls des commentaires `# >>` ont été ajoutés) | `code_commenté/similarite_commente.py`, `archetypes_commente.py`, `guardrails_commente.py`, `conversation_commente.py`, `bd_schema_commente.sql` |
| **Écrit pour le dossier** (versions simplifiées, niveau NSI, pas dans le projet) | `code_commenté/fonctions_nsi.py`, `code_commenté/requetes_sql.sql`, sections « version NSI » de `04` |
| **Mesures faites pour le dossier** (le 1er octobre 2026, sur la vraie base) | Nombre de joueurs sous 0,60 / 0,45, résultats des 10 requêtes SQL, exemples Vardy / Kanté / Busquets / Özil |
| **Chiffres du projet** (documents existants) | Précision par seuil (`docs/similarity_thresholds.md`), précision du chatbot (`docs/CHATBOT_ACCURACY.md`) |

> **À savoir avant la réunion :** dans l'historique Git du projet, tous les commits portent la mention « Co-Authored-By: Claude » (assistant IA de programmation). Si la question de l'aide d'une IA se pose, réponds franchement (voir `09_QUESTIONS_PROF.md`, question 19). Ce dossier sert à **comprendre** ton code pour pouvoir l'expliquer, pas à le présenter autrement qu'il n'est.

## Les 3 points clés à retenir pour demain

1. **Le score n'est pas une probabilité.** « 94 % » est un cosinus. Les mesures montrent environ 50 % de bonnes paires, quel que soit le seuil. C'est le constat de départ de ta problématique.
2. **Le seuil 0,60 a été mesuré, pas inventé.** La règle a été fixée avant les mesures, et les niveaux *Strong* et *Good* ont été retirés parce que les données ne les justifiaient pas. Limites : 100 paires seulement, étiquetées par une IA.
3. **Le seuil est appliqué par le code, à deux endroits.** Dans l'interface, un badge *Fair*/*Weak*. Dans le chatbot, avertir sous 0,60 et refuser sous 0,45, **avant** que l'IA ne voie la liste.

## Conventions du dossier

- Les mots techniques sont expliqués une fois, dans le lexique de `01_RESUME_SIMPLE.md`, section 6.
- `fichier.py:123` = fichier `fichier.py`, ligne 123.
- Les scores sont écrits avec une virgule (0,60) dans le texte et avec un point (0.60) dans le code.
