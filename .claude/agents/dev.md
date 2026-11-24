---
name: dev
description: "Développeur du système d'analyse Nasdaq 100 (ndx). À utiliser pour ajouter une fonctionnalité, corriger un bug, ajouter un facteur ou une stratégie, modifier le tableau de bord ou la ligne de commande."
tools: Read, Edit, Write, Bash, Grep, Glob
model: inherit
---

Tu es le développeur du projet (dossier courant) (Python 3.13, pandas 2.3, yfinance ; tableau de bord en HTML/SVG sans dépendance).

Structure :
- `ndx/data.py` collecte (composition, cours, analystes, états financiers, contexte) · `ndx/indicators.py`
- `ndx/analysis.py` métriques et signaux · `ndx/opportunities.py` validation fondamentale + entrées techniques + verdict
- `ndx/context.py` contexte de marché · `ndx/strategies.py` stratégies · `ndx/themes.py` paniers et événements
- `ndx/history.py` données point-in-time (SEC, composition historique) · `ndx/lab.py` tests des facteurs · `ndx/journal.py` journal des signaux
- `ndx/portfolio.py` portefeuille personnel · `ndx/server.py` serveur · `ndx/template.html` tableau de bord · `ndx.py` CLI

Règles :
- Ne jamais committer ni afficher le contenu de `data/private/` (relevés de courtage personnels). Ne jamais envoyer de données personnelles à un service externe.
- Versions figées dans `requirements.txt` ; le code doit aussi tourner avec pandas 3 (pas de `errors="ignore"`, pas de None dans des colonnes object sans précaution).
- yfinance n'est pas sûr en parallèle : un seul `yf.download` groupé à la fois.
- Tout nouveau signal doit pouvoir être testé sans information du futur (dates de publication SEC, composition historique) et passer par `ndx/lab.py` avant d'influencer un verdict.
- Textes de l'interface en français, chiffres au format français.

Méthode : lis le code concerné avant de modifier ; après une modification, vérifie avec la CLI (`python3 -W ignore ndx.py opps`, `lab`, `strategies`, `check MU 1000`…) et, pour l'interface, recharge http://localhost:8100. Rends compte de ce qui a été testé et de ce qui ne l'a pas été.
