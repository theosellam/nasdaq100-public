"""Collecte des données européennes (toutes gratuites et publiques).

- Euronext : liste des actions cotées à Paris (marché réglementé + Euronext Growth), avec ISIN et mnémonique.
- filings.xbrl.org : rapports annuels ESEF (format XBRL obligatoire depuis 2021 sur le marché réglementé).
- GLEIF : correspondance LEI (identifiant de l'émetteur dans ESEF) → ISIN (identifiant de l'action chez Euronext).
- Yahoo Finance : cours, volumes et capitalisations (mnémonique Euronext + « .PA »).

Point-in-time : un rapport annuel n'est réputé connu qu'au 30 avril suivant la clôture (délai légal de 4 mois),
jamais à sa date réelle d'indexation, pour ne jamais utiliser une information avant qu'elle soit publique.
"""
from __future__ import annotations

import csv
import io
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .. import data as D

EU_DIR = D.DATA_DIR / "eu"
ESEF_DIR = EU_DIR / "esef"
LISTING_FILE = EU_DIR / "euronext.csv"
FILINGS_FILE = EU_DIR / "filings.csv"
LEI_FILE = EU_DIR / "lei_isin.json"
FUND_FILE = EU_DIR / "fundamentals.csv"
PRICES_FILE = EU_DIR / "prices.csv"
VOLUMES_FILE = EU_DIR / "volumes.csv"
CAPS_FILE = EU_DIR / "market_caps.csv"
BUILD_FILE = EU_DIR / "build.json"
UA = "Mozilla/5.0 (outil de recherche personnel)"
MARKETS = {"Euronext Paris": "Paris", "Euronext Growth Paris": "Growth"}


def _get(url: str, data: bytes | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ───────────────────────── Univers Euronext ─────────────────────────
def fetch_listing() -> pd.DataFrame:
    """Actions cotées à Paris (marché réglementé et Euronext Growth), en euros."""
    body = urllib.parse.urlencode({"args[fe_type]": "csv", "args[fe_decimal_separator]": ".",
                                   "args[fe_date_format]": "d/m/Y"}).encode()
    raw = _get("https://live.euronext.com/pd_es/data/stocks/download?mics=XPAR%2CALXP", body).decode("utf-8-sig")
    rows = [r for r in csv.reader(io.StringIO(raw), delimiter=";") if len(r) >= 5 and r[1][:2].isalpha() and len(r[1]) == 12]
    df = pd.DataFrame([{"name": r[0], "isin": r[1], "symbol": r[2], "market": MARKETS.get(r[3]), "currency": r[4]} for r in rows])
    df = df[df["market"].notna() & (df["currency"] == "EUR")].drop_duplicates("isin")
    df["ticker"] = df["symbol"].str.strip() + ".PA"
    if len(df) < 300:
        raise RuntimeError(f"liste Euronext incomplète ({len(df)} titres)")
    return df.reset_index(drop=True)


# ───────────────────────── Rapports ESEF ─────────────────────────
def fetch_filings_index(country: str = "FR") -> pd.DataFrame:
    rows, page = [], 1
    while True:
        q = f"https://filings.xbrl.org/api/filings?filter%5Bcountry%5D={country}&page%5Bsize%5D=500&page%5Bnumber%5D={page}&include=entity"
        d = json.loads(_get(q, timeout=120))
        ents = {e["id"]: e["attributes"] for e in d.get("included", [])}
        for x in d["data"]:
            a = x["attributes"]
            e = ents.get(x["relationships"]["entity"]["data"]["id"], {})
            if a.get("json_url"):
                rows.append({"lei": e.get("identifier"), "entity": e.get("name"), "period_end": a["period_end"],
                             "json_url": a["json_url"], "errors": a.get("error_count") or 0})
        if len(d["data"]) < 500:
            break
        page += 1
        time.sleep(0.5)
    f = pd.DataFrame(rows).dropna(subset=["lei"])
    # plusieurs versions d'un même exercice : on garde celle avec le moins d'erreurs
    return f.sort_values(["lei", "period_end", "errors"]).drop_duplicates(["lei", "period_end"]).reset_index(drop=True)


def map_lei_isin(leis: list[str], listed_isins: set[str]) -> dict[str, str]:
    """LEI → ISIN de l'action cotée (intersection avec la liste Euronext). Cache persistant."""
    cache = json.loads(LEI_FILE.read_text()) if LEI_FILE.exists() else {}

    def one(lei: str):
        try:
            d = json.loads(_get(f"https://api.gleif.org/api/v1/lei-records/{lei}/isins?page%5Bsize%5D=200", timeout=30))
            return lei, [x["attributes"]["isin"] for x in d.get("data", [])]
        except Exception:
            return lei, None
    todo = [l for l in leis if l not in cache]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for lei, isins in pool.map(one, todo):
            if isins is not None:
                cache[lei] = isins
    LEI_FILE.write_text(json.dumps(cache))
    out = {}
    for lei in leis:
        hit = [i for i in cache.get(lei, []) if i in listed_isins]
        if hit:
            out[lei] = hit[0]
    return out


# Concepts IFRS : premier disponible (les sociétés n'utilisent pas toutes les mêmes)
FLOW = {
    "revenue": ["ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"],
    "op_income": ["ifrs-full:ProfitLossFromOperatingActivities"],
    "net_income": ["ifrs-full:ProfitLossAttributableToOwnersOfParent", "ifrs-full:ProfitLoss"],
    "cfo": ["ifrs-full:CashFlowsFromUsedInOperatingActivities"],
    "capex_ppe": ["ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                  "ifrs-full:PurchaseOfPropertyPlantAndEquipment",
                  "ifrs-full:PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets"],
    "capex_int": ["ifrs-full:PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", "ifrs-full:PurchaseOfIntangibleAssets"],
    "dividends": ["ifrs-full:DividendsPaidClassifiedAsFinancingActivities", "ifrs-full:DividendsPaid",
                  "ifrs-full:DividendsPaidToEquityHoldersOfParentClassifiedAsFinancingActivities"],
    "buyback": ["ifrs-full:PaymentsToAcquireOrRedeemEntitysShares", "ifrs-full:PurchaseOfTreasuryShares"],
    "eps": ["ifrs-full:BasicEarningsLossPerShare"],
}
STOCK = {
    "cash": ["ifrs-full:CashAndCashEquivalents"],
    "equity": ["ifrs-full:EquityAttributableToOwnersOfParent", "ifrs-full:Equity"],
    "assets": ["ifrs-full:Assets"],
    "debt_nc": ["ifrs-full:NoncurrentBorrowings", "ifrs-full:LongtermBorrowings", "ifrs-full:NoncurrentPortionOfNoncurrentBorrowings"],
    "debt_c": ["ifrs-full:CurrentBorrowings", "ifrs-full:ShorttermBorrowings", "ifrs-full:CurrentPortionOfNoncurrentBorrowings"],
    "debt": ["ifrs-full:Borrowings"],
}


def _end(period: str) -> pd.Timestamp:
    """Fin de période XBRL (convention « minuit du lendemain ») → date de clôture."""
    e = period.split("/")[-1]
    ts = pd.Timestamp(e[:10])
    return ts - pd.Timedelta(days=1) if "T00:00:00" in e else ts


def extract(facts: dict) -> dict[str, dict]:
    """Valeurs consolidées (sans dimension) par date de clôture : {AAAA-MM-JJ: {champ: valeur}}."""
    by: dict[str, dict[str, float]] = {}
    want = {c: (k, "flow") for k, cs in FLOW.items() for c in cs}
    want.update({c: (k, "stock") for k, cs in STOCK.items() for c in cs})
    prio = {c: i for cs in list(FLOW.values()) + list(STOCK.values()) for i, c in enumerate(cs)}
    best: dict[tuple, tuple[int, float]] = {}
    for f in facts.values():
        dims = f.get("dimensions", {})
        c = dims.get("concept")
        if c not in want or set(dims) - {"concept", "entity", "period", "unit", "language"}:
            continue
        unit = dims.get("unit", "")
        key, kind = want[c]
        if key != "eps" and "EUR" not in unit:
            continue
        p = dims.get("period", "")
        if kind == "flow":
            if "/" not in p:
                continue
            a, b = p.split("/")
            days = (pd.Timestamp(b[:10]) - pd.Timestamp(a[:10])).days
            if not 350 <= days <= 380:          # exercices de 12 mois seulement
                continue
        elif "/" in p:
            continue
        try:
            v = float(f["value"])
        except (TypeError, ValueError):
            continue
        d = f"{_end(p):%Y-%m-%d}"
        k = (d, key)
        if k not in best or prio[c] < best[k][0]:
            best[k] = (prio[c], v)
    for (d, key), (_, v) in best.items():
        by.setdefault(d, {})[key] = v
    return by


def fetch_fundamentals(filings: pd.DataFrame, lei_isin: dict, log=print) -> pd.DataFrame:
    """Télécharge (avec cache) les rapports ESEF des émetteurs cotés et assemble une ligne par exercice."""
    ESEF_DIR.mkdir(parents=True, exist_ok=True)
    f = filings[filings["lei"].isin(lei_isin)]

    def one(row):
        cache = ESEF_DIR / f"{row.lei}_{row.period_end}.json"
        if cache.exists():
            return row.lei, row.period_end, json.loads(cache.read_text())
        try:
            j = json.loads(_get("https://filings.xbrl.org" + row.json_url, timeout=120))
            ex = extract(j.get("facts", {}))
            cache.write_text(json.dumps(ex))
            return row.lei, row.period_end, ex
        except Exception as e:
            return row.lei, row.period_end, {"_error": str(e)[:200]}
    rows = []
    n_err = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for lei, pe, ex in pool.map(one, f.itertuples()):
            if "_error" in ex:
                n_err += 1
                continue
            for d, vals in ex.items():
                rows.append({"lei": lei, "isin": lei_isin[lei], "fy_end": d, "report_end": pe, **vals})
    if n_err:
        log(f"  ⚠ {n_err} rapports ESEF illisibles ou indisponibles")
    F = pd.DataFrame(rows)
    if F.empty:
        return F
    # un exercice apparaît dans deux rapports (année N et comparatif N-1) : on garde la version la plus récente
    # un exercice apparaît dans plusieurs rapports (année N, comparatif N-1, capitaux propres d'ouverture N-2) :
    # pour chaque champ, la valeur la plus récente disponible ; on écarte les dates sans compte de résultat
    F = F.sort_values(["isin", "fy_end", "report_end"]).groupby(["isin", "fy_end"], as_index=False).last()
    F = F[F[[c for c in ("revenue", "net_income", "cfo") if c in F]].notna().any(axis=1)]
    F["fy_end"] = pd.to_datetime(F["fy_end"])
    # date de disponibilité point-in-time : 4 mois après la clôture (délai légal), fin de mois
    F["available"] = (F["fy_end"] + pd.DateOffset(months=4)).dt.to_period("M").dt.to_timestamp("M")
    return F.reset_index(drop=True)


# ───────────────────────── Cours et liquidité (Yahoo) ─────────────────────────
def fetch_prices(tickers: list[str], period: str = "7y") -> tuple[pd.DataFrame, pd.DataFrame]:
    closes, vols = [], []
    for i in range(0, len(tickers), 150):          # par paquets : Yahoo limite les très gros téléchargements
        chunk = tickers[i:i + 150]
        df = D.download(chunk, period=period, interval="1d", auto_adjust=True, progress=False,
                        group_by="column", threads=D.YF_THREADS)
        if df is None or df.empty:
            continue
        c, v = df["Close"], df["Volume"]
        if isinstance(c, pd.Series):
            c, v = c.to_frame(chunk[0]), v.to_frame(chunk[0])
        closes.append(c)
        vols.append(v)
    close = pd.concat(closes, axis=1) if closes else pd.DataFrame()
    vol = pd.concat(vols, axis=1) if vols else pd.DataFrame()
    close = close.loc[:, close.notna().sum() > 20]
    return close.sort_index(), vol.reindex(columns=close.columns).sort_index()


def refresh_prices(log=print) -> None:
    """Mise à jour quotidienne légère : dernier mois de cours, fusionné avec l'historique."""
    close = pd.read_csv(PRICES_FILE, index_col=0, parse_dates=True)
    vol = pd.read_csv(VOLUMES_FILE, index_col=0, parse_dates=True)
    c, v = fetch_prices(list(close.columns), period="1mo")
    if c.shape[1] < 0.5 * close.shape[1]:
        raise RuntimeError(f"cours récents incomplets ({c.shape[1]}/{close.shape[1]} titres)")
    close = c.combine_first(close)
    vol = v.combine_first(vol)
    close.to_csv(PRICES_FILE)
    vol.to_csv(VOLUMES_FILE)
    log(f"  cours européens à jour au {close.index[-1]:%d/%m/%Y}")


def prices_age_hours() -> float:
    return (time.time() - PRICES_FILE.stat().st_mtime) / 3600 if PRICES_FILE.exists() else 1e9


def build(log=print) -> dict:
    """Mise à jour complète (≈ 45 à 75 min la première fois : rapports ESEF ; quelques minutes ensuite grâce aux caches)."""
    t0 = time.time()
    EU_DIR.mkdir(parents=True, exist_ok=True)
    log("→ Liste des actions Euronext Paris et Euronext Growth…")
    try:
        L = fetch_listing()
        L.to_csv(LISTING_FILE, index=False)
    except Exception as e:
        if not LISTING_FILE.exists():
            raise
        log(f"  ⚠ liste Euronext indisponible ({e}) : copie locale utilisée")
        L = pd.read_csv(LISTING_FILE)
    log(f"  {len(L)} actions ({(L['market'] == 'Paris').sum()} marché réglementé, {(L['market'] == 'Growth').sum()} Growth)")
    log("→ Index des rapports annuels ESEF (filings.xbrl.org)…")
    try:
        Fi = fetch_filings_index()
        Fi.to_csv(FILINGS_FILE, index=False)
    except Exception as e:
        if not FILINGS_FILE.exists():
            raise
        log(f"  ⚠ index ESEF indisponible ({e}) : copie locale utilisée")
        Fi = pd.read_csv(FILINGS_FILE)
    log(f"  {Fi['lei'].nunique()} émetteurs, {len(Fi)} rapports")
    log("→ Correspondance émetteur → action (GLEIF)…")
    li = map_lei_isin(sorted(Fi["lei"].unique()), set(L["isin"]))
    log(f"  {len(li)} émetteurs ESEF reliés à une action cotée")
    log("→ Comptes annuels ESEF (avec cache)…")
    F = fetch_fundamentals(Fi, li, log)
    F.to_csv(FUND_FILE, index=False)
    log(f"  {len(F)} exercices pour {F['isin'].nunique() if len(F) else 0} sociétés")
    log("→ Cours et volumes quotidiens (Yahoo, 7 ans)…")
    close, vol = fetch_prices(L["ticker"].tolist())
    close.to_csv(PRICES_FILE)
    vol.to_csv(VOLUMES_FILE)
    log(f"  {close.shape[1]} titres avec des cours")
    log("→ Capitalisations boursières…")
    caps = D.fetch_market_caps(list(close.columns))
    caps.to_csv(CAPS_FILE)
    info = {"built": pd.Timestamp.now().isoformat(timespec="seconds"), "listed": int(len(L)),
            "esef_issuers": int(Fi["lei"].nunique()), "linked": len(li), "fund_rows": int(len(F)),
            "priced": int(close.shape[1]), "seconds": round(time.time() - t0)}
    BUILD_FILE.write_text(json.dumps(info))
    log(f"✓ Données européennes prêtes en {info['seconds']} s")
    return info


def load() -> dict | None:
    if not (LISTING_FILE.exists() and PRICES_FILE.exists()):
        return None
    L = pd.read_csv(LISTING_FILE)
    F = pd.read_csv(FUND_FILE, parse_dates=["fy_end", "available"]) if FUND_FILE.exists() else pd.DataFrame()
    close = pd.read_csv(PRICES_FILE, index_col=0, parse_dates=True)
    vol = pd.read_csv(VOLUMES_FILE, index_col=0, parse_dates=True)
    caps = pd.read_csv(CAPS_FILE, index_col=0).iloc[:, 0] if CAPS_FILE.exists() else pd.Series(dtype=float)
    info = json.loads(BUILD_FILE.read_text()) if BUILD_FILE.exists() else {}
    return {"listing": L, "fund": F, "close": close, "volume": vol, "caps": caps, "info": info}


def age_days() -> float:
    return (time.time() - BUILD_FILE.stat().st_mtime) / 86400 if BUILD_FILE.exists() else 1e9
