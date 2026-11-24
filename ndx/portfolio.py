"""Suivi du portefeuille personnel et analyse du comportement de trading.

Deux sources possibles (dossier data/private/, exclu de git) :
  - positions saisies à la main dans l'onglet « Mon portefeuille » → data/private/positions.json
    (symbole Yahoo, quantité, prix de revient unitaire, devise ; liquidités) ;
  - ou les relevés Saxo Bank déposés dans data/private/imports/ (prioritaires s'ils existent) :
  - AggregatedAmounts_*.xlsx : flux quotidiens par instrument (achats/ventes, P/L, frais, valeur du compte)
  - Holdings_*.pdf           : relevé des positions (quantité, prix d'entrée, P/L latent)

L'analyse est descriptive : elle mesure ce qui s'est passé (tailles, durées de détention, renforcement des
positions perdantes, levier, rythme des opérations) et confronte les positions au moteur d'opportunités.
Les garde-fous (data/private/rules.json) sont des limites que l'utilisateur fixe lui-même.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .data import DATA_DIR

PRIVATE_DIR = DATA_DIR / "private"
IMPORTS_DIR = PRIVATE_DIR / "imports"
RULES_FILE = PRIVATE_DIR / "rules.json"
MANUAL_FILE = PRIVATE_DIR / "positions.json"
EXTRA_PRICES = PRIVATE_DIR / "extra_prices.csv"
EXTRA_VOLUMES = PRIVATE_DIR / "extra_volumes.csv"
EXTRA_ANALYSTS = PRIVATE_DIR / "extra_analysts.json"
EXTRA_FUND = PRIVATE_DIR / "extra_fundamentals.json"
EXTRA_CAPS = PRIVATE_DIR / "extra_caps.json"
FX_TICKER = "EURUSD=X"

TRADE_TYPES = {"Share Amount": "Action", "Warrant Amount": "Warrant", "Turbo Amount": "Turbo"}
LEVERAGED = {"Warrant", "Turbo", "SrdOnEtf"}
SUFFIX = {"xnas": "", "xnys": "", "arcx": "", "bats": "", "xpar": ".PA", "xetr": ".DE", "xams": ".AS", "xbru": ".BR",
          "xmil": ".MI", "xmad": ".MC", "xlon": ".L", "xswx": ".SW", "xtse": ".TO"}

# Thèmes de quelques titres courants hors Nasdaq 100 (mêmes clés que ndx/themes.py).
# Pour un autre titre, renseignez la colonne « Thème » dans l'onglet Mon portefeuille.
EXTRA_THEMES = {
    "ANET": "ai_compute", "AAOI": "ai_compute", "CRDO": "ai_compute", "POET": "ai_compute", "LWLG": "ai_compute",
    "SMCI": "ai_compute", "TSM": "ai_compute", "DELL": "ai_compute", "HPE": "ai_compute", "CIEN": "ai_compute",
    "COHR": "ai_compute", "ALAB": "ai_compute", "NBIS": "neoclouds", "CRWV": "neoclouds",
    "VRT": "ai_power", "GEV": "ai_power", "ETN": "ai_power", "VST": "ai_power", "CCJ": "ai_power", "OKLO": "ai_power",
    "ORCL": "hyperscalers", "IBM": "software", "CRM": "software", "NET": "software", "NOW": "software",
    "SNOW": "software", "CLBT": "software", "MDB": "software", "TEM": "ai_apps", "PATH": "ai_apps", "AI": "ai_apps",
    "ASTS": "space", "RDW": "space", "RKLB": "space", "LUNR": "space", "PL": "space",
}
THEME_CHOICES = ["hyperscalers", "neoclouds", "ai_compute", "memory", "semi_equip", "semi_other", "ai_power", "software",
                 "ai_apps", "megacap_other", "consumer", "health", "industrials", "space"]
EUR_SUFFIXES = (".PA", ".DE", ".AS", ".BR", ".MI", ".MC", ".F", ".VI", ".HE", ".LS", ".IR")
SPACE_LABEL = "Spatial"

DEFAULT_RULES = {
    "_note": "Limites que VOUS fixez. Valeurs de départ à ajuster ; le tableau de bord signale les dépassements.",
    "max_poids_ligne": 0.10,
    "max_poids_levier": 0.05,
    "min_liquidites": 0.05,
    "max_lignes": 25,
    "max_poids_theme_ia": 0.60,
    "seuil_revue_perte_ligne": -0.25,
    "pause_apres_perte_jour": -0.04,
    "max_operations_semaine": 10,
}

N = r"-?\d{1,3}(?:[\s  ]\d{3})*(?:,\d+)?"
ROW = re.compile(rf"^([A-Z]{{3}})\s+({N})\s+({N})\s+({N})\s+({N})\s+({N})\s*%\s+({N})\s+({N})\s+({N})\s*%$")


def _fr(x: str) -> float:
    return float(re.sub(r"[\s  ]", "", x).replace(",", "."))


def _eur(x: float, signed: bool = True) -> str:
    """Montant en euros au format français (espace fine comme séparateur de milliers)."""
    t = f"{abs(x):,.0f}".replace(",", "\u202f")
    return (("+" if x > 0 else "−" if x < 0 else "") if signed else ("−" if x < 0 else "")) + t + " €"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def latest_file(pattern: str) -> Path | None:
    if not IMPORTS_DIR.exists():
        return None
    files = sorted(IMPORTS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


# ───────────────────────── Relevé des positions (PDF) ─────────────────────────
def parse_holdings_pdf(path: Path) -> dict:
    import pypdf
    lines: list[str] = []
    for page in pypdf.PdfReader(str(path)).pages:
        lines += [l.strip() for l in (page.extract_text() or "").splitlines() if l.strip()]
    asof = next((re.search(r"(\d{1,2}-\w+\.?-\d{4})", l).group(1) for l in lines if l.startswith("En date du") is False
                 and re.match(r"^\d{1,2}-\w+\.?-\d{4}$", l)), None)
    positions, buf, section = [], [], None
    for l in lines:
        if l in ("Actions", "Warrants", "Turbos", "ETF", "Etf", "Obligations", "Options", "CFD"):
            section, buf = l, []
            continue
        if l.startswith("Instrument") or l.startswith("Devise de") or l.startswith("l'instrument") or l.startswith("Quantité") \
                or l.startswith("Fluctuations") or l.startswith("prix %") or l.startswith("B/P non"):
            buf = []
            continue
        m = ROW.match(l)
        if m and section:
            name = " ".join(buf)
            isin = re.search(r"ISIN:\s*([A-Z0-9]+)", name)
            name = re.sub(r"\s*\(ISIN:.*$", "", name).replace("T echnology", "Technology").replace("T empus", "Tempus").strip()
            ccy, qty, fx, entry, price, chg, pl, value, w = m.groups()
            positions.append({"name": name, "isin": isin.group(1) if isin else None, "section": section, "ccy": ccy,
                              "qty": _fr(qty), "fx": _fr(fx), "entry": _fr(entry), "price": _fr(price),
                              "chg": _fr(chg) / 100, "pl": _fr(pl), "value": _fr(value), "weight": _fr(w) / 100})
            buf = []
            continue
        if l.startswith("Total") or l.startswith("Saxo Bank") or l.startswith("Page ") \
                or l.startswith("Devise:") or l.startswith("Compte(s)") or l.startswith("Date du rapport") \
                or l.startswith("Généré") or l.startswith("Positions,") or l.startswith("En date") or l.startswith("Toutes les") \
                or re.match(r"^\d{1,2}-\w+\.?-\d{4}$", l):
            buf = []
            continue
        buf.append(l)
    cash = next((_fr(re.search(rf"({N})\s+({N})\s*%$", l).group(1)) for l in lines
                 if l.startswith("Tous les comptes") and re.search(rf"({N})\s+({N})\s*%$", l)), 0.0)
    total = next((l for l in lines if l.startswith("Total général")), "")
    nums = re.findall(N, total.replace("Total général", ""))
    return {"file": path.name, "asof": asof, "positions": positions, "cash": cash,
            "total_pl": _fr(nums[0]) if nums else None, "total_value": _fr(nums[1]) if len(nums) > 1 else None}


# ───────────────────────── Flux (Excel) ─────────────────────────
def load_amounts(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lecture de l'export Excel (≈ 10 s pour 100 000 lignes), mise en cache dans data/private/."""
    cache = PRIVATE_DIR / f".cache_{path.stem}_{int(path.stat().st_mtime)}.pkl"
    if cache.exists():
        try:
            return pd.read_pickle(cache)
        except Exception:
            pass
    a = pd.read_excel(path, sheet_name="Montants cumulés")
    perf = pd.read_excel(path, sheet_name="Performance")
    for d in (a, perf):
        d["Date"] = pd.to_datetime(d["Date"], format="%d-%m-%Y")
    a = a.rename(columns={"Nom du type de montant": "kind", "Montant, devise du client": "eur", "Symbole": "sym",
                          "Nom instrument": "name", "Type d'actif": "asset"})
    for old in PRIVATE_DIR.glob(f".cache_{path.stem}_*.pkl"):
        old.unlink(missing_ok=True)
    pd.to_pickle((a, perf), cache)
    return a, perf


def yahoo_symbol(sym: str | float, asset: str | float) -> str | None:
    if not isinstance(sym, str) or ":" not in sym or asset not in ("Stock", "Etf"):
        return None
    base, mic = sym.split(":", 1)
    if mic not in SUFFIX:
        return None
    return base.replace(".", "-") + SUFFIX[mic] if SUFFIX[mic] == "" else base + SUFFIX[mic]


def episodes(a: pd.DataFrame) -> list[dict]:
    """Découpe l'historique de chaque instrument en épisodes de détention (ouverture → retour à zéro)."""
    trades = a[a["kind"].isin(TRADE_TYPES)].groupby(["sym", "Date"], as_index=False).agg(
        eur=("eur", "sum"), name=("name", "first"), asset=("asset", "first"))
    pv = a[a["kind"] == "Position Values"].groupby(["sym", "Date"])["eur"].sum()
    pl = a[a["kind"] == "Net P/L"].groupby(["sym", "Date"])["eur"].sum()
    last_day = a["Date"].max()
    out = []
    for sym, g in trades.groupby("sym"):
        g = g.sort_values("Date")
        empty = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        vals = pv.loc[sym] if sym in pv.index.get_level_values(0) else empty
        pls = pl.loc[sym] if sym in pl.index.get_level_values(0) else empty
        ep, eps = None, []
        for _, t in g.iterrows():
            if ep is None:
                ep = {"sym": sym, "name": t["name"], "asset": t["asset"], "start": t["Date"], "buys": [], "sells": []}
            (ep["buys"] if t["eur"] < 0 else ep["sells"]).append((t["Date"], float(t["eur"])))
            after = vals[vals.index > t["Date"]]
            closed = t["eur"] > 0 and (after.empty or abs(after.iloc[0]) < 1 or after.index[0] > t["Date"] + pd.Timedelta(days=6))
            if closed:
                ep["end"] = t["Date"]
                eps.append(ep)
                ep = None
        if ep is not None:
            ep["end"] = None
            eps.append(ep)
        # P/L de chaque épisode : P/L quotidien attribué à l'épisode en cours
        bounds = [e["start"] for e in eps] + [pd.Timestamp.max]
        for i, e in enumerate(eps):
            seg = pls[(pls.index >= bounds[i]) & (pls.index < bounds[i + 1])]
            invested = -sum(x for _, x in e["buys"])
            # renforcement en perte : achat alors que le P/L cumulé de l'épisode est négatif
            cum = seg.cumsum()
            avg_down = sum(1 for d, _ in e["buys"][1:] if len(cum[cum.index < d]) and cum[cum.index < d].iloc[-1] < 0)
            sells_gain = sum(1 for d, _ in e["sells"] if len(cum[cum.index < d]) and cum[cum.index < d].iloc[-1] > 0)
            end = e["end"] or last_day
            out.append({
                "sym": sym, "name": e["name"], "asset": e["asset"], "leveraged": e["asset"] in LEVERAGED,
                "start": e["start"].strftime("%Y-%m-%d"), "end": e["end"].strftime("%Y-%m-%d") if e["end"] is not None else None,
                "open": e["end"] is None, "days": int((end - e["start"]).days),
                "n_buys": len(e["buys"]), "n_sells": len(e["sells"]), "invested": invested,
                "max_value": float(vals[(vals.index >= e["start"]) & (vals.index <= end)].max()) if len(vals) else invested,
                "pl": float(seg.sum()), "ret": float(seg.sum() / invested) if invested > 0 else np.nan,
                "avg_down_buys": avg_down, "sells_in_gain": sells_gain,
            })
    return out


# ───────────────────────── Comportement ─────────────────────────
def behavior(a: pd.DataFrame, perf: pd.DataFrame, eps: list[dict], bench: pd.Series | None) -> dict:
    E = pd.DataFrame(eps)
    closed, opened = E[~E["open"]], E[E["open"]]
    trades = a[a["kind"].isin(TRADE_TYPES)].groupby(["sym", "Date"], as_index=False).agg(eur=("eur", "sum"), asset=("asset", "first"))
    trades["side"] = np.where(trades["eur"] < 0, "Achat", "Vente")
    acc = perf.set_index("Date")
    value = acc["AccountValueTimeSeries"]
    deposits = acc["CashTransferTimeSeries"]
    twr = acc["AccumulatedTimeWeightedTimeSeries"] / 100
    daily = acc["% daily returns"]

    # Coûts
    costs_kinds = ["Commission", "Client Commission Credit", "Commission Rebate", "Exchange Fee", "French Financial Transaction Tax",
                   "CurrencyConversion", "Structured Product Cost", "Depository Charges", "Client SRD Finance", "Client Interest"]
    mv = a[a["Affecte le solde"].isin(["Oui", "Yes"])]
    costs = float(mv[mv["kind"].isin(costs_kinds)]["eur"].sum())
    divs = float(mv[mv["kind"].str.startswith("Corporate Actions", na=False)]["eur"].sum())

    # Levier
    pv = a[a["kind"] == "Position Values"]
    lev_val = pv[pv["asset"].isin(LEVERAGED)].groupby("Date")["eur"].sum().reindex(value.index).fillna(0)
    lev_share = (lev_val / value).replace([np.inf, -np.inf], np.nan)
    pl_by_asset = E.groupby(E["leveraged"].map({True: "Produits à levier", False: "Actions / ETF"}))["pl"].sum().to_dict()

    # Rythme des opérations
    t = trades.copy()
    t["week"] = t["Date"].dt.to_period("W")
    weekly = t.groupby("week").size()
    monthly = t.assign(m=t["Date"].dt.strftime("%Y-%m")).groupby("m").agg(
        n=("eur", "size"), achats=("eur", lambda s: float(-s[s < 0].sum())), ventes=("eur", lambda s: float(s[s > 0].sum())))
    # Opérations dans les 3 jours suivant une forte baisse du compte vs en temps normal
    bad = daily[daily <= -0.03].index
    tdays = t.groupby("Date").size()
    after_bad = [int(tdays[(tdays.index > d) & (tdays.index <= d + pd.Timedelta(days=4))].sum()) for d in bad]
    base_rate = tdays.sum() / max(1, len(daily)) * 4

    # Disposition : gagnants vendus vs perdants gardés
    cw, cl = closed[closed["pl"] > 0], closed[closed["pl"] <= 0]
    ow, ol = opened[opened["pl"] > 0], opened[opened["pl"] <= 0]
    stocks_open = opened[~opened["leveraged"]]
    by_size = E[E["invested"] > 0].copy()

    # Performance vs Nasdaq 100 sur la même période (indicatif, indice en USD)
    bench_ret = None
    if bench is not None and len(value):
        b = bench.dropna()
        b0 = b[b.index <= value.index[0]]
        if len(b0):
            bench_ret = float(b.iloc[-1] / b0.iloc[-1] - 1)

    facts = []
    def fact(kind, title, text, value=None):
        facts.append({"kind": kind, "title": title, "text": text, "value": value})

    big_lev = E[E["leveraged"]].sort_values("pl").head(1)
    if len(big_lev):
        r = big_lev.iloc[0]
        fact("risk", "Produits à levier", f"{len(E[E['leveraged']])} épisodes sur warrants/turbos, résultat cumulé "
             f"{_eur(pl_by_asset.get('Produits à levier', 0))}. Le plus coûteux : {r['name']} ({_eur(r['pl'])}, "
             f"{r['n_buys']} achats dans l'épisode). Part maximale du compte en levier : {lev_share.max():.0%}.",
             float(lev_share.max()))
    if len(stocks_open):
        deep = stocks_open[stocks_open["pl"] / stocks_open["invested"].clip(lower=1) <= -0.5]
        fact("risk", "Pertes laissées courir", f"{len(ol)} positions ouvertes en perte contre {len(ow)} en gain ; "
             f"{len(deep)} lignes ont perdu plus de 50 % depuis leur achat. Les positions ouvertes perdantes sont détenues "
             f"depuis {ol['days'].median():.0f} jours en médiane.", len(deep))
    if len(closed):
        fact("info", "Positions clôturées", f"{len(closed)} positions clôturées : {len(cw)} gagnantes (gain médian "
             f"{cw['ret'].median():+.0%}, détention {cw['days'].median():.0f} j) et {len(cl)} perdantes (perte médiane "
             f"{cl['ret'].median():+.0%}, détention {cl['days'].median():.0f} j)." if len(cw) and len(cl) else
             f"{len(closed)} positions clôturées.", len(closed))
    tot_avg = int(E["avg_down_buys"].sum())
    if tot_avg:
        fact("warn", "Renforcement en perte", f"{tot_avg} achats ont renforcé une position déjà perdante "
             f"({int((E['avg_down_buys'] > 0).sum())} titres concernés).", tot_avg)
    if len(weekly):
        top = monthly.sort_values("n", ascending=False).head(2)
        fact("warn", "Rafales d'opérations", f"{len(t)} opérations au total ; record {weekly.max()} en une semaine. "
             f"Mois les plus actifs : " + ", ".join(f"{m} ({int(r.n)} opérations, {_eur(r.achats, False)} d'achats)"
                                                     for m, r in top.iterrows()) + ".", int(weekly.max()))
    if len(bad):
        fact("good" if np.mean(after_bad) <= base_rate else "warn", "Après une forte baisse du compte", f"{len(bad)} séances à −3 % ou pire. Dans les 4 jours suivants : "
             f"{np.mean(after_bad):.1f} opérations en moyenne, contre {base_rate:.1f} sur 4 jours ordinaires : pas de trading de revanche."
             if np.mean(after_bad) <= base_rate else f"{np.mean(after_bad):.1f} opérations en moyenne, contre {base_rate:.1f} sur 4 jours ordinaires : activité accrue après les pertes.", float(np.mean(after_bad)))
    fact("info", "Coûts", f"Frais, commissions, taxes et conversions de devises : {_eur(costs)} ; dividendes nets {_eur(divs)}.", costs)

    # Grande rotation : journée de ventes la plus lourde et ce qui a été racheté ensuite
    sells_day = t[t["eur"] > 0].groupby("Date")["eur"].agg(["sum", "size"])
    if len(sells_day):
        d0 = sells_day["sum"].idxmax()
        nxt = t[(t["Date"] > d0) & (t["Date"] <= d0 + pd.Timedelta(days=35)) & (t["eur"] < 0)]
        lev_bought = float(-nxt[nxt["asset"].isin(LEVERAGED)]["eur"].sum())
        realized = closed[closed["end"] == d0.strftime("%Y-%m-%d")]["pl"].sum()
        fact("warn", "La grande rotation", f"Le {d0:%d/%m/%Y} : {int(sells_day.loc[d0, 'size'])} ventes pour "
             f"{_eur(sells_day.loc[d0, 'sum'], False)} (dont {_eur(realized)} de gains sur les positions soldées ce jour-là). "
             f"Dans les 5 semaines suivantes : {_eur(-nxt['eur'].sum(), False)} d'achats, dont {_eur(lev_bought, False)} en produits à levier.",
             float(sells_day.loc[d0, "sum"]))

    # Achats selon la position du Nasdaq 100 par rapport à son plus haut
    if bench is not None:
        b = bench.dropna()
        ddb = b / b.rolling(252, min_periods=20).max() - 1
        buys = t[t["eur"] < 0].copy()
        buys["dd"] = [ddb[ddb.index <= d].iloc[-1] if len(ddb[ddb.index <= d]) else np.nan for d in buys["Date"]]
        tot = -buys["eur"].sum()
        near = -buys[buys["dd"] >= -0.03]["eur"].sum() / tot if tot else 0
        deep = -buys[buys["dd"] <= -0.10]["eur"].sum() / tot if tot else 0
        fact("warn" if near > 0.6 else "info", "Moment des achats",
             f"{near:.0%} des montants achetés l'ont été quand le Nasdaq 100 était à moins de 3 % de son plus haut annuel, "
             f"{deep:.0%} quand il était à −10 % ou plus bas.", float(near))

    return {
        "facts": facts,
        "summary": {
            "value": float(value.iloc[-1]), "deposits": float(deposits.iloc[-1]),
            "pl_total": float(value.iloc[-1] - deposits.iloc[-1]), "twr": float(twr.iloc[-1]),
            "twr_peak": float(twr.max()), "dd_from_peak": float((1 + twr.iloc[-1]) / (1 + twr.max()) - 1),
            "start": value.index[0].strftime("%Y-%m-%d"), "bench_ret": bench_ret,
            "n_trades": int(len(t)), "n_instruments": int(E["sym"].nunique()), "costs": costs, "dividends": divs,
            "lev_share_max": float(lev_share.max()), "lev_share_now": float(lev_share.iloc[-1]),
            "pl_by_asset": pl_by_asset, "win_rate_closed": float(len(cw) / len(closed)) if len(closed) else None,
        },
        "equity": {"dates": [d.strftime("%Y-%m-%d") for d in value.index], "value": value.round(2).tolist(),
                   "deposits": deposits.round(2).tolist(), "twr": (twr * 100).round(2).tolist(),
                   "lev": (lev_share.fillna(0) * 100).round(2).tolist()},
        "monthly": [{"month": m, **{k: float(v) for k, v in r.items()}} for m, r in monthly.iterrows()],
        "episodes": sorted(eps, key=lambda e: e["pl"]),
    }


# ───────────────────────── Titres détenus hors Nasdaq 100 ─────────────────────────
def extra_tickers(pf: dict, ndx: list[str]) -> list[str]:
    return sorted({p["yahoo"] for p in pf["positions"] if p.get("yahoo") and p["yahoo"] not in ndx})


def extras_age_hours() -> float:
    from .data import file_age_hours
    return file_age_hours(EXTRA_FUND)


def merge_live_extras(close: pd.DataFrame, volume: pd.DataFrame, tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    from . import data
    lc, lv = data.fetch_prices(tickers + [FX_TICKER], "1mo")
    return lc.combine_first(close), lv.combine_first(volume)


def fetch_extras(tickers: list[str]) -> None:
    """Historique, analystes, états financiers et capitalisation des titres détenus hors indice."""
    from . import data
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    if not tickers:
        return
    close, volume = data.fetch_prices(tickers + [FX_TICKER], "2y")
    close.to_csv(EXTRA_PRICES)
    volume.to_csv(EXTRA_VOLUMES)
    with ThreadPoolExecutor(max_workers=6) as pool:
        an = dict(zip(tickers, pool.map(data._fetch_analyst, tickers)))
        fu = dict(zip(tickers, pool.map(data._fetch_fundamental, tickers)))
    caps = dict(data.fetch_market_caps(tickers).items())
    upd = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    EXTRA_ANALYSTS.write_text(json.dumps({"updated": upd, "data": an}, default=str), encoding="utf-8")
    EXTRA_FUND.write_text(json.dumps({"updated": upd, "data": fu}), encoding="utf-8")
    EXTRA_CAPS.write_text(json.dumps(caps), encoding="utf-8")


def load_extras() -> tuple[pd.DataFrame, pd.DataFrame, dict, dict, dict]:
    rd = lambda p: pd.read_csv(p, index_col=0, parse_dates=True) if p.exists() else pd.DataFrame()
    rj = lambda p: json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return rd(EXTRA_PRICES), rd(EXTRA_VOLUMES), rj(EXTRA_ANALYSTS).get("data", {}), rj(EXTRA_FUND).get("data", {}), rj(EXTRA_CAPS)


# ───────────────────────── Chargement ─────────────────────────
_CACHE: dict = {}


def _num(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        v = float(str(x).replace("\u202f", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return v if np.isfinite(v) else None


def load_manual() -> dict:
    """Positions saisies à la main (onglet Mon portefeuille)."""
    if not MANUAL_FILE.exists():
        return {"cash": 0.0, "positions": []}
    try:
        d = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
        return {"cash": _num(d.get("cash")) or 0.0, "positions": list(d.get("positions") or [])}
    except Exception:
        return {"cash": 0.0, "positions": []}


def save_manual(payload: dict) -> dict:
    """Valide et enregistre les positions saisies. Renvoie {ok, saved, errors}."""
    errors, out = [], []
    for i, r in enumerate(payload.get("positions") or [], 1):
        t = str(r.get("ticker") or "").strip().upper().replace(" ", "")
        if not t:
            continue
        qty, entry = _num(r.get("qty")), _num(r.get("entry"))
        if qty is None or qty <= 0 or entry is None or entry <= 0:
            errors.append(f"Ligne {i} ({t}) : quantité et prix de revient doivent être des nombres positifs")
            continue
        if "." in t and not t.endswith(EUR_SUFFIXES):
            errors.append(f"Ligne {i} ({t}) : place de cotation non gérée (titres US, ou européens en euros : .PA, .DE, .AS, .MI…)")
            continue
        ccy = str(r.get("ccy") or "").upper() or ("EUR" if t.endswith(EUR_SUFFIXES) else "USD")
        if ccy not in ("USD", "EUR"):
            errors.append(f"Ligne {i} ({t}) : devise {ccy} non gérée (USD ou EUR)")
            continue
        theme = r.get("theme") or None
        out.append({"ticker": t, "name": str(r.get("name") or "").strip()[:80], "qty": qty, "entry": entry, "ccy": ccy,
                    "theme": theme if theme in THEME_CHOICES else None,
                    "leverage": bool(r.get("leverage"))})
    cash = _num(payload.get("cash")) or 0.0
    if errors:
        return {"ok": False, "errors": errors}
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    MANUAL_FILE.write_text(json.dumps({"cash": cash, "positions": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE.clear()
    return {"ok": True, "saved": len(out)}


def _load_manual_pf() -> dict | None:
    if not MANUAL_FILE.exists():
        return None
    key = ("manual", MANUAL_FILE.stat().st_mtime)
    if _CACHE.get("key") == key:
        return _CACHE["pf"]
    m = load_manual()
    if not m["positions"] and not m["cash"]:
        return None
    asof = pd.Timestamp(MANUAL_FILE.stat().st_mtime, unit="s").strftime("%d/%m/%Y")
    pos = []
    for r in m["positions"]:
        t = r["ticker"]
        pos.append({"name": r.get("name") or t, "isin": None, "section": "Actions", "ccy": r["ccy"], "qty": r["qty"],
                    "fx": 1.0, "entry": r["entry"], "price": r["entry"], "chg": 0.0, "pl": None, "value": None,
                    "cost": None, "weight": None, "sym": t, "asset": "Turbo" if r.get("leverage") else "Stock",
                    "yahoo": t, "ticker": t, "theme": r.get("theme")})
    pf = {"files": ["positions saisies (data/private/positions.json)"], "positions": pos, "a": None, "manual": True,
          "holdings": {"cash": m["cash"], "asof": asof}}
    _CACHE.update(key=key, pf=pf)
    return pf


def load() -> dict | None:
    """Lit les derniers relevés Saxo importés, sinon les positions saisies à la main
    (mis en cache tant que les fichiers ne changent pas)."""
    xl, pdf = latest_file("AggregatedAmounts_*.xlsx"), latest_file("Holdings_*.pdf")
    if not xl and not pdf:
        return _load_manual_pf()
    key = tuple((p.name, p.stat().st_mtime) for p in (xl, pdf) if p)
    if _CACHE.get("key") == key:
        return _CACHE["pf"]
    pf: dict = {"files": [p.name for p in (xl, pdf) if p], "positions": [], "a": None}
    names_to_sym: dict = {}
    if xl:
        a, perf = load_amounts(xl)
        pf["a"], pf["perf"] = a, perf
        names_to_sym = (a.dropna(subset=["sym", "name"]).drop_duplicates("name").set_index("name")[["sym", "asset"]]
                        .to_dict("index"))
    if pdf:
        h = parse_holdings_pdf(pdf)
        pf["holdings"] = {k: v for k, v in h.items() if k != "positions"}
        for p in h["positions"]:
            key = _norm(p["name"])
            info = names_to_sym.get(p["name"]) or next((v for n, v in names_to_sym.items() if _norm(n) == key), None) \
                or next((v for n, v in names_to_sym.items() if _norm(n)[:14] == key[:14]), {})
            p["sym"] = info.get("sym")
            p["asset"] = info.get("asset") or ("Warrant" if p["section"] == "Warrants" else "Stock")
            p["yahoo"] = yahoo_symbol(p["sym"], p["asset"])
            p["ticker"] = p["yahoo"] or (p["sym"] or p["name"]).split(":")[0]
            p["cost"] = p["value"] - p["pl"]
            pf["positions"].append(p)
    _CACHE.update(key=key, pf=pf)
    return pf


def rules() -> dict:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    if not RULES_FILE.exists():
        RULES_FILE.write_text(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        return {**DEFAULT_RULES, **json.loads(RULES_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULT_RULES)


# ───────────────────────── Vue complète (cours en direct + crible du système) ─────────────────────────
def view(pf: dict, ndx_result, close: pd.DataFrame, extra_close: pd.DataFrame, extra_vol: pd.DataFrame,
         extra_an: dict, extra_fund: dict, extra_caps: dict) -> dict:
    from . import analysis, fundamentals as fm, opportunities as opp
    from .themes import THEMES

    stocks = ndx_result.stocks
    ctx = ndx_result.context or {}
    bench = close["^NDX"].dropna()
    fx = extra_close[FX_TICKER].dropna() if FX_TICKER in extra_close else pd.Series(dtype=float)
    usd_eur = float(1 / fx.iloc[-1]) if len(fx) else None
    ndx_ops = {o["ticker"]: o for o in ndx_result.opportunities}

    # Analyse des titres hors indice avec les mêmes règles
    extra_rows = []
    for t in [c for c in extra_close.columns if c not in (FX_TICKER, "^NDX")]:
        s = extra_close[t].dropna()
        if len(s) < 60:
            continue
        m = analysis._stock_metrics(s, bench, extra_vol.get(t, pd.Series(dtype=float)))
        m.update(analysis._analyst_metrics(extra_an.get(t, {}), m["price"]))
        m.update(fm.metrics(extra_fund.get(t, {}), extra_caps.get(t, np.nan)))
        name = next((p["name"] for p in pf["positions"] if p.get("yahoo") == t), t)
        extra_rows.append({"ticker": t, "company": name, "sector": "Hors Nasdaq 100", "market_cap": extra_caps.get(t),
                           "theme": EXTRA_THEMES.get(t), "theme_label": THEMES.get(EXTRA_THEMES.get(t), {}).get("label", SPACE_LABEL if EXTRA_THEMES.get(t) == "space" else "—"),
                           **m})
    extra_ops = {}
    if extra_rows:
        ex = pd.DataFrame(extra_rows).set_index("ticker")
        both = pd.concat([stocks, ex], axis=0, join="outer")
        analysis._oversold_scores(both)
        ex = both.loc[ex.index]
        extra_ops = {o["ticker"]: o for o in opp.run(ex, ctx.get("customers", {}), ctx.get("regime", {}).get("stress", False))}

    R = rules()
    rows, total = [], 0.0
    for p in pf["positions"]:
        t = p.get("yahoo")
        live_price, r1 = None, None
        if t and t in close.columns and close[t].notna().any():
            s = close[t].dropna(); live_price, r1 = float(s.iloc[-1]), float(s.iloc[-1] / s.iloc[-2] - 1)
        elif t and t in extra_close.columns and extra_close[t].notna().any():
            s = extra_close[t].dropna(); live_price, r1 = float(s.iloc[-1]), float(s.iloc[-1] / s.iloc[-2] - 1)
        fx_now = usd_eur if p["ccy"] == "USD" and usd_eur else p["fx"]
        cost = p["cost"] if p.get("cost") is not None else p["qty"] * p["entry"] * fx_now   # saisie : PRU converti au cours actuel
        value = p["qty"] * live_price * fx_now if live_price else (p["value"] if p.get("value") is not None else cost)
        total += value
        o = ndx_ops.get("GOOGL" if t == "GOOG" else t) or extra_ops.get(t)
        theme = p.get("theme") or ((stocks.at[t, "theme"] if t in stocks.index else EXTRA_THEMES.get(t)) if t else None)
        rows.append({
            "ticker": p["ticker"], "yahoo": t, "name": p["name"], "asset": p["asset"], "in_ndx": bool(t in stocks.index) if t else False,
            "qty": p["qty"], "entry": p["entry"], "price": live_price or p["price"], "live": bool(live_price),
            "r_1J": r1, "value": value, "cost": cost, "pl": value - cost, "ret": (value - cost) / cost if cost else None,
            "theme": theme if isinstance(theme, str) else None,
            "theme_label": (THEMES[theme]["label"] if isinstance(theme, str) and theme in THEMES else SPACE_LABEL if theme == "space" else "Produit à levier" if p["asset"] in LEVERAGED else "—"),
            "verdict": o["verdict"] if o else None, "fund_score": o["fund_score"] if o else None,
            "flags": o["flags"] if o else [], "warnings": o["warnings"] if o else [], "setup": o["setup"] if o else None,
            "upside": o["upside"] if o else None, "rr": o["rr"] if o else None, "pillars": o["pillars"] if o else None,
            "dd_from_ath": o["dd_from_ath"] if o else None,
        })
    cash = float(pf.get("holdings", {}).get("cash") or 0)
    total += cash
    for r in rows:
        r["weight"] = r["value"] / total if total else None

    # Concentration et garde-fous
    by_theme: dict = {}
    for r in rows:
        by_theme[r["theme_label"]] = by_theme.get(r["theme_label"], 0) + r["value"] / total
    ai_keys = {k for k, v in THEMES.items() if v["ai"]}
    ai_share = sum(r["value"] for r in rows if r["theme"] in ai_keys) / total if total else 0
    lev_share = sum(r["value"] for r in rows if r["asset"] in LEVERAGED) / total if total else 0
    alerts = []
    for r in rows:
        if r["weight"] and r["weight"] > R["max_poids_ligne"]:
            alerts.append({"rule": "max_poids_ligne", "text": f"{r['ticker']} pèse {r['weight']:.1%} (limite {R['max_poids_ligne']:.0%})"})
        if r["ret"] is not None and r["ret"] <= R["seuil_revue_perte_ligne"] and r["asset"] not in LEVERAGED:
            alerts.append({"rule": "seuil_revue_perte_ligne", "text": f"{r['ticker']} à {r['ret']:+.0%} depuis l'achat : thèse à réexaminer"
                                                                       + (f" (système : {r['verdict']})" if r["verdict"] else "")})
    if lev_share > R["max_poids_levier"]:
        alerts.append({"rule": "max_poids_levier", "text": f"Produits à levier : {lev_share:.1%} du portefeuille (limite {R['max_poids_levier']:.0%})"})
    if cash / total < R["min_liquidites"]:
        alerts.append({"rule": "min_liquidites", "text": f"Liquidités : {cash / total:.1%} (minimum {R['min_liquidites']:.0%}) — aucune marge pour saisir une entrée validée"})
    if len(rows) > R["max_lignes"]:
        alerts.append({"rule": "max_lignes", "text": f"{len(rows)} lignes (maximum {R['max_lignes']}) — portefeuille difficile à suivre"})
    if ai_share > R["max_poids_theme_ia"]:
        alerts.append({"rule": "max_poids_theme_ia", "text": f"Chaîne de valeur IA : {ai_share:.0%} du portefeuille (limite {R['max_poids_theme_ia']:.0%})"})

    # Pondération vs Nasdaq 100 par thème
    ndx_theme = stocks.groupby("theme_label")["weight"].sum().to_dict()

    # Opportunités validées déjà détenues / non détenues
    held = {r["ticker"] for r in rows}
    validated = [o for o in ndx_result.opportunities if o["verdict"] == "Entrée validée"]
    return {
        "positions": sorted(rows, key=lambda r: -r["value"]),
        "total": total, "cash": cash, "usd_eur": usd_eur,
        "pl_latent": sum(r["pl"] for r in rows), "ai_share": ai_share, "lev_share": lev_share,
        "by_theme": [{"label": k, "weight": v, "ndx": ndx_theme.get(k)} for k, v in sorted(by_theme.items(), key=lambda x: -x[1])],
        "rules": R, "alerts": alerts,
        "verdicts": {v: sum(1 for r in rows if r["verdict"] == v) for v in ("Entrée validée", "À surveiller", "Piège potentiel", "Neutre")},
        "validated_not_held": [o["ticker"] for o in validated if o["ticker"] not in held],
        "holdings_asof": pf.get("holdings", {}).get("asof"), "files": pf["files"], "manual": bool(pf.get("manual")),
    }


# ───────────────────────── Achats passés face aux signaux (laboratoire) ─────────────────────────
def trades_vs_signals(pf: dict) -> dict | None:
    """Pour chaque achat d'action passé : état des signaux à cette date (score factoriel, valorisation, signal
    technique) et performance relative au Nasdaq 100 sur les 60 séances suivantes."""
    from . import history as H
    panel_p, events_p = H.HIST_DIR / "panel.csv", H.HIST_DIR / "entry_events.csv"
    if pf.get("a") is None or not panel_p.exists() or not H.PRICES_FILE.exists():
        return None
    P = pd.read_csv(panel_p, parse_dates=["date"], usecols=["date", "ticker", "comp_pct", "val_pct"])
    E = pd.read_csv(events_p, parse_dates=["date"], usecols=["date", "ticker", "setup"]) if events_p.exists() else pd.DataFrame()
    px = pd.read_csv(H.PRICES_FILE, index_col=0, parse_dates=True)
    a = pf["a"]
    buys = a[(a["kind"] == "Share Amount") & (a["eur"] < 0)].groupby(["sym", "Date"], as_index=False)["eur"].sum()
    buys["ticker"] = [yahoo_symbol(s, "Stock") for s in buys["sym"]]
    rows = []
    for _, b in buys.iterrows():
        t, d = b["ticker"], b["Date"]
        if not t or t not in px.columns:
            continue
        g = P[(P["ticker"] == t) & (P["date"] < d)]
        if g.empty or (d - g["date"].max()).days > 40:
            continue
        last = g.iloc[-1]
        i = px.index.searchsorted(d)
        fx = None
        if i + 60 < len(px) and pd.notna(px[t].iloc[i]):
            fx = (px[t].iloc[i + 60] / px[t].iloc[i] - 1) - (px[INDEX_TICKER_H].iloc[i + 60] / px[INDEX_TICKER_H].iloc[i] - 1)
        setup = None
        if len(E):
            e = E[(E["ticker"] == t) & (E["date"] <= d) & (E["date"] >= d - pd.Timedelta(days=5))]
            setup = e["setup"].iloc[-1] if len(e) else None
        rows.append({"ticker": t, "date": d.strftime("%Y-%m-%d"), "amount": float(-b["eur"]), "comp_pct": last["comp_pct"],
                     "val_pct": last["val_pct"], "setup": setup, "x60": fx})
    if not rows:
        return None
    T = pd.DataFrame(rows)
    def grp(mask, label):
        g = T[mask]
        v = g["x60"].dropna()
        return {"label": label, "n": int(len(g)), "amount": float(g["amount"].sum()), "share": float(g["amount"].sum() / T["amount"].sum()),
                "x60": float(v.mean()) if len(v) else None, "hit": float((v > 0).mean()) if len(v) else None}
    groups = [
        grp(T["comp_pct"] >= 0.6, "Score factoriel élevé (40 % les mieux notés)"),
        grp(T["comp_pct"] <= 0.4, "Score factoriel faible (40 % les moins bien notés)"),
        grp(T["val_pct"] >= 0.6, "Bon marché (rendement FCF élevé)"),
        grp(T["val_pct"] <= 0.4, "Cher (rendement FCF faible)"),
        grp(T["setup"].notna(), "Avec un signal technique dans les 5 jours"),
        grp(T["setup"].isna(), "Sans signal technique"),
    ]
    return {"n": int(len(T)), "covered_amount": float(T["amount"].sum()), "groups": groups,
            "buys": T.sort_values("date", ascending=False).head(60).to_dict("records"),
            "note": "Achats d'actions du Nasdaq 100 couverts par le laboratoire (les titres hors indice et les produits à levier ne le sont pas)."}


INDEX_TICKER_H = "^NDX"


# ───────────────────────── Liste de vérification avant achat ─────────────────────────
def pre_trade(ticker: str, amount: float, v: dict, stock: dict | None, op: dict | None, lab_row: dict | None) -> dict:
    """Contrôle un achat envisagé contre le moteur, le laboratoire et les garde-fous (rules.json)."""
    R = v["rules"]
    total, cash = v["total"], v["cash"]
    held = sum(p["value"] for p in v["positions"] if p["ticker"] == ticker or (ticker == "GOOGL" and p["ticker"] == "GOOG"))
    from .themes import THEMES
    theme = (stock or {}).get("theme") or EXTRA_THEMES.get(ticker)
    is_ai = isinstance(theme, str) and theme in THEMES and THEMES[theme]["ai"]
    new_line = held == 0
    w_after = (held + amount) / total if total else None
    ai_after = (v["ai_share"] * total + (amount if is_ai else 0)) / total if total else None
    size_max = (op or {}).get("position_pct")
    checks = []
    def c(label, ok, detail, blocking=True):
        checks.append({"label": label, "ok": ok, "detail": detail, "blocking": blocking})
    verdict = (op or {}).get("verdict")
    c("Verdict du moteur = « Entrée validée »", verdict == "Entrée validée", verdict or "titre hors Nasdaq 100 ou non analysé")
    flags = (op or {}).get("flags") or []
    c("Aucun drapeau rouge fondamental", not flags, "; ".join(flags) or "aucun")
    lp = (lab_row or {}).get("pct")
    c("Score factoriel hors des 40 % les plus faibles", lp is None or lp >= 0.4,
      f"rang {lp:.0%}" if lp is not None else "non disponible", blocking=lp is not None)
    vp = (lab_row or {}).get("val_fcf_pct")
    c("Valorisation pas chère (rendement FCF hors des 40 % les plus faibles)", vp is None or vp > 0.4,
      f"rang {vp:.0%}" if vp is not None else "non disponible", blocking=False)
    rr = (op or {}).get("rr")
    c("Rapport gain / risque ≥ 2", rr is not None and rr >= 2, f"{rr:.1f}" if rr is not None else "n.d.")
    ed = (stock or {}).get("earnings_date")
    days = (pd.Timestamp(ed) - pd.Timestamp.now().normalize()).days if ed else None
    c("Pas de résultats dans les 10 jours", days is None or not (0 <= days <= 10), f"résultats le {ed}" if ed else "date inconnue", blocking=False)
    c("Liquidités suffisantes", cash >= amount, f"{cash:,.0f} € disponibles pour {amount:,.0f} €".replace(",", " "))
    c(f"Poids de la ligne après achat ≤ {R['max_poids_ligne']:.0%}", w_after is not None and w_after <= R["max_poids_ligne"],
      f"{w_after:.1%}" if w_after is not None else "n.d.")
    if is_ai:
        c(f"Part chaîne IA après achat ≤ {R['max_poids_theme_ia']:.0%}", ai_after <= R["max_poids_theme_ia"], f"{ai_after:.0%}")
    if new_line:
        c(f"Nombre de lignes après achat ≤ {R['max_lignes']}", len(v["positions"]) + 1 <= R["max_lignes"], f"{len(v['positions']) + 1} lignes")
    if size_max is not None and total:
        c("Montant ≤ taille indicative du plan (1 % de risque, réduite en stress)", amount <= size_max * total,
          f"maximum ≈ {size_max * total:,.0f} €".replace(",", " "))
    day_pl = sum((p.get("r_1J") or 0) * p["value"] / (1 + (p.get("r_1J") or 0)) for p in v["positions"]) / total if total else 0
    c(f"Pas de pause imposée (séance du compte > {R['pause_apres_perte_jour']:.0%})", day_pl > R["pause_apres_perte_jour"], f"séance {day_pl:+.1%}")
    blocking_fail = [x for x in checks if not x["ok"] and x["blocking"]]
    return {"ticker": ticker, "amount": amount, "checks": checks, "go": not blocking_fail,
            "summary": "Tous les contrôles bloquants sont passés." if not blocking_fail else
                       f"{len(blocking_fail)} contrôle(s) bloquant(s) échoué(s) : " + ", ".join(x["label"] for x in blocking_fail)}
