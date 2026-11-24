---
description: "Comité d'investissement sur un titre — bull, bear et risk manager en parallèle, puis décision du PM. Usage : /comite TICKER [montant €]"
argument-hint: "TICKER [montant €]"
---

Réunis le comité d'investissement sur : $ARGUMENTS

1. Lance EN PARALLÈLE trois sous-agents :
   - `bull-analyst` : thèse haussière sur le titre ;
   - `bear-analyst` : thèse baissière sur le titre ;
   - `risk-manager` : contrôle de la position pour le portefeuille (avec le montant s'il est donné, sinon 1 000 €).
2. Quand les trois ont répondu, lance le sous-agent `pm` en lui transmettant les trois rapports complets.
3. Présente ensuite à l'utilisateur, en français :
   - la décision du PM en une ligne, puis sa note ;
   - un tableau « Bull / Bear » des 3 arguments principaux de chaque côté ;
   - le verdict du risk manager et ses conditions ;
   - les sources citées par les analystes.
Rappelle que la décision finale appartient à l'utilisateur.
