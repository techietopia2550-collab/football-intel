# 06 — Programmation orientée objet (POO) dans le projet

## Sommaire
1. Vue d'ensemble : 78 classes, 5 familles
2. Les classes importantes, une par une
3. Les concepts POO utilisés
4. Ce qui est au programme, ce qui ne l'est pas

---

## 1. Vue d'ensemble : 78 classes, 5 familles

Le projet contient **78 classes** (commande `grep -rn "^class " src`). Elles se rangent en 5 familles :

| Famille | Rôle | Exemples | Nombre |
|---|---|---|---|
| **Tables de la base** (SQLAlchemy) | Une classe = une table SQL | `Player`, `PlayerMatchStats`, `PlayerArchetype` | 12 |
| **Schémas de données** (pydantic) | Décrire et **vérifier** la forme d'un résultat | `SimilarityResult`, `SimilarPlayer`, `SimilarityFilters` | 35 |
| **Modèles de calcul** (`@dataclass`) | Regrouper les résultats d'un calcul | `GroupModel`, `ArchetypeModel` | 12 |
| **Erreurs** (exceptions) | Signaler un problème précis | `GroqError`, `AgeDataUnavailable` | 14 |
| **Autres** (objets « actifs », interface…) | Garder un état et agir | `TokenWindow`, `GroqClient`, `Settings`, `ChatClient` | 5 |

## 2. Les classes importantes, une par une

### 2.1 `TokenWindow`, la classe la plus « NSI » du projet
**Fichier :** `src/chat/llm_client.py:91`. **Rôle :** compter combien de « jetons » (morceaux de mots) le chatbot a envoyés au service Groq dans la **dernière minute**, et attendre si on dépasse le quota.

| Attributs | Méthodes |
|---|---|
| `budget` (jetons autorisés par minute), `window` (60 secondes), `_spent` (une **file** `deque` de couples (heure, jetons)), `blocked_until` | `__init__(…)`, `_trim(now)` (retire les entrées trop anciennes avec `popleft()`), `used()`, `wait_for_room(estimate)`, `record(tokens)` |

```python
fenetre = TokenWindow(budget=8000)
fenetre.record(3000)          # on vient d'envoyer 3 000 jetons
fenetre.used()                # -> 3000
fenetre.wait_for_room(4000)   # 3000 + 4000 <= 8000 : pas d'attente -> 0.0
# 60 secondes plus tard, _trim() a retiré l'entrée : used() -> 0
```
**Structure de données :** une **file** (premier entré, premier sorti). Les plus anciens appels sortent en premier. C'est exactement l'usage vu en cours.

### 2.2 `SimilarityFilters` : une classe qui se vérifie toute seule
**Fichier :** `src/models/similarity.py:8`. **Rôle :** les filtres de la recherche (même équipe, minutes minimum…).

| Attributs | Méthodes |
|---|---|
| `same_team_only`, `exclude_same_team` (booléens), `max_age`, `min_minutes` (`Field(ge=0)` : doit être ≥ 0) | `_team_filters_conflict()` : refuse les deux filtres d'équipe contradictoires |

```python
SimilarityFilters(exclude_same_team=True, min_minutes=1500)   # OK
SimilarityFilters(min_minutes=-5)                              # ValidationError
SimilarityFilters(same_team_only=True, exclude_same_team=True) # ValidationError
```
**Intérêt :** une donnée incorrecte est refusée **dès sa création**. C'est de la gestion des bugs par le typage.

### 2.3 `SimilarityResult` et `SimilarPlayer` : le résultat de la recherche
**Fichier :** `src/models/similarity.py:47` et `:35`. **Rôle :** le résultat complet de `find_similar`. Il contient la liste des joueurs similaires, mais aussi **les informations qui servent à la confiance** : `cluster_confidence`, `group_size`, `features_dropped`, `explained_variance` et `warnings` (liste d'avertissements).
Chaque `SimilarPlayer` a `rank`, `player_name`, `team`, `similarity` (entre -1 et 1), `shared_metrics` et `differing_metrics` (les 3 statistiques qui rapprochent ou éloignent les deux joueurs).

> **Lien avec la problématique :** l'objet transporte **le score ET ses limites**. L'affichage et le chatbot peuvent ainsi prévenir l'utilisateur.

### 2.4 `GroupModel` : le modèle de calcul de similarité
**Fichier :** `src/analytics/similarity.py:126`. Écrit avec `@dataclass`, un raccourci de Python qui écrit `__init__` automatiquement.

| Attributs | Méthode / propriété |
|---|---|
| `members` (les joueurs), `features` (statistiques gardées), `dropped`, `z` (tableau centré-réduit), `pca`, `scores`, `explained_variance`, `_cache` | `projection` (`@property` : se lit comme un attribut, mais est **calculée** à la première lecture puis gardée dans `_cache`) |

Créé par `fit_group(members, "Destroyer", features)` (`code_commenté/similarite_commente.py`).

### 2.5 `Player` et les autres tables
**Fichier :** `src/models/schema.py:39`. **Rôle :** une ligne de la table `players`.

```python
class Player(Base):                      # hérite de Base
    __tablename__ = "players"            # nom de la table SQL
    player_id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    …
```
```python
kante = Player(player_id="statsbomb:3961", canonical_name="N'Golo Kanté")
kante.canonical_name     # -> "N'Golo Kanté"
```
On manipule un **objet Python**, et SQLAlchemy écrit le SQL. C'est ce qu'on appelle un **ORM** (correspondance objet-relationnel).

### 2.6 La famille des erreurs du chatbot
**Fichier :** `src/chat/llm_client.py:40-66`.

```
RuntimeError            (classe de Python)
 └── GroqError          toute erreur avec le service Groq
      ├── GroqAuthError         clé absente ou fausse (erreur HTTP 401)
      ├── GroqRateLimitError    trop de requêtes (429) ; attribut wait_seconds
      └── GroqToolCallError     appel d'outil refusé (400) ; attribut detail
```
```python
e = GroqRateLimitError("trop de requêtes", wait_seconds=5)
isinstance(e, GroqError)   # -> True : une GroqRateLimitError EST une GroqError
```
**Intérêt :** dans `conversation.py`, `except GroqRateLimitError` traite le cas « attendre ». Un `except GroqError` placé ensuite attrape **toutes** les autres erreurs Groq.

### 2.7 `ChatClient` : une interface
**Fichier :** `src/chat/conversation.py:70`. `ChatClient(Protocol)` dit seulement : « un client de chat doit avoir une méthode `chat(messages, tools, …)` ». Il n'y a **aucun code** dedans.
- `GroqClient` (`llm_client.py:139`) est la vraie implémentation (requêtes HTTPS vers Groq).
- Les tests (`tests/test_conversation.py`) passent un **faux client** qui renvoie des réponses écrites à l'avance, sans Internet et sans frais.

C'est exactement la distinction **interface / implémentation** du programme.

## 3. Les concepts POO utilisés

| Concept | Définition simple | Exemple dans le projet |
|---|---|---|
| **Classe / objet** | La classe est le « moule », l'objet est un exemplaire fabriqué avec | `TokenWindow` / `fenetre = TokenWindow(8000)` |
| **Attribut / méthode** | Donnée / fonction attachée à l'objet | `fenetre.budget` / `fenetre.used()` |
| **`__init__`** | Méthode appelée à la création, qui remplit les attributs | `TokenWindow.__init__`, `GroqClient.__init__` (vérifie que la clé n'est pas vide) |
| **`__str__` / `__repr__`** | Texte affiché par `print()` / dans la console | Écrits automatiquement par pydantic et `@dataclass` : `SimilarityFilters(same_team_only=False, …)` |
| **Encapsulation** | Cacher les détails internes, ne montrer qu'une interface | Attributs préfixés par `_` (`_spent`, `_cache`, `_api_key`) : « ne pas toucher de l'extérieur ». En Python, c'est une **convention**, pas une interdiction. |
| **Héritage** | Une classe reprend tout d'une autre et ajoute des choses | `GroqRateLimitError(GroqError)`, `Player(Base)`, `SimilarityResult(BaseModel)` |
| **Polymorphisme** | Même appel, comportement différent selon l'objet | `llm.chat(...)` marche avec `GroqClient` ou avec le faux client des tests |
| **Propriété** | Méthode qui se lit comme un attribut | `GroupModel.projection` |
| **Attribut de classe** | Partagé par tous les objets | `Player.__tablename__` |

## 4. Ce qui est au programme, ce qui ne l'est pas

- **Au programme de Terminale :** classe, attribut, méthode, objet, `__init__`, interface et implémentation.
- **Hors programme** (le BO dit qu'on n'aborde pas l'héritage ni le polymorphisme) : héritage, polymorphisme, `@dataclass`, `Protocol`, `@property`, ORM.
- **Conseil pour l'oral :** commence par `TokenWindow` (classe + file) et `SimilarityFilters` (vérification des données). Ne parle de l'héritage que si le professeur le demande, en expliquant le principe avec les mots simples du tableau.

Version NSI simplifiée (classes `Joueur` et `Gardien`) : `04_FONCTIONS_NSI.md`, section 6.
