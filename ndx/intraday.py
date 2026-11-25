"""Screener intraday : ce qui bouge pendant la séance, à partir de la bougie du jour de chaque titre.

Mesures (barre du jour vs historique récent) : variation, gap d'ouverture, mouvement depuis l'ouverture,
position dans le range du jour, volume relatif corrigé de l'heure, force relative contre le Nasdaq 100,
mouvement en ATR, cassure du plus haut / plus bas de 20 séances.

Limite assumée : ces signaux ne sont PAS validés sur l'historique (il faudrait des bougies de 5 minutes
sur plusieurs années). Le risk manager les traite donc comme non validés : taille plafonnée.
Outil d'analyse — pas un conseil en investissement.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from .data import INDEX_TICKER
from .report import NY, market_status

SIGNALS = {
    "gap_go": ("Gap & go", "long",
               "Gap haussier ≥ 2 % qui tient : cours au-dessus de l'ouverture, dans le haut du range, volume relatif ≥ 1,5"),
    "breakout20": ("Cassure 20 séances en séance", "long",
                   "Cours au-dessus du plus haut des 20 dernières séances, haut du range, volume relatif ≥ 1,3"),
    "rs_intraday": ("Force relative intraday", "long",
                    "Le titre bat le Nasdaq 100 d'au moins 2 points alors que l'indice baisse, avec du volume"),
    "flush": ("Capitulation puis rebond", "long",
              "Chute ≥ 2 ATR sur volume ≥ 2× la normale, mais le cours remonte nettement de son plus bas"),
    "gap_fade": ("Gap comblé", "éviter",
                 "Gap haussier ≥ 2 % entièrement reperdu : les acheteurs de l'ouverture sont piégés"),
    "breakdown20": ("Cassure du plus bas 20 séances", "éviter",
                    "Cours sous le plus bas des 20 dernières séances, sur volume"),
}


def _session_fraction(now: datetime | None = None) -> float:
    """Part de la séance écoulée (9 h 30 – 16 h, New York) ; 1 hors séance."""
    now = (now or datetime.now(NY)).astimezone(NY)
    m = now.hour * 60 + now.minute - 570
    return float(np.clip(m / 390, 0.05, 1.0)) if market_status(now)["state"] == "open" else 1.0


def _f(v, d=4):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, d) if np.isfinite(v) else None


def screen(bars: dict | None, stocks: pd.DataFrame | None = None, extended: dict | None = None,
           now: datetime | None = None) -> dict:
    """bars : {Open, High, Low, Close, Volume} (dates × titres) avec la barre du jour en dernière ligne."""
    mk = market_status(now)
    if not bars or "Close" not in bars or len(bars["Close"]) < 22:
        return {"rows": [], "market": mk, "note": "Bougies du jour indisponibles."}
    C, O, Hh, L, V = (bars[k] for k in ("Close", "Open", "High", "Low", "Volume"))
    keep = C.notna().mean(axis=1) >= 0.5                     # ligne du jour vide en pré-ouverture
    C, O, Hh, L, V = (d[keep] for d in (C, O, Hh, L, V))
    day = C.index[-1]
    frac = _session_fraction(now)
    idx_chg = _f(C[INDEX_TICKER].iloc[-1] / C[INDEX_TICKER].iloc[-2] - 1) if INDEX_TICKER in C else None
    tr = pd.concat([Hh - L, (Hh - C.shift()).abs(), (L - C.shift()).abs()]).groupby(level=0).max()
    atr = tr.iloc[-15:-1].mean()
    EQ = (extended or {}).get("quotes", {})
    rows = []
    for t in C.columns:
        if t == INDEX_TICKER or t.startswith("^"):
            continue
        last, prev, op, hi, lo, vol = C[t].iloc[-1], C[t].iloc[-2], O[t].iloc[-1], Hh[t].iloc[-1], L[t].iloc[-1], V[t].iloc[-1]
        if not all(np.isfinite([last, prev, op, hi, lo])) or prev <= 0 or op <= 0:
            continue
        avg_v = V[t].iloc[-21:-1].mean()
        rvol = vol / (avg_v * frac) if avg_v and np.isfinite(vol) and avg_v > 0 else np.nan
        chg, gap, from_open = last / prev - 1, op / prev - 1, last / op - 1
        rng = (last - lo) / (hi - lo) if hi > lo else 0.5
        rel = chg - idx_chg if idx_chg is not None else np.nan
        hi20, lo20 = Hh[t].iloc[-21:-1].max(), L[t].iloc[-21:-1].min()
        a = atr.get(t, np.nan)
        move_atr = (last - prev) / a if a and np.isfinite(a) and a > 0 else np.nan
        sig = []
        if gap >= 0.02 and from_open >= 0 and rng >= 0.6 and rvol >= 1.5:
            sig.append("gap_go")
        if last > hi20 and rng >= 0.7 and rvol >= 1.3:
            sig.append("breakout20")
        if idx_chg is not None and idx_chg <= 0 and rel >= 0.02 and rvol >= 1.2:
            sig.append("rs_intraday")
        if move_atr <= -2 and rvol >= 2 and rng >= 0.4:
            sig.append("flush")
        if gap >= 0.02 and last <= prev:
            sig.append("gap_fade")
        if last < lo20 and rvol >= 1.2:
            sig.append("breakdown20")
        # plan indicatif pour un signal d'achat : stop sous le plus bas du jour (ou sous l'ouverture pour un gap & go)
        stop = min(op, lo) * 0.998 if "gap_go" in sig else lo * 0.998
        risk = last - stop
        r = stocks.loc[t] if stocks is not None and t in stocks.index else None
        q = EQ.get(t, {})
        rows.append({
            "ticker": t, "company": r.get("company") if r is not None else t,
            "theme": r.get("theme_label") if r is not None else None,
            "price": _f(last, 2), "chg": _f(chg), "gap": _f(gap), "from_open": _f(from_open), "range_pos": _f(rng, 2),
            "rvol": _f(rvol, 2), "rel": _f(rel), "move_atr": _f(move_atr, 2), "hi20": _f(hi20, 2), "lo20": _f(lo20, 2),
            "ext_pct": _f(q.get("ext_pct")), "signals": sig,
            "stop": _f(stop, 2) if risk > 0 else None, "target": _f(last + 2 * risk, 2) if risk > 0 else None,
            "earnings_date": r.get("earnings_date") if r is not None else None,
        })
    rows.sort(key=lambda x: (-len([s for s in x["signals"] if SIGNALS[s][1] == "long"]), -(x["rvol"] or 0)))
    return {"rows": rows, "market": mk, "day": f"{day:%Y-%m-%d}", "index_chg": idx_chg, "session_fraction": round(frac, 2),
            "signals": {k: {"label": v[0], "side": v[1], "rule": v[2]} for k, v in SIGNALS.items()},
            "note": ("Séance en cours : volume relatif corrigé de l'heure (approximation linéaire)."
                     if mk["state"] == "open" else "Hors séance : chiffres de la dernière séance complète.")}
