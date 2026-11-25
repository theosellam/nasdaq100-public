"""Laboratoire de signaux : facteurs « point-in-time », tests statistiques, régimes et score composite.

Méthode
- Chaque fin de mois depuis 2020, pour les membres du Nasdaq 100 À CETTE DATE (composition historique), on calcule
  des facteurs uniquement avec l'information disponible ce jour-là (comptes SEC déjà publiés, avis d'analystes passés,
  cours jusqu'à la veille).
- On mesure la performance relative suivante (21 et 63 séances, vs la moyenne équipondérée des membres).
- IC = corrélation de rang entre le facteur et la performance suivante (0,03–0,05 = utile ; > 0,08 = fort).
  t-stat = IC moyen / écart-type × √n (≥ 2 = significatif). Écart Q5–Q1 = meilleur quintile moins pire quintile.
- Régimes : taux 10 ans (hausse / stable / baisse sur 3 mois), marché (stress / calme), leadership IA.
- Score composite hors échantillon : à chaque date, poids = IC moyens PASSÉS (déjà réalisés), mélangés avec l'IC du
  même régime ; facteurs à IC négatif mis à zéro. Aucune information future n'entre dans le poids.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd

from . import history as H
from .data import INDEX_TICKER
from .themes import THEMES

LAB_FILE = H.HIST_DIR / "lab.json"
AI_TICKERS = {t for th in THEMES.values() if th["ai"] for t in th["tickers"]}

FACTORS = {
    # clé: (libellé, groupe, sens) — sens +1 : plus haut = meilleur attendu
    "mom_12_1": ("Tendance 12 mois (hors dernier mois)", "Technique", 1),
    "rev_1m": ("Retour à la moyenne 1 mois (baisse récente)", "Technique", 1),
    "dist_high": ("Proximité du plus haut 52 semaines", "Technique", 1),
    "oversold_z10": ("Survente 10 séances (z-score)", "Technique", 1),
    "low_vol": ("Faible volatilité", "Risque", 1),
    "low_beta": ("Faible bêta au Nasdaq 100", "Risque", 1),
    "rate_sens": ("Profite des hausses de taux 10 ans", "Macro", 1),
    "val_fcf": ("Rendement FCF (FCF / capitalisation)", "Valorisation", 1),
    "val_earn": ("Rendement bénéficiaire (bénéfice / capi.)", "Valorisation", 1),
    "val_sales": ("Chiffre d'affaires / capitalisation", "Valorisation", 1),
    "q_gross": ("Marge brute (pouvoir de prix)", "Qualité", 1),
    "q_op": ("Marge opérationnelle", "Qualité", 1),
    "q_fcf": ("Marge FCF", "Qualité", 1),
    "q_roe": ("Rentabilité des capitaux propres", "Qualité", 1),
    "g_rev": ("Croissance du CA sur 12 mois", "Croissance", 1),
    "g_accel": ("Accélération de la croissance", "Croissance", 1),
    "g_deferred": ("Revenus différés (contrats) en hausse", "Croissance", 1),
    "fin_netcash": ("Trésorerie nette / capitalisation", "Financement", 1),
    "fin_buyback": ("Rachats d'actions (anti-dilution)", "Financement", 1),
    "fin_capex_cov": ("Capex autofinancé (flux opé. / capex)", "Financement", 1),
    "an_net": ("Relèvements − abaissements d'analystes (90 j)", "Analystes", 1),
    "an_tgt": ("Révision des objectifs de cours (90 j)", "Analystes", 1),
    "sue": ("Surprise du dernier BPA publié", "Résultats", 1),
}


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


# ───────────────────────── Fondamentaux point-in-time ─────────────────────────
def _ttm(q: list, upto_idx: int) -> float:
    """Somme des 4 trimestres se terminant à l'index donné, s'ils sont consécutifs (≈ 1 an)."""
    if upto_idx < 3:
        return np.nan
    win = q[upto_idx - 3: upto_idx + 1]
    span = (pd.Timestamp(win[-1][0]) - pd.Timestamp(win[0][0])).days
    return float(sum(x[1] for x in win)) if 250 <= span <= 300 else np.nan


def fund_states(sec: dict) -> pd.DataFrame:
    """Une ligne par date de publication : l'état des fondamentaux connu juste après ce dépôt."""
    flows, inst = sec["flows"], sec["instants"]
    filed_dates = sorted({x[2] for v in flows.values() for x in v} | {x[2] for v in inst.values() for x in v})
    rows = []
    for fd in filed_dates:
        st = {"filed": fd}
        known = {k: [x for x in v if x[2] <= fd] for k, v in flows.items()}
        for k in ("revenue", "gross_profit", "op_income", "net_income", "ocf", "capex", "interest"):
            q = known.get(k, [])
            st[k] = _ttm(q, len(q) - 1) if q else np.nan
            st[k + "_prev"] = _ttm(q, len(q) - 5) if len(q) >= 8 else np.nan
        rq = known.get("revenue", [])
        if len(rq) >= 6:
            def yoy(i):
                e = pd.Timestamp(rq[i][0])
                prev = [x for x in rq[:i] if 340 <= (e - pd.Timestamp(x[0])).days <= 390]
                return rq[i][1] / prev[-1][1] - 1 if prev and prev[-1][1] > 0 else np.nan
            st["rev_q_yoy"], st["rev_q_yoy_prev"] = yoy(len(rq) - 1), yoy(len(rq) - 2)
        for k, v in inst.items():
            kv = [x for x in v if x[2] <= fd]
            st[k] = kv[-1][1] if kv else np.nan
            if kv:
                e = pd.Timestamp(kv[-1][0])
                old = [x for x in kv if 320 <= (e - pd.Timestamp(x[0])).days <= 410]
                st[k + "_1y"] = old[-1][1] if old else np.nan
            else:
                st[k + "_1y"] = np.nan
        rows.append(st)
    df = pd.DataFrame(rows)
    if len(df):
        df["filed"] = pd.to_datetime(df["filed"])
        df = df.sort_values("filed").drop_duplicates("filed", keep="last")
    return df


def fund_metrics(st: pd.Series, price: float) -> dict:
    g = lambda k: st.get(k, np.nan)
    rev, ocf, capex = g("revenue"), g("ocf"), g("capex")
    fcf = ocf - capex if _ok(ocf) and _ok(capex) else (ocf if _ok(ocf) else np.nan)
    shares = g("shares")
    mcap = price * shares if _ok(shares) and shares > 0 else np.nan
    cash = np.nansum([g("cash"), g("st_inv")]) if _ok(g("cash")) else np.nan
    debt = np.nansum([g("debt"), g("debt_cur")])
    d = lambda a, b: a / b if _ok(a) and _ok(b) and b != 0 else np.nan
    return {
        "mcap": mcap, "fcf": fcf, "rev": rev,
        "val_fcf": d(fcf, mcap), "val_earn": d(g("net_income"), mcap), "val_sales": d(rev, mcap),
        "q_gross": d(g("gross_profit"), rev), "q_op": d(g("op_income"), rev), "q_fcf": d(fcf, rev),
        "q_roe": d(g("net_income"), g("equity")) if _ok(g("equity")) and g("equity") > 0 else np.nan,
        "g_rev": d(rev, g("revenue_prev")) - 1 if _ok(g("revenue_prev")) and g("revenue_prev") > 0 else np.nan,
        "g_accel": g("rev_q_yoy") - g("rev_q_yoy_prev") if _ok(g("rev_q_yoy")) and _ok(g("rev_q_yoy_prev")) else np.nan,
        "g_deferred": d(g("deferred"), g("deferred_1y")) - 1 if _ok(g("deferred_1y")) and g("deferred_1y") > 0 else np.nan,
        "fin_netcash": d(cash - debt, mcap) if _ok(cash) else np.nan,
        "fin_buyback": -(d(shares, g("shares_1y")) - 1) if _ok(g("shares_1y")) and g("shares_1y") > 0 else np.nan,
        "fin_capex_cov": min(d(ocf, capex), 10) if _ok(capex) and capex > 0 and _ok(ocf) else np.nan,
        # éléments pour la validation historique des points d'entrée
        "rev_yoy": d(rev, g("revenue_prev")) - 1 if _ok(g("revenue_prev")) and g("revenue_prev") > 0 else np.nan,
        "op_margin": d(g("op_income"), rev), "ni": g("net_income"), "net_debt": debt - cash if _ok(cash) else np.nan,
        "debt_to_op": d(debt - cash, g("op_income")) if _ok(cash) and _ok(g("op_income")) and g("op_income") > 0 else np.nan,
        "int_cov": d(g("op_income"), g("interest")) if _ok(g("interest")) and g("interest") > 0 else np.nan,
        "dilution": d(shares, g("shares_1y")) - 1 if _ok(g("shares_1y")) and g("shares_1y") > 0 else np.nan,
        "capex_to_rev": d(capex, rev), "capex_cov": d(ocf, capex) if _ok(capex) and capex > 0 else np.nan,
    }


def validation(m: dict) -> tuple[float, bool, int]:
    """Version historique de la validation fondamentale (comptes SEC uniquement, pas d'analystes)."""
    net_cash = _ok(m.get("net_debt")) and m["net_debt"] <= 0
    checks = [
        (m.get("rev_yoy"), lambda v: v > 0.05), (m.get("op_margin"), lambda v: v > 0.10),
        (m.get("fcf"), lambda v: v > 0), (m.get("ni"), lambda v: v > 0),
        (0 if net_cash else m.get("debt_to_op"), lambda v: v < 3),
        (99 if net_cash and not _ok(m.get("int_cov")) else m.get("int_cov"), lambda v: v > 6),
        (m.get("dilution"), lambda v: v < 0.02),
        (m.get("capex_cov") if _ok(m.get("capex_to_rev")) and m["capex_to_rev"] > 0.08 else None, lambda v: v >= 1.2),
    ]
    av = [(v, c) for v, c in checks if _ok(v)]
    score = sum(1 for v, c in av if c(v)) / len(av) if av else np.nan
    flags = ((_ok(m.get("fcf")) and m["fcf"] < 0 and _ok(m.get("net_debt")) and m["net_debt"] > 0)
             or (_ok(m.get("dilution")) and m["dilution"] > 0.08) or (_ok(m.get("rev_yoy")) and m["rev_yoy"] < -0.10))
    return score, bool(flags), len(av)


# ───────────────────────── Panel mensuel ─────────────────────────
def _members_at(mem: pd.DataFrame, d: pd.Timestamp) -> set:
    snaps = mem[mem["date"] <= d]
    if snaps.empty:
        snaps = mem[mem["date"] == mem["date"].min()]
    last = snaps["date"].max()
    return set(snaps[snaps["date"] == last]["ticker"].dropna())


def build_panel(close: pd.DataFrame, macro: pd.DataFrame, mem: pd.DataFrame, states: dict,
                analysts: pd.DataFrame, earnings: pd.DataFrame) -> pd.DataFrame:
    px = close
    r = px.pct_change(fill_method=None)
    tnx = macro["^TNX"].reindex(px.index).ffill() if "^TNX" in macro else None
    dy = tnx.diff() if tnx is not None else None
    month_ends = px.groupby(px.index.to_period("M")).apply(lambda g: g.index[-1])
    dates = [d for d in month_ends if d >= pd.Timestamp("2020-01-01")]
    idx_pos = {d: i for i, d in enumerate(px.index)}
    an = analysts.copy()
    an["chg"] = an["currentPriceTarget"] / an["priorPriceTarget"] - 1
    an.loc[~np.isfinite(an["chg"]) | (an["priorPriceTarget"] <= 0), "chg"] = np.nan
    an_by = {t: g.sort_values("date") for t, g in an.groupby("ticker")}
    ea = earnings.dropna(subset=["surprise"])
    ea_by = {t: g.sort_values("date") for t, g in ea.groupby("ticker")}
    rows = []
    for d in dates:
        i = idx_pos[d]
        if i < 260:
            continue
        mem_d = [t for t in _members_at(mem, d) if t in px.columns and pd.notna(px.iloc[i][t])]
        if len(mem_d) < 50:
            continue
        p = px.iloc[i]
        f21 = px.iloc[i + 21] / p - 1 if i + 21 < len(px) else None
        f63 = px.iloc[i + 63] / p - 1 if i + 63 < len(px) else None
        win = r.iloc[i - 125:i + 1]
        bnd = win[INDEX_TICKER]
        dyw = dy.iloc[i - 125:i + 1] if dy is not None else None
        for t in mem_d:
            s = px[t].iloc[: i + 1].dropna()
            if len(s) < 260:
                continue
            ret = s.pct_change()
            vol = ret.iloc[-252:].std()
            rt = win[t]
            m = rt.notna() & bnd.notna()
            beta = float(np.cov(rt[m], bnd[m])[0, 1] / bnd[m].var()) if m.sum() > 60 else np.nan
            rs = float(np.cov(rt[m & dyw.notna()], dyw[m & dyw.notna()])[0, 1] / dyw[m & dyw.notna()].var()) if dyw is not None and (m & dyw.notna()).sum() > 60 else np.nan
            row = {"date": d, "ticker": t,
                   "mom_12_1": s.iloc[-22] / s.iloc[-253] - 1, "rev_1m": -(s.iloc[-1] / s.iloc[-22] - 1),
                   "dist_high": s.iloc[-1] / s.iloc[-252:].max() - 1,
                   "oversold_z10": -((s.iloc[-1] / s.iloc[-11] - 1) / (vol * np.sqrt(10))) if vol else np.nan,
                   "low_vol": -vol, "low_beta": -beta, "rate_sens": rs, "beta": beta,
                   "f21": f21[t] if f21 is not None else np.nan, "f63": f63[t] if f63 is not None else np.nan}
            stt = states.get(t)
            if stt is not None and len(stt):
                k = stt["filed"].searchsorted(d, side="right") - 1
                if k >= 0:
                    fm = fund_metrics(stt.iloc[k], float(s.iloc[-1]))
                    row.update({f: fm[f] for f in ("val_fcf", "val_earn", "val_sales", "q_gross", "q_op", "q_fcf", "q_roe",
                                                    "g_rev", "g_accel", "g_deferred", "fin_netcash", "fin_buyback", "fin_capex_cov")})
                    sc, fl, nav = validation(fm)
                    row.update({"val_score": sc, "val_flag": fl, "val_n": nav})
            ag = an_by.get(t)
            if ag is not None:
                w = ag[(ag["date"] <= d) & (ag["date"] > d - pd.Timedelta(days=90))]
                if len(w):
                    row["an_net"] = ((w["Action"] == "up").sum() - (w["Action"] == "down").sum()) / max(len(w), 3)
                    row["an_tgt"] = w["chg"].mean()
            eg = ea_by.get(t)
            if eg is not None:
                w = eg[(eg["date"] <= d) & (eg["date"] > d - pd.Timedelta(days=80))]
                if len(w):
                    row["sue"] = float(np.clip(w["surprise"].iloc[-1], -100, 100))
            rows.append(row)
    P = pd.DataFrame(rows)
    # Performance relative à la moyenne équipondérée des membres
    for h in ("f21", "f63"):
        P[h + "_x"] = P[h] - P.groupby("date")[h].transform("mean")
    # Régimes
    reg = {}
    lvl = px[INDEX_TICKER]
    sma200 = lvl.rolling(200).mean()
    for d in P["date"].unique():
        i = idx_pos[d]
        dtnx = tnx.iloc[i] - tnx.iloc[i - 63] if tnx is not None else 0
        dd = lvl.iloc[i] / lvl.iloc[max(0, i - 252):i + 1].max() - 1
        g = P[P["date"] == d]
        ai_m = [t for t in g["ticker"] if t in AI_TICKERS]
        rest = [t for t in g["ticker"] if t not in AI_TICKERS]
        r63 = px.iloc[i] / px.iloc[i - 63] - 1
        reg[d] = {"rates": "hausse" if dtnx > 0.25 else "baisse" if dtnx < -0.25 else "stables",
                  "market": "stress" if (lvl.iloc[i] < sma200.iloc[i] or dd <= -0.10) else "calme",
                  "ai": "IA en tête" if r63[ai_m].mean() > r63[rest].mean() else "IA à la traîne"}
    for k in ("rates", "market", "ai"):
        P["reg_" + k] = P["date"].map(lambda d: reg[d][k])
    return P


def current_regime(close: pd.DataFrame, macro: pd.DataFrame, members: list[str]) -> dict:
    i = len(close) - 1
    lvl = close[INDEX_TICKER]
    tnx = macro["^TNX"].reindex(close.index).ffill()
    dtnx = tnx.iloc[i] - tnx.iloc[i - 63]
    dd = lvl.iloc[i] / lvl.iloc[i - 252:].max() - 1
    r63 = close.iloc[i] / close.iloc[i - 63] - 1
    ai_m = [t for t in members if t in AI_TICKERS and t in r63]
    rest = [t for t in members if t not in AI_TICKERS and t in r63]
    return {"rates": "hausse" if dtnx > 0.25 else "baisse" if dtnx < -0.25 else "stables",
            "market": "stress" if (lvl.iloc[i] < lvl.rolling(200).mean().iloc[i] or dd <= -0.10) else "calme",
            "ai": "IA en tête" if r63[ai_m].mean() > r63[rest].mean() else "IA à la traîne",
            "d_tnx_3m": float(dtnx), "dd": float(dd)}


# ───────────────────────── Statistiques ─────────────────────────
def _ic_series(P: pd.DataFrame, f: str, h: str) -> pd.Series:
    def one(g):
        x = g[[f, h]].dropna()
        return x[f].rank().corr(x[h].rank()) if len(x) >= 25 else np.nan
    return P.groupby("date").apply(one, include_groups=False).dropna()


def _spread(P: pd.DataFrame, f: str, h: str) -> float:
    def one(g):
        x = g[[f, h]].dropna()
        if len(x) < 25:
            return np.nan
        q = x[f].rank(pct=True)
        return x[h][q >= 0.8].mean() - x[h][q <= 0.2].mean()
    return float(P.groupby("date").apply(one, include_groups=False).mean())


def factor_stats(P: pd.DataFrame) -> tuple[list[dict], dict]:
    out, ics = [], {}
    for f, (label, group, sign) in FACTORS.items():
        if f not in P or P[f].notna().sum() < 500:
            continue
        s21, s63 = _ic_series(P, f, "f21_x"), _ic_series(P, f, "f63_x")
        ics[f] = s21
        def st(s):
            return {"ic": float(s.mean()), "t": float(s.mean() / s.std() * np.sqrt(len(s))) if len(s) > 2 and s.std() else np.nan,
                    "hit": float((s > 0).mean()), "n": int(len(s))}
        by = {}
        for k in ("rates", "market", "ai"):
            for v in P["reg_" + k].unique():
                dts = set(P[P["reg_" + k] == v]["date"])
                ss = s21[s21.index.isin(dts)]
                if len(ss) >= 4:
                    by[f"{k}:{v}"] = {"ic": float(ss.mean()), "n": int(len(ss))}
        out.append({"key": f, "label": label, "group": group, "coverage": float(P[f].notna().mean()),
                    "h21": st(s21), "h63": st(s63), "spread21": _spread(P, f, "f21_x"), "spread63": _spread(P, f, "f63_x"),
                    "by_regime": by})
    return out, ics


def composite_backtest(P: pd.DataFrame, ics: dict, min_hist: int = 12) -> dict:
    """Score composite testé hors échantillon. Pondération RETENUE : égale (moyenne des rangs de tous les facteurs),
    fixée à l'avance. Les pondérations apprises sur les IC passés sont calculées pour comparaison."""
    dates = sorted(P["date"].unique())
    keys = list(ics)
    R = P.copy()
    for f in keys:
        R[f + "_r"] = R.groupby("date")[f].rank(pct=True) - 0.5
    past_ic = lambda f, past: ics[f][ics[f].index.isin(past)]
    schemes = {
        "eq": ("Pondération égale (retenue)", lambda past: {f: 1.0 for f in keys}),
        "ic": ("Poids = IC moyen passé", lambda past: {f: max(0.0, past_ic(f, past).mean()) if len(past_ic(f, past)) else 0.0 for f in keys}),
        "ic36": ("Poids = IC des 36 derniers mois", lambda past: {f: max(0.0, past_ic(f, past).iloc[-36:].mean()) if len(past_ic(f, past)) else 0.0 for f in keys}),
        "sign": ("Facteurs à IC passé positif", lambda past: {f: 1.0 if len(past_ic(f, past)) and past_ic(f, past).mean() > 0 else 0.0 for f in keys}),
    }
    res = {k: [] for k in schemes}
    for j, d in enumerate(dates):
        if j < min_hist + 1:
            continue
        past = dates[: j - 1]                                   # IC à 21 séances réalisés seulement
        g = R[R["date"] == d]
        for k, (_, fn) in schemes.items():
            w = fn(past)
            tot = sum(w.values())
            if tot <= 0:
                continue
            c = sum(g[f + "_r"].fillna(0) * w[f] for f in keys) / tot
            x = pd.DataFrame({"c": c, "y": g["f21_x"], "y63": g["f63_x"]}).dropna(subset=["c", "y"])
            if len(x) < 25:
                continue
            q = x["c"].rank(pct=True)
            res[k].append({"date": d, "ic": x["c"].rank().corr(x["y"].rank()), "ls": x["y"][q >= 0.8].mean() - x["y"][q <= 0.2].mean(),
                           "q5": x["y"][q >= 0.8].mean(), "q1": x["y"][q <= 0.2].mean(),
                           "ls63": (x["y63"][q >= 0.8].mean() - x["y63"][q <= 0.2].mean()) if x["y63"].notna().sum() > 25 else np.nan})
    cmp_ = []
    for k, (label, _) in schemes.items():
        B = pd.DataFrame(res[k])
        if len(B):
            cmp_.append({"key": k, "label": label, "ic": float(B["ic"].mean()), "t": float(B["ic"].mean() / B["ic"].std() * np.sqrt(len(B))),
                         "ls": float(B["ls"].mean()), "hit": float((B["ls"] > 0).mean()), "n": int(len(B))})
    B = pd.DataFrame(res["eq"])
    cum = lambda s: ((1 + s.fillna(0)).cumprod() - 1)
    return {
        "n": int(len(B)), "period": f"{pd.Timestamp(B['date'].iloc[0]):%Y-%m} → {pd.Timestamp(B['date'].iloc[-1]):%Y-%m}",
        "ic": float(B["ic"].mean()), "ic_t": float(B["ic"].mean() / B["ic"].std() * np.sqrt(len(B))),
        "hit": float((B["ls"] > 0).mean()), "ls_month": float(B["ls"].mean()), "q5_month": float(B["q5"].mean()),
        "q1_month": float(B["q1"].mean()), "ls63": float(B["ls63"].mean()), "schemes": cmp_,
        "series": {"dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in B["date"]],
                   "ls": (cum(B["ls"]) * 100).round(2).tolist(), "q5": (cum(B["q5"]) * 100).round(2).tolist(),
                   "q1": (cum(B["q1"]) * 100).round(2).tolist()},
    }


# ───────────────────────── Points d'entrée techniques × fondamentaux ─────────────────────────
def _rsi(s: pd.DataFrame, n: int) -> pd.DataFrame:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def entry_study(close: pd.DataFrame, mem: pd.DataFrame, states: dict, analysts: pd.DataFrame, P: pd.DataFrame | None = None) -> dict:
    px = close.drop(columns=[INDEX_TICKER], errors="ignore")
    px = px.loc[:, px.notna().sum() > 300]
    sma20, sma50, sma200 = px.rolling(20).mean(), px.rolling(50).mean(), px.rolling(200).mean()
    slope200 = sma200 / sma200.shift(21) - 1
    rsi14, rsi2 = _rsi(px, 14), _rsi(px, 2)
    vol = px.pct_change(fill_method=None).rolling(252, min_periods=120).std()
    z10 = (px / px.shift(10) - 1) / (vol * np.sqrt(10))
    z5 = (px / px.shift(5) - 1) / (vol * np.sqrt(5))
    hi252 = px.rolling(252, min_periods=200).max()
    r30 = px / px.shift(30) - 1
    up = (px > sma200) & (slope200 > 0)
    btw = lambda df, a, b: (df >= a) & (df <= b)
    setups = {
        "pullback": up & btw(px / sma50 - 1, -0.08, 0.015) & btw(rsi14, 30, 50),
        "sma200": ((px / sma200 - 1).abs() <= 0.03) & (slope200 > 0),
        "oversold": (z10 <= -2.0) | ((rsi2 < 5) & (z5 <= -1.5)),
        "reversal": ((rsi14 > 30) & (rsi14.shift(1).rolling(5).min() < 30)) | ((px > sma20) & (px.shift(1) < sma20.shift(1)) & (r30 < -0.05)),
        "breakout": px >= hi252 * 0.999,
    }
    labels = {"pullback": "Pullback dans une tendance haussière", "sma200": "Test de la MM200 montante",
              "oversold": "Survente extrême", "reversal": "Reprise confirmée après survente", "breakout": "Cassure du plus haut 52 semaines"}
    fwd20 = px.shift(-20) / px - 1
    fwd60 = px.shift(-60) / px - 1
    # Membres à chaque date (composition trimestrielle)
    mem_dates = sorted(mem["date"].unique())
    is_mem = pd.DataFrame(False, index=px.index, columns=px.columns)
    for k, md in enumerate(mem_dates):
        end = mem_dates[k + 1] if k + 1 < len(mem_dates) else px.index[-1] + pd.Timedelta(days=1)
        tick = [t for t in mem[mem["date"] == md]["ticker"].dropna() if t in px.columns]
        is_mem.loc[(px.index >= md) & (px.index < end), tick] = True
    ew20 = fwd20.where(is_mem).mean(axis=1)
    ew60 = fwd60.where(is_mem).mean(axis=1)
    x20, x60 = fwd20.sub(ew20, axis=0), fwd60.sub(ew60, axis=0)
    lvl = close[INDEX_TICKER]
    stress = (lvl < lvl.rolling(200).mean()) | (lvl / lvl.rolling(252, min_periods=50).max() - 1 <= -0.10)
    an = analysts.copy()
    an_by = {t: g.sort_values("date") for t, g in an.groupby("ticker")}
    ev = []
    for key, mask in setups.items():
        m = (mask & is_mem).fillna(False)
        for t in m.columns:
            days = m.index[m[t].values]
            last = None
            for d in days:
                if last is not None and (d - last).days < 15:
                    continue
                last = d
                if pd.isna(x20.at[d, t]):
                    continue
                row = {"setup": key, "ticker": t, "date": d, "x20": x20.at[d, t], "x60": x60.at[d, t], "stress": bool(stress.get(d, False))}
                stt = states.get(t)
                if stt is not None and len(stt):
                    k = stt["filed"].searchsorted(d, side="right") - 1
                    if k >= 0:
                        sc, fl, nav = validation(fund_metrics(stt.iloc[k], float(px.at[d, t])))
                        row.update({"vscore": sc, "vflag": fl, "vn": nav})
                ag = an_by.get(t)
                if ag is not None:
                    w = ag[(ag["date"] <= d) & (ag["date"] > d - pd.Timedelta(days=90))]
                    row["an_net"] = ((w["Action"] == "up").sum() - (w["Action"] == "down").sum()) if len(w) else 0
                ev.append(row)
    E = pd.DataFrame(ev)
    for c, dflt in (("vscore", np.nan), ("vflag", False), ("vn", 0), ("an_net", 0)):   # sans comptes SEC : colonnes vides
        if c not in E:
            E[c] = dflt
    E["valid"] = (E["vscore"] >= 0.65) & (~E["vflag"].fillna(False).astype(bool)) & (E["vn"] >= 5)
    E["trap"] = (E["vscore"] < 0.45) | (E["vflag"].fillna(False).astype(bool))
    E["valid_an"] = E["valid"] & (E["an_net"].fillna(0) >= 0)
    E["cheap"] = False
    E["dear"] = False
    if P is not None and "val_fcf" in P:
        V = P[["date", "ticker", "val_fcf"]].copy()
        V["vq"] = V.groupby("date")["val_fcf"].rank(pct=True)
        V["m"] = V["date"].dt.to_period("M")
        E["m"] = (E["date"] - pd.offsets.MonthEnd(1)).dt.to_period("M")     # valorisation connue à la fin du mois précédent
        E = E.merge(V[["m", "ticker", "vq"]], on=["m", "ticker"], how="left")
        E["cheap"], E["dear"] = E["vq"] >= 0.6, E["vq"] <= 0.4

    def stats(g):
        return {"n": int(len(g)), "x20": float(g["x20"].mean()) if len(g) else None, "x60": float(g["x60"].mean()) if g["x60"].notna().any() else None,
                "hit20": float((g["x20"] > 0).mean()) if len(g) else None, "med20": float(g["x20"].median()) if len(g) else None}
    table = []
    for key in setups:
        g = E[E["setup"] == key]
        table.append(_groups(key, labels[key], g, stats))
    allg = E.drop_duplicates(["ticker", "date"])
    table.append(_groups("any", "Tous les signaux techniques", allg, stats))
    return {"table": table, "n_events": int(len(E)), "period": f"{E['date'].min():%Y-%m} → {E['date'].max():%Y-%m}",
            "events": E}


def _groups(key, label, g, stats) -> dict:
    return {"key": key, "label": label, "all": stats(g), "valid": stats(g[g["valid"]]), "trap": stats(g[g["trap"]]),
            "valid_an": stats(g[g["valid_an"]]), "cheap": stats(g[g["cheap"]]), "dear": stats(g[g["dear"]]),
            "cheap_valid": stats(g[g["cheap"] & g["valid"]]),
            "stress_all": stats(g[g["stress"]]), "stress_valid": stats(g[g["valid"] & g["stress"]]),
            "stress_trap": stats(g[g["trap"] & g["stress"]]), "stress_cheap": stats(g[g["cheap"] & g["stress"]])}


# ───────────────────────── Scores du jour ─────────────────────────
def today_scores(P: pd.DataFrame, ics: dict, regime: dict) -> tuple[list[dict], dict]:
    """Score factoriel du jour : moyenne des rangs (pondération égale, celle validée hors échantillon)."""
    d = P["date"].max()
    g = P[P["date"] == d].copy()
    keys = list(ics)
    for f in keys:
        g[f + "_r"] = g[f].rank(pct=True) - 0.5
    g["comp"] = g[[f + "_r" for f in keys]].fillna(0).mean(axis=1)      # donnée manquante = neutre (comme dans le test)
    g["comp_pct"] = g["comp"].rank(pct=True)
    rows = []
    for _, r in g.sort_values("comp", ascending=False).iterrows():
        contrib = sorted(((f, float(r[f + "_r"])) for f in keys if _ok(r[f + "_r"])), key=lambda x: -abs(x[1]))
        rows.append({"ticker": r["ticker"], "comp": float(r["comp"]), "pct": float(r["comp_pct"]),
                     "quintile": int(min(5, 1 + int(r["comp_pct"] * 5))) if _ok(r["comp_pct"]) else None,
                     "plus": [{"f": f, "label": FACTORS[f][0], "v": v} for f, v in contrib if v > 0][:3],
                     "minus": [{"f": f, "label": FACTORS[f][0], "v": v} for f, v in contrib if v < 0][:3],
                     "val_fcf_pct": float(g["val_fcf"].rank(pct=True)[_]) if _ok(r.get("val_fcf")) else None,
                     "n_factors": int(sum(1 for f in keys if _ok(r[f + "_r"])))})
    return rows, {f: 1 / len(keys) for f in keys}


# ───────────────────────── Orchestration ─────────────────────────
def build(current: list[str], log=print) -> dict:
    t0 = time.time()
    close = pd.read_csv(H.PRICES_FILE, index_col=0, parse_dates=True)
    macro = pd.read_csv(H.MACRO_FILE, index_col=0, parse_dates=True)
    mem = H.load_membership()
    analysts = pd.read_csv(H.ANALYST_FILE, parse_dates=["date"]) if H.ANALYST_FILE.exists() else pd.DataFrame(columns=["date", "ticker"])
    earnings = pd.read_csv(H.EARNINGS_FILE, parse_dates=["date"]) if H.EARNINGS_FILE.exists() else pd.DataFrame(columns=["date", "ticker", "surprise"])
    log("→ Fondamentaux point-in-time (comptes SEC)…")
    states = {}
    for t in close.columns:
        sec = H.load_sec(t)
        if sec:
            states[t] = fund_states(sec)
    log("→ Panel mensuel des facteurs (le mois en cours est évalué à la dernière séance)…")
    P = build_panel(close, macro, mem, states, analysts, earnings)
    if not states:
        log("  ⚠ aucun compte SEC disponible : facteurs fondamentaux vides (les autres facteurs restent testés)")
    for c in list(FACTORS) + ["rev", "fcf", "ni", "mcap"]:      # colonnes attendues même sans comptes SEC
        if c not in P:
            P[c] = np.nan
    log(f"  {P['date'].nunique()} mois × ~{int(P.groupby('date').size().mean())} titres = {len(P)} observations")
    log("→ Tests statistiques des facteurs…")
    fstats, ics = factor_stats(P)
    comp = composite_backtest(P, ics)
    regime = current_regime(close, macro, [t for t in current if t in close.columns])
    today, weights = today_scores(P, ics, regime)
    for fs in fstats:
        fs["weight_now"] = weights.get(fs["key"], 0.0)
        fs["ic_now_regime"] = fs["by_regime"].get(f"rates:{regime['rates']}", {}).get("ic")
    log("→ Étude des points d'entrée (technique × fondamentaux)…")
    es = entry_study(close, mem, states, analysts, P)
    E = es.pop("events")
    E.to_csv(H.HIST_DIR / "entry_events.csv", index=False)
    keys = list(ics)
    P["comp"] = sum((P.groupby("date")[f].rank(pct=True) - 0.5).fillna(0) for f in keys) / len(keys)
    P["comp_pct"] = P.groupby("date")["comp"].rank(pct=True)
    P["val_pct"] = P.groupby("date")["val_fcf"].rank(pct=True)
    P.to_csv(H.HIST_DIR / "panel.csv", index=False)
    out = {"built": pd.Timestamp.now().isoformat(timespec="seconds"), "asof": str(P["date"].max().date()),
           "coverage": {"months": int(P["date"].nunique()), "obs": int(len(P)), "tickers": int(P["ticker"].nunique()),
                        "sec": len(states), "analyst_actions": int(len(analysts)), "earnings": int(len(earnings)),
                        "missing_prices": sorted(set(H.universe(current)) - set(close.columns))},
           "regime": regime, "factors": fstats, "composite": comp, "today": today, "entries": es,
           "seconds": round(time.time() - t0)}
    LAB_FILE.write_text(json.dumps(out, ensure_ascii=False, default=lambda x: None if isinstance(x, float) and not np.isfinite(x) else str(x)), encoding="utf-8")
    log(f"✓ Laboratoire construit en {out['seconds']} s")
    return out


def load() -> dict | None:
    return json.loads(LAB_FILE.read_text(encoding="utf-8")) if LAB_FILE.exists() else None
