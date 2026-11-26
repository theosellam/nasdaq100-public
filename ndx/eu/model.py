"""Mesures, filtre et laboratoire des petites et moyennes capitalisations françaises.

Facteurs (tous calculés avec les seuls comptes connus à la date, cf. sources.py) :
  valeur     : rendement FCF (flux opé. − investissements) / capi., rendement bénéficiaire, rendement actionnaire
               (dividendes + rachats) / capi., trésorerie nette / capi. ;
  qualité    : rentabilité des capitaux propres, marge opérationnelle ;
  croissance : évolution du chiffre d'affaires (affichée, non notée : elle n'a pas de pouvoir prédictif établi).
Score composite = moyenne des rangs centiles (au moins 4 facteurs renseignés sur 6).

Laboratoire : un portefeuille par an, constitué fin mai (après la publication obligatoire des comptes au 30 avril),
équipondéré, comparé à l'ensemble des titres éligibles ; frais aller-retour déduits chaque année
(0,6 % marché réglementé, 1,5 % Euronext Growth, hypothèse prudente de rotation complète).
Validé seulement si l'avantage est positif à la fois brut et sur des rendements écrêtés aux centiles 2 et 98
(un titre extrême ne doit pas décider du verdict, dans un sens comme dans l'autre) ; sinon « à confirmer ».
Limite assumée : 5 à 6 années seulement (ESEF existe depuis 2021) et biais du survivant partiel (les sociétés
retirées de la cote n'ont souvent plus de cours chez Yahoo) → résultats indicatifs, pas une preuve.
Outil d'analyse — pas un conseil en investissement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MIN_CAP, MAX_CAP = 20e6, 2e9          # petites et moyennes capitalisations
MIN_ADV = 20_000                      # liquidité minimale : 20 000 € échangés par jour (médiane 60 séances)
COSTS = {"Paris": 0.006, "Growth": 0.015}
FACTORS = {"fcf_yield": "Rendement FCF", "earn_yield": "Rendement bénéficiaire", "sh_yield": "Rendement actionnaire",
           "netcash_yield": "Trésorerie nette / capi.", "roe": "Rentabilité des fonds propres", "op_margin": "Marge opérationnelle"}
BENCH = "EU_EW"                       # indice équipondéré des titres liquides (référence du desk)


def derive(F: pd.DataFrame) -> pd.DataFrame:
    if F is None or F.empty:
        return pd.DataFrame()
    F = F.copy()
    for c in ("revenue", "op_income", "net_income", "cfo", "capex_ppe", "capex_int", "dividends", "buyback", "eps",
              "cash", "equity", "assets", "debt_nc", "debt_c", "debt"):
        if c not in F:
            F[c] = np.nan
    capex = F["capex_ppe"].abs().fillna(0) + F["capex_int"].abs().fillna(0)
    F["fcf"] = F["cfo"] - capex.where(F["capex_ppe"].notna() | F["capex_int"].notna())
    parts = F[["debt_nc", "debt_c"]]
    F["debt_total"] = F["debt"].fillna(parts.sum(axis=1, min_count=1))
    F["net_cash"] = F["cash"] - F["debt_total"]
    ok = F["eps"].abs() > 0.01
    sh = (F["net_income"] / F["eps"]).where(ok)
    F["shares"] = sh.where(sh > 0)
    F["op_margin"] = (F["op_income"] / F["revenue"]).where(F["revenue"] > 0)
    F["roe"] = (F["net_income"] / F["equity"]).where(F["equity"] > 0)
    F["payout"] = F["dividends"].abs().fillna(0) + F["buyback"].abs().fillna(0)
    F = F.sort_values(["isin", "fy_end"])
    prev = F.groupby("isin")["revenue"].shift()
    F["rev_growth"] = (F["revenue"] / prev - 1).where(prev > 0)
    return F


def adv_eur(close: pd.DataFrame, vol: pd.DataFrame, asof: pd.Timestamp, n: int = 60) -> pd.Series:
    c, v = close.loc[:asof].tail(n), vol.loc[:asof].tail(n)
    return (c * v).median()


def _price_at(close: pd.DataFrame, t: pd.Timestamp) -> pd.Series:
    return close.loc[:t].ffill().iloc[-1] if len(close.loc[:t]) else pd.Series(dtype=float)


def snapshot(data: dict, asof: pd.Timestamp | None = None, Fd: pd.DataFrame | None = None) -> pd.DataFrame:
    """Une ligne par titre : comptes connus à « asof », capitalisation, liquidité, facteurs, score."""
    close, vol, L = data["close"], data["volume"], data["listing"]
    asof = asof or close.index[-1]
    Fd = derive(data["fund"]) if Fd is None else Fd
    px = _price_at(close, asof)
    px_now = close.ffill().iloc[-1]
    adv = adv_eur(close, vol, asof)
    base = L.set_index("isin")[["name", "ticker", "market"]]
    known = Fd[Fd["available"] <= asof].sort_values("fy_end").groupby("isin").tail(1).set_index("isin") if len(Fd) else pd.DataFrame()
    rows = []
    caps_now = data.get("caps", pd.Series(dtype=float))
    for isin, b in base.iterrows():
        t = b["ticker"]
        p = px.get(t)
        if p is None or not np.isfinite(p) or p <= 0:
            continue
        f = known.loc[isin] if isin in known.index else None
        shares = f["shares"] if f is not None and pd.notna(f.get("shares")) else None
        cap = None
        if shares:
            cap = shares * p
        elif t in caps_now and pd.notna(caps_now[t]) and pd.notna(px_now.get(t)) and px_now[t] > 0:
            cap = float(caps_now[t]) * p / float(px_now[t])     # capitalisation actuelle ramenée au cours de la date
        r = {"isin": isin, "ticker": t, "name": b["name"], "market": b["market"], "price": float(p),
             "cap": cap, "adv": float(adv.get(t)) if pd.notna(adv.get(t)) else None, "fy_end": None}
        if f is not None and cap:
            r.update({"fy_end": f"{f['fy_end']:%Y-%m-%d}", "revenue": f["revenue"], "net_income": f["net_income"],
                      "fcf": f["fcf"], "net_cash": f["net_cash"], "equity": f["equity"],
                      "fcf_yield": f["fcf"] / cap, "earn_yield": f["net_income"] / cap,
                      "sh_yield": f["payout"] / cap if pd.notna(f["cfo"]) else np.nan,
                      "netcash_yield": f["net_cash"] / cap, "roe": f["roe"], "op_margin": f["op_margin"],
                      "rev_growth": f["rev_growth"]})
        rows.append(r)
    S = pd.DataFrame(rows)
    if S.empty:
        return S
    for c in FACTORS:
        if c not in S:
            S[c] = np.nan
    S["eligible"] = S["cap"].between(MIN_CAP, MAX_CAP) & (S["adv"].fillna(0) >= MIN_ADV)
    E = S[S["eligible"]]
    ranks = E[list(FACTORS)].astype(float).rank(pct=True)
    n = ranks.notna().sum(axis=1)
    S.loc[E.index, "score"] = ranks.mean(axis=1).where(n >= 4)
    S["score_pct"] = S["score"].rank(pct=True)
    return S


def flags(row: pd.Series) -> list[str]:
    out = []
    if pd.notna(row.get("net_income")) and row["net_income"] < 0:
        out.append("perte")
    if pd.notna(row.get("fcf")) and row["fcf"] < 0:
        out.append("FCF négatif")
    if pd.notna(row.get("equity")) and row["equity"] <= 0:
        out.append("fonds propres négatifs")
    if pd.notna(row.get("netcash_yield")) and row["netcash_yield"] < -1:
        out.append("dette > capitalisation")
    return out


def special_flags(close: pd.DataFrame, S: pd.DataFrame) -> dict[str, list[str]]:
    """Signaux automatiques de situations spéciales (à confirmer dans les communiqués / avis AMF)."""
    out: dict[str, list[str]] = {}
    r = close.pct_change(fill_method=None)
    vol20 = r.tail(20).std() * np.sqrt(252)
    jump = r.tail(90).max()
    for _, x in S.iterrows():
        t, f = x["ticker"], []
        if t not in close:
            continue
        if vol20.get(t, 1) < 0.05 and jump.get(t, 0) >= 0.15:
            f.append("cours figé après un saut : offre publique probable")
        elif vol20.get(t, 1) < 0.03:
            f.append("cours quasi figé : offre ou retrait possible")
        if pd.notna(x.get("netcash_yield")) and x["netcash_yield"] >= 1:
            f.append("capitalisation < trésorerie nette")
        if f:
            out[t] = f
    return out


def ew_index(close: pd.DataFrame, vol: pd.DataFrame) -> pd.Series:
    """Indice équipondéré des titres liquides (référence pour le desk et la revue des décisions)."""
    r = close.pct_change(fill_method=None)
    liq = (close * vol).rolling(60, min_periods=20).median().shift() >= MIN_ADV
    rr = r.where(liq & (r.abs() < 0.5)).mean(axis=1).fillna(0)
    return (1 + rr).cumprod().rename(BENCH) * 100


# ───────────────────────── Laboratoire ─────────────────────────
def backtest(data: dict, q: float = 0.2) -> dict:
    close = data["close"]
    Fd = derive(data["fund"])
    if Fd.empty:
        return {"periods": [], "verdict": "non validé", "why": "aucun compte ESEF disponible"}
    first = int(Fd["available"].min().year)
    dates = [pd.Timestamp(y, 5, 31) for y in range(max(first, 2021), close.index[-1].year + 1)]
    dates = [d for d in dates if d < close.index[-1] - pd.Timedelta(days=60)]
    periods, fac = [], {k: [] for k in FACTORS}
    for i, t0 in enumerate(dates):
        t1 = dates[i + 1] if i + 1 < len(dates) else close.index[-1]
        S = snapshot(data, t0, Fd)
        if S.empty:
            continue
        E = S[S["eligible"] & S["score"].notna()].copy()
        if len(E) < 25:
            continue
        p0 = _price_at(close, t0)
        sub = close.loc[t0:t1]
        last = sub.ffill().iloc[-1]                    # titre retiré de la cote : sortie au dernier cours connu
        E["ret"] = [last.get(t) / p0.get(t) - 1 if pd.notna(last.get(t)) and p0.get(t) else np.nan for t in E["ticker"]]
        E = E[E["ret"].notna() & (E["ret"] > -0.99)]
        E["cost"] = E["market"].map(COSTS).fillna(0.01)
        lo, hi = E["ret"].quantile(0.02), E["ret"].quantile(0.98)
        E["ret_w"] = E["ret"].clip(lo, hi)           # un seul titre (ex. biotech à +2 000 %) ne doit pas décider du verdict
        k = max(5, int(len(E) * q))
        top = E.nlargest(k, "score")
        bot = E.nsmallest(k, "score")
        uni = float(E["ret"].mean())
        top_net = float((top["ret"] - top["cost"]).mean())
        ic = float(E["score"].rank().corr(E["ret"].rank()))
        ex_w = float((top["ret_w"] - top["cost"]).mean() - E["ret_w"].mean())
        periods.append({"start": f"{t0:%Y-%m-%d}", "end": f"{t1:%Y-%m-%d}", "n": int(len(E)), "k": int(k),
                        "top": round(float(top["ret"].mean()), 4), "top_net": round(top_net, 4),
                        "bottom": round(float(bot["ret"].mean()), 4), "universe": round(uni, 4),
                        "excess_net": round(top_net - uni, 4), "excess_robust": round(ex_w, 4),
                        "universe_median": round(float(E["ret"].median()), 4), "ic": round(ic, 3),
                        "names": top.sort_values("score", ascending=False)["ticker"].head(12).tolist()})
        for f in FACTORS:
            g = E[E[f].notna()]
            if len(g) >= 25:
                kk = max(5, len(g) // 3)
                tt = g.nlargest(kk, f)
                fac[f].append(float((tt["ret_w"] - tt["cost"]).mean() - g["ret_w"].mean()))
    ex = [p["excess_robust"] for p in periods]
    n_pos = sum(e > 0 for e in ex)
    raw = [p["excess_net"] for p in periods]
    robust_ok = len(ex) >= 4 and np.mean(ex) > 0 and n_pos >= 0.75 * len(ex)
    ok = robust_ok and np.mean(raw) > 0          # validé seulement si le résultat ne dépend pas du choix de mesure
    verdict = "validé" if ok else "à confirmer" if robust_ok else "non validé"
    why = (f"{len(ex)} périodes testées : avantage moyen après frais {np.mean(ex):+.1%} par an (rendements extrêmes écrêtés), "
           f"positif {n_pos} périodes sur {len(ex)} ; sans écrêtage {np.mean(raw):+.1%} par an"
           if ex else "pas assez d'années testables")
    factors = [{"key": f, "label": FACTORS[f], "excess": round(float(np.mean(v)), 4) if v else None,
                "positive": int(sum(x > 0 for x in v)), "n": len(v)} for f, v in fac.items()]
    return {"periods": periods, "verdict": verdict, "validated": bool(ok), "why": why,
            "mean_excess": round(float(np.mean(ex)), 4) if ex else None, "factors": factors,
            "method": __doc__.split("Laboratoire :")[1].split("Outil d'analyse")[0].strip()}
