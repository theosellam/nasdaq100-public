"""Ligne de commande « python3 ndx.py eu … » (petites et moyennes capitalisations françaises).

  eu update                    données (Euronext, ESEF, GLEIF, Yahoo) puis laboratoire
  eu lab                       recalcule et affiche le laboratoire
  eu screen [-n 30] [--all]    meilleurs scores valeur/qualité parmi les titres éligibles
  eu stock MC                  fiche d'un titre
  eu situations                situations spéciales suivies + pistes automatiques
  eu situation-add MC --type opa --price 52.5 --close 2027-02-15 --notes "…" --source "avis AMF …"
  eu status | check | buy | sell | note | review     desk Europe (mêmes options que « ndx.py desk »)
"""
from __future__ import annotations

import sys

import pandas as pd

from . import model, situations, sources
from .desk_eu import engine as desk
from .service import EUROPE


def _pct(v, d=1):
    return "—" if v is None or pd.isna(v) else f"{v * 100:+.{d}f} %"


def _m(v):
    return "—" if v is None or pd.isna(v) else f"{v / 1e6:,.0f} M€".replace(",", " ")


def _t(x: str | None) -> str:
    x = (x or "").upper().strip()
    return x if not x or "." in x else x + ".PA"


def run(a) -> int:
    act = a.action
    if act == "update":
        sources.build()
        EUROPE.load()
        lab = EUROPE.run_lab()
        print(f"Laboratoire : {lab['verdict']} — {lab['why']}")
        return 0
    EUROPE.ensure()
    if EUROPE.data is None:
        print("Données européennes absentes : lancez « python3 ndx.py eu update » (≈ 45 à 75 min la première fois, quelques minutes ensuite).")
        return 1
    S = EUROPE.snap
    if act == "lab":
        lab = EUROPE.run_lab()
        print(f"Laboratoire valeur/qualité — {lab['verdict']} : {lab['why']}")
        for p in lab["periods"]:
            print(f"  {p['start']} → {p['end']}  {p['n']:>4} titres  meilleurs {p['k']:>3} : {_pct(p['top_net'])} net"
                  f"  ensemble {_pct(p['universe'])} (médiane {_pct(p['universe_median'])})  écart {_pct(p['excess_net'])}  écart robuste {_pct(p['excess_robust'])}  IC {p['ic']:+.2f}")
        print("Par facteur (tiers supérieur − ensemble, après frais, moyenne annuelle) :")
        for f in lab.get("factors", []):
            print(f"  {f['label']:32} {_pct(f['excess'])}  positif {f['positive']}/{f['n']}")
        return 0
    if act == "screen":
        E = S[S["eligible"]] if not a.all else S
        E = E.sort_values("score", ascending=False, na_position="last").head(a.n)
        for r in E.itertuples():
            print(f"  {r.ticker:10} {str(r.name)[:26]:26} {r.market:6} capi {_m(r.cap):>9}  liq. {(r.adv or 0) / 1e3:>6,.0f} k€/j"
                  f"  FCF {_pct(r.fcf_yield)}  ROE {_pct(r.roe)}  score {r.score if pd.notna(r.score) else float('nan'):.2f}")
        return 0
    if act == "stock":
        t = _t(a.ticker)
        r = S[S["ticker"] == t]
        if r.empty:
            print("Titre introuvable (mnémonique Euronext, ex. MC ou ALCLA).")
            return 1
        r = r.iloc[0]
        for k in ("name", "market", "price", "cap", "adv", "fy_end", "revenue", "net_income", "fcf", "net_cash",
                  *model.FACTORS, "rev_growth", "score", "score_pct", "eligible"):
            print(f"  {k:14} {r.get(k)}")
        print("  alertes      ", ", ".join(model.flags(r)) or "aucune")
        return 0
    if act == "situations":
        for s in situations.view(EUROPE.prices(), dict(zip(S["ticker"], S["name"]))):
            print(f"  [{s['status']}] {s['ticker']:10} {s['type_label']:30} offre {s.get('offer_price')}  cours {s.get('price')}"
                  f"  écart {_pct(s.get('spread'))}  annualisé {_pct(s.get('annualized'))}  {s.get('notes', '')[:50]}")
        for t, f in model.special_flags(EUROPE.data["close"], S).items():
            print(f"  piste {t:10} {'; '.join(f)}")
        return 0
    if act == "situation-add":
        it = situations.add(a.ticker, a.type, a.price, a.close, a.notes or "", a.source or "")
        print(f"✓ Situation ajoutée ({it['id']})")
        return 0
    prices = EUROPE.prices()
    if act == "status":
        b = desk.book(prices)
        print(f"Desk Europe : {b['equity']:,.0f} €  (départ {b['start']:,.0f} €, {b['pl_total_pct']:+.2%}) · liquidités {b['cash']:,.0f} €".replace(",", " "))
        for p in b["positions"]:
            print(f"  {p['ticker']:10} {p['qty']:>9.2f} @ {p['cost']:>8.2f} → {p['last']:>8.2f}  {p['pl_pct']:+.1%}  poids {p['weight']:.1%}  [{p['strategy']}]")
        return 0
    if act == "review":
        rv = desk.review(EUROPE.data["close"])
        s = rv["summary"]
        print(f"Revue : {s.get('n', 0)} décisions, {s.get('judged', 0)} jugées, {s.get('good', 0)} bonnes ; référence : indice équipondéré des titres liquides")
        for l in rv["lessons"]:
            print("  →", l["text"])
        return 0
    t = _t(a.ticker)
    if not t:
        print("Indiquez un titre.", file=sys.stderr)
        return 1
    if act == "note":
        d = EUROPE.note({"ticker": t, "action": a.decision, "reason": a.reason, "actor": a.by, "strategy": a.strategy})
        print(f"✓ Décision notée : {d['action']} {t}")
        return 0
    order = {"ticker": t, "side": "sell" if act == "sell" else "buy", "amount": a.amount, "qty": a.qty, "all": a.all,
             "stop": a.stop, "target": a.target, "strategy": a.strategy, "reason": a.reason, "actor": a.by}
    res = EUROPE.order(order, dry=act == "check")
    for c in res["checks"]:
        print(f"  {'✓' if c['ok'] else '✗'} {c['label']:62} {c['detail']}")
    if act == "check":
        print("✓ Acceptable." if res["ok"] else "✗ BLOQUÉ par le risk manager.")
    else:
        print(f"✓ Ordre paper exécuté ({res['qty']:g} {t} à {res['price']:.2f} €)" if res["ok"] else "✗ Ordre BLOQUÉ — décision notée au registre.")
    return 0 if res["ok"] else 2


def add_parser(sub) -> None:
    e = sub.add_parser("eu", help="petites et moyennes capitalisations françaises : données, filtre, laboratoire, situations, desk")
    e.add_argument("action", choices=["update", "lab", "screen", "stock", "situations", "situation-add",
                                      "status", "check", "buy", "sell", "note", "review"])
    e.add_argument("ticker", nargs="?")
    e.add_argument("-n", type=int, default=30); e.add_argument("--all", action="store_true")
    e.add_argument("--amount", type=float); e.add_argument("--qty", type=float)
    e.add_argument("--stop", type=float); e.add_argument("--target", type=float)
    e.add_argument("--strategy", default=None); e.add_argument("--reason", default=""); e.add_argument("--by", default="vous")
    e.add_argument("--decision", default="attendre", choices=["attendre", "rejeter", "surveiller", "alléger", "sortir"])
    e.add_argument("--type", default="opa", choices=list(situations.TYPES)); e.add_argument("--price", type=float)
    e.add_argument("--close", default=None, help="date de clôture attendue AAAA-MM-JJ")
    e.add_argument("--notes", default=""); e.add_argument("--source", default="")
