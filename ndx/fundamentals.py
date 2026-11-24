"""Métriques fondamentales à partir des états financiers trimestriels (Yahoo Finance).

Tout est en données publiées : chiffre d'affaires, marges, flux de trésorerie, capex, dette, trésorerie,
nombre d'actions, revenus différés (proxy des contrats signés et facturés d'avance).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NAN = float("nan")


def _v(part: dict, key: str) -> list:
    return [np.nan if x is None else x for x in part.get(key, [])]


_DATES: list = []   # dates du bloc en cours (du plus récent au plus ancien)


def _ttm(vals: list, dates: list | None = None) -> float:
    """Somme des 12 derniers mois. Tient compte des trimestres manquants chez Yahoo :
    on ne garde que les trimestres publiés dans l'année qui précède le dernier, puis on annualise."""
    dates = dates or _DATES
    if not vals:
        return NAN
    if dates and len(dates) == len(vals):
        k = next((i for i, v in enumerate(vals) if not pd.isna(v)), 0)
        d0 = pd.Timestamp(dates[k])
        q = [v for v, d in zip(vals[k:], dates[k:]) if (d0 - pd.Timestamp(d)).days < 330 and not pd.isna(v)]
    else:
        q = [v for v in vals[:4] if not pd.isna(v)]
    if len(q) < 2:
        return NAN
    return float(sum(q)) * 4 / len(q) if len(q) < 4 else float(sum(q[:4]))


def _yoy(vals: list, dates: list | None = None) -> float:
    """Variation sur un an du dernier trimestre publié (vs le trimestre ~365 jours plus tôt).
    Les colonnes vides en tête (trimestre annoncé mais pas encore saisi par Yahoo) sont ignorées."""
    dates = dates or _DATES
    k = next((i for i, v in enumerate(vals) if not pd.isna(v)), None)
    if k is None:
        return NAN
    if dates and len(dates) == len(vals):
        d0 = pd.Timestamp(dates[k])
        prev = next((v for v, d in zip(vals[k + 1:], dates[k + 1:]) if 340 <= (d0 - pd.Timestamp(d)).days <= 390), None)
    else:
        prev = vals[k + 4] if len(vals) > k + 4 else None
    if prev is None or pd.isna(prev) or prev <= 0:
        return NAN
    return vals[k] / prev - 1


def _div(a, b):
    return a / b if b and not pd.isna(a) and not pd.isna(b) else NAN


def metrics(f: dict, market_cap: float = NAN) -> dict:
    inc, cf, bs = f.get("income", {}), f.get("cashflow", {}), f.get("balance", {})
    rev, gp, opi, ni = _v(inc, "revenue"), _v(inc, "gross_profit"), _v(inc, "op_income"), _v(inc, "net_income")
    ebitda, ebit, intexp = _v(inc, "ebitda"), _v(inc, "ebit"), _v(inc, "interest_expense")
    ocf, capex, fcf, sbc = _v(cf, "ocf"), _v(cf, "capex"), _v(cf, "fcf"), _v(cf, "sbc")
    cash, debt, shares = _v(bs, "cash"), _v(bs, "debt"), _v(bs, "shares")
    dcur, dnc = _v(bs, "deferred_rev_cur"), _v(bs, "deferred_rev_nc")
    deferred = [(0 if pd.isna(a) else a) + (0 if pd.isna(b) else b) if not (pd.isna(a) and pd.isna(b)) else np.nan
                for a, b in zip(dcur, dnc)] if dcur or dnc else []

    di, dc, db = inc.get("dates", []), cf.get("dates", []), bs.get("dates", [])
    rev_ttm, ocf_ttm = _ttm(rev, di), _ttm(ocf, dc)
    capex_ttm = -_ttm(capex, dc) if capex else NAN
    fcf_ttm = _ttm(fcf, dc) if fcf else (ocf_ttm - capex_ttm)
    ebitda_ttm, ebit_ttm, int_ttm = _ttm(ebitda, di), _ttm(ebit, di), _ttm(intexp, di)
    c0 = cash[0] if cash else NAN
    d0 = debt[0] if debt else 0.0
    d0 = 0.0 if pd.isna(d0) else d0
    net_debt = d0 - c0 if not pd.isna(c0) else NAN
    rev_yoy = _yoy(rev, di)
    rev_yoy_prev = _yoy(rev[1:], di[1:]) if len(rev) > 5 else NAN
    m = {
        "fin_quarter": (inc.get("dates") or cf.get("dates") or [None])[0],
        "rev_ttm": rev_ttm,
        "rev_yoy": rev_yoy,
        "rev_accel": rev_yoy - rev_yoy_prev if not pd.isna(rev_yoy_prev) else NAN,
        "gross_margin": _div(_ttm(gp, di), rev_ttm),
        "op_margin": _div(_ttm(opi, di), rev_ttm),
        "net_margin": _div(_ttm(ni, di), rev_ttm),
        "ni_ttm": _ttm(ni, di),
        "ocf_ttm": ocf_ttm,
        "capex_ttm": capex_ttm,
        "capex_yoy": _yoy([-x for x in capex], dc) if capex else NAN,
        "capex_to_rev": _div(capex_ttm, rev_ttm),
        "capex_coverage": _div(ocf_ttm, capex_ttm),          # OCF / capex : >1 = capex autofinancé
        "fcf_ttm": fcf_ttm,
        "fcf_margin": _div(fcf_ttm, rev_ttm),
        "fcf_last_q": fcf[0] if fcf else NAN,
        "fcf_neg_quarters": int(sum(1 for x in fcf[:4] if not pd.isna(x) and x < 0)),
        "sbc_to_rev": _div(_ttm(sbc, dc), rev_ttm),
        "cash": c0, "debt": d0, "net_debt": net_debt,
        "net_debt_ebitda": _div(net_debt, ebitda_ttm) if not pd.isna(net_debt) and ebitda_ttm and ebitda_ttm > 0 else NAN,
        "interest_coverage": _div(ebit_ttm, int_ttm) if int_ttm and int_ttm > 0 else NAN,
        "debt_yoy": _yoy(debt, db) if debt else NAN,
        "dilution_yoy": _yoy(shares, db) if shares else NAN,
        "deferred_rev": deferred[0] if deferred else NAN,
        "deferred_yoy": _yoy(deferred, db) if deferred else NAN,
        "fcf_yield": _div(fcf_ttm, market_cap),
    }
    return m


def capex_quarters(fund: dict, tickers: list[str]) -> list[dict]:
    """Capex, OCF et FCF trimestriels cumulés d'un groupe (calendrier aligné sur la fin de trimestre)."""
    rows: dict[str, dict] = {}
    for t in tickers:
        cf = fund.get(t, {}).get("cashflow", {})
        for i, d in enumerate(cf.get("dates", [])):
            q = str(pd.Period(d, "Q"))
            r = rows.setdefault(q, {"quarter": q, "capex": 0.0, "ocf": 0.0, "fcf": 0.0, "n": 0})
            cx, oc, fc = (cf.get(k, [None] * 9)[i] for k in ("capex", "ocf", "fcf"))
            if cx is None or oc is None:
                continue
            r["capex"] += -cx
            r["ocf"] += oc
            r["fcf"] += fc if fc is not None else oc + cx
            r["n"] += 1
    full = [r for r in rows.values() if r["n"] == len(tickers)]
    return sorted(full, key=lambda r: r["quarter"])[-6:]
