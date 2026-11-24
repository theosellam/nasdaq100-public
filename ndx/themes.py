"""Grille de lecture du Nasdaq 100 adaptée au contexte de novembre 2026.

Paniers thématiques (chaîne de valeur de l'IA vs reste de l'indice), expositions politiques /
réglementaires, et chronologie des événements. Tout est modifiable : les événements sont aussi
chargés depuis data/events.json (créé au premier lancement) pour pouvoir en ajouter sans toucher au code.
"""
from __future__ import annotations

import json

from .data import EVENTS_FILE

# Paniers thématiques — un titre appartient à un seul panier (GOOG exclu : doublon de GOOGL).
THEMES: dict[str, dict] = {
    "hyperscalers": {"label": "Hyperscalers (payeurs du capex IA)", "ai": True,
                     "tickers": ["MSFT", "AMZN", "GOOGL", "META"]},
    "neoclouds": {"label": "Néo-clouds IA (financés par dette)", "ai": True,
                  "tickers": ["CRWV", "NBIS"]},
    "ai_compute": {"label": "IA – calcul & réseau", "ai": True,
                   "tickers": ["NVDA", "AMD", "AVGO", "MRVL", "ARM", "ALAB", "LITE"]},
    "memory": {"label": "Mémoire & stockage", "ai": True,
               "tickers": ["MU", "SNDK", "WDC", "STX"]},
    "semi_equip": {"label": "Équipementiers semi", "ai": True,
                   "tickers": ["ASML", "AMAT", "LRCX", "KLAC", "TER"]},
    "semi_other": {"label": "Semi analogiques & mobiles", "ai": False,
                   "tickers": ["QCOM", "INTC", "TXN", "ADI", "MCHP", "NXPI", "MPWR"]},
    "ai_power": {"label": "Énergie des data centers", "ai": True,
                 "tickers": ["CEG", "AEP", "EXC", "XEL", "BKR"]},
    "software": {"label": "Logiciel (risque de disruption IA)", "ai": False,
                 "tickers": ["ADBE", "ADSK", "INTU", "WDAY", "CDNS", "SNPS", "ROP", "TRI", "DDOG",
                             "CRWD", "PANW", "FTNT", "SHOP", "TTWO"]},
    "ai_apps": {"label": "Applications IA / données", "ai": True,
                "tickers": ["PLTR", "APP", "MSTR"]},
    "megacap_other": {"label": "Autres méga-caps", "ai": False,
                      "tickers": ["AAPL", "TSLA", "NFLX", "SPCX"]},
    "consumer": {"label": "Consommation & services", "ai": False,
                 "tickers": ["COST", "WMT", "PEP", "KDP", "MDLZ", "MNST", "CCEP", "SBUX", "ROST", "ORLY",
                             "MAR", "BKNG", "ABNB", "DASH", "MELI", "PDD", "CMCSA", "TMUS", "CSCO"]},
    "health": {"label": "Santé & biotech", "ai": False,
               "tickers": ["AMGN", "GILD", "REGN", "VRTX", "ALNY", "MRNA", "ISRG", "IDXX", "DXCM", "GEHC"]},
    "industrials": {"label": "Industrie, transport & services aux entreprises", "ai": False,
                    "tickers": ["HON", "HONA", "CSX", "ODFL", "PCAR", "FAST", "CTAS", "CPRT", "PAYX", "ADP",
                                "AXON", "RKLB", "LIN", "FER", "FANG", "PYPL"]},
}

# Expositions politiques / réglementaires (un titre peut en avoir plusieurs). Ce sont des expositions
# sectorielles connues, pas des prévisions : elles servent à regrouper les titres sensibles à l'actualité.
POLICY: dict[str, dict] = {
    "datacenter_policy": {"label": "Politique data centers & énergie (États, réseau, permis)",
                          "tickers": ["MSFT", "AMZN", "GOOGL", "META", "CRWV", "NBIS", "CEG", "AEP", "EXC", "XEL",
                                      "NVDA", "AVGO", "AMD"]},
    "export_controls": {"label": "Contrôles à l'export / Chine",
                        "tickers": ["NVDA", "AMD", "AVGO", "QCOM", "MU", "ASML", "AMAT", "LRCX", "KLAC",
                                    "INTC", "MRVL", "TXN", "ADI", "NXPI", "MCHP", "PDD", "AAPL", "TSLA"]},
    "drug_pricing": {"label": "Prix des médicaments", "tickers": ["AMGN", "GILD", "REGN", "VRTX", "ALNY", "MRNA"]},
    "tariffs": {"label": "Droits de douane / chaînes d'approvisionnement",
                "tickers": ["AAPL", "TSLA", "PDD", "COST", "WMT", "ROST", "PCAR", "DXCM", "GEHC", "ORLY"]},
    "gov_contracts": {"label": "Contrats publics / défense",
                      "tickers": ["PLTR", "AXON", "RKLB", "SPCX", "HON", "MSFT", "AMZN"]},
    "antitrust": {"label": "Antitrust / régulation des plateformes",
                  "tickers": ["GOOGL", "META", "AMZN", "AAPL", "MSFT", "NVDA"]},
}

# Chronologie vérifiée (sources presse, novembre 2026). "date" = première séance concernée.
DEFAULT_EVENTS = [
    {"date": "2026-10-06", "label": "Record historique du Nasdaq 100", "kind": "marché"},
    {"date": "2026-11-04", "label": "Lendemain des midterms (vague démocrate)", "kind": "politique"},
    {"date": "2026-11-05", "label": "Pire séance depuis 2020 (selon la presse)", "kind": "marché"},
    {"date": "2026-11-12", "label": "Démission de Jensen Huang (Nvidia)", "kind": "IA"},
    {"date": "2026-11-19", "label": "IPO d'Anthropic (ANT, prix d'introduction 83 $)", "kind": "IA"},
    {"date": "2026-11-23", "label": "Jensen Huang réintégré PDG", "kind": "IA"},
]

ANT_IPO = {"ticker": "ANT", "date": "2026-11-19", "ipo_price": 83.0, "first_open": 111.50,
           "note": "Éligible au Nasdaq 100 après 15 séances de cotation (règle des grandes IPO)."}
MIDTERMS_DATE = "2026-11-04"
RECORD_DATE = "2026-10-06"


def load_events() -> list[dict]:
    if not EVENTS_FILE.exists():
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        EVENTS_FILE.write_text(json.dumps(DEFAULT_EVENTS, ensure_ascii=False, indent=2), encoding="utf-8")
        return list(DEFAULT_EVENTS)
    try:
        return json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return list(DEFAULT_EVENTS)


def theme_of(ticker: str) -> str | None:
    return next((k for k, v in THEMES.items() if ticker in v["tickers"]), None)


def policy_of(ticker: str) -> list[str]:
    return [k for k, v in POLICY.items() if ticker in v["tickers"]]


AI_SUPPLIERS = {"ai_compute", "memory", "semi_equip", "ai_power", "neoclouds"}
