"""Analyse du Nasdaq 100 : métriques par titre, survente multi-horizons, analystes,
secteurs, ampleur du marché et signaux."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from . import context as ctxmod
from . import fundamentals as fm
from . import indicators as ind
from . import opportunities as opp
from . import strategies as strat
from .data import INDEX_TICKER
from .themes import POLICY, THEMES, policy_of, theme_of

PERIODS = {"1J": 1, "1S": 5, "1M": 21, "3M": 63, "6M": 126, "1A": 252}
HORIZONS = (5, 10, 30)            # horizons de survente, en séances
REC_COLS = ["strongBuy", "buy", "hold", "sell", "strongSell"]


@dataclass
class Analysis:
    asof: pd.Timestamp
    stocks: pd.DataFrame
    sectors: pd.DataFrame
    index: dict
    breadth: dict
    breadth_history: pd.DataFrame
    index_history: pd.DataFrame
    signals: dict[str, pd.DataFrame]
    correlation: pd.DataFrame
    analyst_actions: list[dict] = field(default_factory=list)
    earnings: list[dict] = field(default_factory=list)
    analysts_updated: str | None = None
    context: dict | None = None
    opportunities: list[dict] = field(default_factory=list)
    fundamentals_updated: str | None = None
    strategies: dict | None = None


# ───────────────────────── Métriques techniques ─────────────────────────
def _stock_metrics(s: pd.Series, bench: pd.Series, vol: pd.Series) -> dict:
    s = s.dropna()
    last = s.iloc[-1]
    sma20, sma50, sma200 = ind.sma(s, 20), ind.sma(s, 50), ind.sma(s, 200)
    sd20 = s.rolling(20).std()
    macd_line, macd_sig, _ = ind.macd(s)
    hi52, lo52 = s.iloc[-252:].max(), s.iloc[-252:].min()
    daily = s.pct_change(fill_method=None)
    dvol = daily.iloc[-252:].std()
    m = {"price": last, "prev_close": s.iloc[-2] if len(s) > 1 else np.nan}
    for label, days in PERIODS.items():
        m[f"r_{label}"] = ind.period_return(s, days)
    m["r_YTD"] = ind.ytd_return(s)
    m["mom_12_1"] = (s.iloc[-22] / s.iloc[-253] - 1) if len(s) > 253 else np.nan
    # Survente / surachat multi-horizons : rendement et z-score (rendement / volatilité attendue)
    for h in HORIZONS:
        r = ind.period_return(s, h)
        m[f"r_{h}j"] = r
        m[f"z_{h}j"] = r / (dvol * np.sqrt(h)) if dvol else np.nan
        m[f"min_{h}j"] = s.iloc[-h - 1:].min() / s.iloc[-h - 1] - 1 if len(s) > h else np.nan
    # Séances consécutives de baisse (négatif) ou de hausse (positif)
    sign = np.sign(daily.iloc[-30:].fillna(0).values)[::-1]
    streak = 0
    for v in sign:
        if v == 0 or (streak and np.sign(streak) != v):
            break
        streak += int(v)
    m.update({
        "streak": streak,
        "rsi2": ind.rsi(s, 2).iloc[-1],
        "rsi14": ind.rsi(s).iloc[-1],
        "rsi14_min10": ind.rsi(s).iloc[-10:].min(),
        "bb_pct": (last - (sma20.iloc[-1] - 2 * sd20.iloc[-1])) / (4 * sd20.iloc[-1]) if sd20.iloc[-1] else np.nan,
        "sma50": sma50.iloc[-1], "sma200": sma200.iloc[-1],
        "dist_sma20": last / sma20.iloc[-1] - 1,
        "dist_sma50": last / sma50.iloc[-1] - 1,
        "dist_sma200": last / sma200.iloc[-1] - 1,
        "macd_bull": bool(macd_line.iloc[-1] > macd_sig.iloc[-1]),
        "vol_1a": ind.volatility(s),
        "maxdd_1a": ind.max_drawdown(s),
        "dd_from_high": last / hi52 - 1,
        "from_low": last / lo52 - 1,
        "beta": ind.beta(s, bench),
        "sharpe_1a": ind.sharpe(s),
        "rel_3M": ind.period_return(s, 63) - ind.period_return(bench, 63),
        "vol_ratio": vol.iloc[-1] / vol.iloc[-21:-1].mean() if vol.notna().sum() > 21 else np.nan,
        "new_high": bool(last >= hi52 * 0.999),
        "new_low": bool(last <= lo52 * 1.001),
        "golden_cross": bool(((sma50 > sma200) & (sma50.shift() <= sma200.shift())).iloc[-10:].any()),
        "death_cross": bool(((sma50 < sma200) & (sma50.shift() >= sma200.shift())).iloc[-10:].any()),
        # Éléments pour les points d'entrée
        "dvol": dvol,
        "hi52": hi52, "lo52": lo52,
        "hi_2a": s.max(), "dd_from_ath": last / s.max() - 1,
        "sma20": sma20.iloc[-1],
        "sma200_slope": sma200.iloc[-1] / sma200.iloc[-21] - 1 if sma200.notna().sum() > 21 else np.nan,
        "sma50_slope": sma50.iloc[-1] / sma50.iloc[-11] - 1 if sma50.notna().sum() > 11 else np.nan,
        "low_10": s.iloc[-10:].min(), "low_20": s.iloc[-20:].min(),
        "reclaim_sma20": bool(last > sma20.iloc[-1] and (s.iloc[-4:-1] < sma20.iloc[-4:-1]).any()),
        "rsi_reclaim30": bool(ind.rsi(s).iloc[-1] > 30 and (ind.rsi(s).iloc[-6:-1] < 30).any()),
        "up_day_vol": bool(daily.iloc[-1] > 0 and (vol.iloc[-1] / vol.iloc[-21:-1].mean() if vol.notna().sum() > 21 else 0) > 1.3),
    })
    return m


def _pct_rank(s: pd.Series, ascending: bool = True) -> pd.Series:
    return s.rank(pct=True, ascending=ascending) * 100


def _score(df: pd.DataFrame) -> pd.Series:
    """Score technique 0–100 : momentum (50 %), tendance (30 %), risque (20 %)."""
    momentum = (_pct_rank(df["mom_12_1"]).fillna(50) * 0.5
                + _pct_rank(df["r_6M"]).fillna(50) * 0.3
                + _pct_rank(df["r_3M"]).fillna(50) * 0.2)
    trend = ((df["dist_sma50"] > 0).astype(int) * 35
             + (df["dist_sma200"] > 0).astype(int) * 40
             + (df["sma50"] > df["sma200"]).astype(int) * 15
             + df["macd_bull"].astype(int) * 10)
    risk = 100 - _pct_rank(df["vol_1a"]).fillna(50)
    return (momentum * 0.5 + trend * 0.3 + risk * 0.2).round(1)


def _oversold_scores(df: pd.DataFrame) -> None:
    """Score de survente 0–100 par horizon (100 = le plus survendu du Nasdaq 100).
    Combine la baisse normalisée par la volatilité (z-score), le RSI et l'écart à la moyenne."""
    cfg = {5: ("rsi2", "dist_sma20"), 10: ("rsi14", "dist_sma20"), 30: ("rsi14", "dist_sma50")}
    for h, (rsi_col, dist_col) in cfg.items():
        score = (_pct_rank(df[f"z_{h}j"], ascending=False).fillna(50) * 0.55
                 + _pct_rank(df[rsi_col], ascending=False).fillna(50) * 0.25
                 + _pct_rank(df[dist_col], ascending=False).fillna(50) * 0.20)
        df[f"os_{h}j"] = score.round(1)
        rsi_thr = 10 if rsi_col == "rsi2" else 30
        df[f"oversold_{h}j"] = (df[f"z_{h}j"] <= -1.5) | (df[rsi_col] < rsi_thr)
        df[f"overbought_{h}j"] = (df[f"z_{h}j"] >= 1.5) | (df[rsi_col] > 100 - rsi_thr)


# ───────────────────────── Analystes ─────────────────────────
def _analyst_metrics(a: dict, price: float) -> dict:
    if not a:
        return {}
    m = {k: a.get(k) for k in ["target_mean", "target_median", "target_high", "target_low",
                               "n_analysts", "rec_mean", "rec_key", "pe_ttm", "peg", "ps",
                               "margin", "rev_growth", "eps_growth", "eps_fwd", "short_float", "div_yield",
                               "debt_to_equity", "current_ratio", "roe", "inst_own"]}
    tm = m.get("target_mean")
    if tm and price:
        m["upside"] = tm / price - 1
        m["upside_low"] = (m["target_low"] or np.nan) / price - 1
        m["upside_high"] = (m["target_high"] or np.nan) / price - 1
    if m.get("eps_fwd") and m["eps_fwd"] > 0 and price:
        m["pe_fwd"] = price / m["eps_fwd"]          # recalculé avec le dernier cours
    recs = {r.get("period"): r for r in a.get("recommendations", [])}
    if "0m" in recs:
        cur = recs["0m"]
        tot = sum(cur.get(c) or 0 for c in REC_COLS)
        m.update({c: cur.get(c) or 0 for c in REC_COLS})
        m["pct_buy"] = ((cur.get("strongBuy") or 0) + (cur.get("buy") or 0)) / tot if tot else np.nan
        m["pct_sell"] = ((cur.get("sell") or 0) + (cur.get("strongSell") or 0)) / tot if tot else np.nan
        old = recs.get("-3m")
        if old:
            tot_o = sum(old.get(c) or 0 for c in REC_COLS)
            if tot_o and tot:
                m["rec_trend_3m"] = m["pct_buy"] - ((old.get("strongBuy") or 0) + (old.get("buy") or 0)) / tot_o
    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    recent = [x for x in a.get("actions", []) if (x.get("date") or "") >= cutoff]
    m["upgrades_30j"] = sum(1 for x in recent if x.get("Action") == "up")
    m["downgrades_30j"] = sum(1 for x in recent if x.get("Action") == "down")
    revs = [x["currentPriceTarget"] / x["priorPriceTarget"] - 1 for x in recent
            if x.get("currentPriceTarget") and x.get("priorPriceTarget")]
    m["tgt_rev_30j"] = float(np.mean(revs)) if revs else np.nan
    m["tgt_changes_30j"] = len(revs)
    for key, out in (("eps_est", "eps"), ("rev_est", "rev")):
        rows = {r.get("period"): r for r in a.get(key, [])}
        m[f"{out}_g_cy"] = (rows.get("0y") or {}).get("growth")
        m[f"{out}_g_ny"] = (rows.get("+1y") or {}).get("growth")
        m[f"{out}_g_nq"] = (rows.get("0q") or {}).get("growth")
    ts = a.get("earnings_ts")
    if ts:
        m["earnings_date"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return m


def _analyst_score(df: pd.DataFrame) -> pd.Series:
    """Score analystes 0–100 : potentiel (40 %), consensus (30 %), révisions d'objectifs (15 %),
    évolution des recommandations sur 3 mois (15 %)."""
    if "upside" not in df:
        return pd.Series(np.nan, index=df.index)
    s = (_pct_rank(df["upside"]).fillna(50) * 0.40
         + _pct_rank(df["rec_mean"], ascending=False).fillna(50) * 0.30
         + _pct_rank(df.get("tgt_rev_30j", pd.Series(np.nan, index=df.index))).fillna(50) * 0.15
         + _pct_rank(df.get("rec_trend_3m", pd.Series(np.nan, index=df.index))).fillna(50) * 0.15)
    return s.where(df["upside"].notna()).round(1)


# ───────────────────────── Ampleur ─────────────────────────
def _breadth_history(close: pd.DataFrame, days: int = 252) -> pd.DataFrame:
    px = close.drop(columns=[INDEX_TICKER], errors="ignore")
    above50 = (px > px.rolling(50).mean()).where(px.rolling(50).count() == 50)
    above200 = (px > px.rolling(200).mean()).where(px.rolling(200).count() == 200)
    rsi = px.apply(lambda c: ind.rsi(c.dropna()).reindex(px.index))
    out = pd.DataFrame({
        "above50": above50.mean(axis=1) * 100,
        "above200": above200.mean(axis=1) * 100,
        "oversold": (rsi < 30).sum(axis=1).where(rsi.notna().sum(axis=1) > 50),
        "overbought": (rsi > 70).sum(axis=1).where(rsi.notna().sum(axis=1) > 50),
    })
    return out.dropna().iloc[-days:]


# ───────────────────────── Orchestration ─────────────────────────
def run(cons: pd.DataFrame, close: pd.DataFrame, volume: pd.DataFrame,
        caps: pd.Series, analysts: dict | None = None, fundamentals: dict | None = None,
        ctx: pd.DataFrame | None = None, lab: dict | None = None) -> Analysis:
    analysts = analysts or {"updated": None, "data": {}}
    fundamentals = fundamentals or {"updated": None, "data": {}}
    fdata = fundamentals.get("data", {})
    adata = analysts.get("data", {})
    bench = close[INDEX_TICKER].dropna()
    rows = []
    for _, c in cons.iterrows():
        t = c["ticker"]
        if t not in close or close[t].dropna().shape[0] < 60:
            continue
        m = _stock_metrics(close[t], bench, volume.get(t, pd.Series(dtype=float)))
        m.update(_analyst_metrics(adata.get(t, {}), m["price"]))
        rows.append({"ticker": t, "company": c["company"], "sector": c["sector"],
                     "subsector": c["subsector"], **m})
    stocks = pd.DataFrame(rows).set_index("ticker")
    stocks["market_cap"] = caps.reindex(stocks.index)
    stocks["weight"] = stocks["market_cap"] / stocks["market_cap"].sum()
    fund = pd.DataFrame({t: fm.metrics(fdata.get(t, {}), stocks.at[t, "market_cap"]) for t in stocks.index}).T
    for c in fund.columns:
        stocks[c] = fund[c] if c == "fin_quarter" else pd.to_numeric(fund[c], errors="coerce")
    themes_ = [theme_of(t) for t in stocks.index]
    stocks["theme"] = pd.Series(themes_, index=stocks.index, dtype=object)
    stocks["theme_label"] = [THEMES[k]["label"] if isinstance(k, str) else "—" for k in themes_]
    stocks["policy"] = [", ".join(POLICY[k]["label"].split(" (")[0] for k in policy_of(t)) for t in stocks.index]
    stocks["score"] = _score(stocks)
    stocks["score_analysts"] = _analyst_score(stocks)
    _oversold_scores(stocks)
    stocks = stocks.sort_values("market_cap", ascending=False, na_position="last")

    def wavg(g: pd.DataFrame, col: str) -> float:
        if col not in g:
            return float("nan")
        w, v = g["market_cap"].fillna(0), g[col]
        ok = v.notna() & (w > 0)
        return float(np.average(v[ok], weights=w[ok])) if ok.any() else float(v.mean())

    sectors = pd.DataFrame([{
        "sector": name, "n": len(g), "weight": g["weight"].sum(),
        **{f"r_{p}": wavg(g, f"r_{p}") for p in ["1J", "1S", "1M", "3M", "YTD", "1A"]},
        **{f"r_{h}j": wavg(g, f"r_{h}j") for h in HORIZONS},
        "pct_above200": (g["dist_sma200"] > 0).mean() * 100,
        "avg_rsi": g["rsi14"].mean(),
        "upside": wavg(g, "upside"),
        "pe_fwd": float(g["pe_fwd"].median()) if "pe_fwd" in g else float("nan"),
    } for name, g in stocks.groupby("sector")]).set_index("sector").sort_values("weight", ascending=False)

    sma50, sma200 = ind.sma(bench, 50), ind.sma(bench, 200)
    up = wavg(stocks, "upside")
    index = {
        "level": bench.iloc[-1], "prev_close": bench.iloc[-2],
        **{f"r_{p}": ind.period_return(bench, d) for p, d in PERIODS.items()},
        **{f"r_{h}j": ind.period_return(bench, h) for h in HORIZONS},
        "r_YTD": ind.ytd_return(bench),
        "rsi14": ind.rsi(bench).iloc[-1],
        "dist_sma50": bench.iloc[-1] / sma50.iloc[-1] - 1,
        "dist_sma200": bench.iloc[-1] / sma200.iloc[-1] - 1,
        "vol_1a": ind.volatility(bench),
        "vol_1m": ind.volatility(bench, 21),
        "dd_from_high": bench.iloc[-1] / bench.max() - 1,
        "maxdd_1a": ind.max_drawdown(bench),
        "trend": ("Haussière" if bench.iloc[-1] > sma200.iloc[-1] and sma50.iloc[-1] > sma200.iloc[-1]
                  else "Baissière" if bench.iloc[-1] < sma200.iloc[-1] and sma50.iloc[-1] < sma200.iloc[-1]
                  else "Neutre"),
        "analyst_upside": up,
        "analyst_target": bench.iloc[-1] * (1 + up) if pd.notna(up) else None,
        "pct_buy": float(stocks["pct_buy"].mean()) if "pct_buy" in stocks else None,
        "pe_fwd": wavg(stocks.assign(inv_pe=1 / stocks["pe_fwd"]) if "pe_fwd" in stocks else stocks, "inv_pe"),
    }
    if index["pe_fwd"] and pd.notna(index["pe_fwd"]):
        index["pe_fwd"] = 1 / index["pe_fwd"]   # PER pondéré (moyenne harmonique)
    index_history = pd.DataFrame({"close": bench, "sma50": sma50, "sma200": sma200,
                                  "drawdown": ind.drawdown_series(bench)}).iloc[-252:]

    d1 = stocks["r_1J"]
    breadth = {
        "advancers": int((d1 > 0).sum()), "decliners": int((d1 < 0).sum()),
        "above50": (stocks["dist_sma50"] > 0).mean() * 100,
        "above200": (stocks["dist_sma200"] > 0).mean() * 100,
        "new_highs": int(stocks["new_high"].sum()), "new_lows": int(stocks["new_low"].sum()),
        "overbought": int((stocks["rsi14"] > 70).sum()), "oversold": int((stocks["rsi14"] < 30).sum()),
        **{f"oversold_{h}j": int(stocks[f"oversold_{h}j"].sum()) for h in HORIZONS},
        "top10_weight": stocks["weight"].nlargest(10).sum(),
        "ew_vs_cw_3M": stocks["r_3M"].mean() - index["r_3M"],
    }

    lab_today = {r["ticker"]: r for r in (lab or {}).get("today", [])}
    lab_today.setdefault("GOOG", lab_today.get("GOOGL"))
    stocks["lab_pct"] = [lab_today.get(t, {}) and lab_today[t].get("pct") if lab_today.get(t) else np.nan for t in stocks.index]
    stocks["lab_q"] = [lab_today[t].get("quintile") if lab_today.get(t) else np.nan for t in stocks.index]
    stocks["val_pct"] = [lab_today[t].get("val_fcf_pct") if lab_today.get(t) else np.nan for t in stocks.index]
    context = ctxmod.run(stocks, close, ctx if ctx is not None else pd.DataFrame(), fundamentals, index)
    opportunities = opp.run(stocks, context["customers"], context["regime"]["stress"])
    ov = {o["ticker"]: o for o in opportunities}
    ov["GOOG"] = ov.get("GOOGL", {})
    for col in ("fund_score", "verdict", "setup", "rr"):
        stocks[col] = [ov[t].get(col) for t in stocks.index]

    cols = ["company", "sector", "price", "r_1J", "r_5j", "r_10j", "r_30j", "rsi14", "upside", "score"]
    cols = [c for c in cols if c in stocks]
    signals = {
        "Survente 5 j": stocks[stocks["oversold_5j"]].sort_values("os_5j", ascending=False)[cols],
        "Survente 10 j": stocks[stocks["oversold_10j"]].sort_values("os_10j", ascending=False)[cols],
        "Survente 30 j": stocks[stocks["oversold_30j"]].sort_values("os_30j", ascending=False)[cols],
        "Surachat (RSI > 70)": stocks[stocks["rsi14"] > 70].sort_values("rsi14", ascending=False)[cols],
        "Plus hauts 52 semaines": stocks[stocks["new_high"]][cols],
        "Plus bas 52 semaines": stocks[stocks["new_low"]][cols],
        "Golden cross (10 j)": stocks[stocks["golden_cross"]][cols],
        "Death cross (10 j)": stocks[stocks["death_cross"]][cols],
        "Volume inhabituel (> 2× moy. 20 j)": stocks[stocks["vol_ratio"] > 2].sort_values("vol_ratio", ascending=False)[cols],
    }
    if "upside" in stocks:
        opp_df = stocks[(stocks["os_10j"] >= 80) & (stocks["upside"] >= 0.20) & (stocks["rec_mean"] <= 2.0)]
        signals["Survendus + analystes acheteurs"] = opp_df.sort_values("upside", ascending=False)[cols]
    signals["Entrées validées par les fondamentaux"] = stocks[stocks["verdict"] == "Entrée validée"].sort_values("fund_score", ascending=False)[cols]
    signals["Pièges potentiels (technique sans fondamentaux)"] = stocks[stocks["verdict"] == "Piège potentiel"][cols]

    rets = close.drop(columns=[INDEX_TICKER], errors="ignore").pct_change(fill_method=None).iloc[-252:]
    correlation = rets[stocks.index[:20]].corr()

    # Fil des actions d'analystes (14 jours) et calendrier de résultats (30 jours)
    names = stocks["company"].to_dict()
    cutoff = (pd.Timestamp.now() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    actions = sorted(
        ({"ticker": t, "company": names.get(t, t), **x} for t, a in adata.items() if t in names
         for x in a.get("actions", []) if (x.get("date") or "") >= cutoff),
        key=lambda x: x["date"], reverse=True)
    today = pd.Timestamp.now().normalize()
    earnings = sorted(
        ({"ticker": t, "company": r["company"], "date": r["earnings_date"],
          "eps_g_nq": r.get("eps_g_nq"), "upside": r.get("upside"), "weight": r["weight"]}
         for t, r in stocks.iterrows()
         if isinstance(r.get("earnings_date"), str)
         and today - pd.Timedelta(days=1) <= pd.Timestamp(r["earnings_date"]) <= today + pd.Timedelta(days=30)),
        key=lambda x: x["date"])

    return Analysis(asof=close.index[-1], stocks=stocks, sectors=sectors, index=index,
                    breadth=breadth, breadth_history=_breadth_history(close),
                    index_history=index_history, signals=signals, correlation=correlation,
                    analyst_actions=actions, earnings=earnings,
                    analysts_updated=analysts.get("updated"), context=context,
                    opportunities=opportunities, fundamentals_updated=fundamentals.get("updated"),
                    strategies=strat.run(stocks, close, ctx if ctx is not None else pd.DataFrame(), opportunities,
                                         context["regime"]))
