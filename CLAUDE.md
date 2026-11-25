# Volatilistat — règles du desk (lues par Claude Code à chaque session)

Ce dépôt contient un « fonds à une personne » en **paper trading** : le PM (modèle principal) décide quoi tester,
les analystes (`bull-analyst`, `bear-analyst`) argumentent, le comité valide les stratégies hors échantillon,
et **le code** — pas le modèle — décide de ce qui peut atteindre le courtier fictif.

## Règles qui ne se discutent pas
1. **Paper uniquement.** Aucun ordre réel, aucune connexion à un courtier, aucune clé de courtier. Le seul courtier est
   `python3 ndx.py desk buy|sell` (simulation).
2. **Toute décision est écrite au registre**, y compris « ne rien faire » :
   `python3 ndx.py desk note TICKER --decision attendre|rejeter|surveiller --reason "…" --by PM`.
3. **Les limites vivent dans le code** (`data/private/desk/rules.json`, modifiable par l'utilisateur seulement).
   Le hook `.claude/settings.json` refuse tout `desk buy` hors limites et toute écriture directe dans `data/private/desk/`.
   Ne cherche jamais à contourner un refus : explique-le.
4. **Une stratégie non validée hors échantillon** (`python3 ndx.py desk comite`) est plafonnée à 2 % du compte.
   Le screener intraday et le discrétionnaire ne sont jamais validés.
5. **En cas de doute, ne rien faire** et noter pourquoi.
6. Ce n'est pas un conseil en investissement ; l'utilisateur reste seul décideur. Ne jamais envoyer `data/private/` ailleurs.

## Commandes du desk
- `python3 -W ignore ndx.py desk status` : compte, positions, dernières décisions
- `python3 -W ignore ndx.py desk check TICKER --amount 2000 --stop 150 --strategy rs` : contrôle sans exécuter (code 2 = bloqué)
- `python3 -W ignore ndx.py desk buy TICKER --amount … --stop … --target … --strategy … --reason "…" --by PM`
- `python3 -W ignore ndx.py desk sell TICKER --all --reason "…" --by PM`
- `python3 -W ignore ndx.py desk review` : revue des décisions contre le Nasdaq 100 ; les échecs répétés d'une stratégie
  la renvoient au comité
- `python3 -W ignore ndx.py desk comite` : statut hors échantillon de chaque setup
- `python3 -W ignore ndx.py swing` : setups du jour ; le screener intraday est dans l'onglet « Desk » du tableau de bord
