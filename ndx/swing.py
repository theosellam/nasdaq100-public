"""Swing trading (2 à 20 séances) : lecture du price action, 5 setups, plan de trade et validation historique.

Méthode de test (sans information du futur) :
- signal constaté à la CLÔTURE du jour J, entrée à l'OUVERTURE de J+1 ; stop et objectif fixés à la clôture de J ;
- sortie au stop (ou à l'ouverture si elle est sous le stop), à l'objectif, ou à la clôture après 20 séances ;
  si le stop et l'objectif sont touchés le même jour, on retient le stop (hypothèse prudente) ;
- seuls les titres membres du Nasdaq 100 à la date du signal sont retenus (composition trimestrielle historique) ;
- un même setup n'est pas re-déclenché sur un titre pendant 10 séances ;
- le résultat est comparé à la moyenne de TOUS les jours/titres de l'univers (même règle d'entrée/sortie) :
  un setup n'a de la valeur que s'il fait mieux que d'acheter au hasard.
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd

from . import history as H
from .data import INDEX_TICKER
from .strategies import EXIT_DATE, RETURN_DATE
from .themes import RECORD_DATE

SWING_FILE = H.HIST_DIR / "swing.json"
EVENTS_FILE = H.HIST_DIR / "swing_events.csv"
HORIZONS = (5, 10, 20)
MAX_HOLD = 20
COOLDOWN = 10
ANCHORS = {RECORD_DATE: "Record", EXIT_DATE: "Départ Huang", RETURN_DATE: "Retour Huang"}

SETUPS = {
    "pullback": ("Repli dans la tendance",
                 "Tendance haussière (cours > MM50 > MM200, MM50 montante), repli de ≥ 3 % qui touche l'EMA 21 et clôture au-dessus, volume en baisse"),
    "breakout": ("Cassure de base",
                 "Clôture au-dessus du plus haut de 20 séances, base serrée (≤ 12 %), volume ≥ 1,5× la moyenne, clôture dans le haut de la bougie"),
    "undercut": ("Faux décrochage",
                 "Passage sous le plus bas de 20 séances puis clôture au-dessus, dans la moitié haute de la bougie"),
    "gap": ("Gap tenu",
            "Gap haussier ≥ 3 % (ou ≥ 0,75 ATR) sur fort volume, non comblé deux séances plus tard"),
    "rs": ("Force relative en correction",
           "Le Nasdaq 100 est sous son EMA 21 mais le titre le bat de ≥ 5 % sur 10 séances, reste au-dessus de son EMA 21 et sa force relative est au plus haut de 3 mois"),
}
NEAR = {
    "pullback": "Repli en approche de l'EMA 21",
    "breakout": "Base serrée sous la résistance (range étroit)",
    "rs": "Force relative qui se construit",
}


def _f(v, d: int = 4):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, d) if np.isfinite(v) else None


# ───────────────────────── Indicateurs (tableaux larges : dates × titres) ─────────────────────────
def features(O: dict[str, pd.DataFrame]) -> dict:
    cols = [c for c in O["Close"].columns if c != INDEX_TICKER]
    o, h, l, c, v = (O[f][cols] for f in H.FIELDS)
    ndx = O["Close"][INDEX_TICKER]
    pc = c.shift(1)
    tr = pd.DataFrame(np.fmax(np.fmax((h - l).values, (h - pc).abs().values), (l - pc).abs().values), index=c.index, columns=cols)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ema21 = c.ewm(span=21, adjust=False, min_periods=21).mean()
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    vavg = v.rolling(50, min_periods=20).mean().shift(1)
    hi20, lo20 = h.rolling(20).max().shift(1), l.rolling(20).min().shift(1)
    rng = (h - l).where(lambda x: x > 0)
    pos = (c - l) / rng
    r10 = c / c.shift(10) - 1
    n10 = ndx / ndx.shift(10) - 1
    rs = c.div(ndx, axis=0)
    nr7 = rng <= rng.rolling(7).min()
    inside = (h <= h.shift(1)) & (l >= l.shift(1))
    return dict(o=o, h=h, l=l, c=c, v=v, pc=pc, atr=atr, ema21=ema21, sma50=sma50, sma200=sma200, vavg=vavg,
                hi20=hi20, lo20=lo20, pos=pos, r10=r10, n10=n10, rs=rs, nr7=nr7, inside=inside, ndx=ndx,
                ndx_ema21=ndx.ewm(span=21, adjust=False, min_periods=21).mean())


def setups(F: dict) -> tuple[dict, dict]:
    o, h, l, c, v, pc, atr, ema21 = F["o"], F["h"], F["l"], F["c"], F["v"], F["pc"], F["atr"], F["ema21"]
    sma50, sma200, vavg, hi20, lo20, pos = F["sma50"], F["sma200"], F["vavg"], F["hi20"], F["lo20"], F["pos"]
    ok = c.notna() & atr.notna() & vavg.notna()
    up = (c > sma50) & (sma50 > sma200) & (sma50 > sma50.shift(10))
    base = (hi20 - lo20) / c <= 0.12
    ndx_weak = (F["ndx"] < F["ndx_ema21"]).to_numpy()[:, None]
    xs10 = F["r10"].sub(F["n10"], axis=0)
    rs_hi = F["rs"] >= F["rs"].rolling(63).max() * 0.995
    gap_day = (o / pc - 1 >= (0.75 * atr / pc).clip(lower=0.03)) & (v >= 1.5 * vavg)
    S = {
        "pullback": up & (l <= ema21 * 1.01) & (c > ema21) & (c / h.rolling(10).max() - 1 <= -0.03)
                    & (v.rolling(5).mean() < vavg),
        "breakout": (c > hi20) & base & (v >= 1.5 * vavg) & (pos >= 0.7) & (c > sma50),
        "undercut": (l < lo20) & (c > lo20) & (pos >= 0.5),
        "gap": gap_day.shift(2, fill_value=False) & (l.rolling(3).min() > c.shift(3)) & (c >= o.shift(2)),
        "rs": (xs10 >= 0.05) & (c > ema21) & rs_hi & ndx_weak,
    }
    N = {
        "pullback": up & (c > ema21) & (c / ema21 - 1 <= 0.03) & (c / h.rolling(10).max() - 1 <= -0.02),
        "breakout": base & (c >= hi20 * 0.97) & (c <= hi20) & (F["nr7"] | F["inside"]) & (c > sma50),
        "rs": (xs10 >= 0.03) & (c > ema21) & ndx_weak,
    }
    S = {k: (m & ok).fillna(False).astype(bool) for k, m in S.items()}
    N = {k: (m & ok & ~S[k]).fillna(False).astype(bool) for k, m in N.items()}
    return S, N


def structural_stop(F: dict) -> dict:
    """Niveau d'invalidation de chaque setup (avant marge de 0,2 ATR)."""
    l = F["l"]
    return {"pullback": l.rolling(3).min(), "breakout": l, "undercut": l,
            "gap": l.rolling(3).min(), "rs": l.rolling(5).min()}


def plan(entry: float, struct: float, atr: float, res: float | None) -> tuple[float, float]:
    """Stop structurel (borné entre 1 et 3 ATR sous l'entrée) ; objectif = résistance si ≥ 1,5 R, sinon 2 R (plafonné à 4 R)."""
    dist = entry - (struct - 0.2 * atr)
    dist = min(max(dist, 1.0 * atr), 3.0 * atr)
    stop = entry - dist
    tgt = res if res is not None and np.isfinite(res) and res >= entry + 1.5 * dist else entry + 2 * dist
    return stop, min(tgt, entry + 4 * dist)


# ───────────────────────── Price action (lecture du graphique) ─────────────────────────
def pivots(h: np.ndarray, l: np.ndarray, k: int = 2) -> tuple[list[int], list[int]]:
    ph = [i for i in range(k, len(h) - k) if np.isfinite(h[i]) and h[i] == np.nanmax(h[i - k:i + k + 1])]
    pl = [i for i in range(k, len(l) - k) if np.isfinite(l[i]) and l[i] == np.nanmin(l[i - k:i + k + 1])]
    return ph, pl


def levels(h: np.ndarray, l: np.ndarray, c: float, atr: float, lookback: int = 120) -> dict:
    """Supports / résistances : pivots des 120 dernières séances regroupés à 0,75 ATR près (force = nombre de contacts)."""
    h, l = h[-lookback:], l[-lookback:]
    ph, pl = pivots(h, l)
    pts = sorted([h[i] for i in ph] + [l[i] for i in pl])
    zones: list[list[float]] = []
    for p in pts:
        if zones and p - zones[-1][0] <= 0.75 * atr:      # zone de 0,75 ATR au plus (pas d'enchaînement)
            zones[-1].append(p)
        else:
            zones.append([p])
    Z = [{"level": float(np.mean(z)), "touches": len(z)} for z in zones]
    sup = sorted([z for z in Z if z["level"] < c], key=lambda z: -z["level"])[:3]
    res = sorted([z for z in Z if z["level"] > c], key=lambda z: z["level"])[:3]
    return {"support": sup, "resistance": res}


def structure(h: np.ndarray, l: np.ndarray) -> str:
    ph, pl = pivots(h[-120:], l[-120:])
    if len(ph) < 2 or len(pl) < 2:
        return "Indéterminée"
    hh, hl = h[-120:][ph[-1]] > h[-120:][ph[-2]], l[-120:][pl[-1]] > l[-120:][pl[-2]]
    return "Haussière (sommets et creux ascendants)" if hh and hl else \
        "Baissière (sommets et creux descendants)" if not hh and not hl else "Range / transition"


def avwap(O: dict, t: str, anchor: str) -> pd.Series:
    h, l, c, v = (O[f][t] for f in ("High", "Low", "Close", "Volume"))
    m = c.index >= pd.Timestamp(anchor)
    tp = ((h + l + c) / 3)[m]
    vv = v[m].fillna(0)
    return (tp * vv).cumsum() / vv.cumsum().replace(0, np.nan)


# ───────────────────────── Validation historique ─────────────────────────
def _member_mask(index: pd.DatetimeIndex, columns: list[str], mem: pd.DataFrame) -> pd.DataFrame:
    is_mem = pd.DataFrame(False, index=index, columns=columns)
    dates = sorted(mem["date"].unique())
    for k, md in enumerate(dates):
        end = dates[k + 1] if k + 1 < len(dates) else index[-1] + pd.Timedelta(days=1)
        tick = [t for t in mem[mem["date"] == md]["ticker"].dropna() if t in is_mem.columns]
        is_mem.loc[(index >= md) & (index < end), tick] = True
    return is_mem


def _simulate(o, h, l, c, i, entry, stop, tgt):
    """Trade géré : renvoie (R, jours, sortie)."""
    risk = entry - stop
    n = len(c)
    last = min(i + 1 + MAX_HOLD, n) - 1
    for k in range(i + 1, last + 1):
        lo, hi, op = l[k], h[k], o[k]
        if not (np.isfinite(lo) and np.isfinite(hi)):
            continue
        if lo <= stop:
            px = op if (k > i + 1 and np.isfinite(op) and op < stop) else stop
            return (px - entry) / risk, k - i, "stop"
        if hi >= tgt:
            px = op if (k > i + 1 and np.isfinite(op) and op > tgt) else tgt
            return (px - entry) / risk, k - i, "objectif"
    if last <= i + 1 or not np.isfinite(c[last]):
        return np.nan, 0, "ouvert"
    return (c[last] - entry) / risk, last - i, "temps" if last - i >= MAX_HOLD else "ouvert"


def _tstat_by_month(d: pd.Series, dates: pd.Series) -> float | None:
    m = d.groupby(dates.dt.to_period("M")).mean().dropna()
    if len(m) < 6 or m.std() == 0:
        return None
    return float(m.mean() / (m.std() / np.sqrt(len(m))))


def backtest(O: dict, mem: pd.DataFrame, panel: pd.DataFrame | None, log=print) -> tuple[pd.DataFrame, dict]:
    F = features(O)
    S, _ = setups(F)
    cols = list(F["c"].columns)
    is_mem = _member_mask(F["c"].index, cols, mem)
    start = mem["date"].min()
    o, h, l, c = (F[k].to_numpy() for k in ("o", "h", "l", "c"))
    atr = F["atr"].to_numpy()
    res120 = F["h"].rolling(120, min_periods=40).max().to_numpy()
    nd = O["Close"][INDEX_TICKER].to_numpy()
    nd_o = O["Open"][INDEX_TICKER].to_numpy()
    idx = F["c"].index
    n = len(idx)

    # Référence « au hasard » : entrée à l'ouverture de J+1, sortie à la clôture J+h, tous les membres, tous les jours
    base = {}
    ent = F["o"].shift(-1)
    nd_ent = pd.Series(nd_o, index=idx).shift(-1)
    for hz in HORIZONS:
        r = F["c"].shift(-hz) / ent - 1
        rn = pd.Series(nd, index=idx).shift(-hz) / nd_ent - 1
        x = r.sub(rn, axis=0).where(is_mem & (idx >= start)[:, None])
        base[hz] = {"ret": float(r.where(is_mem).stack().mean()), "excess": float(x.stack().mean()),
                    "hit": float((x.stack() > 0).mean())}
    base_by_day = {hz: F["c"].shift(-hz).div(ent).sub(1).sub(
        pd.Series(nd, index=idx).shift(-hz).div(nd_ent).sub(1), axis=0).where(is_mem).mean(axis=1) for hz in HORIZONS}

    stops = structural_stop(F)
    # Référence en R : entrées tirées au hasard (membres, mêmes règles de stop / objectif / durée)
    rng = np.random.default_rng(7)
    cand = np.argwhere(is_mem.to_numpy() & (idx >= start)[:, None] & np.isfinite(atr) & np.isfinite(c))
    cand = cand[cand[:, 0] < n - 1]
    pick = cand[rng.choice(len(cand), size=min(40000, len(cand)), replace=False)]
    st3 = stops["pullback"].to_numpy()
    rb = []
    for i, j in pick:
        entry = o[i + 1, j]
        if not np.isfinite(entry):
            continue
        stop, tgt = plan(c[i, j], st3[i, j], atr[i, j], res120[i, j])
        if entry <= stop:
            continue
        stop, tgt = entry - (c[i, j] - stop), entry + (tgt - c[i, j])
        R, _, _ = _simulate(o[:, j], h[:, j], l[:, j], c[:, j], i, entry, stop, tgt)
        if np.isfinite(R):
            rb.append((idx[i], R))
    RB = pd.DataFrame(rb, columns=["date", "R"])
    base_R_month = RB.groupby(RB["date"].dt.to_period("M"))["R"].mean()
    base["R"] = {"exp_R": float(RB["R"].mean()), "win": float((RB["R"] > 0).mean()), "n": int(len(RB))}
    lvl = pd.Series(nd, index=idx)
    stress = ((lvl < lvl.rolling(200).mean()) | (lvl / lvl.rolling(252, min_periods=50).max() - 1 <= -0.10)).to_numpy()
    ev = []
    for key, mask in S.items():
        M = (mask & is_mem).to_numpy()
        M[idx < start] = False
        st = stops[key].to_numpy()
        for j, t in enumerate(cols):
            last = -10 ** 9
            for i in np.flatnonzero(M[:, j]):
                if i - last < COOLDOWN or i + 1 >= n:
                    continue
                last = i
                entry = o[i + 1, j]
                if not (np.isfinite(entry) and np.isfinite(atr[i, j])):
                    continue
                stop, tgt = plan(c[i, j], st[i, j], atr[i, j], res120[i, j])
                if entry <= stop:              # ouverture sous le stop : trade non pris
                    ev.append({"setup": key, "ticker": t, "date": idx[i], "taken": False})
                    continue
                stop, tgt = entry - (c[i, j] - stop), entry + (tgt - c[i, j])   # plan translaté à l'entrée réelle
                R, days, how = _simulate(o[:, j], h[:, j], l[:, j], c[:, j], i, entry, stop, tgt)
                row = {"setup": key, "ticker": t, "date": idx[i], "taken": True, "R": R, "days": days, "exit": how,
                       "rr": (tgt - entry) / (entry - stop), "stop_atr": (entry - stop) / atr[i, j], "stress": bool(stress[i])}
                for hz in HORIZONS:
                    k = i + hz
                    if k < n and np.isfinite(c[k, j]) and np.isfinite(nd[k]) and np.isfinite(nd_o[i + 1]):
                        row[f"r{hz}"] = c[k, j] / entry - 1
                        row[f"x{hz}"] = row[f"r{hz}"] - (nd[k] / nd_o[i + 1] - 1)
                        row[f"b{hz}"] = base_by_day[hz].iloc[i]
                ev.append(row)
    E = pd.DataFrame(ev)
    E["date"] = pd.to_datetime(E["date"])
    E["bR"] = E["date"].dt.to_period("M").map(base_R_month).astype(float)
    # Filtres fondamentaux connus à la fin du mois précédent (panel mensuel du laboratoire)
    E["cheap"] = E["dear"] = E["lab_ok"] = E["lab_weak"] = False
    if panel is not None and len(panel):
        V = panel[["date", "ticker", "val_pct", "comp_pct"]].copy()
        V["m"] = pd.to_datetime(V["date"]).dt.to_period("M")
        E["m"] = (E["date"] - pd.offsets.MonthEnd(1)).dt.to_period("M")
        E = E.merge(V[["m", "ticker", "val_pct", "comp_pct"]], on=["m", "ticker"], how="left").drop(columns="m")
        E["cheap"], E["dear"] = E["val_pct"] >= 0.6, E["val_pct"] <= 0.4
        E["lab_ok"], E["lab_weak"] = E["comp_pct"] >= 0.4, E["comp_pct"] < 0.4
    return E, base


def _stats(g: pd.DataFrame) -> dict:
    g = g[g["taken"].astype(bool)] if "taken" in g else g
    out = {"n": int(len(g))}
    if not len(g):
        return out
    R = g["R"].dropna()
    out.update({"exp_R": _f(R.mean(), 3), "win": _f((R > 0).mean(), 3), "avg_win": _f(R[R > 0].mean(), 2),
                "avg_loss": _f(R[R <= 0].mean(), 2), "days": _f(g["days"].mean(), 1),
                "stop_pct": _f((g["exit"] == "stop").mean(), 3), "tgt_pct": _f((g["exit"] == "objectif").mean(), 3)})
    if "bR" in g:   # avantage en R sur des entrées au hasard du même mois
        d = (g["R"] - g["bR"]).dropna()
        out["edge_R"] = _f(d.mean(), 3)
        out["t_R"] = _f(_tstat_by_month(d, g.loc[d.index, "date"]), 2)
    for hz in HORIZONS:
        if f"x{hz}" in g:
            d = (g[f"x{hz}"] - g[f"b{hz}"]).dropna()
            out[f"x{hz}"] = _f(g[f"x{hz}"].mean())
            out[f"edge{hz}"] = _f(d.mean())
            out[f"hit{hz}"] = _f((g[f"x{hz}"] > 0).mean(), 3)
            out[f"t{hz}"] = _f(_tstat_by_month(d, g.loc[d.index, "date"]), 2)
    return out


def verdict(st: dict, halves: tuple[dict, dict]) -> tuple[str, str]:
    """Statut d'un setup : validé seulement s'il bat le hasard, de façon stable sur les deux moitiés de l'historique."""
    n, e, t = st.get("n", 0), st.get("edge_R"), st.get("t_R")
    stable = all((hh.get("edge_R") or -1) > 0 for hh in halves)
    if e is None or t is None:
        return "non validé", "trop peu de cas"
    txt = f"{e:+.2f} R par trade de plus qu'une entrée au hasard (t = {t:.1f})"
    if n >= 150 and e >= 0.05 and t >= 2 and stable:
        return "validé", txt + ", stable sur les deux périodes"
    if e > 0 and t >= 1:
        return "à confirmer", txt + (", instable selon la période" if not stable else ", avantage encore fragile")
    return "non validé", txt + " : ne bat pas le hasard"


def summarize(E: pd.DataFrame, base: dict) -> dict:
    mid = E["date"].min() + (E["date"].max() - E["date"].min()) / 2
    out = []
    for key, (label, rule) in SETUPS.items():
        g = E[E["setup"] == key]
        st = _stats(g)
        halves = (_stats(g[g["date"] < mid]), _stats(g[g["date"] >= mid]))
        v, why = verdict(st, halves)
        tk = g[g["taken"].astype(bool)]
        out.append({"key": key, "label": label, "rule": rule, "all": st, "status": v, "why": why,
                    "first_half": halves[0], "second_half": halves[1],
                    "calm": _stats(tk[~tk["stress"].astype(bool)]), "stress": _stats(tk[tk["stress"].astype(bool)]),
                    "cheap": _stats(tk[tk["cheap"]]), "dear": _stats(tk[tk["dear"]]),
                    "lab_ok": _stats(tk[tk["lab_ok"]]), "lab_weak": _stats(tk[tk["lab_weak"]]),
                    "cheap_calm": _stats(tk[tk["cheap"] & ~tk["stress"].astype(bool)]),
                    "skipped": int((~g["taken"].astype(bool)).sum()),
                    "by_year": {str(y): _stats(gg) for y, gg in tk.groupby(tk["date"].dt.year)}})
    tk = E[E["taken"].astype(bool)].drop_duplicates(["ticker", "date"])
    return {"setups": out, "base": {str(k): v for k, v in base.items()}, "all": _stats(tk),
            "period": f"{E['date'].min():%m/%Y} → {E['date'].max():%m/%Y}", "mid": f"{mid:%m/%Y}", "n_events": int(len(E))}


def build(log=print) -> dict:
    t0 = time.time()
    O = H.load_ohlcv()
    if O is None:
        raise RuntimeError("Bougies historiques absentes : lancez « python3 ndx.py lab --full ».")
    mem = H.load_membership()
    panel_file = H.HIST_DIR / "panel.csv"
    panel = pd.read_csv(panel_file, parse_dates=["date"], usecols=["date", "ticker", "val_pct", "comp_pct"]) \
        if panel_file.exists() else None
    log("→ Swing : détection des setups sur l'historique et simulation des trades…")
    E, base = backtest(O, mem, panel, log)
    E.to_csv(EVENTS_FILE, index=False)
    out = summarize(E, base)
    out.update({"built": pd.Timestamp.now().isoformat(timespec="seconds"), "seconds": round(time.time() - t0),
                "method": __doc__.split("Méthode de test (sans information du futur) :")[1].strip()})
    SWING_FILE.write_text(json.dumps(out, ensure_ascii=False, default=lambda x: None), encoding="utf-8")
    log(f"✓ Swing : {out['n_events']} signaux testés en {out['seconds']} s")
    return out


def load() -> dict | None:
    return json.loads(SWING_FILE.read_text(encoding="utf-8")) if SWING_FILE.exists() else None


# ───────────────────────── Screener du jour ─────────────────────────
def live_frames(hist: dict | None, live: dict | None, tickers: list[str], rows: int = 320) -> dict | None:
    """Bougies récentes : historique + barre du jour (mise à jour pendant la séance)."""
    if hist is None:
        return None
    cols = [t for t in tickers if t in hist["Close"].columns] + [INDEX_TICKER]
    O = {f: hist[f].reindex(columns=cols).iloc[-rows:] for f in H.FIELDS}
    if live:
        O = {f: live[f].reindex(columns=cols).combine_first(O[f]) if f in live else O[f] for f in H.FIELDS}
    # barres vides (pré-ouverture : Yahoo crée la ligne du jour avant toute cotation)
    keep = O["Close"].drop(columns=[INDEX_TICKER]).notna().mean(axis=1) >= 0.5
    return {f: d[keep] for f, d in O.items()}


def screen(O: dict, stocks: pd.DataFrame, opportunities: list, regime: dict, hist_stats: dict | None,
           pf_total: float | None = None, rules: dict | None = None, extended: dict | None = None) -> dict:
    F = features(O)
    S, N = setups(F)
    stops = structural_stop(F)
    st_by = {s["key"]: s for s in (hist_stats or {}).get("setups", [])}
    ops = {o["ticker"]: o for o in opportunities}
    stress = bool((regime or {}).get("stress"))
    rules = rules or {}
    risk_pct = 0.01 * (0.5 if stress else 1.0)
    cols = list(F["c"].columns)
    last = F["c"].index[-1]
    res120 = F["h"].rolling(120, min_periods=40).max()
    # rang de force relative (perf. 63 séances relative à l'indice)
    rs63 = (F["c"].iloc[-1] / F["c"].iloc[-64] - 1) - (F["ndx"].iloc[-1] / F["ndx"].iloc[-64] - 1)
    rs_rank = rs63.rank(pct=True)
    EQ = (extended or {}).get("quotes", {})

    def ctx(t: str) -> dict:
        r = stocks.loc[t] if t in stocks.index else None
        g = lambda k: _f(r.get(k)) if r is not None else None
        op = ops.get("GOOGL" if t == "GOOG" else t, {})
        return {"company": r.get("company") if r is not None else t, "theme": r.get("theme_label") if r is not None else None,
                "verdict": op.get("verdict"), "fund_score": _f(op.get("fund_score"), 0), "lab_pct": g("lab_pct"),
                "val_pct": g("val_pct"), "rs_rank": _f(rs_rank.get(t), 3), "earnings": r.get("earnings_date") if r is not None else None}

    def gate(key: str, c: dict, rr: float) -> tuple[str, list, list]:
        pros, cons = [], []
        hs = st_by.get(key, {})
        status = hs.get("status")
        if status == "validé":
            pros.append(f"Setup validé sur l'historique ({hs.get('why')})")
        elif status == "à confirmer":
            cons.append(f"Setup à confirmer : {hs.get('why')}")
        elif status == "non validé":
            cons.append(f"Setup non validé historiquement : {hs.get('why')}")
        else:
            cons.append("Historique du setup pas encore calculé")
        excl = False
        if c["verdict"] == "Piège potentiel":
            cons.append("Fondamentaux : piège potentiel"); excl = True
        if c["lab_pct"] is not None and c["lab_pct"] < 0.4:
            cons.append(f"Score factoriel faible ({c['lab_pct'] * 100:.0f}e centile)")
        elif c["lab_pct"] is not None:
            pros.append(f"Score factoriel {c['lab_pct'] * 100:.0f}e centile")
        if c["val_pct"] is not None and c["val_pct"] <= 0.4:
            cons.append("Valorisation chère (rendement FCF parmi les 40 % les plus faibles)")
        if rr < 1.5:
            cons.append(f"Gain/risque {rr:.1f} < 1,5")
        if stress:
            cons.append("Marché sous tension : risque par trade divisé par 2")
        if c["earnings"]:
            try:
                d = (pd.Timestamp(c["earnings"]) - last).days
                if 0 <= d <= 14:
                    cons.append(f"Résultats dans {d} jours (risque de gap)")
            except (TypeError, ValueError):
                pass
        if status == "non validé" and not excl:
            excl = True
        if excl:
            return "Écarté", pros, cons
        green = status == "validé" and rr >= 1.5 and not (c["lab_pct"] is not None and c["lab_pct"] < 0.4) \
            and c["verdict"] != "Piège potentiel"
        return ("Feu vert" if green else "Sous conditions"), pros, cons

    sigs, near = [], []
    for key, M in S.items():
        for t in [t for t in cols if bool(M[t].iloc[-1])]:
            c0, a0 = float(F["c"][t].iloc[-1]), float(F["atr"][t].iloc[-1])
            stop, tgt = plan(c0, float(stops[key][t].iloc[-1]), a0, float(res120[t].iloc[-1]))
            rr = (tgt - c0) / (c0 - stop)
            cx = ctx(t)
            status, pros, cons = gate(key, cx, rr)
            size = None
            if pf_total:
                amt = pf_total * risk_pct / ((c0 - stop) / c0)
                cap = pf_total * rules.get("max_poids_ligne", 0.10)
                size = {"risk_eur": round(pf_total * risk_pct), "amount": round(min(amt, cap)), "capped": amt > cap}
            hs = st_by.get(key, {}).get("all", {})
            q = EQ.get(t)
            sigs.append({"ticker": t, **cx, "setup": key, "label": SETUPS[key][0], "status": status, "pros": pros, "cons": cons,
                         "entry": _f(c0, 2), "stop": _f(stop, 2), "target": _f(tgt, 2), "rr": _f(rr, 2),
                         "stop_pct": _f(stop / c0 - 1), "atr_pct": _f(a0 / c0), "vol_ratio": _f(F["v"][t].iloc[-1] / F["vavg"][t].iloc[-1], 2),
                         "hist_exp": hs.get("exp_R"), "hist_win": hs.get("win"), "hist_n": hs.get("n"),
                         "hist_status": st_by.get(key, {}).get("status"), "size": size,
                         "ext_pct": _f(q.get("ext_pct")) if q else None, "ext_session": q.get("ext_session") if q else None})
    order = {"Feu vert": 0, "Sous conditions": 1, "Écarté": 2}
    sigs.sort(key=lambda s: (order[s["status"]], -(s["rr"] or 0)))
    for key, M in N.items():
        for t in [t for t in cols if bool(M[t].iloc[-1])]:
            c0 = float(F["c"][t].iloc[-1])
            trig = {"pullback": f"contact de l'EMA 21 ({F['ema21'][t].iloc[-1]:.2f}) puis clôture au-dessus",
                    "breakout": f"clôture > {F['hi20'][t].iloc[-1]:.2f} avec volume ≥ 1,5× la moyenne",
                    "rs": "écart de performance ≥ 5 % sur 10 séances avec force relative au plus haut 3 mois"}[key]
            near.append({"ticker": t, **ctx(t), "setup": key, "label": NEAR[key], "price": _f(c0, 2), "trigger": trig,
                         "hist_status": st_by.get(key, {}).get("status")})
    near.sort(key=lambda s: -(s["rs_rank"] or 0))
    # lecture price action de chaque titre (table de suivi)
    pa = []
    for t in cols:
        h, l = F["h"][t].to_numpy(), F["l"][t].to_numpy()
        c0, a0 = F["c"][t].iloc[-1], F["atr"][t].iloc[-1]
        if not (np.isfinite(c0) and np.isfinite(a0)):
            continue
        L = levels(h, l, c0, a0)
        av = {}
        for d, name in ANCHORS.items():
            if F["c"].index[-1] >= pd.Timestamp(d):
                s = avwap(O, t, d).dropna()
                if len(s):
                    av[name] = _f(c0 / s.iloc[-1] - 1)
        sup = L["support"][0]["level"] if L["support"] else None
        res = L["resistance"][0]["level"] if L["resistance"] else None
        pa.append({"ticker": t, "company": ctx(t)["company"], "structure": structure(h, l), "price": _f(c0, 2),
                   "atr_pct": _f(a0 / c0), "rs_rank": _f(rs_rank.get(t), 3),
                   "vs_ema21": _f(c0 / F["ema21"][t].iloc[-1] - 1), "vs_sma50": _f(c0 / F["sma50"][t].iloc[-1] - 1),
                   "sup_atr": _f((c0 - sup) / a0, 2) if sup else None, "res_atr": _f((res - c0) / a0, 2) if res else None,
                   "sup": _f(sup, 2), "res": _f(res, 2), "avwap": av,
                   "nr7": bool(F["nr7"][t].iloc[-1]), "inside": bool(F["inside"][t].iloc[-1]),
                   "vol_ratio": _f(F["v"][t].iloc[-1] / F["vavg"][t].iloc[-1], 2),
                   "gap": _f(F["o"][t].iloc[-1] / F["pc"][t].iloc[-1] - 1),
                   "signals": [k for k, M in S.items() if bool(M[t].iloc[-1])]})
    return {"asof": last.strftime("%d/%m/%Y"), "asof_iso": last.strftime("%Y-%m-%d"), "signals": sigs, "near": near, "pa": pa, "stress": stress,
            "risk_pct": risk_pct, "pf_total": pf_total, "hist": hist_stats}


def chart(O: dict, t: str, bars: int = 130, sig: dict | None = None) -> dict:
    """Données du graphique de la fiche titre : bougies, VWAP ancrés, supports / résistances."""
    d = {f: O[f][t].iloc[-bars:] for f in H.FIELDS}
    c = O["Close"][t]
    F = features({f: O[f][[t, INDEX_TICKER]] for f in H.FIELDS})
    a0 = float(F["atr"][t].iloc[-1])
    L = levels(O["High"][t].to_numpy(), O["Low"][t].to_numpy(), float(c.iloc[-1]), a0)
    av = {}
    for anchor, name in ANCHORS.items():
        if c.index[-1] >= pd.Timestamp(anchor):
            s = avwap(O, t, anchor).reindex(d["Close"].index)
            av[name] = [_f(v, 2) for v in s]
    return {"dates": [x.strftime("%Y-%m-%d") for x in d["Close"].index],
            "o": [_f(v, 2) for v in d["Open"]], "h": [_f(v, 2) for v in d["High"]],
            "l": [_f(v, 2) for v in d["Low"]], "c": [_f(v, 2) for v in d["Close"]],
            "v": [_f(v, 0) for v in d["Volume"]], "ema21": [_f(v, 2) for v in F["ema21"][t].iloc[-bars:]],
            "avwap": av, "levels": L, "atr": _f(a0, 2), "structure": structure(O["High"][t].to_numpy(), O["Low"][t].to_numpy()),
            "signal": sig}
