# Volatilistat

**L'État des valeurs tech : chaque signal passe la douane du laboratoire.**

Analyse en direct des ~100 titres du Nasdaq 100 (^NDX) : cours (séance, pré-ouverture, après-clôture, overnight via
le future NQ=F), analystes, fondamentaux, opportunités validées, stratégies, swing trading, laboratoire de signaux
testés sans biais du futur, et suivi de **votre** portefeuille. Tout tourne sur votre ordinateur, dans votre navigateur.

> Outil d'analyse uniquement — **pas un conseil en investissement**. Données Yahoo Finance (non officielles,
> parfois en retard ou incomplètes), SEC EDGAR et Wikipedia.
> Nasdaq, Nasdaq-100 et NDX sont des marques de Nasdaq, Inc. Volatilistat est un projet indépendant, ni affilié à Nasdaq ni approuvé par elle.

## Version application (sans installer Python)

Dans la page **Releases** du dépôt (colonne de droite sur GitHub), téléchargez le fichier qui correspond à votre ordinateur :
`Volatilistat-Windows.zip`, `Volatilistat-Mac-AppleSilicon.zip` (Mac M1, M2, M3…) ou `Volatilistat-Mac-Intel.zip`.

- **Mac** : décompressez, glissez **Volatilistat** dans le dossier Applications et ouvrez-la. L'application n'est pas signée
  par un développeur Apple : au premier lancement, macOS la bloque. Ouvrez alors **Réglages Système → Confidentialité
  et sécurité**, puis cliquez sur **Ouvrir quand même** en bas de la page (une seule fois).
- **Windows** : clic droit sur le ZIP → **Extraire tout**, puis double-clic sur **Volatilistat.exe** dans le dossier extrait.
  Si « Windows a protégé votre ordinateur » s'affiche : **Informations complémentaires → Exécuter quand même**.

Une petite fenêtre « Volatilistat » s'ouvre avec le navigateur : gardez-la ouverte pendant l'utilisation, **Quitter** arrête tout.
Les données (dont vos positions) sont enregistrées dans `~/Library/Application Support/Volatilistat` (Mac)
ou `%APPDATA%\Volatilistat` (Windows), bouton **Dossier des données** de la fenêtre.

Pour publier une nouvelle version de l'application : `git tag v1.1 && git push --tags` — GitHub construit les trois
versions (onglet **Actions**, ≈ 10 min) et les ajoute à la Release. Construction locale sur Mac : `bash packaging/build-mac.sh`.

## Installation à partir du code (Python requis)

Il faut **Python 3.11 à 3.13** (gratuit) et une connexion Internet.

### Mac
1. Installez Python 3.13 depuis [python.org](https://www.python.org/downloads/macos/) (celui livré avec macOS est trop ancien).
2. Téléchargez ce dépôt (bouton vert **Code → Download ZIP**) et décompressez-le, par exemple dans `Documents`.
3. Double-cliquez sur **`installer-mac.command`** (une seule fois, ≈ 1 min).
   Si macOS refuse d'ouvrir le fichier : clic droit → **Ouvrir** → **Ouvrir**.
4. Ensuite, à chaque utilisation : double-clic sur **`lancer-mac.command`**. Le navigateur s'ouvre sur http://localhost:8100.

En ligne de commande (équivalent) :
```bash
cd nasdaq100 && bash installer-mac.command && bash lancer-mac.command
```

### Windows
1. Installez Python 3.13 depuis [python.org](https://www.python.org/downloads/windows/) en cochant
   **« Add python.exe to PATH »** sur le premier écran de l'installateur.
2. Téléchargez ce dépôt (**Code → Download ZIP**), puis clic droit sur le ZIP → **Extraire tout**.
3. Double-cliquez sur **`installer-windows.bat`** (une seule fois). Si Windows affiche « Windows a protégé votre ordinateur » :
   **Informations complémentaires → Exécuter quand même**.
4. Ensuite, à chaque utilisation : double-clic sur **`lancer-windows.bat`**. Le navigateur s'ouvre sur http://localhost:8100.

### Premier lancement
- Les données de marché sont téléchargées au premier démarrage (≈ 1 à 2 min avant l'affichage) ;
  les prévisions des analystes et les états financiers se complètent ensuite en arrière-plan.
- Le **laboratoire** (historique SEC, composition historique de l'indice, tests des facteurs et des setups swing)
  se construit en tâche de fond (≈ 5 à 10 min la première fois) ; les onglets concernés l'indiquent en attendant.
- Pour arrêter : fermez la fenêtre du terminal (ou Ctrl+C). Les cours se rafraîchissent automatiquement toutes les 60 s.

## Mon portefeuille : saisir vos actions

Onglet **Mon portefeuille** → **Mes positions** :
- une ligne par titre : **symbole Yahoo Finance** (`NVDA`, `GOOGL`, `MC.PA` pour Paris, `SAP.DE` pour Francfort…),
  quantité, **prix de revient unitaire** dans la devise du titre (USD ou EUR), et en option le nom, le thème
  et la case « levier » (turbo, warrant, ETF à levier) ;
- vos **liquidités** en euros ;
- **Enregistrer** : tout le portefeuille est recalculé (valeur en euros aux cours du jour, P/L, poids, exposition par thème
  comparée au Nasdaq 100, verdict du système sur chaque ligne, garde-fous dépassés, liste de vérification avant achat).
  Les titres hors Nasdaq 100 sont analysés avec les mêmes règles.

Les positions sont enregistrées **uniquement sur votre ordinateur**, dans `data/private/positions.json`
(dossier exclu de git : il n'est jamais envoyé sur GitHub). Le prix de revient des titres en dollars est converti
en euros au cours du jour (le P/L affiché reflète donc la variation du titre, hors effet de change).

**Garde-fous** : `data/private/rules.json` est créé au premier enregistrement avec des valeurs de départ
(poids maximal par ligne 10 %, levier 5 %, liquidités minimales 5 %, 25 lignes, chaîne IA 60 %,
revue de la thèse à −25 %…). Modifiez-le avec un éditeur de texte pour fixer vos propres limites.

**Clients Saxo Bank (facultatif)** : déposez vos exports `AggregatedAmounts_*.xlsx` (flux par instrument) et
`Holdings_*.pdf` (relevé des positions) dans `data/private/imports/`. Ils remplacent alors la saisie manuelle et ajoutent
le **profil de trading** (levier, pertes laissées courir, renforcements en perte, rafales d'opérations…),
la courbe de valeur du compte et vos achats passés reclassés selon les signaux de l'époque.

## Agents Claude Code (facultatif)
Le dossier `.claude/` contient des agents pour [Claude Code](https://claude.com/claude-code) : `bull-analyst`, `bear-analyst`,
`risk-manager`, `pm` et `dev`, plus la commande `/comite TICKER [montant]` qui réunit un comité d'investissement
(thèses haussière et baissière, contrôle du risque sur votre portefeuille, décision argumentée).
Ouvrez Claude Code dans le dossier du projet pour les utiliser.

## Ligne de commande

```bash
# Mac : .venv/bin/python ndx.py …   ·   Windows : .venv\\Scripts\\python ndx.py …
python3 ndx.py update              # tout retélécharger (composition, cours, capitalisations, analystes)
python3 ndx.py summary --live      # synthèse avec les derniers cours
python3 ndx.py oversold --h 5      # plus survendus sur 5 séances (aussi 10, 30)
python3 ndx.py stock NVDA --live   # fiche complète d'un titre
python3 ndx.py screen              # tous les signaux
python3 ndx.py rank --by upside -n 20
python3 ndx.py opps [--all]        # entrées techniques validées par les fondamentaux (+ pièges, + à surveiller)
python3 ndx.py check NVDA          # grille de validation fondamentale complète d'un titre + plan d'entrée
python3 ndx.py context --live      # régime, plus hauts/plus bas, paniers IA, capex des hyperscalers, événements, ANT
python3 ndx.py analysts            # recharger uniquement les prévisions des analystes
python3 ndx.py fundamentals        # recharger uniquement les états financiers trimestriels
python3 ndx.py report              # export HTML statique (reports/nasdaq100.html)
```

## Laboratoire de signaux (onglet « Laboratoire », `python3 ndx.py lab`)
Teste chaque signal sur l'historique **sans information du futur** :
- composition historique du Nasdaq 100 (Wikipedia, trimestrielle depuis 2020) pour limiter le biais du survivant ;
- comptes trimestriels **SEC EDGAR** avec leur date de dépôt, historique des avis d'analystes et des résultats (Yahoo) ;
- 23 facteurs mensuels (valorisation, qualité, croissance, financement, analystes, surprises, technique, taux) ;
  IC, t-stat, écart meilleur − pire quintile, par régime (taux, stress, leadership IA) ;
- score composite à **pondération égale** (les pondérations apprises échouent hors échantillon, comparaison affichée) ;
- étude des points d'entrée techniques avec / sans validation fondamentale et selon la valorisation ;
- **journal quotidien** des signaux (`data/journal/`) : performance réelle à 5, 20 et 60 séances, sans aucun biais ;
- vos achats passés (relevés Saxo) reclassés selon les signaux de l'époque, et **liste de vérification avant achat**
  (onglet Mon portefeuille, ou `python3 ndx.py check MU 1000`).
Historique complet reconstruit chaque semaine (`python3 ndx.py lab --full`, ≈ 4 min), recalcul quotidien automatique.
Conséquence intégrée au moteur : une « Entrée validée » exige un score factoriel hors des 40 % les plus faibles.

## Stratégies (onglet « Stratégies », `python3 ndx.py strategies`)
Trois stratégies adaptées au contexte de fin 2026 (dégonflement du trade IA, inflation qui résiste, Fed restrictive),
avec règles explicites, score, plan d'entrée, « presque » (une règle manquante) et test historique du mécanisme de prix :
1. **Rattrapage post-retour de Jensen** : chaîne IA, fondamentaux validés, sous-évalué vs son panier, potentiel ≥ 15 %,
   rebond depuis le creux de l'absence inférieur à ce que son bêta prévoit, creux tenu.
2. **Qualité résistante à une Fed restrictive** : trésorerie nette, marge brute ≥ 45 %, marge FCF ≥ 15 %, faible
   réaction aux hausses du taux 10 ans, a mieux résisté que l'indice, acheté sur repli.
3. **IA à prix raisonnable** : BPA attendu ≥ +15 %, PEG ≤ 1 ou PER ≤ 22, FCF positif, ≥ 25 % sous le plus haut,
   estimations pas en effondrement, aucun drapeau rouge.
Plus une liste « à éviter en régime de taux hauts ». Repères : record (6/10), veille du départ de Jensen (11/11),
creux de l'absence, J1 du retour (23/11), maintenant. Le contexte macro est dans `ndx/strategies.py` (MACRO).

## Méthode
- **Opportunités** : un point d'entrée technique (pullback en tendance, test de la MM200 montante, survente extrême,
  reprise confirmée après survente, cassure de plus haut avec volume) n'est retenu QUE si la société passe la validation
  fondamentale : score ≥ 65/100 sur 6 piliers (analystes 22 %, croissance & contrats 18 %, rentabilité 20 %,
  financement 22 %, clients & débouchés 10 %, valorisation 8 %), aucun drapeau rouge, et gain/risque ≥ 2.
  Drapeaux rouges : brûle du cash en étant endetté, intérêts couverts < 2×, dilution > 8 %, CA en chute > 10 %,
  consensus négatif, dette en forte hausse avec FCF négatif. Un signal technique sans fondamentaux = « piège potentiel ».
  Contrats : revenus différés (facturés d'avance). Clients : pour la chaîne IA, solvabilité des hyperscalers et néo-clouds.
  Plan : stop sous le plus bas 10 séances (−3 % à −25 %), objectif = objectif analystes ou plus haut 52 s,
  taille pour 1 % de capital risqué, divisée par deux en régime de stress (VXN > 30, indice sous MM200 ou à −10 % du record).
- **Contexte (novembre 2026)** : record du 6 octobre, midterms du 3 novembre, démission (12/11) puis retour (23/11) de
  Jensen Huang, IPO d'Anthropic (ANT, 19/11). Paniers thématiques (`ndx/themes.py`), expositions politiques,
  capex/flux de trésorerie des hyperscalers, réaction aux événements (`data/events.json`, modifiable).
- **Survente sur 5, 10 et 30 séances** : performance, z-score (baisse ÷ volatilité attendue sur la période),
  RSI 2 (5 j) ou RSI 14 (10 et 30 j), écart à la MM20 ou à la MM50, séances consécutives, plus bas atteint.
  Score 0–100 = z-score 55 % + RSI 25 % + écart à la moyenne 20 % (rang relatif). Signal « survendu » si z ≤ −1,5 ou RSI sous le seuil.
- **Analystes** : objectifs moyen, médian, bas et haut ; potentiel ; consensus et répartition des avis (mois en cours et 3 mois) ;
  relèvements et abaissements ; révisions d'objectifs sur 30 j ; estimations de BPA et de CA ; prochains résultats.
  Score analystes = potentiel 40 % + consensus 30 % + révisions 15 % + évolution des avis 15 %.
  Objectif implicite de l'indice = niveau × (1 + potentiel moyen pondéré par capitalisation).
- **Score technique** : momentum 50 %, tendance 30 %, faible volatilité 20 %.
- **Valorisation** : PER prévisionnel (recalculé au dernier cours), PER, PEG, cours/CA, marge, rendement.
- **Ampleur** : hausses/baisses, % au-dessus des MM50/MM200, nombre de titres en survente/surachat (historique 1 an).

Structure : `ndx/data.py` (collecte), `ndx/indicators.py`, `ndx/analysis.py`, `ndx/server.py` (serveur en direct),
`ndx/report.py` + `ndx/template.html` (tableau de bord).
Outil d'analyse uniquement — pas un conseil en investissement.

## Confidentialité
- Aucune donnée personnelle ne quitte votre ordinateur : le programme interroge seulement Yahoo Finance, SEC EDGAR
  et Wikipedia pour les données de marché, avec des requêtes anonymes.
- `data/private/` (positions, garde-fous, relevés) est exclu de git. Si vous publiez votre propre copie, vérifiez
  avec `git status` qu'aucun fichier de ce dossier n'apparaît.
