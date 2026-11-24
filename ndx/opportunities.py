"""Recherche d'opportunités : un point d'entrée technique n'est retenu QUE s'il est validé par les fondamentaux.

1. Validation fondamentale (0–100), six piliers de contrôles explicites (réussi / échoué / sans donnée) :
   analystes · croissance & contrats · rentabilité · financement · clients & débouchés · valorisation.
   + drapeaux rouges bloquants (brûle du cash en étant endetté, dilution massive, intérêts mal couverts…).
2. Points d'entrée techniques : pullback en tendance, retour sur MM200, survente extrême,
   reprise confirmée après survente, cassure de plus haut avec volume.
3. Plan : entrée, stop (sous le plus bas 10 séances ou 2 écarts-types), objectif (analystes / plus haut),
   rapport gain/risque, taille indicative pour 1 % de capital risqué — réduite de moitié en régime de stress.
4. Verdict : Entrée validée · À surveiller · Piège potentiel · Neutre.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .themes import AI_SUPPLIERS

PILLARS = {  # poids dans le score fondamental
    "Analystes": 22, "Croissance & contrats": 18, "Rentabilité": 20,
    "Financement": 22, "Clients & débouchés": 10, "Valorisation": 8,
}
VALID_SCORE = 65
TRAP_SCORE = 45
MIN_RR = 2.0
LAB_MIN_PCT = 0.40   # score factoriel minimum (rang) pour valider une entrée — voir le Laboratoire


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _pct(v, d=0):
    return f"{v * 100:+.{d}f} %" if _ok(v) else "n.d."


def _num(v, d=1, suf=""):
    return f"{v:.{d}f}{suf}" if _ok(v) else "n.d."


def _check(pillar: str, label: str, value, cond, shown: str) -> dict:
    passed = None if not _ok(value) else bool(cond(value))
    return {"pillar": pillar, "label": label, "passed": passed, "value": shown}


def fundamental_checks(r: pd.Series, customers: dict) -> tuple[list[dict], list[str], list[str]]:
    g = r.get
    utility = r.get("sector") == "Utilities"   # modèle régulé : capex financé par dette par construction
    net_cash = _ok(g("net_debt")) and g("net_debt") <= 0
    capex_heavy = _ok(g("capex_to_rev")) and g("capex_to_rev") > 0.08
    deferred_material = _ok(g("deferred_rev")) and _ok(g("rev_ttm")) and g("rev_ttm") and g("deferred_rev") / g("rev_ttm") > 0.03
    c = [
        # Analystes
        _check("Analystes", "Consensus achat (note ≤ 2,2)", g("rec_mean"), lambda v: v <= 2.2, _num(g("rec_mean"), 2)),
        _check("Analystes", "Potentiel vers l'objectif moyen ≥ 15 %", g("upside"), lambda v: v >= 0.15, _pct(g("upside"))),
        _check("Analystes", "Objectifs relevés ou stables (30 j)", g("tgt_rev_30j"), lambda v: v >= -0.02, _pct(g("tgt_rev_30j"), 1)),
        _check("Analystes", "Plus de relèvements que d'abaissements (30 j)",
               (g("upgrades_30j") or 0) - (g("downgrades_30j") or 0) if _ok(g("upgrades_30j")) else None,
               lambda v: v >= 0, f"{int(g('upgrades_30j') or 0)} ↑ / {int(g('downgrades_30j') or 0)} ↓"),
        _check("Analystes", "Couverture suffisante (≥ 8 analystes)", g("n_analysts"), lambda v: v >= 8, _num(g("n_analysts"), 0)),
        # Croissance & contrats
        _check("Croissance & contrats", "Chiffre d'affaires en hausse (> 5 % sur un an)", g("rev_yoy"), lambda v: v > 0.05, _pct(g("rev_yoy"))),
        _check("Croissance & contrats", "Croissance du CA attendue l'an prochain > 5 %", g("rev_g_ny"), lambda v: v > 0.05, _pct(g("rev_g_ny"))),
        _check("Croissance & contrats", "BPA attendu en hausse l'an prochain", g("eps_g_ny"), lambda v: v > 0, _pct(g("eps_g_ny"))),
        _check("Croissance & contrats", "Revenus différés (contrats facturés d'avance) en hausse",
               g("deferred_yoy") if deferred_material else None, lambda v: v >= 0, _pct(g("deferred_yoy")) if deferred_material else "non significatif"),
        # Rentabilité
        _check("Rentabilité", "Marge opérationnelle > 10 %", g("op_margin"), lambda v: v > 0.10, _pct(g("op_margin"))),
        _check("Rentabilité", "Free cash-flow positif (12 mois)", g("fcf_ttm"), lambda v: v > 0, _pct(g("fcf_margin")) + " du CA"),
        _check("Rentabilité", "Aucun trimestre à FCF négatif (4 derniers)", g("fcf_neg_quarters"), lambda v: v == 0, f"{_num(g('fcf_neg_quarters'), 0)} trim. négatif(s)"),
        _check("Rentabilité", "Bénéfice net positif (12 mois)", g("ni_ttm"), lambda v: v > 0, _pct(g("net_margin")) + " de marge"),
        _check("Rentabilité", "Rentabilité des capitaux propres > 10 %", g("roe"), lambda v: v > 0.10, _pct(g("roe"))),
        # Financement
        _check("Financement", "Trésorerie nette ou dette nette < " + ("6×" if utility else "2,5×") + " EBITDA",
               0 if net_cash else g("net_debt_ebitda"), lambda v: v < (6 if utility else 2.5),
               "trésorerie nette" if net_cash else _num(g("net_debt_ebitda"), 1, "×")),
        _check("Financement", "Intérêts couverts > 6× par l'EBIT",
               99 if net_cash and not _ok(g("interest_coverage")) else g("interest_coverage"), lambda v: v > 6,
               _num(g("interest_coverage"), 1, "×") if _ok(g("interest_coverage")) else "trésorerie nette"),
        _check("Financement", "Pas de dilution (actions < +2 % sur un an)", g("dilution_yoy"), lambda v: v < 0.02, _pct(g("dilution_yoy"), 1)),
        _check("Financement", "Dette maîtrisée (< +30 % sur un an, ou trésorerie nette)",
               0 if net_cash else g("debt_yoy"), lambda v: v < 0.30, "trésorerie nette" if net_cash else _pct(g("debt_yoy"))),
        _check("Financement", "Capex autofinancé (flux opérationnel ≥ 1,2× capex)",
               g("capex_coverage") if capex_heavy else None, lambda v: v >= 1.2,
               _num(g("capex_coverage"), 2, "×") if capex_heavy else "capex faible"),
        _check("Financement", "Liquidité courante > 1", g("current_ratio"), lambda v: v > 1, _num(g("current_ratio"), 2)),
        # Valorisation
        _check("Valorisation", "PER prévisionnel < 35", g("pe_fwd"), lambda v: v < 35, _num(g("pe_fwd"), 1)),
        _check("Valorisation", "Rendement FCF > 2 %", g("fcf_yield"), lambda v: v > 0.02, _pct(g("fcf_yield"), 1)),
    ]
    # Clients & débouchés : pour la chaîne d'approvisionnement IA, la solvabilité des clients
    # (hyperscalers et néo-clouds) compte autant que la société elle-même.
    if r.get("theme") in AI_SUPPLIERS and customers:
        c.append(_check("Clients & débouchés", "Hyperscalers clients : capex autofinancé (OCF ≥ 1,2× capex)",
                        customers.get("coverage"), lambda v: v >= 1.2, _num(customers.get("coverage"), 2, "×")))
        c.append(_check("Clients & débouchés", "Hyperscalers clients : FCF cumulé positif",
                        customers.get("fcf_ttm"), lambda v: v > 0, f"{customers.get('fcf_ttm', 0) / 1e9:,.0f} Md$"))
        c.append(_check("Clients & débouchés", "Néo-clouds clients : pas de FCF négatif financé par dette",
                        customers.get("neocloud_ok"), lambda v: v, "oui" if customers.get("neocloud_ok") else "non"))
    else:
        c.append(_check("Clients & débouchés", "Pouvoir de prix (marge brute ≥ 35 %)", g("gross_margin"), lambda v: v >= 0.35, _pct(g("gross_margin"))))
        c.append(_check("Clients & débouchés", "Croissance qui ne décélère pas (> −5 pts)", g("rev_accel"), lambda v: v > -0.05, _pct(g("rev_accel"), 1)))

    flags, warnings = [], []
    if _ok(g("fcf_ttm")) and g("fcf_ttm") < 0 and _ok(g("net_debt")) and g("net_debt") > 0 and not utility:
        flags.append("Brûle du cash en étant endetté")
    if _ok(g("interest_coverage")) and g("interest_coverage") < 2 and not net_cash:
        flags.append("Intérêts mal couverts (< 2×)")
    if _ok(g("dilution_yoy")) and g("dilution_yoy") > 0.08:
        flags.append(f"Dilution massive ({_pct(g('dilution_yoy'))})")
    if _ok(g("rev_yoy")) and g("rev_yoy") < -0.10:
        flags.append("Chiffre d'affaires en chute (> −10 %)")
    if _ok(g("rec_mean")) and g("rec_mean") > 3.0:
        flags.append("Consensus analystes négatif")
    if _ok(g("debt_yoy")) and g("debt_yoy") > 0.5 and _ok(g("fcf_ttm")) and g("fcf_ttm") < 0:
        flags.append("Dette en forte hausse alors que le FCF est négatif")
    # Avertissements (non bloquants, mais affichés)
    if _ok(g("tgt_rev_30j")) and g("tgt_rev_30j") < -0.15:
        warnings.append(f"Objectifs de cours abaissés de {_pct(g('tgt_rev_30j'))} en moyenne (30 j)")
    if _ok(g("capex_to_rev")) and g("capex_to_rev") > 0.25 and _ok(g("capex_coverage")) and g("capex_coverage") < 1.3:
        warnings.append(f"Capex lourd ({_pct(g('capex_to_rev'))} du CA) peu couvert par le flux opérationnel")
    if _ok(g("sbc_to_rev")) and g("sbc_to_rev") > 0.15:
        warnings.append(f"Rémunérations en actions élevées ({_pct(g('sbc_to_rev'))} du CA)")
    if r.get("earnings_date"):
        d = (pd.Timestamp(r["earnings_date"]) - pd.Timestamp.now().normalize()).days
        if 0 <= d <= 10:
            warnings.append(f"Résultats dans {d} j ({r['earnings_date']}) : risque d'écart")
    return c, flags, warnings


def fundamental_score(checks: list[dict]) -> tuple[float, dict, float]:
    pillars, wsum, total = {}, 0.0, 0.0
    for p, w in PILLARS.items():
        cs = [c for c in checks if c["pillar"] == p and c["passed"] is not None]
        if not cs:
            pillars[p] = None
            continue
        sc = 100 * sum(c["passed"] for c in cs) / len(cs)
        pillars[p] = round(sc)
        wsum += w
        total += w * sc
    coverage = sum(c["passed"] is not None for c in checks) / len(checks)
    return (round(total / wsum, 1) if wsum else float("nan")), pillars, coverage


def technical_setups(r: pd.Series) -> list[dict]:
    g = r.get
    out = []
    up_trend = _ok(g("dist_sma200")) and g("dist_sma200") > 0 and (g("sma200_slope") or 0) > 0
    if up_trend and -0.08 <= (g("dist_sma50") or 9) <= 0.015 and 30 <= (g("rsi14") or 0) <= 50:
        out.append({"key": "pullback", "label": "Pullback dans une tendance haussière",
                    "detail": f"{_pct(g('dist_sma50'), 1)} vs MM50, RSI {_num(g('rsi14'), 0)}, MM200 montante",
                    "strength": 75 + min(15, (50 - g("rsi14")) / 2)})
    if _ok(g("dist_sma200")) and abs(g("dist_sma200")) <= 0.03 and (g("sma200_slope") or 0) > 0:
        out.append({"key": "sma200", "label": "Test de la MM200 montante (support majeur)",
                    "detail": f"{_pct(g('dist_sma200'), 1)} de la MM200", "strength": 70})
    if (g("os_10j") or 0) >= 85 or ((g("rsi2") or 100) < 5 and (g("z_5j") or 0) <= -1.5):
        counter = not up_trend
        out.append({"key": "oversold", "label": "Survente extrême" + (" (contre-tendance)" if counter else ""),
                    "detail": f"score survente 10 j {_num(g('os_10j'), 0)}, RSI 2 = {_num(g('rsi2'), 0)}, z 10 j = {_num(g('z_10j'), 1)}",
                    "strength": 55 if counter else 70})
    if g("rsi_reclaim30") or (g("reclaim_sma20") and (g("r_30j") or 0) < -0.05):
        out.append({"key": "reversal", "label": "Reprise confirmée après survente",
                    "detail": ("RSI 14 repasse au-dessus de 30" if g("rsi_reclaim30") else "cours repasse au-dessus de la MM20")
                              + (" avec volume" if g("up_day_vol") else ""),
                    "strength": 80 + (8 if g("up_day_vol") else 0)})
    if g("new_high") and (g("vol_ratio") or 0) > 1.5:
        out.append({"key": "breakout", "label": "Cassure du plus haut 52 semaines avec volume",
                    "detail": f"volume {_num(g('vol_ratio'), 1)}× la moyenne", "strength": 72})
    return sorted(out, key=lambda x: -x["strength"])


def trade_plan(r: pd.Series, stress: bool) -> dict:
    price, dvol = r.get("price"), r.get("dvol") or 0.02
    stop = min((r.get("low_10") or price) * 0.985, price * (1 - 2 * dvol * math.sqrt(5)))
    stop = min(max(stop, price * 0.75), price * 0.97)   # stop entre −3 % et −25 %
    hi52, tgt = r.get("hi52"), r.get("target_mean")
    cands = [x for x in (tgt, hi52) if _ok(x) and x > price * 1.01]
    target = min(cands) if cands else price + 2 * (price - stop)
    risk = (price - stop) / price
    rr = (target - price) / (price - stop) if price > stop else float("nan")
    size = min(0.25, 0.01 / risk) * (0.5 if stress else 1) if risk > 0 else float("nan")
    return {"entry": price, "stop": stop, "target": target, "risk_pct": risk,
            "gain_pct": target / price - 1, "rr": rr, "position_pct": size}


def watch_trigger(r: pd.Series) -> str:
    g = r.get
    if _ok(g("dist_sma200")) and g("dist_sma200") > 0 and _ok(g("dist_sma50")) and g("dist_sma50") > 0.015:
        return f"Attendre un repli vers la MM50 ({_num(g('sma50'), 2)} $)"
    if _ok(g("rsi14")) and g("rsi14") < 30:
        return "Attendre que le RSI 14 repasse au-dessus de 30 (confirmation)"
    if _ok(g("dist_sma20")) and g("dist_sma20") < 0:
        return f"Attendre une clôture au-dessus de la MM20 ({_num(g('sma20'), 2)} $)"
    return "Pas de déclencheur proche"


def run(stocks: pd.DataFrame, customers: dict, stress: bool) -> list[dict]:
    rows = []
    for t, r in stocks.iterrows():
        if t == "GOOG":      # doublon de GOOGL (deux classes d'actions)
            continue
        checks, flags, warnings = fundamental_checks(r, customers)
        score, pillars, cov = fundamental_score(checks)
        setups = technical_setups(r)
        plan = trade_plan(r, stress)
        lab_ok = not _ok(r.get("lab_pct")) or r.get("lab_pct") >= LAB_MIN_PCT
        valid = _ok(score) and score >= VALID_SCORE and not flags and cov >= 0.6 and lab_ok
        if _ok(r.get("lab_pct")) and r.get("lab_pct") < LAB_MIN_PCT:
            warnings.append(f"Score factoriel faible ({r['lab_pct']:.0%} — parmi les 40 % les moins bien notés) : "
                            "historiquement, ces signaux font moins bien")
        if _ok(r.get("val_pct")) and r.get("val_pct") <= 0.4:
            warnings.append("Valorisation chère (rendement FCF dans les 40 % les plus faibles) : historiquement −0,5 % à 60 séances "
                            "après un signal technique, contre +1,2 % pour les titres bon marché")
        if setups and valid and plan["rr"] >= MIN_RR:
            verdict = "Entrée validée"
        elif setups and (flags or (_ok(score) and score < TRAP_SCORE)):
            verdict = "Piège potentiel"
        elif valid:
            verdict = "À surveiller"
        else:
            verdict = "Neutre"
        tech_q = setups[0]["strength"] if setups else 0
        rows.append({
            "ticker": t, "company": r.get("company"), "sector": r.get("sector"), "theme": r.get("theme_label"),
            "price": r.get("price"), "r_1J": r.get("r_1J"), "r_30j": r.get("r_30j"), "dd_from_ath": r.get("dd_from_ath"),
            "fund_score": score, "pillars": pillars, "coverage": cov, "checks": checks, "flags": flags, "warnings": warnings,
            "setups": setups, "setup": setups[0]["label"] if setups else None, "tech_strength": tech_q,
            **plan, "verdict": verdict,
            "trigger": watch_trigger(r) if not setups else (
                f"Signal présent mais gain/risque {plan['rr']:.1f} < {MIN_RR:.0f} : attendre un repli plus proche du stop"
                if _ok(plan["rr"]) and plan["rr"] < MIN_RR else None),
            "rank": (score if _ok(score) else 0) * 0.6 + tech_q * 0.4
                    + (10 if verdict == "Entrée validée" else 0) + min(10, (plan["rr"] or 0) * 2 if _ok(plan["rr"]) else 0),
            "policy": r.get("policy"), "upside": r.get("upside"), "rec_mean": r.get("rec_mean"),
            "earnings_date": r.get("earnings_date"), "lab_pct": r.get("lab_pct"), "lab_q": r.get("lab_q"), "val_pct": r.get("val_pct"),
        })
    order = {"Entrée validée": 0, "À surveiller": 1, "Piège potentiel": 2, "Neutre": 3}
    return sorted(rows, key=lambda x: (order[x["verdict"]], -x["rank"]))
