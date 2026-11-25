"""Ligne de commande du desk (utilisée par vous et par les agents du comité).

  python3 ndx.py desk status                       compte paper, positions, dernières décisions
  python3 ndx.py desk check NVDA --amount 2000 --stop 170 --strategy rs     contrôle sans exécuter (code 2 si bloqué)
  python3 ndx.py desk buy NVDA --amount 2000 --stop 170 --target 200 --strategy rs --reason "…" --by PM
  python3 ndx.py desk sell NVDA [--qty 5 | --all] --reason "…"
  python3 ndx.py desk note NVDA --decision attendre --reason "résultats jeudi"
  python3 ndx.py desk review | comite | rules
"""
from __future__ import annotations

import sys

import pandas as pd

from . import data, desk, swing


def _prices(tickers: list[str]) -> dict[str, float]:
    """Cours frais pour les titres concernés (titre de l'ordre, positions, indice) ; dernier cours stocké sinon."""
    tickers = sorted({t for t in tickers if t} | {data.INDEX_TICKER})
    out: dict[str, float] = {}
    try:
        _, close, _, _ = data.load()
        last = close.ffill().iloc[-1]
        out.update({k: float(v) for k, v in last.items() if pd.notna(v)})
    except Exception:
        pass
    try:
        lc, _ = data.fetch_prices(tickers, "5d")
        last = lc.ffill().iloc[-1]
        out.update({k: float(v) for k, v in last.items() if pd.notna(v) and v > 0})
    except Exception as e:
        print(f"  (cours en direct indisponibles : {e} — derniers cours stockés)", file=sys.stderr)
    return out


def _held() -> list[str]:
    return [p["ticker"] for p in desk.book().get("positions", [])]


def _earnings() -> dict[str, str]:
    try:
        an = data.load_analysts()
        ts = pd.to_numeric(an["earnings_ts"], errors="coerce").dropna()
        return {str(t): pd.Timestamp.fromtimestamp(v).strftime("%Y-%m-%d") for t, v in ts.items()}
    except Exception:
        return {}


def _print_checks(res: dict) -> None:
    for c in res["checks"]:
        print(f"  {'✓' if c['ok'] else '✗'} {c['label']:62} {c['detail']}")


def run(args) -> int:
    act = args.action
    t = (args.ticker or "").upper().replace(".", "-")
    if act == "rules":
        for k, v in desk.rules().items():
            print(f"  {k:24} {v}")
        print(f"\nFichier : {desk.RULES_FILE}")
        return 0
    if act == "comite":
        ev = desk.evaluate(swing.EVENTS_FILE)
        print(f"Comité — validation hors échantillon ({ev.get('period', '')}, période réservée depuis {ev.get('cutoff', '')})")
        for s in ev.get("setups", []):
            print(f"  {'✓' if s['validated'] else '✗'} {s['label']:34} {s['why']}")
        if not ev.get("setups"):
            print(" ", ev.get("note"))
        return 0
    ev = desk.evaluate(swing.EVENTS_FILE)
    val = desk.validated_map(ev)
    prices = _prices([t] + _held())
    idx = prices.get(data.INDEX_TICKER)
    if act == "status":
        b = desk.book(prices)
        print(f"Compte paper : {b['equity']:,.0f} $  (départ {b['start']:,.0f} $, {b['pl_total_pct']:+.2%})  ·  "
              f"liquidités {b['cash']:,.0f} $".replace(",", " "))
        for p in b["positions"]:
            print(f"  {p['ticker']:6} {p['qty']:>9.3f} @ {p['cost']:>9.2f} → {p['last']:>9.2f}  {p['pl_pct']:+7.2%}  "
                  f"poids {p['weight']:.1%}  stop {p['stop']}  objectif {p['target']}  [{p['strategy']}]")
        for d in desk.decisions()[-8:]:
            print(f"  {d['time'][:16]}  {d['actor']:10} {d['action']:9} {d['ticker']:6} {d['status']:22} {d.get('reason', '')[:60]}")
        return 0
    if act == "review":
        _, close, _, _ = data.load()
        rv = desk.review(close)
        s = rv["summary"]
        print(f"Revue : {s.get('n', 0)} décisions, {s.get('judged', 0)} jugées, {s.get('good', 0)} bonnes, "
              f"{s.get('blocked', 0)} bloquées par le risque")
        for r in rv["rows"][:25]:
            x = r.get("x20", r.get("x5", r.get("x_now")))
            tag = "—" if r.get("good") is None else "bonne" if r["good"] else "à revoir"
            print(f"  {r['time'][:10]} {r['action']:9} {r['ticker']:6} {r['status']:22} vs indice "
                  f"{'' if x is None else f'{x:+.1%}':>7}  {tag}")
        for l in rv["lessons"]:
            print("  →", l["text"])
        return 0
    if not t:
        print("Indiquez un titre.", file=sys.stderr)
        return 1
    if act == "note":
        d = desk.note(t, args.decision, args.reason or "", args.by or "vous", args.strategy, prices.get(t), idx)
        print(f"✓ Décision notée : {d['action']} {t} — {d['reason']}")
        return 0
    order = {"ticker": t, "side": "sell" if act == "sell" else "buy", "amount": args.amount, "qty": args.qty,
             "all": args.all, "stop": args.stop, "target": args.target, "strategy": args.strategy,
             "reason": args.reason, "actor": args.by}
    if act == "check":
        res = desk.check(order, prices, val, _earnings())
        _print_checks(res)
        print("✓ Ordre acceptable par le risk manager." if res["ok"] else "✗ Ordre BLOQUÉ par le risk manager.")
        return 0 if res["ok"] else 2
    res = desk.submit(order, prices, val, _earnings(), idx)
    _print_checks(res)
    if res["ok"]:
        print(f"✓ Ordre paper exécuté : {'achat' if order['side'] == 'buy' else 'vente'} de {res['qty']:g} {t} à {res['price']:.2f} "
              f"(décision {res['decision']['id']} enregistrée)")
        return 0
    print(f"✗ Ordre BLOQUÉ par le risk manager — décision {res['decision']['id']} enregistrée avec la raison.")
    return 2


def add_parser(sub) -> None:
    dk = sub.add_parser("desk", help="desk paper trading : compte, ordres contrôlés par le risk manager, registre, comité")
    dk.add_argument("action", choices=["status", "check", "buy", "sell", "note", "review", "comite", "rules"])
    dk.add_argument("ticker", nargs="?")
    dk.add_argument("--amount", type=float); dk.add_argument("--qty", type=float); dk.add_argument("--all", action="store_true")
    dk.add_argument("--stop", type=float); dk.add_argument("--target", type=float)
    dk.add_argument("--strategy", default=None, help="clé du setup (pullback, rs…), intraday:gap_go… ou discrétionnaire")
    dk.add_argument("--reason", default=""); dk.add_argument("--by", default="vous", help="auteur : vous, PM, agent…")
    dk.add_argument("--decision", default="attendre", choices=["attendre", "rejeter", "surveiller", "alléger", "sortir"])
