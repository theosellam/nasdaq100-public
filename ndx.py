#!/usr/bin/env python3
"""Système d'analyse du Nasdaq 100.

Commandes :
  python3 ndx.py serve               Tableau de bord EN DIRECT (http://localhost:8100) — recommandé
  python3 ndx.py update              Télécharge composition, cours, capitalisations et analystes
  python3 ndx.py summary [--live]    Synthèse : indice, ampleur, secteurs, analystes
  python3 ndx.py oversold [--h 10]   Titres les plus survendus sur 5, 10 ou 30 séances
  python3 ndx.py stock NVDA [--live] Fiche détaillée d'un titre (technique + analystes)
  python3 ndx.py screen              Tous les signaux (survente, croisements, plus hauts/bas…)
  python3 ndx.py rank --by upside    Classement (score, upside, os_10j, r_3M, pe_fwd…)
  python3 ndx.py context [--live]    Contexte de marché : régime, divergences, paniers IA, capex, événements
  python3 ndx.py opps [--all]        Opportunités : entrée technique validée par les fondamentaux
  python3 ndx.py strategies [--live] Stratégies du contexte (rattrapage post-Jensen, Fed restrictive, IA à prix raisonnable)
  python3 ndx.py check NVDA          Grille de validation fondamentale complète d'un titre
  python3 ndx.py portfolio [--fetch] Mon portefeuille : positions, garde-fous, profil de trading
                                     (positions saisies dans le tableau de bord, ou relevés Saxo dans data/private/imports/)
  python3 ndx.py lab [--build|--full] Laboratoire de signaux : facteurs testés sur l'historique, score composite
  python3 ndx.py check NVDA 1000     (voir « check ») — avec un montant : liste de vérification avant achat
  python3 ndx.py analysts            Recharge les prévisions des analystes
  python3 ndx.py fundamentals        Recharge les états financiers trimestriels
  python3 ndx.py report              Export HTML statique (reports/nasdaq100.html)
  python3 ndx.py all                 update + summary + report
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

from ndx import analysis, data, report

pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 200)


def pct(x, signed=True):
    if x is None or pd.isna(x):
        return "—"
    return f"{x * 100:+.1f} %" if signed else f"{x * 100:.1f} %"


def num(x, d=1):
    return "—" if x is None or pd.isna(x) else f"{x:,.{d}f}"


def _lab():
    try:
        from ndx import lab as L
        return L.load()
    except Exception:
        return None


def load_analysis(live: bool = False) -> analysis.Analysis:
    cons, close, volume, caps = data.load()
    if live:
        print("→ Récupération des derniers cours…")
        close, volume = data.merge_live(close, volume, cons["ticker"].tolist())
    ctx = data.load_context()
    if live or ctx.empty:
        try:
            ctx = data.merge_context(ctx)
        except Exception:
            pass
    return analysis.run(cons, close, volume, caps, data.load_analysts(), data.load_fundamentals(), ctx, _lab())


def cmd_context(a: analysis.Analysis) -> None:
    c = a.context
    print("\n══ Contexte du Nasdaq 100 ══")
    for n in c["notes"]:
        print(" •", n)
    print("\nMarqueurs :", "   ".join(
        f"{m['label']} {num(m['last'], 2)} ({num(m['chg_1j'], 2) + ' pts' if m['chg_kind'] == 'pts' else pct(m['chg_1j'])})"
        for m in c["macro"].values()))
    print("\nPaniers thématiques (équipondérés)            1J      5J   record  midterms  % bear  potentiel")
    for t in sorted(c["themes"], key=lambda x: x["r_record"] or 0):
        print(f"  {t['label'][:42]:42} {pct(t['r_1J']):>8} {pct(t['r_5j']):>8} {pct(t['r_record']):>8} "
              f"{pct(t['r_midterms']):>8} {pct(t['pct_bear'], False):>7} {pct(t['upside']):>8}")
    cs = c["customers"]
    print(f"\nCapex IA des hyperscalers : {num((cs['capex_ttm'] or 0) / 1e9, 0)} Md$ / 12 mois, "
          f"{pct(cs['capex_yoy'])} sur un an, {pct(cs['capex_to_rev'], False)} du CA, "
          f"couverture OCF {num(cs['coverage'], 2)}×, FCF {num((cs['fcf_ttm'] or 0) / 1e9, 0)} Md$")
    for r in c["capex"]["hyperscalers"] + c["capex"]["neoclouds"]:
        print(f"  {r['ticker']:5} capex {num((r.get('capex_ttm') or 0) / 1e9, 0):>5} Md$  {pct(r.get('capex_yoy')):>8} a/a  "
              f"{pct(r.get('capex_to_rev'), False):>7} du CA  OCF/capex {num(r.get('capex_coverage'), 2):>5}×  "
              f"FCF dernier trim. {num((r.get('fcf_last_q') or 0) / 1e9, 1):>6} Md$  dette {pct(r.get('debt_yoy'))}")
    print("\nÉvénements (réaction le jour J → depuis)")
    for e in c["events"]:
        d, s = e["day"], e["since"]
        print(f"  {e['date']}  {e['label'][:48]:48}  NDX {pct(d.get('NDX'))} → {pct(s.get('NDX'))}   "
              f"NVDA {pct(d.get('NVDA'))} → {pct(s.get('NVDA'))}   IA calcul {pct(d.get('ai_compute'))}")


def cmd_lab(out: dict) -> None:
    cv, c = out["coverage"], out["composite"]
    print(f"\n══ Laboratoire de signaux — {cv['months']} mois, {cv['obs']} observations, {cv['tickers']} sociétés ══")
    print(f"Régime actuel : taux {out['regime']['rates']} · marché {out['regime']['market']} · {out['regime']['ai']}")
    print(f"\n{'Facteur':46} {'IC 1m':>7} {'t':>5} {'IC 3m':>7} {'t':>5} {'Q5−Q1/mois':>11}")
    for f in sorted(out["factors"], key=lambda x: -x["h21"]["ic"]):
        print(f"  {f['label'][:44]:44} {f['h21']['ic']:+7.3f} {f['h21']['t']:+5.1f} {f['h63']['ic']:+7.3f} {f['h63']['t']:+5.1f} {pct(f['spread21']):>11}")
    print(f"\nScore composite (hors échantillon, {c['period']}) : IC {c['ic']:+.3f} (t {c['ic_t']:+.1f}), "
          f"meilleur − pire quintile {pct(c['ls_month'])} par mois, {pct(c['hit'], False)} de mois gagnants")
    for s in c["schemes"]:
        print(f"   {s['label']:36} IC {s['ic']:+.3f} (t {s['t']:+.1f})  écart {pct(s['ls'])}/mois")
    print("\nPoints d'entrée techniques (performance vs membres, 60 séances) :")
    for r in out["entries"]["table"]:
        g = lambda k: f"{pct(r[k]['x60'])} ({r[k]['n']})"
        print(f"  {r['label'][:36]:36} tous {g('all'):>16}  validés {g('valid'):>16}  bon marché {g('cheap'):>16}  chers {g('dear'):>16}")
    print("\nScore factoriel du jour — 10 meilleurs :", ", ".join(f"{r['ticker']}" for r in out["today"][:10]))
    print("10 moins bons :", ", ".join(f"{r['ticker']}" for r in out["today"][-10:]))


def cmd_swing(a: analysis.Analysis, build: bool, show_all: bool) -> None:
    from ndx import swing as SW, history as Hh
    if build or not SW.SWING_FILE.exists():
        uni = Hh.universe(a.stocks.index.tolist())
        Hh.update_ohlcv(uni) if Hh.OHLCV_FILE.exists() else Hh.fetch_ohlcv(uni)
        SW.build()
    hist = SW.load()
    print(f"\n══ Swing — validation historique {hist['period']} (entrée à l'ouverture suivante, stop / objectif / 20 séances) ══")
    print(f"Référence : entrée au hasard = {hist['base']['R']['exp_R']:+.2f} R par trade\n")
    print(f"  {'Setup':32} {'cas':>5} {'esp. R':>7} {'vs hasard':>9} {'t':>5} {'gagnants':>8}  statut")
    for s in hist["setups"]:
        x = s["all"]
        print(f"  {s['label']:32} {x['n']:5} {x['exp_R']:+7.2f} {x['edge_R']:+9.2f} {x['t_R']:+5.1f} {pct(x['win'], False):>8}  {s['status']}")
    O = SW.live_frames(Hh.load_ohlcv(), None, a.stocks.index.tolist())
    sc = SW.screen(O, a.stocks, a.opportunities, (a.context or {}).get("regime", {}), hist)
    sigs = [x for x in sc["signals"] if show_all or x["status"] != "Écarté"]
    print(f"\nSignaux du {sc['asof']} ({len(sc['signals'])} au total{'' if show_all else ', écartés masqués (--all)'}) :")
    for x in sigs:
        print(f"  {x['status']:16} {x['ticker']:6} {x['label']:30} entrée {x['entry']:>9.2f}  stop {x['stop']:>9.2f} ({pct(x['stop_pct'])})"
              f"  objectif {x['target']:>9.2f}  R/R {x['rr']:.1f}")
        for c in x["cons"][:3]:
            print(f"{'':20}! {c}")
    print(f"\nEn formation ({len(sc['near'])}) :", ", ".join(f"{n['ticker']} ({n['label'].lower()})" for n in sc["near"][:15]))
    print("\nOutil d'analyse, pas un conseil en investissement personnalisé.")


def cmd_strategies(a: analysis.Analysis) -> None:
    S = a.strategies
    m = S["macro"]
    print("\n══ Stratégies — contexte ══")
    print(" •", m["fed"]); print(" •", m["inflation"])
    print(" • À venir :", " · ".join(f"{c['date']} {c['label']}" for c in m["calendar"]))
    print(f"\n{'Repère':38} {'vs record':>9} {'absence':>8} {'rebond':>8} {'récup.':>7}")
    for r in S["reference"]:
        print(f"  {r['name'][:36]:36} {pct(r.get('r_record')):>9} {pct(r.get('dd_absence')):>8} {pct(r.get('rebound')):>8} {pct(r.get('recovery'), False):>7}")
    for s in S["strategies"]:
        print(f"\n══ {s['name']} — {len(s['matches'])} titres ══")
        print("  " + s["thesis"])
        bt = S["backtests"].get(s["key"])
        if bt:
            vals = {k: v for k, v in bt.items() if isinstance(v, float)}
            print(f"  Test historique ({bt['n']} épisodes, {bt['period']}) : " + "  ".join(f"{k} {pct(v)}" for k, v in vals.items()))
        for r in s["matches"][:12]:
            print(f"   {r['ticker']:5} {str(r['company'])[:22]:22} score {r['score']:>4.0f}  {(r['verdict'] or ''):15} "
                  f"entrée {num(r['entry'], 2)} stop {num(r['stop'], 2)} obj. {num(r['target'], 2)} R/R {num(r['rr'], 1)}")
        if s["near"]:
            print("   Presque : " + ", ".join(f"{r['ticker']} (manque : {r['failed'][0]})" for r in s["near"][:5]))
    print("\nÀ éviter en régime de taux hauts :", ", ".join(f"{e['ticker']}" for e in S["exclusions"]))


def cmd_portfolio(a: analysis.Analysis, fetch: bool) -> None:
    from ndx import portfolio as P
    pf = P.load()
    if not pf:
        sys.exit("Aucune position. Saisissez-les dans l'onglet « Mon portefeuille » du tableau de bord (python3 ndx.py serve), "
                 f"ou déposez vos exports Saxo (AggregatedAmounts_*.xlsx, Holdings_*.pdf) dans {P.IMPORTS_DIR}")
    tickers = data.load()[0]["ticker"].tolist()
    extras = P.extra_tickers(pf, tickers)
    if fetch or (extras and not P.EXTRA_PRICES.exists()):
        print(f"→ Données des {len(extras)} titres détenus hors Nasdaq 100…")
        P.fetch_extras(extras)
    _, close, _, _ = data.load()
    xc, xv, xa, xf, xk = P.load_extras()
    if P.FX_TICKER not in xc:                       # taux USD → EUR pour valoriser les titres en dollars
        fx = data.fetch_prices([P.FX_TICKER], "1mo")[0][[P.FX_TICKER]]
        xc = fx if xc.empty else xc.combine_first(fx)
    v = P.view(pf, a, close, xc, xv, xa, xf, xk)
    print(f"\n══ Mon portefeuille — {'positions saisies le' if v.get('manual') else 'relevé du'} {v['holdings_asof']} · cours actualisés ══")
    print(f"Valeur {v['total']:,.0f} € · P/L latent {v['pl_latent']:+,.0f} € · liquidités {v['cash']:,.0f} € · "
          f"chaîne IA {v['ai_share']:.0%} · levier {v['lev_share']:.1%}".replace(",", " "))
    print("Verdict du système sur vos lignes : " + " · ".join(f"{k} {n}" for k, n in v["verdicts"].items()))
    print(f"\n{'Titre':7} {'Valeur':>8} {'Poids':>6} {'P/L':>8} {'%':>7}  {'Verdict':16} {'Fond.':>5}  Alertes")
    for r in v["positions"]:
        print(f"{r['ticker'][:7]:7} {r['value']:>8,.0f} {r['weight']:>6.1%} {r['pl']:>+8,.0f} {pct(r['ret']):>7}  "
              f"{(r['verdict'] or '—'):16} {num(r['fund_score'], 0):>5}  {'; '.join(r['flags'])[:60]}".replace(",", " "))
    print("\nGarde-fous dépassés (data/private/rules.json) :")
    for al in v["alerts"]:
        print("  ✗", al["text"])
    if v["validated_not_held"]:
        print("\nEntrées validées que vous ne détenez pas :", ", ".join(v["validated_not_held"]))
    if pf.get("a") is not None:
        b = P.behavior(pf["a"], pf["perf"], P.episodes(pf["a"]), close["^NDX"])
        sm = b["summary"]
        print(f"\n══ Profil de trading depuis le {sm['start']} ══")
        print(f"Versements nets {sm['deposits']:,.0f} € · valeur {sm['value']:,.0f} € · résultat {sm['pl_total']:+,.0f} € · "
              f"performance pondérée {sm['twr']:+.1%} (sommet {sm['twr_peak']:+.1%})".replace(",", " ")
              + f" · Nasdaq 100 sur la période {pct(sm['bench_ret'])} (en $, indicatif)")
        for f in b["facts"]:
            print(f"  {'!' if f['kind'] in ('risk', 'warn') else '✓' if f['kind'] == 'good' else '·'} {f['title']} — {f['text']}")


def cmd_opps(a: analysis.Analysis, show_all: bool) -> None:
    reg = a.context["regime"]
    print(f"\n══ Opportunités — {reg['phase']}{' · régime de stress : tailles réduites de moitié' if reg['stress'] else ''} ══")
    groups = ["Entrée validée", "Piège potentiel"] + (["À surveiller"] if show_all else [])
    for v in groups:
        rows = [o for o in a.opportunities if o["verdict"] == v]
        print(f"\n{v} ({len(rows)})")
        for o in rows[:25 if v != "À surveiller" else 40]:
            line = (f"  {o['ticker']:5} {str(o['company'])[:22]:22} fond. {num(o['fund_score'], 0):>3}/100  "
                    f"{(o['setup'] or o['trigger'] or '')[:44]:44}")
            if v != "À surveiller":
                line += (f" entrée {num(o['entry'], 2)}  stop {num(o['stop'], 2)} ({pct(-o['risk_pct'])})  "
                         f"obj. {num(o['target'], 2)} ({pct(o['gain_pct'])})  R/R {num(o['rr'], 1)}  taille {pct(o['position_pct'], False)}")
            print(line)
            for f in o["flags"]:
                print(f"        ✗ {f}")
            for w in o["warnings"]:
                print(f"        ! {w}")
    if not show_all:
        print(f"\n(+ {sum(o['verdict'] == 'À surveiller' for o in a.opportunities)} titres « à surveiller » : python3 ndx.py opps --all)")


def cmd_check(a: analysis.Analysis, ticker: str) -> None:
    t = ticker.upper().replace(".", "-")
    t = "GOOGL" if t == "GOOG" else t
    o = next((x for x in a.opportunities if x["ticker"] == t), None)
    if not o:
        sys.exit(f"{t} ne fait pas partie du Nasdaq 100.")
    print(f"\n══ {t} — {o['company']} — {o['verdict']} ══")
    print(f"Score fondamental {num(o['fund_score'], 0)}/100 (données disponibles pour {o['coverage'] * 100:.0f} % des contrôles)")
    print("Piliers : " + "  ".join(f"{k} {'—' if v is None else v}" for k, v in o["pillars"].items()))
    cur = None
    for c in o["checks"]:
        if c["pillar"] != cur:
            cur = c["pillar"]
            print(f"\n  {cur}")
        mark = "✓" if c["passed"] else "✗" if c["passed"] is False else "·"
        print(f"   {mark} {c['label']:62} {c['value']}")
    for f in o["flags"]:
        print(f"\n  ✗ DRAPEAU ROUGE : {f}")
    for w in o["warnings"]:
        print(f"  ! {w}")
    print("\nTechnique : " + ("; ".join(f"{s['label']} ({s['detail']})" for s in o["setups"]) or "aucun point d'entrée — " + (o["trigger"] or "")))
    print(f"Plan : entrée {num(o['entry'], 2)}  stop {num(o['stop'], 2)} ({pct(-o['risk_pct'])})  objectif {num(o['target'], 2)} "
          f"({pct(o['gain_pct'])})  R/R {num(o['rr'], 1)}  taille indicative {pct(o['position_pct'], False)} du capital (1 % risqué)")


def cmd_summary(a: analysis.Analysis) -> None:
    i, b = a.index, a.breadth
    print(f"\n══ NASDAQ 100 (^NDX) — {a.asof:%d/%m/%Y} ══")
    print(f"Niveau {i['level']:,.2f}   Tendance : {i['trend']}   RSI14 {i['rsi14']:.0f}")
    print("Perf : " + "  ".join(f"{p} {pct(i['r_' + p])}" for p in ["1J", "1S", "1M", "3M", "YTD", "1A"]))
    print(f"Écart MM50 {pct(i['dist_sma50'])}  MM200 {pct(i['dist_sma200'])}   "
          f"Repli vs plus haut {pct(i['dd_from_high'])}   Vol 1 mois {pct(i['vol_1m'], False)}")
    if i.get("analyst_target"):
        print(f"Analystes : objectif implicite {i['analyst_target']:,.0f} ({pct(i['analyst_upside'])})   "
              f"{pct(i['pct_buy'], False)} de recommandations d'achat   PER prévisionnel {num(i['pe_fwd'])}")
    print(f"\nAmpleur : {b['advancers']} hausses / {b['decliners']} baisses   "
          f"> MM50 {b['above50']:.0f} %   > MM200 {b['above200']:.0f} %   "
          f"Plus hauts 52s {b['new_highs']}   Plus bas {b['new_lows']}")
    print(f"Survendus : {b['oversold_5j']} (5 j)  {b['oversold_10j']} (10 j)  {b['oversold_30j']} (30 j)   "
          f"Concentration top 10 : {pct(b['top10_weight'], False)}")
    s = a.sectors.copy()
    for c in ["weight", "r_1J", "r_1M", "r_3M", "r_YTD", "upside"]:
        s[c] = s[c].map(lambda v, c=c: pct(v, c != "weight"))
    s["pct_above200"] = s["pct_above200"].map("{:.0f} %".format)
    print("\nSecteurs (pondérés par capitalisation) :")
    print(s[["n", "weight", "r_1J", "r_1M", "r_3M", "r_YTD", "pct_above200", "upside"]].to_string())
    st = a.stocks
    print("\nMeilleurs du jour :", ", ".join(f"{t} {pct(v)}" for t, v in st["r_1J"].nlargest(5).items()))
    print("Pires du jour     :", ", ".join(f"{t} {pct(v)}" for t, v in st["r_1J"].nsmallest(5).items()))
    if a.earnings:
        print("Résultats à venir :", ", ".join(f"{e['ticker']} {e['date'][8:10]}/{e['date'][5:7]}" for e in a.earnings[:10]))


def cmd_oversold(a: analysis.Analysis, h: int, n: int) -> None:
    st = a.stocks.sort_values(f"os_{h}j", ascending=False).head(n)
    rsi_col = "rsi2" if h == 5 else "rsi14"
    out = pd.DataFrame({
        "société": st["company"].str.slice(0, 24),
        f"perf {h}j": st[f"r_{h}j"].map(pct),
        "z-score": st[f"z_{h}j"].map(lambda v: num(v, 2)),
        rsi_col.upper(): st[rsi_col].map(lambda v: num(v, 0)),
        "vs MM20": st["dist_sma20"].map(pct),
        "séances": st["streak"],
        "potentiel": st.get("upside", pd.Series(index=st.index)).map(pct),
        "consensus": st.get("rec_key", pd.Series(index=st.index)).fillna("—"),
        "score surv.": st[f"os_{h}j"].map(lambda v: num(v, 0)),
        "signal": st[f"oversold_{h}j"].map({True: "SURVENDU", False: ""}),
    })
    print(f"\n── Les {n} titres les plus survendus sur {h} séances ──")
    print(out.to_string())
    print("\nz-score = baisse divisée par la volatilité attendue sur la période (≤ -1,5 : mouvement inhabituel).")


def cmd_stock(a: analysis.Analysis, ticker: str) -> None:
    t = ticker.upper().replace(".", "-")
    if t not in a.stocks.index:
        sys.exit(f"{t} ne fait pas partie du Nasdaq 100.")
    r = a.stocks.loc[t]
    rank = int(a.stocks["score"].rank(ascending=False)[t])
    print(f"\n══ {t} — {r['company']} ══")
    print(f"{r['sector']} / {r['subsector']}")
    print(f"Cours {r['price']:,.2f}   Capitalisation {r['market_cap'] / 1e9:,.0f} Md$   Poids {pct(r['weight'], False)}")
    print("Perf : " + "  ".join(f"{p} {pct(r['r_' + p])}" for p in ["1J", "1S", "1M", "3M", "6M", "YTD", "1A"]))
    print(f"Survente : 5 j {pct(r['r_5j'])} (z {num(r['z_5j'], 2)}, score {num(r['os_5j'], 0)})   "
          f"10 j {pct(r['r_10j'])} (z {num(r['z_10j'], 2)}, score {num(r['os_10j'], 0)})   "
          f"30 j {pct(r['r_30j'])} (z {num(r['z_30j'], 2)}, score {num(r['os_30j'], 0)})")
    print(f"RSI14 {r['rsi14']:.0f}  RSI2 {r['rsi2']:.0f}   MACD {'haussier' if r['macd_bull'] else 'baissier'}   "
          f"Écart MM20 {pct(r['dist_sma20'])}  MM50 {pct(r['dist_sma50'])}  MM200 {pct(r['dist_sma200'])}")
    print(f"Volatilité 1A {pct(r['vol_1a'], False)}   Bêta {r['beta']:.2f}   Sharpe {r['sharpe_1a']:.2f}   "
          f"Perte max 1A {pct(r['maxdd_1a'])}   Vs plus haut 52s {pct(r['dd_from_high'])}")
    print(f"Score technique {r['score']:.0f}/100 — rang {rank}/{len(a.stocks)}")
    if pd.notna(r.get("target_mean")):
        print(f"\nAnalystes ({num(r['n_analysts'], 0)}) : consensus {r['rec_key']} ({num(r['rec_mean'], 2)}/5)   "
              f"achat {pct(r['pct_buy'], False)}   vente {pct(r['pct_sell'], False)}")
        print(f"Objectif moyen {num(r['target_mean'], 2)} ({pct(r['upside'])})   "
              f"bas {num(r['target_low'], 2)} ({pct(r['upside_low'])})   haut {num(r['target_high'], 2)} ({pct(r['upside_high'])})")
        print(f"30 derniers jours : {r['upgrades_30j']} relèvement(s), {r['downgrades_30j']} abaissement(s), "
              f"révision moyenne des objectifs {pct(r['tgt_rev_30j'])}")
        print(f"Croissance BPA attendue : année en cours {pct(r['eps_g_cy'])}  année prochaine {pct(r['eps_g_ny'])}   "
              f"CA : {pct(r['rev_g_cy'])} / {pct(r['rev_g_ny'])}")
        print(f"PER prévisionnel {num(r.get('pe_fwd'))}  PER {num(r['pe_ttm'])}  PEG {num(r['peg'], 2)}  "
              f"Marge nette {pct(r['margin'], False)}   Prochains résultats {r.get('earnings_date') or '—'}")


def cmd_screen(a: analysis.Analysis) -> None:
    for name, df in a.signals.items():
        print(f"\n── {name} ({len(df)}) ──")
        if df.empty:
            print("  aucun")
            continue
        out = df.copy()
        for c in ["r_1J", "r_5j", "r_10j", "r_30j", "upside"]:
            if c in out:
                out[c] = out[c].map(pct)
        out["rsi14"] = out["rsi14"].map("{:.0f}".format)
        out["price"] = out["price"].map("{:,.2f}".format)
        print(out.drop(columns=["company"]).to_string())


def cmd_rank(a: analysis.Analysis, by: str, n: int, asc: bool) -> None:
    if by not in a.stocks:
        sys.exit(f"Colonne inconnue : {by}. Choix : {', '.join(a.stocks.columns)}")
    cols = [c for c in ["company", "price", "r_1M", "r_3M", "r_YTD", "rsi14", "upside", "rec_key", "pe_fwd", "score", by]
            if c in a.stocks]
    out = a.stocks.sort_values(by, ascending=asc)[list(dict.fromkeys(cols))].head(n).copy()
    for c in ["r_1M", "r_3M", "r_YTD", "upside"]:
        if c in out:
            out[c] = out[c].map(pct)
    out["price"] = out["price"].map("{:,.2f}".format)
    out["rsi14"] = out["rsi14"].map("{:.0f}".format)
    if "pe_fwd" in out:
        out["pe_fwd"] = out["pe_fwd"].map(num)
    out["company"] = out["company"].str.slice(0, 26)
    print(out.to_string())


def main() -> None:
    p = argparse.ArgumentParser(description="Analyse du Nasdaq 100")
    sub = p.add_subparsers(dest="cmd")
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8100)))
    sv.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="0.0.0.0 pour un hébergement en ligne")
    sv.add_argument("--no-browser", action="store_true")
    u = sub.add_parser("update"); u.add_argument("--period", default="2y"); u.add_argument("--no-caps", action="store_true"); u.add_argument("--no-analysts", action="store_true")
    sub.add_parser("analysts")
    sub.add_parser("fundamentals")
    pp = sub.add_parser("portfolio"); pp.add_argument("--fetch", action="store_true"); pp.add_argument("--live", action="store_true")
    sg = sub.add_parser("strategies"); sg.add_argument("--live", action="store_true")
    cx = sub.add_parser("context"); cx.add_argument("--live", action="store_true")
    op = sub.add_parser("opps"); op.add_argument("--all", action="store_true"); op.add_argument("--live", action="store_true")
    ck = sub.add_parser("check"); ck.add_argument("ticker"); ck.add_argument("amount", nargs="?", type=float); ck.add_argument("--live", action="store_true")
    sw = sub.add_parser("swing", help="screener swing : setups du jour, plans de trade, validation historique")
    sw.add_argument("--build", action="store_true", help="met à jour les bougies et refait la validation historique")
    sw.add_argument("--all", action="store_true", help="affiche aussi les signaux écartés")
    lb = sub.add_parser("lab"); lb.add_argument("--build", action="store_true"); lb.add_argument("--full", action="store_true")
    sm = sub.add_parser("summary"); sm.add_argument("--live", action="store_true")
    o = sub.add_parser("oversold"); o.add_argument("--h", type=int, choices=[5, 10, 30], default=10); o.add_argument("-n", type=int, default=15); o.add_argument("--live", action="store_true")
    s = sub.add_parser("stock"); s.add_argument("ticker"); s.add_argument("--live", action="store_true")
    sub.add_parser("screen")
    r = sub.add_parser("rank"); r.add_argument("--by", default="score"); r.add_argument("-n", type=int, default=100); r.add_argument("--asc", action="store_true")
    rp = sub.add_parser("report"); rp.add_argument("--out", default=None)
    sub.add_parser("all")
    from ndx import desk_cli
    desk_cli.add_parser(sub)
    from ndx.eu import cli as eu_cli
    eu_cli.add_parser(sub)
    args = p.parse_args()
    cmd = args.cmd or "serve"

    if cmd == "serve":
        from ndx import server
        server.serve(getattr(args, "port", int(os.environ.get("PORT", 8100))),
                     not getattr(args, "no_browser", False) and not os.environ.get("NDX_HEADLESS"),
                     getattr(args, "host", os.environ.get("HOST", "127.0.0.1")))
        return
    if cmd == "desk":
        sys.exit(desk_cli.run(args))
    if cmd == "eu":
        sys.exit(eu_cli.run(args))
    if cmd == "analysts":
        tickers = pd.read_csv(data.CONSTITUENTS_FILE)["ticker"].tolist()
        print("→ Prévisions des analystes…")
        data.fetch_analysts(tickers)
        print("✓ fait")
        return
    if cmd == "lab":
        from ndx import lab as L, history as Hh
        tickers = pd.read_csv(data.CONSTITUENTS_FILE)["ticker"].tolist()
        if args.full or not Hh.PRICES_FILE.exists():
            Hh.build_all(tickers)
        elif args.build:
            Hh.update_recent_prices()
        out = L.build(tickers) if (args.build or args.full or not L.LAB_FILE.exists()) else L.load()
        cmd_lab(out)
        return
    if cmd == "fundamentals":
        tickers = pd.read_csv(data.CONSTITUENTS_FILE)["ticker"].tolist()
        print("→ États financiers trimestriels…")
        data.fetch_fundamentals(tickers)
        print("✓ fait")
        return
    if cmd in ("update", "all"):
        data.update(getattr(args, "period", "2y"), not getattr(args, "no_caps", False),
                    not getattr(args, "no_analysts", False))
        if cmd == "update":
            return
    a = load_analysis(getattr(args, "live", False))
    if cmd == "summary":
        cmd_summary(a)
    elif cmd == "oversold":
        cmd_oversold(a, args.h, args.n)
    elif cmd == "stock":
        cmd_stock(a, args.ticker)
    elif cmd == "strategies":
        cmd_strategies(a)
    elif cmd == "swing":
        cmd_swing(a, args.build, args.all)
    elif cmd == "portfolio":
        cmd_portfolio(a, args.fetch)
    elif cmd == "context":
        cmd_context(a)
    elif cmd == "opps":
        cmd_opps(a, args.all)
    elif cmd == "check":
        cmd_check(a, args.ticker)
        if args.amount:
            from ndx import portfolio as Pm, lab as L
            pf = Pm.load()
            if pf:
                _, close, _, _ = data.load()
                xc, xv, xa, xf, xk = Pm.load_extras()
                v = Pm.view(pf, a, close, xc, xv, xa, xf, xk)
                t = args.ticker.upper()
                tt = "GOOGL" if t == "GOOG" else t
                op = next((o for o in a.opportunities if o["ticker"] == tt), None)
                lr = next((r for r in (L.load() or {}).get("today", []) if r["ticker"] == tt), None)
                res = Pm.pre_trade(t, args.amount, v, a.stocks.loc[t].to_dict() if t in a.stocks.index else None, op, lr)
                print(f"\n══ Avant d'acheter {t} pour {args.amount:,.0f} € ══".replace(",", " "))
                for x in res["checks"]:
                    print(f"  {'✓' if x['ok'] else '✗' if x['blocking'] else '!'} {x['label']:62} {x['detail']}")
                print("  →", res["summary"])
    elif cmd == "screen":
        cmd_screen(a)
    elif cmd == "rank":
        cmd_rank(a, args.by, args.n, args.asc)
    elif cmd in ("report", "all"):
        if cmd == "all":
            cmd_summary(a)
        path = report.build(a, getattr(args, "out", None))
        print(f"\n✓ Export statique : {path}")
        print("  Pour le tableau de bord en direct : python3 ndx.py serve")


if __name__ == "__main__":
    main()
