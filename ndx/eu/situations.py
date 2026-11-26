"""Situations spéciales : offres publiques (OPA, OPE, OPR, retrait obligatoire), rachats, scissions.

Vous saisissez la situation (prix de l'offre, date de clôture attendue, conditions) à partir de l'avis AMF ou du
communiqué ; le système calcule l'écart avec le cours, son rendement annualisé et le suit jusqu'au dénouement.
Les détections automatiques (cours figé, saut puis immobilité, capitalisation sous la trésorerie) ne sont que des
pistes à vérifier. Fichier : data/private/eu_situations.json (personnel, jamais publié).
Outil d'analyse — pas un conseil en investissement.
"""
from __future__ import annotations

import json
import uuid

import pandas as pd

from ..data import DATA_DIR

FILE = DATA_DIR / "private" / "eu_situations.json"
TYPES = {"opa": "OPA (numéraire)", "ope": "OPE (échange de titres)", "opr": "Offre publique de retrait",
         "ro": "Retrait obligatoire", "rachat": "Offre de rachat d'actions", "scission": "Scission", "autre": "Autre"}


def load() -> list[dict]:
    return json.loads(FILE.read_text(encoding="utf-8")) if FILE.exists() else []


def _save(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add(ticker: str, kind: str, offer_price: float | None, close_date: str | None, notes: str = "",
        source: str = "") -> dict:
    if kind not in TYPES:
        raise ValueError("type : " + ", ".join(TYPES))
    t = ticker.upper().strip()
    if not t.endswith(".PA") and "." not in t:
        t += ".PA"
    it = {"id": uuid.uuid4().hex[:8], "ticker": t, "kind": kind, "offer_price": float(offer_price) if offer_price else None,
          "close_date": close_date or None, "notes": notes.strip()[:1000], "source": source.strip()[:300],
          "added": pd.Timestamp.now().isoformat(timespec="seconds"), "status": "ouverte"}
    items = load()
    items.append(it)
    _save(items)
    return it


def close(item_id: str, outcome: str) -> dict:
    items = load()
    for it in items:
        if it["id"] == item_id:
            it["status"] = "close"
            it["outcome"] = outcome[:300]
            it["closed"] = pd.Timestamp.now().isoformat(timespec="seconds")
            _save(items)
            return it
    raise ValueError("situation introuvable")


def view(prices: dict[str, float], names: dict[str, str] | None = None) -> list[dict]:
    out = []
    today = pd.Timestamp.now().normalize()
    for it in load():
        p = prices.get(it["ticker"])
        r = {**it, "type_label": TYPES.get(it["kind"], it["kind"]), "name": (names or {}).get(it["ticker"], it["ticker"]),
             "price": p}
        if p and it.get("offer_price"):
            spread = it["offer_price"] / p - 1
            r["spread"] = round(spread, 4)
            if it.get("close_date"):
                days = max((pd.Timestamp(it["close_date"]) - today).days, 1)
                r["days"] = days
                r["annualized"] = round((1 + spread) ** (365 / days) - 1, 4) if spread > -1 else None
        out.append(r)
    return sorted(out, key=lambda x: (x["status"] != "ouverte", -(x.get("annualized") or -9)))
