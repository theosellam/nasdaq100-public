"""Desk Europe : même moteur que le desk Nasdaq 100 (risk manager en code, courtier fictif, registre), avec
son propre compte en euros, ses propres limites et une règle de liquidité adaptée aux petites capitalisations.

Fichiers : data/private/desk_eu/ (rules.json, fills.csv, decisions.jsonl, equity.csv).
"""
from __future__ import annotations

import importlib.util

from ..data import DATA_DIR
from .model import BENCH

# Copie indépendante du module ndx.desk (chemins et réglages propres). On passe par le chargeur du paquet,
# et non par le fichier source : l'application compilée ne contient que la version compilée de desk.py.
_spec = importlib.util.find_spec("ndx.desk")
engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine)

engine.DESK_DIR = DATA_DIR / "private" / "desk_eu"
engine.RULES_FILE = engine.DESK_DIR / "rules.json"
engine.FILLS = engine.DESK_DIR / "fills.csv"
engine.DECISIONS = engine.DESK_DIR / "decisions.jsonl"
engine.EQUITY = engine.DESK_DIR / "equity.csv"
engine.INDEX_TICKER = BENCH
engine.CUR = "€"
engine.DOT_TO_DASH = False
engine.SLIPPAGE = 0.003                    # petites capitalisations : 30 pb contre nous à chaque ordre
engine.DEFAULT_RULES = {
    "_note": "Limites du desk Europe (paper trading, euros). Appliquées par le code : un ordre qui les dépasse est refusé.",
    "capital_depart": 100000,
    "max_poids_ligne": 0.06,           # stratégie validée par le laboratoire
    "max_poids_non_valide": 0.03,      # situation spéciale, discrétionnaire, stratégie non validée
    "risque_max_trade": 0.015,         # perte au stop / compte (si un stop est fixé)
    "stop_obligatoire": False,         # investissement valeur : pas de stop imposé, mais taille limitée
    "max_positions": 25,
    "min_liquidites": 0.05,
    "max_ordres_semaine": 8,
    "pause_apres_perte_jour": -0.04,
    "jours_sans_resultats": 0,
    "max_part_volume_ordre": 0.10,     # un ordre ≤ 10 % des échanges quotidiens (médiane 60 séances)
    "max_position_volume": 0.5,        # une position ≤ 50 % des échanges quotidiens : sortie en ~5 séances
}

STRATEGIES = [
    {"key": "valeur_qualite", "label": "Valeur et qualité (score du laboratoire)"},
    {"key": "situation_speciale", "label": "Situation spéciale (offre, retrait, rachat…)"},
    {"key": "tresorerie_nette", "label": "Décote sur trésorerie nette"},
    {"key": "discrétionnaire", "label": "Discrétionnaire"},
]
