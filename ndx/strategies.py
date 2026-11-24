"""Stratégies adaptées au contexte de fin 2026 : dégonflement du trade IA, inflation qui résiste, Fed restrictive,
départ (12/11) puis retour (23/11) de Jensen Huang.

Chaque stratégie = une thèse, des règles explicites (réussie / échouée pour chaque titre), un score de classement,
le plan d'entrée du moteur d'opportunités, et un test historique du MÉCANISME sur les cours des 2 dernières années
(les fondamentaux historiques ne sont pas disponibles : seuls les mécanismes de prix sont testés).

Points de repère par titre : record de l'indice (6/10), veille du départ de Jensen (11/11), creux pendant son absence,
clôture du jour du retour (J1, 23/11), dernier cours.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import INDEX_TICKER
from .themes import RECORD_DATE, THEMES

EXIT_DATE = "2026-11-12"      # première séance après l'annonce du départ
RETURN_DATE = "2026-11-23"    # première séance après l'annonce du retour (J1)
AI_KEYS = {k for k, v in THEMES.items() if v["ai"]}

# Contexte macro (sources presse, novembre 2026) — modifiable
MACRO = {
    "asof": "2026-11-23",
    "fed": "Fourchette des Fed funds 3,75–4,00 %. Le compte rendu indique que « presque tous » les membres jugent "
           "d'autres hausses appropriées ; le marché anticipe +25 pb le 9 décembre (une hausse de 50 pb reste un scénario minoritaire).",
    "inflation": "Inflation sous-jacente (CPI core) à +0,3 % sur un mois pour le 3e mois consécutif ; CPI d'octobre +0,4 %.",
    "calendar": [{"date": "2026-11-25", "label": "Indice PCE (inflation préférée de la Fed)"},
                 {"date": "2026-12-09", "label": "Décision de la Fed (FOMC)"},
                 {"date": "2026-12-11", "label": "Anthropic (ANT) éligible au Nasdaq 100 (~)"}],
}


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _at(s: pd.Series, date: str, before: bool = False) -> float:
    s = s.dropna()
    sel = s[s.index < pd.Timestamp(date)] if before else s[s.index <= pd.Timestamp(date)]
    return float(sel.iloc[-1]) if len(sel) else np.nan


def _rank(s: pd.Series, ascending: bool = True) -> pd.Series:
    return s.rank(pct=True, ascending=ascending) * 100


# ───────────────────────── Mesures communes ─────────────────────────
def anchors(close: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    rows = {}
    has_return = close.index.max() >= pd.Timestamp(RETURN_DATE)
    for t in tickers + [INDEX_TICKER]:
        if t not in close:
            continue
        s = close[t].dropna()
        if len(s) < 60:
            continue
        now = float(s.iloc[-1])
        rec = _at(s, RECORD_DATE)
        pre = _at(s, EXIT_DATE, before=True)
        absent = s[(s.index >= pd.Timestamp(EXIT_DATE)) & (s.index < pd.Timestamp(RETURN_DATE))]
        trough = float(absent.min()) if len(absent) else np.nan
        d1 = _at(s, RETURN_DATE) if has_return else np.nan
        rows[t] = {
            "p_record": rec, "p_pre_exit": pre, "p_trough": trough, "p_d1": d1, "p_now": now,
            "r_record": now / rec - 1, "r_since_exit": now / pre - 1,
            "dd_absence": trough / pre - 1, "rebound": now / trough - 1,
            "recovery": (now - trough) / (pre - trough) if pre > trough else np.nan,
            "r_d1": d1 / trough - 1 if _ok(d1) else np.nan,           # réaction du jour du retour (vs creux)
            "r_after_d1": now / d1 - 1 if _ok(d1) else np.nan,         # rattrapage après J1
        }
    return pd.DataFrame(rows).T


def sensitivities(close: pd.DataFrame, ctx: pd.DataFrame, tickers: list[str], days: int = 126) -> pd.DataFrame:
    """Bêta au Nasdaq 100, bêta à Nvidia, et réaction à une hausse de 10 pb du taux 10 ans (régression sur 6 mois)."""
    r = close.pct_change(fill_method=None).iloc[-days:]
    out = pd.DataFrame(index=[t for t in tickers if t in r])
    def beta(x, y):
        m = x.notna() & y.notna()
        return float(np.cov(x[m], y[m])[0, 1] / np.var(y[m], ddof=1)) if m.sum() > 40 else np.nan
    out["beta_ndx"] = [beta(r[t], r[INDEX_TICKER]) for t in out.index]
    out["beta_nvda"] = [beta(r[t], r["NVDA"]) if "NVDA" in r else np.nan for t in out.index]
    if "^TNX" in ctx:
        dy = ctx["^TNX"].diff().reindex(r.index)              # variation en points de % (0,10 = 10 pb)
        out["rate_sens"] = [beta(r[t], dy) * 0.10 for t in out.index]   # rendement pour +10 pb
    else:
        out["rate_sens"] = np.nan
    return out


# ───────────────────────── Stratégies ─────────────────────────
def _rule(label: str, value, cond, shown: str) -> dict:
    return {"label": label, "passed": None if not _ok(value) else bool(cond(value)), "value": shown}


def _pct(v, d=0):
    return f"{v * 100:+.{d}f} %" if _ok(v) else "n.d."


def _num(v, d=1, suf=""):
    return f"{v:.{d}f}{suf}" if _ok(v) else "n.d."


def _strategy_rows(df: pd.DataFrame, rules_fn, score_fn, ops: dict, n_rules_min_near: int = 1) -> tuple[list, list]:
    matches, near = [], []
    for t, r in df.iterrows():
        rules = rules_fn(r)
        failed = [x for x in rules if x["passed"] is False]
        missing = [x for x in rules if x["passed"] is None]
        if len(missing) > 1:
            continue
        o = ops.get(t, {})
        row = {"ticker": t, "company": r.get("company"), "theme": r.get("theme_label"), "score": float(score_fn(r)),
               "rules": rules, "failed": [x["label"] for x in failed],
               "price": r.get("price"), "r_1J": r.get("r_1J"), "verdict": o.get("verdict"), "fund_score": o.get("fund_score"),
               "setup": o.get("setup"), "entry": o.get("entry"), "stop": o.get("stop"), "target": o.get("target"),
               "risk_pct": o.get("risk_pct"), "gain_pct": o.get("gain_pct"), "rr": o.get("rr"), "position_pct": o.get("position_pct"),
               "flags": o.get("flags", []), "warnings": o.get("warnings", []),
               **{k: r.get(k) for k in ("r_record", "dd_absence", "rebound", "recovery", "r_d1", "r_after_d1", "lag",
                                         "beta_ndx", "beta_nvda", "rate_sens", "pe_fwd", "peg", "val_disc", "upside",
                                         "fcf_yield", "fcf_margin", "gross_margin", "eps_g_ny", "dd_from_ath", "rsi14")}}
        if not failed:
            matches.append(row)
        elif len(failed) <= n_rules_min_near:
            near.append(row)
    return sorted(matches, key=lambda x: -x["score"]), sorted(near, key=lambda x: -x["score"])[:8]


def run(stocks: pd.DataFrame, close: pd.DataFrame, ctx: pd.DataFrame, opportunities: list[dict], regime: dict) -> dict:
    tickers = [t for t in stocks.index if t != "GOOG"]
    A = anchors(close, tickers)
    S = sensitivities(close, ctx, tickers)
    df = stocks.loc[tickers].join(A, how="left").join(S, how="left")
    ndx = A.loc[INDEX_TICKER] if INDEX_TICKER in A.index else None
    ops = {o["ticker"]: o for o in opportunities}
    df["fund_score"] = [ops.get(t, {}).get("fund_score") for t in df.index]
    df["clean"] = [not ops.get(t, {}).get("flags") for t in df.index]

    # Retard de rattrapage : rebond réel depuis le creux vs rebond attendu (bêta × rebond de l'indice)
    if ndx is not None:
        df["exp_rebound"] = df["beta_ndx"] * ndx["rebound"]
        df["lag"] = df["rebound"] - df["exp_rebound"]          # < 0 = en retard
    theme_rec = df.groupby("theme")["recovery"].transform("median")
    df["lag_vs_theme"] = df["recovery"] - theme_rec
    # Sous-évaluation : décote du PER prévisionnel vs la médiane de son panier, PEG
    theme_pe = df.groupby("theme")["pe_fwd"].transform("median")
    df["val_disc"] = 1 - df["pe_fwd"] / theme_pe
    df["peg"] = df["pe_fwd"] / (df["eps_g_ny"] * 100).where(df["eps_g_ny"] > 0)
    ai = df["theme"].isin(AI_KEYS) | (df["beta_nvda"] >= 0.5)
    rate_med = df["rate_sens"].median()

    strategies = []

    # 1. Rattrapage post-retour de Jensen Huang
    def r1(r):
        return [
            _rule("Exposé à l'IA (chaîne IA ou bêta à Nvidia ≥ 0,5)", 1 if ai.get(r.name) else 0, lambda v: v == 1,
                  f"bêta NVDA {_num(r.get('beta_nvda'), 2)}"),
            _rule("Fondamentaux validés (score ≥ 65, sans drapeau rouge)", r.get("fund_score") if r.get("clean") else 0,
                  lambda v: v >= 65, f"{_num(r.get('fund_score'), 0)}/100" + ("" if r.get("clean") else " · drapeau")),
            _rule("Sous-évalué : PER prévisionnel ≤ médiane du panier ou PEG < 1,2",
                  1 if (_ok(r.get("val_disc")) and r["val_disc"] >= 0) or (_ok(r.get("peg")) and r["peg"] < 1.2) else
                  (0 if _ok(r.get("val_disc")) or _ok(r.get("peg")) else None), lambda v: v == 1,
                  f"décote {_pct(r.get('val_disc'))} · PEG {_num(r.get('peg'), 2)}"),
            _rule("Potentiel analystes ≥ 15 %", r.get("upside"), lambda v: v >= 0.15, _pct(r.get("upside"))),
            _rule("En retard sur le rebond attendu (bêta × rebond de l'indice)", r.get("lag"), lambda v: v < 0,
                  f"{_pct(r.get('rebound'))} vs attendu {_pct(r.get('exp_rebound'))}"),
            _rule("A tenu son creux de l'absence de Jensen (cours > creux)", r.get("rebound"), lambda v: v > 0, _pct(r.get("rebound"))),
            _rule("Pas en chute libre (RSI 14 > 35)", r.get("rsi14"), lambda v: v > 35, _num(r.get("rsi14"), 0)),
        ]
    s1 = lambda r: (0.35 * (100 - _rank(df["lag"]).get(r.name, 50)) + 0.30 * (r.get("fund_score") or 0)
                    + 0.20 * _rank(df["val_disc"].fillna(df["val_disc"].median())).get(r.name, 50)
                    + 0.15 * _rank(df["upside"].fillna(0)).get(r.name, 50))
    m1, n1 = _strategy_rows(df, r1, s1, ops)
    strategies.append({
        "key": "catchup", "name": "Rattrapage post-retour de Jensen",
        "thesis": "Le retour de Jensen Huang a déclenché un rebond violent mais inégal. Les sociétés de la chaîne IA aux "
                  "fondamentaux solides et valorisées sous leurs pairs, qui ont moins rebondi que leur bêta ne le laisse "
                  "prévoir, ont un potentiel de rattrapage — à condition d'avoir tenu leur creux.",
        "matches": m1, "near": n1,
        "watch": "Suivre la colonne « après J1 » à partir du 24/11 : le rattrapage doit se matérialiser en quelques séances, "
                 "sinon le retard reflète autre chose qu'un simple décalage.",
    })

    # 2. Qualité résistante aux taux (Fed restrictive, inflation)
    def r2(r):
        nc = _ok(r.get("net_debt")) and r["net_debt"] <= 0
        return [
            _rule("Bilan sans risque de refinancement (trésorerie nette ou dette < 1× EBITDA)",
                  0 if nc else r.get("net_debt_ebitda"), lambda v: v < 1, "trésorerie nette" if nc else _num(r.get("net_debt_ebitda"), 1, "×")),
            _rule("Pouvoir de prix face à l'inflation (marge brute ≥ 45 %)", r.get("gross_margin"), lambda v: v >= 0.45, _pct(r.get("gross_margin"))),
            _rule("Génère du cash (marge FCF ≥ 15 %)", r.get("fcf_margin"), lambda v: v >= 0.15, _pct(r.get("fcf_margin"))),
            _rule("Peu sensible aux taux (réaction à +10 pb ≥ médiane)", r.get("rate_sens"), lambda v: v >= rate_med,
                  _pct(r.get("rate_sens"), 2) + " pour +10 pb"),
            _rule("Fondamentaux validés (score ≥ 65, sans drapeau rouge)", r.get("fund_score") if r.get("clean") else 0,
                  lambda v: v >= 65, f"{_num(r.get('fund_score'), 0)}/100"),
            _rule("A mieux résisté que l'indice depuis le record", (r.get("r_record") or np.nan) - (ndx["r_record"] if ndx is not None else 0),
                  lambda v: v > 0, f"{_pct(r.get('r_record'))} vs {_pct(ndx['r_record'] if ndx is not None else None)}"),
            _rule("Point d'entrée raisonnable (RSI 14 ≤ 55 ou à moins de 5 % de la MM50)",
                  1 if (_ok(r.get("rsi14")) and r["rsi14"] <= 55) or (_ok(r.get("dist_sma50")) and abs(r["dist_sma50"]) <= 0.05) else 0,
                  lambda v: v == 1, f"RSI {_num(r.get('rsi14'), 0)} · MM50 {_pct(r.get('dist_sma50'), 1)}"),
        ]
    s2 = lambda r: (0.30 * _rank(df["rate_sens"].fillna(rate_med)).get(r.name, 50) + 0.20 * _rank(df["fcf_margin"].fillna(0)).get(r.name, 50)
                    + 0.15 * _rank(df["gross_margin"].fillna(0)).get(r.name, 50) + 0.20 * (r.get("fund_score") or 0)
                    + 0.15 * _rank(df["r_record"].fillna(-1)).get(r.name, 50))
    m2, n2 = _strategy_rows(df, r2, s2, ops)
    strategies.append({
        "key": "rates", "name": "Qualité résistante à une Fed restrictive",
        "thesis": "Avec une inflation sous-jacente qui ne ralentit pas et une Fed qui prépare de nouvelles hausses, les "
                  "valorisations « longues » (bénéfices lointains) souffrent. On cherche l'inverse : pouvoir de prix, cash "
                  "généré aujourd'hui, pas de dette à refinancer, faible réaction historique aux hausses de taux — acheté sur repli.",
        "matches": m2, "near": n2,
        "watch": "Catalyseurs : PCE le 25/11 et Fed le 9/12. Une surprise à la hausse de l'inflation renforce la thèse ; "
                 "un pivot de la Fed l'affaiblit (les titres « longs » repartiraient en tête).",
    })

    # 3. IA à prix raisonnable : bénéfices réels après le dégonflement
    def r3(r):
        return [
            _rule("Chaîne de valeur IA", 1 if r.get("theme") in AI_KEYS else 0, lambda v: v == 1, str(r.get("theme_label"))),
            _rule("Croissance du BPA attendue ≥ 15 % l'an prochain", r.get("eps_g_ny"), lambda v: v >= 0.15, _pct(r.get("eps_g_ny"))),
            _rule("PEG ≤ 1 ou PER prévisionnel ≤ 22", 1 if (_ok(r.get("peg")) and r["peg"] <= 1) or (_ok(r.get("pe_fwd")) and r["pe_fwd"] <= 22)
                  else (0 if _ok(r.get("peg")) or _ok(r.get("pe_fwd")) else None), lambda v: v == 1,
                  f"PEG {_num(r.get('peg'), 2)} · PER {_num(r.get('pe_fwd'), 1)}"),
            _rule("Bénéfices réels (FCF 12 mois positif)", r.get("fcf_ttm"), lambda v: v > 0, _pct(r.get("fcf_margin")) + " du CA"),
            _rule("Décote déjà subie (≥ 25 % sous le plus haut 2 ans)", r.get("dd_from_ath"), lambda v: v <= -0.25, _pct(r.get("dd_from_ath"))),
            _rule("Estimations pas en effondrement (objectifs 30 j > −20 %)", r.get("tgt_rev_30j") if _ok(r.get("tgt_rev_30j")) else 0,
                  lambda v: v > -0.20, _pct(r.get("tgt_rev_30j"))),
            _rule("Sans drapeau rouge", 1 if r.get("clean") else 0, lambda v: v == 1, "ok" if r.get("clean") else "drapeau"),
        ]
    s3 = lambda r: (0.35 * (100 - _rank(df["peg"].fillna(df["peg"].max())).get(r.name, 50)) + 0.25 * _rank(df["eps_g_ny"].fillna(0)).get(r.name, 50)
                    + 0.20 * (r.get("fund_score") or 0) + 0.20 * _rank(df["os_30j"].fillna(50)).get(r.name, 50))
    m3, n3 = _strategy_rows(df, r3, s3, ops)
    strategies.append({
        "key": "garp_ai", "name": "IA à prix raisonnable (bénéfices réels)",
        "thesis": "Le dégonflement du trade IA a fait baisser tout le secteur, y compris des sociétés dont les bénéfices "
                  "progressent vite et dont la valorisation est redevenue modeste. On distingue ces « pelles » rentables des "
                  "histoires financées par la dette : croissance du BPA, PEG bas, cash réel, décote déjà encaissée.",
        "matches": m3, "near": n3,
        "watch": "Le risque principal est une baisse des estimations (révisions d'objectifs, guidance capex des hyperscalers).",
    })

    # Zone d'exclusion en régime de taux hauts : duration longue et financement fragile
    excl = []
    for t, r in df.iterrows():
        reasons = []
        if _ok(r.get("fcf_ttm")) and r["fcf_ttm"] < 0:
            reasons.append("FCF négatif")
        if _ok(r.get("pe_fwd")) and r["pe_fwd"] > 60:
            reasons.append(f"PER prév. {r['pe_fwd']:.0f}")
        if _ok(r.get("rate_sens")) and r["rate_sens"] < df["rate_sens"].quantile(0.2):
            reasons.append(f"très sensible aux taux ({_pct(r['rate_sens'], 2)} pour +10 pb)")
        if ops.get(t, {}).get("flags"):
            reasons += ops[t]["flags"]
        if len(reasons) >= 2:
            excl.append({"ticker": t, "company": r.get("company"), "theme": r.get("theme_label"), "reasons": reasons,
                         "r_record": r.get("r_record"), "rebound": r.get("rebound")})
    excl.sort(key=lambda x: -len(x["reasons"]))

    # Tableau des repères (indice, Nvidia, paniers)
    ref = []
    for t, label in ((INDEX_TICKER, "Nasdaq 100"), ("NVDA", "Nvidia")):
        if t in A.index:
            ref.append({"name": label, **{k: float(v) if _ok(v) else None for k, v in A.loc[t].items()}})
    for k, th in THEMES.items():
        g = df[df["theme"] == k]
        if len(g):
            ref.append({"name": th["label"], "theme": True, **{c: float(g[c].median()) if g[c].notna().any() else None
                       for c in ("r_record", "dd_absence", "rebound", "recovery", "r_d1", "r_after_d1")}})

    return {"macro": MACRO, "regime": regime, "anchors_dates": {"record": RECORD_DATE, "exit": EXIT_DATE, "return": RETURN_DATE,
            "has_after_d1": bool(close.index.max() > pd.Timestamp(RETURN_DATE))},
            "reference": ref, "strategies": strategies, "exclusions": excl[:25],
            "backtests": backtests(close, ctx)}


# ───────────────────────── Tests historiques (mécanismes de prix) ─────────────────────────
def backtests(close: pd.DataFrame, ctx: pd.DataFrame) -> dict:
    px = close.drop(columns=["GOOG"], errors="ignore")
    r = px.pct_change(fill_method=None)
    stocks = [c for c in px.columns if c != INDEX_TICKER]
    bench = r[INDEX_TICKER]
    out = {}

    # A. Après un jour de fort rebond de l'indice (≥ +2 %), les retardataires rattrapent-ils ?
    res = []
    idx = r.index
    for i in np.where(bench >= 0.02)[0]:
        if i < 130 or i + 10 >= len(idx):
            continue
        hist = r.iloc[i - 126:i]
        b = hist[INDEX_TICKER]
        betas = hist[stocks].apply(lambda c: c.cov(b) / b.var())
        resid = r.iloc[i][stocks] - betas * bench.iloc[i]
        fwd = {h: px.iloc[i + h][stocks] / px.iloc[i][stocks] - 1 - (px.iloc[i + h][INDEX_TICKER] / px.iloc[i][INDEX_TICKER] - 1) for h in (5, 10)}
        q = resid.dropna().rank(pct=True)
        lag, lead = q[q <= 0.2].index, q[q >= 0.8].index
        res.append({"date": idx[i].strftime("%Y-%m-%d"), **{f"lag_{h}": float(fwd[h][lag].mean()) for h in (5, 10)},
                    **{f"lead_{h}": float(fwd[h][lead].mean()) for h in (5, 10)}})
    if res:
        R = pd.DataFrame(res)
        out["catchup"] = {
            "title": "Retardataires après un jour de fort rebond (Nasdaq 100 ≥ +2 %)",
            "n": len(R), "period": f"{R['date'].iloc[0]} → {R['date'].iloc[-1]}",
            "lag_5": R["lag_5"].mean(), "lead_5": R["lead_5"].mean(), "lag_10": R["lag_10"].mean(), "lead_10": R["lead_10"].mean(),
            "hit_10": float((R["lag_10"] > R["lead_10"]).mean()),
            "text": "Performance relative à l'indice, sur 5 et 10 séances, du quintile ayant le moins rebondi par rapport à son bêta (retardataires) "
                    "vs le quintile ayant le plus rebondi (meneurs).",
        }

    # B. Quand le taux 10 ans monte de ≥ 25 pb en 20 séances : titres peu vs très sensibles aux taux
    if "^TNX" in ctx:
        y = ctx["^TNX"].reindex(r.index).ffill()
        dy = y.diff()
        res, last = [], -99
        for i in range(150, len(r) - 20):
            if y.iloc[i] - y.iloc[i - 20] >= 0.25 and i - last >= 20:
                last = i
                hist = r.iloc[i - 126:i]
                sens = hist[stocks].apply(lambda c: c.cov(dy.iloc[i - 126:i]) / dy.iloc[i - 126:i].var())
                fwd = px.iloc[i + 20][stocks] / px.iloc[i][stocks] - 1 - (px.iloc[i + 20][INDEX_TICKER] / px.iloc[i][INDEX_TICKER] - 1)
                q = sens.dropna().rank(pct=True)
                res.append({"date": r.index[i].strftime("%Y-%m-%d"), "low": float(fwd[q[q >= 0.8].index].mean()),
                            "high": float(fwd[q[q <= 0.2].index].mean())})
        if res:
            R = pd.DataFrame(res)
            out["rates"] = {
                "title": "Titres peu vs très sensibles aux taux, après +25 pb sur le 10 ans en 20 séances",
                "n": len(R), "period": f"{R['date'].iloc[0]} → {R['date'].iloc[-1]}",
                "low_20": R["low"].mean(), "high_20": R["high"].mean(), "hit_20": float((R["low"] > R["high"]).mean()),
                "text": "Performance relative à l'indice sur les 20 séances suivantes ; épisodes espacés d'au moins 20 séances.",
            }

    # C. En correction (indice ≥ 10 % sous son plus haut), les plus survendus sur 30 séances rebondissent-ils ?
    lvl = px[INDEX_TICKER]
    dd = lvl / lvl.cummax() - 1
    vol = r[stocks].rolling(252, min_periods=120).std()
    res, last = [], -99
    for i in range(130, len(px) - 20):
        if dd.iloc[i] <= -0.08 and i - last >= 10 and not np.isnan(vol.iloc[i]).all():
            last = i
            z = (px.iloc[i][stocks] / px.iloc[i - 30][stocks] - 1) / (vol.iloc[i] * np.sqrt(30))
            fwd = px.iloc[i + 20][stocks] / px.iloc[i][stocks] - 1 - (lvl.iloc[i + 20] / lvl.iloc[i] - 1)
            q = z.dropna().rank(pct=True)
            res.append({"date": px.index[i].strftime("%Y-%m-%d"), "os": float(fwd[q[q <= 0.2].index].mean()),
                        "rest": float(fwd[q[q > 0.2].index].mean())})
    if res:
        R = pd.DataFrame(res)
        out["garp_ai"] = {
            "title": "En correction (indice ≥ 8 % sous son plus haut) : les 20 % les plus survendus sur 30 séances",
            "n": len(R), "period": f"{R['date'].iloc[0]} → {R['date'].iloc[-1]}",
            "os_20": R["os"].mean(), "rest_20": R["rest"].mean(), "hit_20": float((R["os"] > R["rest"]).mean()),
            "text": "Performance relative à l'indice sur 20 séances. Ne teste que la partie « décote / survente » de la stratégie "
                    "(les fondamentaux passés ne sont pas disponibles).",
        }
    return out
