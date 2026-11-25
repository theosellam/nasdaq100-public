"""Le desk : un « fonds à une personne » en paper trading, où les limites vivent dans le code, pas dans le modèle.

- Risk manager (code) : chaque ordre passe par check() ; un ordre bloqué n'atteint jamais le courtier.
- Courtier fictif (paper) : exécution au dernier cours (+ glissement), stops et objectifs surveillés à chaque rafraîchissement.
  AUCUN ordre réel : ce module ne parle à aucun courtier.
- Registre des décisions : chaque décision (exécutée, bloquée, attendre, rejeter) est écrite avec sa raison,
  puis revue à 5 et 20 séances contre le Nasdaq 100. Ce qui a échoué devient le test suivant.
- Comité (évaluateur) : une stratégie n'est « validée » que si elle tient hors échantillon (walk-forward + période réservée).

Fichiers (data/private/desk/, jamais publiés) : rules.json, fills.csv, decisions.jsonl, equity.csv.
fills.csv et decisions.jsonl sont en ajout seul : on ne réécrit pas l'histoire.
Outil d'analyse et d'entraînement — pas un conseil en investissement.
"""
from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd

from .data import DATA_DIR, INDEX_TICKER

DESK_DIR = DATA_DIR / "private" / "desk"
RULES_FILE = DESK_DIR / "rules.json"
FILLS = DESK_DIR / "fills.csv"
DECISIONS = DESK_DIR / "decisions.jsonl"
EQUITY = DESK_DIR / "equity.csv"
FILL_COLS = ["id", "time", "ticker", "side", "qty", "price", "fees", "strategy", "stop", "target", "decision", "why"]
SLIPPAGE = 0.0005           # 5 points de base contre nous, à l'achat comme à la vente
_LOCK = threading.RLock()

DEFAULT_RULES = {
    "_note": "Limites du desk (paper trading). Elles sont appliquées par le code : un ordre qui les dépasse est refusé.",
    "capital_depart": 100000,
    "max_poids_ligne": 0.05,          # stratégie validée hors échantillon
    "max_poids_non_valide": 0.02,     # stratégie non validée, screener intraday, discrétionnaire
    "risque_max_trade": 0.01,         # perte au stop / valeur du compte
    "stop_obligatoire": True,
    "max_positions": 15,
    "min_liquidites": 0.10,
    "max_ordres_semaine": 10,
    "pause_apres_perte_jour": -0.03,  # plus aucun achat dans la journée après cette perte
    "jours_sans_resultats": 2,        # pas d'achat si les résultats tombent dans ce nombre de jours
}


# ───────────────────────── Fichiers ─────────────────────────
def rules() -> dict:
    DESK_DIR.mkdir(parents=True, exist_ok=True)
    if not RULES_FILE.exists():
        RULES_FILE.write_text(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        return {**DEFAULT_RULES, **json.loads(RULES_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULT_RULES)


def fills() -> pd.DataFrame:
    if not FILLS.exists():
        return pd.DataFrame(columns=FILL_COLS)
    f = pd.read_csv(FILLS)
    for c in ("qty", "price", "fees", "stop", "target"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    return f


def _append_fill(row: dict) -> None:
    DESK_DIR.mkdir(parents=True, exist_ok=True)
    new = not FILLS.exists()
    with open(FILLS, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FILL_COLS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in FILL_COLS})


def decisions() -> list[dict]:
    if not DECISIONS.exists():
        return []
    out = []
    for line in DECISIONS.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _append_decision(d: dict) -> dict:
    DESK_DIR.mkdir(parents=True, exist_ok=True)
    with open(DECISIONS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
    return d


def _now() -> str:
    return pd.Timestamp.now().isoformat(timespec="seconds")


# ───────────────────────── Livre de positions ─────────────────────────
def book(prices: dict[str, float] | None = None, R: dict | None = None) -> dict:
    """Positions, liquidités et valeur du compte, recalculées à partir des exécutions (source unique)."""
    R = R or rules()
    prices = prices or {}
    F = fills()
    cash = float(R["capital_depart"])
    pos: dict[str, dict] = {}
    for f in F.to_dict("records"):
        t, q, p, fee = f["ticker"], float(f["qty"]), float(f["price"]), float(f.get("fees") or 0)
        x = pos.setdefault(t, {"ticker": t, "qty": 0.0, "cost": 0.0, "stop": None, "target": None, "strategy": None,
                               "opened": None, "realized": 0.0})
        if f["side"] == "buy":
            cash -= q * p + fee
            if x["qty"] == 0:
                x["opened"] = f["time"]
            x["cost"] = (x["cost"] * x["qty"] + q * p) / (x["qty"] + q)
            x["qty"] += q
            x["stop"] = f["stop"] if pd.notna(f["stop"]) else x["stop"]
            x["target"] = f["target"] if pd.notna(f["target"]) else x["target"]
            x["strategy"] = f["strategy"]
        else:
            cash += q * p - fee
            x["realized"] += q * (p - x["cost"]) - fee
            x["qty"] -= q
    positions, mv = [], 0.0
    for x in pos.values():
        if x["qty"] <= 1e-9:
            continue
        last = prices.get(x["ticker"]) or x["cost"]
        value = x["qty"] * last
        mv += value
        risk = (last - x["stop"]) * x["qty"] if x["stop"] and np.isfinite(x["stop"]) else None
        positions.append({**x, "last": round(last, 2), "value": round(value, 2), "pl": round(value - x["qty"] * x["cost"], 2),
                          "pl_pct": round(last / x["cost"] - 1, 4) if x["cost"] else None,
                          "risk_open": round(risk, 2) if risk is not None else None})
    equity = cash + mv
    for p in positions:
        p["weight"] = round(p["value"] / equity, 4) if equity else None
    realized = sum(x["realized"] for x in pos.values())
    return {"cash": round(cash, 2), "equity": round(equity, 2), "invested": round(mv, 2), "realized": round(realized, 2),
            "start": float(R["capital_depart"]), "pl_total": round(equity - float(R["capital_depart"]), 2),
            "pl_total_pct": round(equity / float(R["capital_depart"]) - 1, 4), "positions": sorted(positions, key=lambda p: -p["value"]),
            "n_fills": int(len(F))}


def _snapshot_equity(b: dict, index_level: float | None) -> None:
    """Une ligne par jour (la dernière l'emporte) : sert à la pause après perte et à la courbe du compte."""
    DESK_DIR.mkdir(parents=True, exist_ok=True)
    day = pd.Timestamp.now().strftime("%Y-%m-%d")
    E = pd.read_csv(EQUITY) if EQUITY.exists() else pd.DataFrame(columns=["date", "equity", "index"])
    E = E[E["date"] != day]
    E = pd.concat([E, pd.DataFrame([{"date": day, "equity": b["equity"], "index": index_level}])], ignore_index=True)
    E.to_csv(EQUITY, index=False)


def equity_curve() -> pd.DataFrame:
    return pd.read_csv(EQUITY) if EQUITY.exists() else pd.DataFrame(columns=["date", "equity", "index"])


def _day_change(b: dict) -> float:
    E = equity_curve()
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    prev = E[E["date"] < today]
    ref = float(prev["equity"].iloc[-1]) if len(prev) else b["start"]
    return b["equity"] / ref - 1 if ref else 0.0


# ───────────────────────── Risk manager (code) ─────────────────────────
def check(order: dict, prices: dict[str, float], validated: dict[str, bool], earnings: dict[str, str] | None = None,
          R: dict | None = None) -> dict:
    """Contrôle un ordre AVANT le courtier. order = {ticker, side, amount|qty, stop, target, strategy}.
    Renvoie {ok, checks:[{label, ok, detail}], qty, price}. Ne modifie rien."""
    R = R or rules()
    t = str(order.get("ticker", "")).upper().strip().replace(".", "-")
    side = order.get("side", "buy")
    price = prices.get(t)
    b = book(prices, R)
    held = next((p for p in b["positions"] if p["ticker"] == t), None)
    checks: list[dict] = []

    def c(label, ok, detail):
        checks.append({"label": label, "ok": bool(ok), "detail": detail})

    c("Titre coté avec un cours disponible", price is not None and price > 0,
      f"{price:,.2f} $".replace(",", " ") if price else f"aucun cours pour {t or '—'}")
    if price is None or price <= 0:
        return {"ok": False, "checks": checks, "ticker": t, "side": side, "qty": 0, "price": None}
    px = price * (1 + SLIPPAGE if side == "buy" else 1 - SLIPPAGE)
    qty = order.get("qty")
    if qty in (None, "", 0) and order.get("amount"):
        qty = float(order["amount"]) / px
    qty = float(qty or 0)
    if side == "sell" and order.get("all"):
        qty = held["qty"] if held else 0.0
    if side == "buy":
        qty = float(np.floor(qty * 1000) / 1000)          # fractions d'action autorisées, au millième
    c("Quantité positive", qty > 0, f"{qty:g} action(s)")

    if side == "sell":
        have = held["qty"] if held else 0
        c("Vente ≤ quantité détenue (pas de vente à découvert)", 0 < qty <= have + 1e-9, f"détenu : {have:g}")
        ok = all(x["ok"] for x in checks)
        return {"ok": ok, "checks": checks, "ticker": t, "side": side, "qty": qty, "price": round(px, 4)}

    amount = qty * px
    strat = order.get("strategy") or "discrétionnaire"
    is_valid = bool(validated.get(strat))
    cap = R["max_poids_ligne"] if is_valid else R["max_poids_non_valide"]
    stop = order.get("stop")
    stop = float(stop) if stop not in (None, "") else None
    eq = b["equity"]
    w_after = ((held["value"] if held else 0) + amount) / eq if eq else 1
    c(f"Stratégie « {strat} » : {'validée hors échantillon' if is_valid else 'non validée'}"
      f" → poids maximum {cap:.0%}", True, "plafond plus strict tant que le comité ne l'a pas validée" if not is_valid else "")
    c(f"Poids de la ligne après achat ≤ {cap:.0%}", w_after <= cap + 1e-9, f"{w_after:.1%} du compte")
    if R.get("stop_obligatoire", True):
        c("Stop défini sous le prix d'achat", stop is not None and 0 < stop < px, f"stop {stop:g}" if stop else "aucun stop")
    if stop and 0 < stop < px:
        risk = (px - stop) * qty
        c(f"Perte au stop ≤ {R['risque_max_trade']:.1%} du compte", risk <= R["risque_max_trade"] * eq + 1e-6,
          f"{risk:,.0f} $ soit {risk / eq:.2%}".replace(",", " "))
    cash_after = b["cash"] - amount
    c(f"Liquidités après achat ≥ {R['min_liquidites']:.0%} du compte", cash_after >= R["min_liquidites"] * eq,
      f"{cash_after:,.0f} $ restants".replace(",", " "))
    if not held:
        c(f"Nombre de positions ≤ {R['max_positions']}", len(b["positions"]) + 1 <= R["max_positions"],
          f"{len(b['positions']) + 1} positions")
    F = fills()
    week = F[(F["side"] == "buy") & (pd.to_datetime(F["time"]) >= pd.Timestamp.now() - pd.Timedelta(days=7))] if len(F) else F
    c(f"Ordres d'achat sur 7 jours < {R['max_ordres_semaine']}", len(week) < R["max_ordres_semaine"], f"{len(week)} cette semaine")
    dchg = _day_change(b)
    c(f"Pas de pause imposée (journée du compte > {R['pause_apres_perte_jour']:.0%})", dchg > R["pause_apres_perte_jour"],
      f"journée {dchg:+.2%}")
    ed = (earnings or {}).get(t)
    if ed:
        days = (pd.Timestamp(ed).normalize() - pd.Timestamp.now().normalize()).days
        c(f"Pas de résultats dans les {R['jours_sans_resultats']} jours", not (0 <= days <= R["jours_sans_resultats"]),
          f"résultats le {ed}")
    ok = all(x["ok"] for x in checks)
    return {"ok": ok, "checks": checks, "ticker": t, "side": side, "qty": round(qty, 3), "price": round(px, 4),
            "amount": round(amount, 2), "strategy": strat, "validated": is_valid}


# ───────────────────────── Courtier fictif + registre ─────────────────────────
def submit(order: dict, prices: dict[str, float], validated: dict[str, bool], earnings: dict | None = None,
           index_level: float | None = None) -> dict:
    """Passe un ordre paper. Le risk manager tranche d'abord ; la décision est TOUJOURS écrite au registre."""
    with _LOCK:
        R = rules()
        res = check(order, prices, validated, earnings, R)
        t = res["ticker"]
        dec = {"id": uuid.uuid4().hex[:10], "time": _now(), "actor": order.get("actor") or "vous", "ticker": t,
               "action": "acheter" if res["side"] == "buy" else "vendre", "strategy": res.get("strategy") or order.get("strategy"),
               "reason": (order.get("reason") or "").strip()[:2000], "price": prices.get(t), "index": index_level,
               "qty": res["qty"], "stop": order.get("stop"), "target": order.get("target"),
               "checks": [x for x in res["checks"] if not x["ok"]], "status": "exécuté" if res["ok"] else "bloqué par le risque"}
        if res["ok"]:
            _append_fill({"id": uuid.uuid4().hex[:10], "time": dec["time"], "ticker": t, "side": res["side"], "qty": res["qty"],
                          "price": res["price"], "fees": 0, "strategy": dec["strategy"], "stop": order.get("stop"),
                          "target": order.get("target"), "decision": dec["id"], "why": "ordre"})
        _append_decision(dec)
        return {**res, "decision": dec}


def note(ticker: str, action: str, reason: str, actor: str = "vous", strategy: str | None = None,
         price: float | None = None, index_level: float | None = None) -> dict:
    """Décision sans ordre (attendre, rejeter, surveiller) : elle sera revue comme les autres."""
    if action not in ("attendre", "rejeter", "surveiller", "alléger", "sortir"):
        raise ValueError("action : attendre, rejeter, surveiller, alléger ou sortir")
    with _LOCK:
        return _append_decision({"id": uuid.uuid4().hex[:10], "time": _now(), "actor": actor or "vous",
                                 "ticker": ticker.upper().replace(".", "-"), "action": action, "strategy": strategy,
                                 "reason": (reason or "").strip()[:2000], "price": price, "index": index_level,
                                 "checks": [], "status": "noté"})


def mark(prices: dict[str, float], index_level: float | None = None) -> list[dict]:
    """À chaque rafraîchissement : déclenche les stops et objectifs au dernier cours, puis note la valeur du compte."""
    done = []
    with _LOCK:
        R = rules()
        b = book(prices, R)
        for p in b["positions"]:
            last = prices.get(p["ticker"])
            if not last:
                continue
            why = "stop" if p["stop"] and last <= p["stop"] else "objectif" if p["target"] and last >= p["target"] else None
            if not why:
                continue
            px = last * (1 - SLIPPAGE)
            dec = _append_decision({"id": uuid.uuid4().hex[:10], "time": _now(), "actor": "courtier (automatique)",
                                    "ticker": p["ticker"], "action": "vendre", "strategy": p["strategy"],
                                    "reason": f"{why} touché ({last:.2f})", "price": last, "index": index_level,
                                    "qty": p["qty"], "checks": [], "status": "exécuté"})
            _append_fill({"id": uuid.uuid4().hex[:10], "time": dec["time"], "ticker": p["ticker"], "side": "sell",
                          "qty": p["qty"], "price": round(px, 4), "fees": 0, "strategy": p["strategy"], "decision": dec["id"],
                          "why": why})
            done.append({"ticker": p["ticker"], "why": why, "price": round(px, 2)})
        _snapshot_equity(book(prices, R), index_level)
    return done


# ───────────────────────── Revue des décisions ─────────────────────────
def review(close: pd.DataFrame) -> dict:
    """Chaque décision est jugée après coup : cours du titre vs Nasdaq 100 depuis la décision (5 et 20 séances).
    Un achat est réussi s'il bat l'indice ; un refus ou une attente est « bon » si le titre a fait moins bien que l'indice."""
    D = decisions()
    if not D or close is None or close.empty:
        return {"rows": [], "summary": {}, "lessons": []}
    idx = close[INDEX_TICKER].dropna() if INDEX_TICKER in close else None
    rows = []
    for d in D:
        t, when = d.get("ticker"), pd.Timestamp(d.get("time"))
        r = {k: d.get(k) for k in ("id", "time", "actor", "ticker", "action", "strategy", "reason", "status")}
        r["blocked_by"] = [x["label"] for x in d.get("checks") or []]
        if t in close and idx is not None:
            s = close[t].dropna()
            s_after, i_after = s[s.index > when.normalize()], idx[idx.index > when.normalize()]
            p0 = d.get("price") or (s[s.index <= when.normalize()].iloc[-1] if (s.index <= when.normalize()).any() else None)
            i0 = d.get("index") or (idx[idx.index <= when.normalize()].iloc[-1] if (idx.index <= when.normalize()).any() else None)
            for h in (5, 20):
                if p0 and i0 and len(s_after) >= h and len(i_after) >= h:
                    r[f"x{h}"] = round(float(s_after.iloc[h - 1] / p0 - 1) - float(i_after.iloc[h - 1] / i0 - 1), 4)
            n = min(len(s_after), len(i_after))
            if p0 and i0 and n:
                r["x_now"] = round(float(s_after.iloc[-1] / p0 - 1) - float(i_after.iloc[-1] / i0 - 1), 4)
            r["days"] = int(n)
        x = r.get("x20", r.get("x5"))
        if x is not None and d.get("actor") != "courtier (automatique)":
            buy_like = d["action"] == "acheter" and d["status"] == "exécuté"
            r["good"] = bool(x > 0) if buy_like else bool(x <= 0) if d["action"] in ("acheter", "attendre", "rejeter", "sortir", "alléger") else None
        rows.append(r)
    judged = [r for r in rows if r.get("good") is not None]
    summary = {"n": len(rows), "judged": len(judged), "good": sum(r["good"] for r in judged),
               "blocked": sum(1 for r in rows if r["status"] == "bloqué par le risque"),
               "executed": sum(1 for r in rows if r["status"] == "exécuté" and r["actor"] != "courtier (automatique)")}
    # leçons : stratégies dont les décisions jugées échouent le plus souvent → à retester au comité
    lessons = []
    by = {}
    for r in judged:
        by.setdefault(r.get("strategy") or "discrétionnaire", []).append(r)
    for s, g in by.items():
        bad = [r for r in g if not r["good"]]
        if len(g) >= 3 and len(bad) / len(g) >= 0.6:
            lessons.append({"strategy": s, "n": len(g), "bad": len(bad),
                            "text": f"« {s} » : {len(bad)} décisions sur {len(g)} ont fait moins bien que prévu. "
                                    "À repasser au comité avant de nouveaux ordres."})
    return {"rows": rows[::-1][:300], "summary": summary, "lessons": lessons}


# ───────────────────────── Comité : validation hors échantillon ─────────────────────────
_EVAL_CACHE: dict = {}


def evaluate(events_file: Path) -> dict:
    """Juge chaque setup de swing hors échantillon, sans jamais utiliser l'avenir pour décider :
    1) walk-forward annuel : au 1er janvier de chaque année, on décide avec les seules données passées
       (même règle que le laboratoire), puis on mesure l'année suivante ;
    2) période réservée : les 30 % les plus récents de l'historique, jamais utilisés pour décider.
    Validé seulement si l'avantage hors échantillon est positif dans les deux tests."""
    if not events_file.exists():
        return {"setups": [], "note": "Historique des setups absent : construisez le laboratoire (onglet Laboratoire)."}
    mtime = events_file.stat().st_mtime
    if _EVAL_CACHE.get("mtime") == mtime:
        return _EVAL_CACHE["out"]
    from . import swing
    E = pd.read_csv(events_file, parse_dates=["date"])
    E = E[E["taken"].astype(bool)]
    years = sorted(E["date"].dt.year.unique())
    cut = E["date"].min() + (E["date"].max() - E["date"].min()) * 0.7
    out = []
    for key, (label, _rule) in swing.SETUPS.items():
        g = E[E["setup"] == key]
        wf_parts, wf_years = [], []
        for y in years[2:]:
            past = g[g["date"] < pd.Timestamp(y, 1, 1)]
            if len(past) < 30:
                continue
            mid = past["date"].min() + (past["date"].max() - past["date"].min()) / 2
            st, _ = swing.verdict(swing._stats(past), (swing._stats(past[past["date"] < mid]), swing._stats(past[past["date"] >= mid])))
            if st != "non validé":
                wf_parts.append(g[g["date"].dt.year == y])
                wf_years.append(int(y))
        wf = swing._stats(pd.concat(wf_parts)) if wf_parts else {"n": 0}
        ins = g[g["date"] < cut]
        mid = ins["date"].min() + (ins["date"].max() - ins["date"].min()) / 2 if len(ins) else None
        ins_v = swing.verdict(swing._stats(ins), (swing._stats(ins[ins["date"] < mid]), swing._stats(ins[ins["date"] >= mid])))[0] \
            if len(ins) else "non validé"
        hold = swing._stats(g[g["date"] >= cut])
        e_wf, t_wf, e_h = wf.get("edge_R"), wf.get("t_R"), hold.get("edge_R")
        ok = (wf.get("n", 0) >= 50 and e_wf is not None and e_wf > 0 and (t_wf or 0) >= 1
              and ins_v != "non validé" and e_h is not None and e_h > 0)
        why = []
        if not wf_parts:
            why.append("jamais retenu en walk-forward (aucune année où les seules données passées le validaient)")
        elif wf.get("n", 0) < 50:
            why.append(f"trop peu de trades hors échantillon ({wf.get('n', 0)})")
        elif e_wf is not None:
            why.append(f"walk-forward {e_wf:+.2f} R/trade (t = {t_wf or 0:.1f}, {wf['n']} trades)")
        why.append(f"période réservée {e_h:+.2f} R/trade ({hold.get('n', 0)} trades)" if e_h is not None else "période réservée vide")
        if ins_v == "non validé":
            why.append("rejeté avant la période réservée")
        out.append({"key": key, "label": label, "validated": bool(ok), "status": "validé hors échantillon" if ok else "rejeté par le comité",
                    "why": " · ".join(why), "walk_forward": wf, "wf_years": wf_years, "in_sample_verdict": ins_v, "holdout": hold})
    res = {"setups": out, "cutoff": f"{cut:%m/%Y}", "period": f"{E['date'].min():%m/%Y} → {E['date'].max():%m/%Y}",
           "method": evaluate.__doc__.split(":", 1)[1].strip()}
    _EVAL_CACHE.update(mtime=mtime, out=res)
    return res


def validated_map(ev: dict) -> dict[str, bool]:
    return {s["key"]: s["validated"] for s in ev.get("setups", [])}
