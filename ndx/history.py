"""Données historiques « point-in-time » pour tester les signaux sans biais du futur.

- Composition historique du Nasdaq 100 : versions trimestrielles des pages Wikipedia (depuis fin 2019).
- Cours quotidiens sur 8 ans (membres actuels + anciens membres encore cotés), indice, taux 10 ans.
- Comptes trimestriels SEC EDGAR (XBRL) avec leur DATE DE PUBLICATION : on ne sait qu'une fois le rapport déposé.
- Historique daté des changements d'avis et d'objectifs des analystes ; résultats vs attentes (surprises de BPA).

Tout est stocké dans data/history/ (données publiques, exclues de git car volumineuses).
"""
from __future__ import annotations

import gzip
import io
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .data import DATA_DIR, INDEX_TICKER, download

HIST_DIR = DATA_DIR / "history"
SEC_DIR = HIST_DIR / "sec"
MEMBERSHIP_FILE = HIST_DIR / "membership.csv"
PRICES_FILE = HIST_DIR / "prices.csv"
MACRO_FILE = HIST_DIR / "macro.csv"
ANALYST_FILE = HIST_DIR / "analyst_actions.csv"
EARNINGS_FILE = HIST_DIR / "earnings.csv"
OHLCV_FILE = HIST_DIR / "ohlcv.pkl"          # ouverture, plus haut, plus bas, clôture, volume (ajustés) — pour le price action
FIELDS = ("Open", "High", "Low", "Close", "Volume")
UA = "ndx-analyse/1.0 (outil de recherche personnel)"   # nom de client générique (pas de donnée personnelle)
START = "2019-10-01"

# Changements de symbole connus (ancien → actuel chez Yahoo)
RENAMES = {"FB": "META", "FISV": "FI", "WLTW": "WTW", "BBBY": None, "TWTR": None, "ATVI": None, "XLNX": None,
           "CERN": None, "MXIM": None, "ALXN": None, "CTXS": None, "NLOK": "GEN", "SPLK": None, "SGEN": None,
           "ANSS": None, "SIRI": "SIRI", "DISCA": "WBD", "DISCK": None, "VIAC": "PARA", "CHKP": "CHKP"}


def _get(url: str, timeout: int = 40) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
        return gzip.decompress(b) if r.headers.get("Content-Encoding") == "gzip" else b


def _yahoo(t: str) -> str | None:
    t = str(t).strip().upper().replace(".", "-")
    if t in RENAMES:
        return RENAMES[t]
    return t


# ───────────────────────── Composition historique ─────────────────────────
def _wiki_members(title: str, when: str) -> list[str] | None:
    q = urllib.parse.urlencode({"action": "query", "prop": "revisions", "titles": title, "rvlimit": 1, "rvstart": when,
                                "rvdir": "older", "rvprop": "ids", "format": "json", "redirects": 1})
    pages = json.loads(_get("https://en.wikipedia.org/w/api.php?" + q))["query"]["pages"]
    revs = list(pages.values())[0].get("revisions")
    if not revs:
        return None
    html = _get(f"https://en.wikipedia.org/w/index.php?oldid={revs[0]['revid']}").decode("utf-8", "ignore")
    try:
        tabs = pd.read_html(io.StringIO(html), flavor="lxml")
    except Exception:
        return None
    for t in tabs:
        cols = {str(c).split("[")[0].strip(): c for c in t.columns}
        col = cols.get("Ticker") or cols.get("Symbol") or cols.get("Ticker symbol")
        if col is not None and 90 <= len(t) <= 110:
            return [str(x) for x in t[col].dropna()]
    return None


def build_membership() -> pd.DataFrame:
    dates = pd.date_range(START, pd.Timestamp.today(), freq="QS")
    rows = []
    for d in dates:
        when = d.strftime("%Y-%m-%dT00:00:00Z")
        members = None
        for attempt in range(4):
            for title in ("Nasdaq-100", "List of NASDAQ-100 companies"):
                try:
                    members = _wiki_members(title, when)
                except Exception:
                    members = None
                if members:
                    break
            if members:
                break
            time.sleep(3 * (attempt + 1))          # Wikipedia limite les requêtes rapprochées
        if members:
            rows += [{"date": d.strftime("%Y-%m-%d"), "wiki": m, "ticker": _yahoo(m)} for m in members]
        time.sleep(1.0)
    df = pd.DataFrame(rows)
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(MEMBERSHIP_FILE, index=False)
    return df


def load_membership() -> pd.DataFrame:
    return pd.read_csv(MEMBERSHIP_FILE, parse_dates=["date"]) if MEMBERSHIP_FILE.exists() else pd.DataFrame()


def universe(current: list[str]) -> list[str]:
    m = load_membership()
    hist = set(m["ticker"].dropna()) if len(m) else set()
    return sorted((hist | set(current)) - {"GOOG"})


# ───────────────────────── Cours longs ─────────────────────────
def fetch_prices(tickers: list[str]) -> pd.DataFrame:
    import yfinance as yf
    raw = download(tickers + [INDEX_TICKER], start="2018-06-01", auto_adjust=True, progress=False, threads=True)
    close = raw["Close"].dropna(how="all")
    close.index = pd.to_datetime(close.index).tz_localize(None)
    missing = [t for t in tickers if t not in close.columns or close[t].notna().sum() <= 60]
    if missing:                                   # seconde tentative (erreurs passagères de Yahoo)
        time.sleep(3)
        r2 = download(missing, start="2018-06-01", auto_adjust=True, progress=False, threads=False)["Close"]
        r2 = r2.to_frame(missing[0]) if isinstance(r2, pd.Series) else r2
        r2.index = pd.to_datetime(r2.index).tz_localize(None)
        close = close.drop(columns=[c for c in missing if c in close.columns]).join(r2, how="outer")
    close = close.loc[:, close.notna().sum() > 60]
    close.to_csv(PRICES_FILE)
    macro = download(["^TNX", "^VIX"], start="2018-06-01", auto_adjust=True, progress=False)["Close"]
    macro.index = pd.to_datetime(macro.index).tz_localize(None)
    macro.to_csv(MACRO_FILE)
    return close


# ───────────────────────── Bougies quotidiennes (OHLCV) ─────────────────────────
def _ohlcv_frames(raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for f in FIELDS:
        df = raw[f]
        df = df.to_frame() if isinstance(df, pd.Series) else df
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[f] = df
    return out


def fetch_ohlcv(tickers: list[str], start: str = "2018-06-01") -> dict[str, pd.DataFrame]:
    """Bougies quotidiennes ajustées (un seul téléchargement groupé, puis une reprise des manquants)."""
    import yfinance as yf
    syms = list(dict.fromkeys(tickers + [INDEX_TICKER]))
    o = _ohlcv_frames(download(syms, start=start, auto_adjust=True, progress=False, threads=True, group_by="column"))
    missing = [t for t in syms if t not in o["Close"] or o["Close"][t].notna().sum() <= 60]
    if missing:
        time.sleep(3)
        r2 = _ohlcv_frames(download(missing, start=start, auto_adjust=True, progress=False, threads=False, group_by="column"))
        if len(missing) == 1:
            r2 = {f: d.set_axis(missing, axis=1) for f, d in r2.items()}
        o = {f: o[f].drop(columns=[c for c in missing if c in o[f]]).join(r2[f], how="outer") for f in FIELDS}
    keep = o["Close"].columns[o["Close"].notna().sum() > 60]
    o = {f: d.reindex(columns=keep).dropna(how="all") for f, d in o.items()}
    pd.to_pickle(o, OHLCV_FILE)
    return o


def load_ohlcv() -> dict[str, pd.DataFrame] | None:
    return pd.read_pickle(OHLCV_FILE) if OHLCV_FILE.exists() else None


def update_ohlcv(extra: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Mise à jour rapide (dernier mois) des bougies ; télécharge tout si le fichier n'existe pas."""
    import yfinance as yf
    o = load_ohlcv()
    if o is None:
        return fetch_ohlcv(sorted(set(extra or [])))
    syms = list(dict.fromkeys(list(o["Close"].columns) + (extra or [])))
    new = [t for t in syms if t not in o["Close"].columns]
    if new:                                     # nouveaux membres : historique complet
        n = fetch_ohlcv_some(new)
        o = {f: o[f].join(n[f], how="outer") for f in FIELDS}
    r = _ohlcv_frames(download(syms, period="1mo", auto_adjust=True, progress=False, threads=True, group_by="column"))
    o = {f: r[f].reindex(columns=o[f].columns).combine_first(o[f]) for f in FIELDS}
    pd.to_pickle(o, OHLCV_FILE)
    return o


def fetch_ohlcv_some(tickers: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    r = _ohlcv_frames(download(tickers, start="2018-06-01", auto_adjust=True, progress=False, threads=False, group_by="column"))
    if len(tickers) == 1:
        r = {f: d.set_axis(tickers, axis=1) for f, d in r.items()}
    return r


# ───────────────────────── SEC EDGAR ─────────────────────────
FLOWS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "gross_profit": ["GrossProfit"],
    "op_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
}
INSTANTS = {
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "st_inv": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "ConvertibleNotesPayable"],
    "debt_cur": ["LongTermDebtCurrent", "DebtCurrent"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "deferred": ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"],
}


def _dedupe(facts: list[dict], instant: bool) -> list[dict]:
    """Une valeur par période : celle de la PREMIÈRE publication (les rapports suivants la répètent en comparatif)."""
    best: dict = {}
    for f in facts:
        if f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"):
            continue
        key = f["end"] if instant else (f.get("start"), f["end"])
        if key not in best or f["filed"] < best[key]["filed"]:
            best[key] = f
    return list(best.values())


def _quarterize(facts: list[dict]) -> list[list]:
    """Transforme des montants cumulés depuis le début d'exercice (3, 6, 9, 12 mois) en trimestres distincts."""
    facts = _dedupe([f for f in facts if f.get("start")], instant=False)
    q: dict = {}
    for f in facts:
        dur = (pd.Timestamp(f["end"]) - pd.Timestamp(f["start"])).days
        if 80 <= dur <= 100:
            q[f["end"]] = (f["val"], f["filed"])
    by_start: dict = {}
    for f in facts:
        by_start.setdefault(f["start"], []).append(f)
    for start, fs in by_start.items():
        fs = sorted(fs, key=lambda x: x["end"])
        for a, b in zip(fs, fs[1:]):
            gap = (pd.Timestamp(b["end"]) - pd.Timestamp(a["end"])).days
            if 80 <= gap <= 100 and b["end"] not in q:
                q[b["end"]] = (b["val"] - a["val"], b["filed"])
    return sorted([[e, v, fl] for e, (v, fl) in q.items()])


def _pick(gaap: dict, names: list[str], unit: str = "USD") -> list[dict]:
    """Concaténation des balises équivalentes (les sociétés changent parfois de balise au fil des ans)."""
    out = []
    for n in names:
        if n in gaap and unit in gaap[n]["units"]:
            out += gaap[n]["units"][unit]
    return out


def sec_extract(facts: dict) -> dict:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    out = {"flows": {}, "instants": {}}
    for k, names in FLOWS.items():
        for n in names:            # première balise qui donne des trimestres, puis complément par les suivantes
            q = _quarterize(_pick(gaap, [n]))
            if q:
                have = {e for e, _, _ in out["flows"].get(k, [])}
                out["flows"][k] = sorted(out["flows"].get(k, []) + [x for x in q if x[0] not in have])
    for k, names in INSTANTS.items():
        vals: dict = {}
        for n in names:
            for f in _dedupe(_pick(gaap, [n]), instant=True):
                if f["end"] not in vals:
                    vals[f["end"]] = (f["val"], f["filed"])
        out["instants"][k] = sorted([[e, v, fl] for e, (v, fl) in vals.items()])
    sh: dict = {}
    for f in dei.get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", []):
        key = (f["end"], f.get("accn"))
        sh.setdefault(key, [0, f["filed"]])
        sh[key][0] += f["val"]                       # plusieurs classes d'actions : on additionne
    first: dict = {}
    for (end, _), (v, fl) in sh.items():
        if end not in first or fl < first[end][1]:
            first[end] = (v, fl)
    out["instants"]["shares"] = sorted([[e, v, fl] for e, (v, fl) in first.items()])
    return out


def fetch_sec(tickers: list[str]) -> dict:
    SEC_DIR.mkdir(parents=True, exist_ok=True)
    cik = {v["ticker"].replace(".", "-"): v["cik_str"] for v in json.loads(_get("https://www.sec.gov/files/company_tickers.json")).values()}
    ok, missing = [], []

    def one(t):
        c = cik.get(t)
        if not c:
            return t, False
        for attempt in range(3):
            try:
                facts = json.loads(_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c:010d}.json", timeout=60))
                ex = sec_extract(facts)
                if not ex["flows"].get("revenue"):
                    return t, False
                (SEC_DIR / f"{t}.json").write_text(json.dumps(ex), encoding="utf-8")
                return t, True
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return t, False

    with ThreadPoolExecutor(max_workers=4) as pool:   # la SEC limite à 10 requêtes/seconde
        for t, good in pool.map(one, tickers):
            (ok if good else missing).append(t)
    return {"ok": ok, "missing": missing}


def load_sec(t: str) -> dict | None:
    p = SEC_DIR / f"{t}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ───────────────────────── Analystes et résultats (Yahoo) ─────────────────────────
def fetch_analyst_and_earnings(tickers: list[str]) -> None:
    import yfinance as yf

    def one(t):
        tk = yf.Ticker(t)
        a = e = None
        try:
            ud = tk.upgrades_downgrades
            if ud is not None and len(ud):
                a = ud.reset_index().rename(columns={"GradeDate": "date"})
                a["ticker"] = t
        except Exception:
            pass
        try:
            ed = tk.get_earnings_dates(limit=40)
            if ed is not None and len(ed):
                e = ed.reset_index()
                e.columns = ["date", "eps_est", "eps_act", "surprise"] + list(e.columns[4:])
                e["ticker"] = t
        except Exception:
            pass
        return a, e

    with ThreadPoolExecutor(max_workers=6) as pool:
        res = list(pool.map(one, tickers))
    A = pd.concat([a for a, _ in res if a is not None], ignore_index=True) if any(a is not None for a, _ in res) else pd.DataFrame()
    E = pd.concat([e for _, e in res if e is not None], ignore_index=True) if any(e is not None for _, e in res) else pd.DataFrame()
    if len(A):
        A["date"] = pd.to_datetime(A["date"], utc=True).dt.tz_localize(None)
        A[["date", "ticker", "Firm", "ToGrade", "FromGrade", "Action", "priceTargetAction", "currentPriceTarget", "priorPriceTarget"]
          ].to_csv(ANALYST_FILE, index=False)
    if len(E):
        E["date"] = pd.to_datetime(E["date"], utc=True).dt.tz_localize(None)
        E[["date", "ticker", "eps_est", "eps_act", "surprise"]].to_csv(EARNINGS_FILE, index=False)


def build_all(current: list[str], log=print) -> dict:
    t0 = time.time()
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    log("→ Composition historique du Nasdaq 100 (Wikipedia, trimestrielle)…")
    m = build_membership()
    log(f"  {m['date'].nunique()} relevés, {m['wiki'].nunique()} sociétés différentes")
    uni = universe(current)
    log(f"→ Cours quotidiens depuis 2018 pour {len(uni)} titres…")
    close = fetch_prices(uni)
    priced = [t for t in uni if t in close.columns]
    log(f"  {len(priced)} titres avec des cours ({len(uni) - len(priced)} radiés introuvables chez Yahoo)")
    log("→ Bougies quotidiennes (ouverture, plus haut, plus bas, volume)…")
    fetch_ohlcv(priced)
    log("→ Comptes trimestriels SEC EDGAR (≈ 2 min)…")
    sec = fetch_sec(priced)
    log(f"  {len(sec['ok'])} sociétés couvertes ; sans comptes US GAAP : {', '.join(sec['missing'][:20])}")
    log("→ Historique des analystes et des résultats (Yahoo)…")
    fetch_analyst_and_earnings(priced)
    info = {"built": pd.Timestamp.now().isoformat(timespec="seconds"), "universe": len(uni), "priced": len(priced),
            "sec_ok": len(sec["ok"]), "sec_missing": sec["missing"], "seconds": round(time.time() - t0)}
    (HIST_DIR / "build.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"✓ Historique prêt en {info['seconds']} s")
    return info


def update_recent_prices() -> pd.DataFrame:
    """Mise à jour rapide (dernier mois) des cours longs, avant de reconstruire le laboratoire chaque jour."""
    import yfinance as yf
    close = pd.read_csv(PRICES_FILE, index_col=0, parse_dates=True)
    raw = download(list(close.columns), period="1mo", auto_adjust=True, progress=False, threads=True)["Close"]
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    close = raw.reindex(columns=close.columns).combine_first(close)
    close.to_csv(PRICES_FILE)
    m = download(["^TNX", "^VIX"], period="1mo", auto_adjust=True, progress=False)["Close"]
    m.index = pd.to_datetime(m.index).tz_localize(None)
    macro = pd.read_csv(MACRO_FILE, index_col=0, parse_dates=True)
    m.combine_first(macro).to_csv(MACRO_FILE)
    return close


def age_days(path: Path) -> float:
    return (time.time() - path.stat().st_mtime) / 86400 if path.exists() else float("inf")
