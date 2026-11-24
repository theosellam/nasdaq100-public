"""Lecture du contexte de marché (novembre 2026) : régime, divergence plus hauts / plus bas,
concentration, paniers IA vs reste, rentabilité du capex IA, réactions aux événements, IPO d'Anthropic."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fundamentals as fm
from .data import CONTEXT_TICKERS, INDEX_TICKER
from .themes import ANT_IPO, MIDTERMS_DATE, POLICY, RECORD_DATE, THEMES, load_events

US_HOLIDAYS = ["2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15"]


def _f(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def _ret_since(s: pd.Series, date: str) -> float:
    s = s.dropna()
    before = s[s.index < pd.Timestamp(date)]
    return s.iloc[-1] / before.iloc[-1] - 1 if len(before) else np.nan


def _day_ret(s: pd.Series, date: str) -> float:
    s = s.dropna()
    d = pd.Timestamp(date)
    before, on = s[s.index < d], s[s.index >= d]
    return on.iloc[0] / before.iloc[-1] - 1 if len(before) and len(on) else np.nan


def _basket(rets: pd.DataFrame, tickers: list[str]) -> pd.Series:
    cols = [t for t in tickers if t in rets]
    return (1 + rets[cols].mean(axis=1).fillna(0)).cumprod()


def _breadth_extremes(px: pd.DataFrame, days: int = 130) -> pd.DataFrame:
    hi, lo = px.rolling(252, min_periods=200).max(), px.rolling(252, min_periods=200).min()
    ath = px.cummax()
    valid = px.notna() & hi.notna()
    out = pd.DataFrame({
        "new_highs": ((px >= hi * 0.999) & valid).sum(axis=1),
        "new_lows": ((px <= lo * 1.001) & valid).sum(axis=1),
        "bear": ((px / ath - 1 <= -0.20) & px.notna()).sum(axis=1),
        "deep": ((px / ath - 1 <= -0.40) & px.notna()).sum(axis=1),
        "n": px.notna().sum(axis=1),
    })
    return out.iloc[-days:]


def _capex_table(stocks: pd.DataFrame, tickers: list[str]) -> list[dict]:
    cols = ["company", "price", "r_30j", "capex_ttm", "capex_yoy", "capex_to_rev", "capex_coverage", "ocf_ttm",
            "fcf_ttm", "fcf_last_q", "fcf_margin", "rev_yoy", "net_debt", "debt_yoy", "dilution_yoy", "market_cap"]
    return [{"ticker": t, **{c: stocks.at[t, c] for c in cols if c in stocks}} for t in tickers if t in stocks.index]


def _ntrading_after(start: str, n: int) -> str:
    days = pd.bdate_range(pd.Timestamp(start), periods=n + 10, freq="C", holidays=US_HOLIDAYS)
    return days[n].strftime("%Y-%m-%d")


def run(stocks: pd.DataFrame, close: pd.DataFrame, ctx: pd.DataFrame, fund: dict, index: dict) -> dict:
    bench = close[INDEX_TICKER].dropna()
    px = close.drop(columns=[INDEX_TICKER, "GOOG"], errors="ignore")
    rets = px.pct_change(fill_method=None)
    start = pd.Timestamp(RECORD_DATE) - pd.Timedelta(days=150)

    # ── Régime
    rec_level = bench.max()
    rec_date = bench.idxmax()
    dd = bench.iloc[-1] / rec_level - 1
    phase = ("Marché baissier (≤ −20 %)" if dd <= -0.20 else "Correction (≤ −10 %)" if dd <= -0.10
             else "Repli (≤ −5 %)" if dd <= -0.05 else "Proche des records")
    macro = {}
    for t, label in CONTEXT_TICKERS.items():
        if t in ctx and ctx[t].notna().any():
            s = ctx[t].dropna()
            macro[t] = {"label": label, "last": _f(s.iloc[-1]),
                        "chg_1j": _f(s.iloc[-1] - s.iloc[-2]) if t in ("^VXN", "^VIX", "^TNX") else _f(s.iloc[-1] / s.iloc[-2] - 1),
                        "chg_kind": "pts" if t in ("^VXN", "^VIX", "^TNX") else "pct",
                        "since_record": _f(_ret_since(s, RECORD_DATE)) if t not in ("ANT",) else None}
    vxn = macro.get("^VXN", {}).get("last")
    stress = bool(index.get("dist_sma200", 0) < 0 or (vxn or 0) > 30 or dd <= -0.10)

    # ── Divergence plus hauts / plus bas et ampleur
    ext = _breadth_extremes(px)
    ew = (1 + rets.mean(axis=1).fillna(0)).cumprod()
    cw = bench / bench.iloc[0]
    since = ew.index >= start
    # base 100 au record ; chaque série filtrée sur ses propres dates (l'indice peut avoir un jour de retard sur les actions)
    rb = lambda s: s[s.index >= start] / s[s.index < pd.Timestamp(RECORD_DATE)].iloc[-1] * 100
    eq_vs_cap = pd.DataFrame({"ndx": rb(cw), "ew": rb(ew)}).ffill().dropna()

    # ── Paniers thématiques (équipondérés, base 100 au record)
    themes, theme_hist = [], {}
    for key, th in THEMES.items():
        b = _basket(rets, th["tickers"])
        g = stocks[stocks["theme"] == key]
        base = b[b.index < pd.Timestamp(RECORD_DATE)]
        theme_hist[key] = (b[since] / (base.iloc[-1] if len(base) else b[since].iloc[0]) * 100).round(2).tolist()
        themes.append({
            "key": key, "label": th["label"], "ai": th["ai"], "n": len(g), "weight": _f(g["weight"].sum()),
            "tickers": th["tickers"],
            "r_1J": _f(b.iloc[-1] / b.iloc[-2] - 1), "r_5j": _f(b.iloc[-1] / b.iloc[-6] - 1),
            "r_record": _f(_ret_since(b, RECORD_DATE)), "r_midterms": _f(_ret_since(b, MIDTERMS_DATE)),
            "r_YTD": _f(b.iloc[-1] / b[b.index.year < b.index[-1].year].iloc[-1] - 1),
            "dd_52w": _f(b.iloc[-1] / b.iloc[-252:].max() - 1),
            "pct_bear": _f((g["dd_from_ath"] <= -0.20).mean()),
            "os_10j": _f(g["os_10j"].mean()), "upside": _f(g["upside"].median()) if "upside" in g else None,
            "pe_fwd": _f(g["pe_fwd"].median()) if "pe_fwd" in g else None,
            "fcf_margin": _f(g["fcf_margin"].median()) if "fcf_margin" in g else None,
            "rev_yoy": _f(g["rev_yoy"].median()) if "rev_yoy" in g else None,
        })
    ai_cols = [t for k, th in THEMES.items() if th["ai"] for t in th["tickers"]]
    non_ai = [t for k, th in THEMES.items() if not th["ai"] for t in th["tickers"]]
    ai_b, non_b = _basket(rets, ai_cols), _basket(rets, non_ai)
    spread = (ai_b / non_b)[since]
    ai_vs_rest = {"dates": [d.strftime("%Y-%m-%d") for d in spread.index],
                  "ratio": (spread / spread.iloc[0] * 100).round(2).tolist(),
                  "ai_record": _f(_ret_since(ai_b, RECORD_DATE)), "rest_record": _f(_ret_since(non_b, RECORD_DATE)),
                  "ai_midterms": _f(_ret_since(ai_b, MIDTERMS_DATE)), "rest_midterms": _f(_ret_since(non_b, MIDTERMS_DATE))}

    # ── Rentabilité du capex IA (hyperscalers = clients de la chaîne IA)
    hyp, neo = THEMES["hyperscalers"]["tickers"], THEMES["neoclouds"]["tickers"]
    fd = fund.get("data", {})
    hq = fm.capex_quarters(fd, hyp)
    h = stocks.loc[[t for t in hyp if t in stocks.index]]
    n = stocks.loc[[t for t in neo if t in stocks.index]]
    neocloud_ok = bool(((n["fcf_ttm"] > 0) | (n["debt_yoy"] < 0.3)).all()) if len(n) and "fcf_ttm" in n else None
    customers = {
        "capex_ttm": _f(h["capex_ttm"].sum()), "ocf_ttm": _f(h["ocf_ttm"].sum()), "fcf_ttm": _f(h["fcf_ttm"].sum()),
        "coverage": _f(h["ocf_ttm"].sum() / h["capex_ttm"].sum()) if h["capex_ttm"].sum() else None,
        "capex_yoy": _f(sum(q["capex"] for q in hq[-1:]) / sum(q["capex"] for q in hq[-5:-4]) - 1) if len(hq) >= 5 else
                     _f(h["capex_yoy"].median()),
        "capex_to_rev": _f(h["capex_ttm"].sum() / h["rev_ttm"].sum()) if h["rev_ttm"].sum() else None,
        "neocloud_ok": neocloud_ok,
    }
    capex = {"hyperscalers": _capex_table(stocks, hyp), "neoclouds": _capex_table(stocks, neo),
             "quarters": hq, "summary": customers,
             "suppliers": _capex_table(stocks, THEMES["ai_compute"]["tickers"] + THEMES["memory"]["tickers"])}

    # ── Événements : réaction le jour J et depuis
    events = []
    series = {"NDX": bench, **{k: _basket(rets, th["tickers"]) for k, th in THEMES.items()}}
    if "NVDA" in px:
        series["NVDA"] = px["NVDA"]
    for ev in load_events():
        d = ev["date"]
        if pd.Timestamp(d) > bench.index[-1]:
            continue
        events.append({**ev,
                       "day": {k: _f(_day_ret(s, d)) for k, s in series.items()},
                       "since": {k: _f(_ret_since(s, d)) for k, s in series.items()}})

    # ── Expositions politiques depuis les midterms
    policy = []
    for key, p in POLICY.items():
        g = stocks.loc[[t for t in p["tickers"] if t in stocks.index]]
        b = _basket(rets, p["tickers"])
        policy.append({"key": key, "label": p["label"], "tickers": p["tickers"],
                       "r_midterms": _f(_ret_since(b, MIDTERMS_DATE)), "r_5j": _f(b.iloc[-1] / b.iloc[-6] - 1),
                       "r_1J": _f(b.iloc[-1] / b.iloc[-2] - 1), "weight": _f(g["weight"].sum()),
                       "upside": _f(g["upside"].median()) if "upside" in g else None})

    # ── Contributions à la performance de l'indice
    w_now = stocks["weight"].fillna(0)
    contrib = {}
    for key, col in (("1J", "r_1J"), ("record", None)):
        r = stocks[col] if col else px.apply(lambda s: _ret_since(s, RECORD_DATE)).reindex(stocks.index)
        w_then = w_now / (1 + r)
        w_then = w_then / w_then.sum()
        c = (w_then * r).dropna().sort_values()
        contrib[key] = {"top": [{"ticker": t, "company": stocks.at[t, "company"], "contrib": _f(v), "ret": _f(r[t])}
                                for t, v in c.iloc[::-1].head(8).items()],
                        "bottom": [{"ticker": t, "company": stocks.at[t, "company"], "contrib": _f(v), "ret": _f(r[t])}
                                   for t, v in c.head(8).items()]}

    # ── Anthropic (ANT)
    ant = None
    if "ANT" in ctx and ctx["ANT"].notna().any():
        s = ctx["ANT"].dropna()
        ant = {**ANT_IPO, "last": _f(s.iloc[-1]), "first_close": _f(s.iloc[0]),
               "vs_ipo": _f(s.iloc[-1] / ANT_IPO["ipo_price"] - 1), "vs_open": _f(s.iloc[-1] / ANT_IPO["first_open"] - 1),
               "r_1J": _f(s.iloc[-1] / s.iloc[-2] - 1) if len(s) > 1 else None,
               "sessions": len(s), "eligible_from": _ntrading_after(ANT_IPO["date"], 15),
               "history": {"dates": [d.strftime("%Y-%m-%d") for d in s.index], "close": [round(float(v), 2) for v in s]}}

    # ── Lecture automatique
    last = ext.iloc[-1]
    nb = int(last["n"])
    notes = [
        f"Nasdaq 100 à {dd * 100:+.1f} % de son record du {rec_date:%d/%m/%Y} : {phase.lower()}.",
        f"{int(last['bear'])} titres sur {nb} sont à plus de 20 % sous leur plus haut (2 ans) et {int(last['deep'])} à plus de 40 % ; "
        f"{int(last['new_highs'])} nouveaux plus hauts 52 s. contre {int(last['new_lows'])} nouveaux plus bas aujourd'hui.",
        f"Depuis le record : Nasdaq 100 équipondéré {eq_vs_cap['ew'].iloc[-1] - 100:+.1f} % vs indice pondéré {eq_vs_cap['ndx'].iloc[-1] - 100:+.1f} % : "
        + ("le titre moyen fait pire que les géants (marché étroit)." if eq_vs_cap['ew'].iloc[-1] < eq_vs_cap['ndx'].iloc[-1]
           else "la baisse vient surtout des géants de l'IA, le titre moyen résiste mieux."),
        f"Depuis les midterms : chaîne IA {ai_vs_rest['ai_midterms'] * 100:+.1f} % vs reste de l'indice {ai_vs_rest['rest_midterms'] * 100:+.1f} %.",
    ]
    if customers.get("coverage"):
        notes.append(f"Hyperscalers : capex {customers['capex_ttm'] / 1e9:,.0f} Md$ sur 12 mois "
                     f"({(customers.get('capex_to_rev') or 0) * 100:.0f} % du CA), couvert {customers['coverage']:.2f}× par leur flux opérationnel ; "
                     f"FCF cumulé {customers['fcf_ttm'] / 1e9:,.0f} Md$.")
    if ant:
        notes.append(f"Anthropic (ANT) : {ant['last']:.2f} $, {ant['vs_ipo'] * 100:+.1f} % vs prix d'IPO, "
                     f"{ant['vs_open'] * 100:+.1f} % vs 1er cours ; candidate au Nasdaq 100 à partir du ~{pd.Timestamp(ant['eligible_from']):%d/%m}.")
    if vxn:
        notes.append(f"VXN à {vxn:.1f} : {'stress élevé, tailles de position réduites de moitié' if vxn > 30 else 'volatilité contenue'}.")

    return {
        "regime": {"record": _f(rec_level), "record_date": rec_date.strftime("%Y-%m-%d"), "dd": _f(dd), "phase": phase,
                   "days_since": int((bench.index >= rec_date).sum() - 1), "stress": stress},
        "macro": macro, "notes": notes,
        "extremes": {"dates": [d.strftime("%Y-%m-%d") for d in ext.index],
                     **{c: ext[c].astype(int).tolist() for c in ("new_highs", "new_lows", "bear", "deep", "n")}},
        "eq_vs_cap": {"dates": [d.strftime("%Y-%m-%d") for d in eq_vs_cap.index],
                      "ndx": eq_vs_cap["ndx"].round(2).tolist(), "ew": eq_vs_cap["ew"].round(2).tolist()},
        "themes": themes,
        "theme_hist": {"dates": [d.strftime("%Y-%m-%d") for d in ew.index[since]], **theme_hist},
        "ai_vs_rest": ai_vs_rest, "capex": capex, "customers": customers,
        "events": events, "policy": policy, "contrib": contrib, "ant": ant,
        "record_date_ref": RECORD_DATE, "midterms_date": MIDTERMS_DATE,
    }
