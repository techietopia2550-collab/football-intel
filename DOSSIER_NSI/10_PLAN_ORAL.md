# 10 — Plan de l'oral final (brouillon)

## Sommaire
1. Le plan minute par minute
2. Ce qu'il faut ouvrir à l'écran
3. Gérer le temps
4. Répondre aux questions difficiles

> **Brouillon.** La date et le format exact (durée, présence d'un ordinateur, cadre de l'évaluation) sont à fixer avec ton professeur. La partie « réponse à la problématique » reste **vide exprès** : c'est à toi de la construire après la discussion de demain.

---

## 1. Le plan minute par minute (≈ 13 min + questions)

| Temps | Partie | Contenu | Écran |
|---|---|---|---|
| 0:00-0:30 | **Introduction** | Phrase d'accroche : « Une application affiche 94 % de ressemblance entre deux joueurs. Est-ce que ça veut dire qu'ils se ressemblent ? » Puis la problématique. | Diapo titre |
| 0:30-2:30 | **Présentation du projet** | But (aide au recrutement), données (StatsBomb 2015/16, 4 championnats), architecture en couches. | Schéma des couches (`01_RESUME_SIMPLE.md`, section 3) |
| 2:30-5:30 | **Démonstration** | 1) Page joueur de Kanté → joueurs similaires avec badge *Fair*. 2) Un cas *Weak* (un gardien, ex. Fabiański). 3) Chatbot : « Who plays like Kanté? », puis une question refusée (« Who will win next season? »). | Application Streamlit |
| 5:30-7:30 | **Lien avec le programme NSI** | 3 exemples au choix : SQL (JOIN), POO (`TokenWindow` et sa file), récursivité (`_flatten`). Plus la notion de flottants (`1e-12`). | `03_PROGRAMME_NSI_LIEN.md` |
| 7:30-12:30 | **La problématique et sa réponse** | *À construire.* Repères : sous-questions (`02_PROBLEMATIQUE.md`, section 2), faits mesurés (section 4), choix et justification, limites. | Tableau de précision, code de `apply_similarity_rule` |
| 12:30-13:30 | **Conclusion** | Ce que tu as appris, une limite, une suite possible. | Diapo de fin |

## 2. Ce qu'il faut ouvrir à l'écran (préparer AVANT)

1. L'application déjà lancée : `streamlit run src/app/main.py`. Le chargement prend du temps, ne le fais pas devant le jury.
2. La page de Kanté et la page d'un gardien *Weak*, chacune dans un onglet.
3. Le chatbot : il dépend d'Internet et du service Groq, qui peut être lent (environ 30 s par réponse) ou indisponible. **Prévois des captures d'écran** en secours.
4. Dans l'éditeur, 3 fichiers ouverts : `src/chat/guardrails.py` (ligne 613), `src/app/design.py` (ligne 225), `docs/similarity_thresholds.md`.
5. Un terminal prêt à lancer `pytest tests/test_guardrails.py`.

## 3. Gérer le temps

- Répète avec un chronomètre. La démonstration déborde presque toujours : coupe-la à 3 min, même si tout n'est pas montré.
- Garde en réserve une version courte de chaque partie (une phrase).
- Ne lis pas le code ligne par ligne. Montre **une** ligne importante, par exemple `FAIR_CUT, REFUSE_CUT = 0.60, 0.45`, et explique l'idée.
- Les notions post-bac (ACP, K-Means) : une phrase d'image simple suffit, et le détail seulement si on te le demande.

## 4. Répondre aux questions difficiles

| Situation | Attitude |
|---|---|
| Tu ne connais pas la réponse | « Je ne sais pas. Je pense que ça se trouve dans tel fichier, je peux regarder. » |
| Question sur une notion hors programme | Revenir à l'idée NSI la plus proche (k plus proches voisins, vecteurs, tri). |
| Critique d'un choix | Reconnaître la limite, puis dire pourquoi le choix a été fait et ce que tu changerais. |
| « C'est toi qui as écrit ça ? » | Répondre honnêtement (voir `09_QUESTIONS_PROF.md`, question 19), puis montrer que tu sais expliquer. |
| Le chatbot plante pendant la démo | Garder son calme, passer aux captures d'écran, et en faire un exemple : « c'est pour ça que le code gère ce cas » (`UNAVAILABLE`). |
