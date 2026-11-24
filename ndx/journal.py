"""Journal quotidien des signaux (SQLite, data/journal/) et suivi de leur performance réelle.

À chaque rafraîchissement, l'état de chaque titre est enregistré (une ligne par titre et par jour, la dernière
version du jour l'emporte). Les performances à 5, 20 et 60 séances sont ensuite mesurées contre le Nasdaq 100 :
c'est un test sans aucune information du futur, qui s'enrichit chaque jour.
"""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd

from .data import DATA_DIR, INDEX_TICKER

JOURNAL_DIR = DATA_DIR / "journal"
DB = JOURNAL_DIR / "signals.sqlite"
HORIZONS = (5, 20, 60)

SCHEMA = """CREATE TABLE IF NOT EXISTS snap (
  date TEXT, ticker TEXT, price REAL, ndx REAL, verdict TEXT, setup TEXT, fund_score REAL, lab_pct REAL, val_pct REAL,
  os_10j REAL, upside REAL, rr REAL, strategies TEXT, regime TEXT, saved TEXT, PRIMARY KEY (date, ticker))"""


SWING_SCHEMA = """CREATE TABLE IF NOT EXISTS swing (
  date TEXT, ticker TEXT, setup TEXT, status TEXT, entry REAL, stop REAL, target REAL, rr REAL, lab_pct REAL,
  saved TEXT, PRIMARY KEY (date, ticker, setup))"""


def _conn() -> sqlite3.Connection:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute(SCHEMA)
    c.execute(SWING_SCHEMA)
    return c


def record(a, close: pd.DataFrame) -> int:
    """Enregistre l'état du jour (séance de référence = dernière date de cours)."""
    day = a.asof.strftime("%Y-%m-%d")
    ndx = float(close[INDEX_TICKER].dropna().iloc[-1])
    strat = {}
    for s in (a.strategies or {}).get("strategies", []):
        for m in s["matches"]:
            strat.setdefault(m["ticker"], []).append(s["key"])
    ops = {o["ticker"]: o for o in a.opportunities}
    reg = json.dumps({"stress": (a.context or {}).get("regime", {}).get("stress")})
    rows = []
    for t, r in a.stocks.iterrows():
        if t == "GOOG":
            continue
        o = ops.get(t, {})
        f = lambda v: float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None
        rows.append((day, t, f(r.get("price")), ndx, o.get("verdict"), o.get("setup"), f(o.get("fund_score")), f(r.get("lab_pct")),
                     f(r.get("val_pct")), f(r.get("os_10j")), f(r.get("upside")), f(o.get("rr")), ",".join(strat.get(t, [])), reg,
                     pd.Timestamp.now().isoformat(timespec="seconds")))
    with _conn() as c:
        c.executemany("INSERT OR REPLACE INTO snap VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def evaluate(close: pd.DataFrame) -> dict:
    """Performance des signaux enregistrés, relative au Nasdaq 100, quand l'horizon est écoulé."""
    if not DB.exists():
        return {"days": 0}
    with _conn() as c:
        S = pd.read_sql("SELECT * FROM snap", c)
    if S.empty:
        return {"days": 0}
    for col in ("price", "ndx", "fund_score", "lab_pct", "val_pct", "os_10j", "upside", "rr"):
        S[col] = pd.to_numeric(S[col], errors="coerce")   # colonne entièrement vide (laboratoire pas encore construit) → float
    idx = close.index
    pos = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(idx)}
    for h in HORIZONS:
        out = []
        for _, r in S.iterrows():
            i = pos.get(r["date"])
            if i is None or i + h >= len(idx) or r["ticker"] not in close or not r["price"]:
                out.append(np.nan)
                continue
            p1, n1 = close[r["ticker"]].iloc[i + h], close[INDEX_TICKER].iloc[i + h]
            out.append((p1 / r["price"] - 1) - (n1 / r["ndx"] - 1) if pd.notna(p1) else np.nan)
        S[f"x{h}"] = out
    S["lab_q"] = pd.cut(S["lab_pct"], [0, 0.2, 0.4, 0.6, 0.8, 1.0001], labels=["Q1", "Q2", "Q3", "Q4", "Q5"])

    def summarize(col, groups=None):
        res = []
        for k, g in S.groupby(col, observed=True):
            if not k:
                continue
            row = {"group": str(k), "n_days": int(g["date"].nunique()), "n": int(len(g))}
            for h in HORIZONS:
                v = g[f"x{h}"].dropna()
                row[f"x{h}"] = float(v.mean()) if len(v) else None
                row[f"hit{h}"] = float((v > 0).mean()) if len(v) else None
                row[f"n{h}"] = int(len(v))
            res.append(row)
        return res

    st = S.assign(strategy=S["strategies"].str.split(",")).explode("strategy")
    st = st[st["strategy"].fillna("") != ""]
    first = S["date"].min()
    next_eval = {h: (idx[pos[first] + h].strftime("%Y-%m-%d") if first in pos and pos[first] + h < len(idx) else
                     (pd.Timestamp(first) + pd.tseries.offsets.BDay(h)).strftime("%Y-%m-%d")) for h in HORIZONS}
    return {"days": int(S["date"].nunique()), "first": first, "last": S["date"].max(), "rows": int(len(S)),
            "next_eval": next_eval, "by_verdict": summarize("verdict"), "by_lab": summarize("lab_q"),
            "by_strategy": [{**r, "group": r["group"]} for r in (lambda S2: [
                {"group": k, "n": int(len(g)), **{f"x{h}": (float(g[f'x{h}'].dropna().mean()) if g[f'x{h}'].notna().any() else None) for h in HORIZONS},
                 **{f"n{h}": int(g[f"x{h}"].notna().sum()) for h in HORIZONS}} for k, g in S2.groupby("strategy")])(st)]}


# ───────────────────────── Signaux de swing ─────────────────────────
def record_swing(signals: list[dict], asof) -> int:
    """Enregistre les signaux de swing du jour (plan figé à la clôture de la séance de référence)."""
    day = pd.Timestamp(asof).strftime("%Y-%m-%d")
    now = pd.Timestamp.now().isoformat(timespec="seconds")
    rows = [(day, s["ticker"], s["setup"], s["status"], s["entry"], s["stop"], s["target"], s["rr"], s.get("lab_pct"), now)
            for s in signals if s.get("entry") and s.get("stop")]
    with _conn() as c:
        c.executemany("INSERT OR REPLACE INTO swing VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def evaluate_swing(O: dict) -> dict:
    """Suivi réel des signaux de swing : entrée à l'ouverture suivante, puis stop / objectif / 20 séances (comme le test)."""
    from .swing import MAX_HOLD
    with _conn() as c:
        S = pd.read_sql("SELECT * FROM swing", c)
    if S.empty:
        return {"n": 0, "rows": []}
    o, h, l, cl = (O[f] for f in ("Open", "High", "Low", "Close"))
    idx = cl.index
    rows = []
    for _, r in S.iterrows():
        d = pd.Timestamp(r["date"])
        if r["ticker"] not in cl or d not in idx:
            continue
        i = idx.get_loc(d)
        res = {"date": r["date"], "ticker": r["ticker"], "setup": r["setup"], "status": r["status"], "plan_entry": r["entry"],
               "stop": r["stop"], "target": r["target"], "state": "en attente d'ouverture", "R": None, "days": 0}
        if i + 1 < len(idx) and pd.notna(o[r["ticker"]].iloc[i + 1]):
            entry = float(o[r["ticker"]].iloc[i + 1])
            risk = r["entry"] - r["stop"]
            stop, tgt = entry - risk, entry + (r["target"] - r["entry"])
            res["entry"] = entry
            if entry <= stop:
                res["state"] = "non pris (ouverture sous le stop)"
            else:
                res["state"], last = "en cours", None
                for k in range(i + 1, min(i + 1 + MAX_HOLD, len(idx))):
                    lo, hi, cc = l[r["ticker"]].iloc[k], h[r["ticker"]].iloc[k], cl[r["ticker"]].iloc[k]
                    if pd.isna(lo):
                        continue
                    last = k
                    if lo <= stop:
                        res.update(state="stop touché", R=-1.0 if k == i + 1 else float((min(o[r["ticker"]].iloc[k], stop) - entry) / risk)); break
                    if hi >= tgt:
                        res.update(state="objectif atteint", R=float((tgt - entry) / risk)); break
                    res["R"] = float((cc - entry) / risk)
                if last is not None:
                    res["days"] = int(last - i)
                    if res["state"] == "en cours" and last - i >= MAX_HOLD:
                        res["state"] = "clôturé (20 séances)"
        rows.append(res)
    if not rows:
        return {"n": 0, "rows": []}
    R = pd.DataFrame(rows)
    done = R[R["state"].isin(["stop touché", "objectif atteint", "clôturé (20 séances)"])]
    by = []
    for k, g in R.groupby("setup"):
        dg = g[g["state"].isin(["stop touché", "objectif atteint", "clôturé (20 séances)"])]
        by.append({"setup": k, "n": int(len(g)), "closed": int(len(dg)), "open": int((g["state"] == "en cours").sum()),
                   "exp_R": float(dg["R"].mean()) if len(dg) else None, "win": float((dg["R"] > 0).mean()) if len(dg) else None})
    return {"n": int(len(R)), "closed": int(len(done)), "exp_R": float(done["R"].mean()) if len(done) else None,
            "first": S["date"].min(), "by_setup": by,
            "rows": R.sort_values("date", ascending=False).head(60).to_dict("records")}
