"""Collecte des données Nasdaq 100 : composition, cours (historique + temps réel), analystes."""
from __future__ import annotations

import io
import os
import sys
import json
import threading
import time
import warnings
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

# yfinance utilise encore des options que pandas 3.1 annonce comme obsolètes : avertissements sans conséquence, masqués.
warnings.filterwarnings("ignore", message=".*inplace keyword.*")
warnings.filterwarnings("ignore", category=FutureWarning)

def _data_dir() -> Path:
    """Dossier des données. Version « application » (empaquetée) : dossier de l'utilisateur, car le paquet est en
    lecture seule ; version « code source » : dossier data/ du projet. NDX_DATA_DIR permet de forcer un dossier."""
    if os.environ.get("NDX_DATA_DIR"):
        return Path(os.environ["NDX_DATA_DIR"]).expanduser()
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent / "data"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Nasdaq100"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home()) / "Nasdaq100"
    else:
        base = Path.home() / ".nasdaq100"
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    seed = Path(getattr(sys, "_MEIPASS", "")) / "data" / "events.json"      # fichier modifiable livré avec l'application
    if seed.exists() and not (d / "events.json").exists():
        (d / "events.json").write_bytes(seed.read_bytes())
    return d


DATA_DIR = _data_dir()

# macOS limite à 256 les fichiers ouverts par un programme lancé depuis le Terminal ; yfinance garde ouverte une
# connexion à son cache par fil de téléchargement, d'où « unable to open database file » puis des erreurs réseau
# (« getaddrinfo() thread failed to start »). On relève la limite (sans effet sous Windows).
try:
    import resource
    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    _target = 10240 if _hard == resource.RLIM_INFINITY else min(_hard, 10240)
    if _soft < _target:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_target, _hard))
except Exception:
    pass


class _MemTzCache:
    """Cache des fuseaux horaires des titres en mémoire. Celui de yfinance (SQLite) ouvre une connexion par fil
    de téléchargement sans la refermer : quelques centaines de fichiers ouverts, puis plus rien ne fonctionne."""
    def __init__(self):
        self._d, self._lock = {}, threading.Lock()

    def lookup(self, tkr):
        return self._d.get(tkr)

    def store(self, tkr, tz):
        with self._lock:
            if tz is None:
                self._d.pop(tkr, None)
            else:
                self._d[tkr] = tz

    @property
    def tz_db(self):
        return None


try:
    import yfinance.cache as _yfc
    _yfc._TzCacheManager._tz_cache = _MemTzCache()
except Exception:
    pass
YF_THREADS = 8          # fils de téléchargement simultanés (yfinance en ouvre sinon jusqu'à 2 × nb de cœurs)
# Cache de yfinance (fuseaux horaires, cookie Yahoo) propre à cette copie du programme : deux copies lancées en même
# temps se bloquaient sur le cache commun du Mac (« OperationalError: unable to open database file »).
try:
    (DATA_DIR / ".yf_cache").mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(DATA_DIR / ".yf_cache"))
except Exception:
    pass
INDEX_TICKER = "^NDX"
CONSTITUENTS_URL = "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies"

CONSTITUENTS_FILE = DATA_DIR / "constituents.csv"
PRICES_FILE = DATA_DIR / "prices.csv"
VOLUMES_FILE = DATA_DIR / "volumes.csv"
CAPS_FILE = DATA_DIR / "market_caps.csv"
ANALYSTS_FILE = DATA_DIR / "analysts.json"
FUNDAMENTALS_FILE = DATA_DIR / "fundamentals.json"
CONTEXT_FILE = DATA_DIR / "context.csv"
EVENTS_FILE = DATA_DIR / "events.json"

# Marqueurs de contexte (hors Nasdaq 100, jamais mélangés aux calculs d'ampleur) :
# volatilité Nasdaq (VXN) et S&P (VIX), taux 10 ans, semi-conducteurs, S&P 500, Anthropic (IPO du 19/11/2026).
CONTEXT_TICKERS = {"^VXN": "VXN (volatilité Nasdaq 100)", "^VIX": "VIX", "^TNX": "Taux US 10 ans",
                   "^SOX": "Indice semi-conducteurs", "^GSPC": "S&P 500", "ANT": "Anthropic (ANT)"}


_YF_LOCK = threading.RLock()


def download(tickers, *args, **kwargs) -> pd.DataFrame:
    """yf.download sérialisé : yfinance partage un état global entre ses téléchargements, et deux appels
    simultanés (rafraîchissement, tâches de fond, fiche d'un titre) font échouer tout le lot
    (« 'NoneType' object is not subscriptable »). Les titres sans aucun cours après un téléchargement groupé
    sont redemandés une fois, un par un (échecs ponctuels de Yahoo)."""
    if kwargs.get("threads", True) is True:
        kwargs["threads"] = YF_THREADS
    with _YF_LOCK:
        raw = yf.download(tickers, *args, **kwargs)
        syms = tickers.split() if isinstance(tickers, str) else list(tickers)
        if (len(syms) < 2 or raw is None or raw.empty or kwargs.get("group_by", "column") != "column"
                or not isinstance(raw.columns, pd.MultiIndex) or "Close" not in raw.columns.get_level_values(0)):
            return raw
        got = raw["Close"].notna().any()
        missing = [t for t in syms if not got.get(t, False)]
        if not missing or len(missing) == len(syms):
            return raw
        try:
            r2 = yf.download(missing, *args, **{**kwargs, "threads": False})
            if r2 is not None and not r2.empty and isinstance(r2.columns, pd.MultiIndex):
                raw = raw.combine_first(r2)
        except Exception:
            pass
        return raw


def _yahoo_symbol(ticker: str) -> str:
    return ticker.strip().replace(".", "-")


# ───────────────────────── Composition ─────────────────────────
def fetch_constituents() -> pd.DataFrame:
    """Composition actuelle du Nasdaq 100 (≈101 lignes : GOOG et GOOGL)."""
    req = urllib.request.Request(CONSTITUENTS_URL, headers={"User-Agent": "Mozilla/5.0"})
    html = urllib.request.urlopen(req, timeout=30).read().decode()
    for table in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in table.columns]
        if "Ticker" in cols and len(table) >= 90:
            table.columns = cols
            sector_col = next(c for c in cols if c.startswith("ICB Industry"))
            sub_col = next(c for c in cols if c.startswith("ICB Subsector"))
            df = pd.DataFrame({
                "ticker": table["Ticker"].map(_yahoo_symbol),
                "company": table["Company"],
                "sector": table[sector_col],
                "subsector": table[sub_col],
            })
            return df.drop_duplicates("ticker").sort_values("ticker").reset_index(drop=True)
    raise RuntimeError("Tableau de composition Nasdaq 100 introuvable sur la page source.")


# ───────────────────────── Cours ─────────────────────────
def fetch_prices(tickers: list[str], period: str = "2y", ohlc: bool = False):
    """Clôtures ajustées et volumes quotidiens, indice inclus. La barre du jour est
    mise à jour en continu par Yahoo pendant la séance (cours le plus récent).
    Avec ohlc=True, renvoie aussi les bougies {Open, High, Low, Close, Volume} du même téléchargement."""
    raw = download(tickers + [INDEX_TICKER], period=period, auto_adjust=True,
                      progress=False, threads=True, group_by="column")
    close = raw["Close"].dropna(how="all")
    volume = raw["Volume"].reindex(close.index)
    close.index = pd.to_datetime(close.index).tz_localize(None)
    volume.index = close.index
    if not ohlc:
        return close, volume
    bars = {"Close": close, "Volume": volume}
    for f in ("Open", "High", "Low"):
        d = raw[f].reindex(raw["Close"].dropna(how="all").index)
        d.index = close.index
        bars[f] = d
    return close, volume, bars


def merge_live(close: pd.DataFrame, volume: pd.DataFrame, tickers: list[str]):
    """Récupère le dernier mois (séance en cours incluse) et le fusionne à l'historique."""
    live_c, live_v = fetch_prices(tickers, period="1mo")
    ok = int(live_c.reindex(columns=tickers).notna().any().sum()) if len(live_c) else 0
    if ok < 0.5 * len(tickers):
        raise RuntimeError(f"Yahoo Finance n'a renvoyé des cours que pour {ok}/{len(tickers)} titres "
                           "(accès limité depuis ce serveur ?) — derniers cours connus affichés")
    return live_c.combine_first(close), live_v.combine_first(volume)


def fetch_intraday(symbol: str = INDEX_TICKER, interval: str = "5m", prepost: bool = False) -> pd.Series:
    """Cours intrajournaliers de la dernière séance (avec pré-ouverture et après-clôture si prepost)."""
    raw = download(symbol, period="5d", interval=interval, auto_adjust=True, prepost=prepost,
                      progress=False, threads=False)
    if raw.empty:
        return pd.Series(dtype=float)
    s = raw["Close"]
    s = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
    s = s.dropna()
    idx = s.index.tz_convert("America/New_York")
    last_day = idx[-1].date()
    s = s[[d.date() == last_day for d in idx]]
    s.index = idx[[d.date() == last_day for d in idx]]
    return s


# ───────────────────── Séances étendues (pré-ouverture, après-clôture, overnight) ─────────────────────
FUTURES_TICKER = "NQ=F"      # future Nasdaq 100 : cote ~23 h/24, seule mesure disponible de la séance overnight
ETF_TICKER = "QQQ"           # l'indice ^NDX ne cote pas hors séance ; QQQ le réplique en pré/après-bourse
QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"


def fetch_extended(symbols: list[str]) -> dict:
    """Cotations hors séance (Yahoo Finance) : pré-ouverture et après-clôture par titre.
    Une requête légère par lot de 150 symboles (pas de yf.download, donc sans conflit de threads)."""
    from yfinance.data import YfData
    yd = YfData()
    out: dict[str, dict] = {}
    syms = [s for s in dict.fromkeys(symbols) if s and not s.startswith("^")]
    for i in range(0, len(syms), 150):
        r = yd.get(QUOTE_URL, params={"symbols": ",".join(syms[i:i + 150]), "formatted": "false"}, timeout=10)
        for q in r.json().get("quoteResponse", {}).get("result", []):
            reg, reg_t = q.get("regularMarketPrice"), q.get("regularMarketTime") or 0
            row = {"state": q.get("marketState"), "reg": reg, "reg_t": reg_t,
                   "reg_pct": q.get("regularMarketChangePercent"), "prev": q.get("regularMarketPreviousClose")}
            # la cotation hors séance la plus récente postérieure à la dernière cotation régulière
            best = None
            for k, lab in (("preMarket", "pre"), ("postMarket", "post")):
                p, t = q.get(k + "Price"), q.get(k + "Time") or 0
                row[lab] = p
                row[lab + "_t"] = t
                if p and reg and t > reg_t and (best is None or t > best[2]):
                    best = (lab, p, t)
            if best:
                row.update(ext_session=best[0], ext=best[1], ext_t=best[2], ext_pct=best[1] / reg - 1)
            out[q["symbol"]] = row
    return out


def fetch_session_chart(now: pd.Timestamp | None = None) -> dict:
    """Courbe 5 min depuis la dernière clôture de 16 h (New York) : QQQ (après-clôture, pré-ouverture, séance)
    et future NQ=F (y compris la nuit), en % de leur niveau à la clôture de référence."""
    raw = download(f"{ETF_TICKER} {FUTURES_TICKER}", period="5d", interval="5m", prepost=True,
                      auto_adjust=True, progress=False, threads=False)
    if raw.empty:
        return {}
    c = raw["Close"].copy()
    c.index = c.index.tz_convert("America/New_York")
    now = (now or pd.Timestamp.now(tz="America/New_York")).tz_convert("America/New_York")
    q = c[ETF_TICKER].dropna()
    # dernière barre 15:55 d'une séance terminée = clôture de référence
    closes = q[(q.index.hour == 15) & (q.index.minute == 55) & (q.index + pd.Timedelta(minutes=5) <= now)]
    if closes.empty:
        return {}
    ref_t = closes.index[-1]
    start = ref_t + pd.Timedelta(minutes=5)
    win = c[c.index >= start]
    fut = c[FUTURES_TICKER].dropna()
    fut_ref = fut[fut.index <= ref_t]
    if win.empty or fut_ref.empty:
        return {}
    qq_ref, nq_ref = float(closes.iloc[-1]), float(fut_ref.iloc[-1])
    # grille fixe : 16 h → 20 h le jour de bourse suivant (week-end sauté, le future rouvre dimanche 18 h)
    end = (ref_t.normalize() + pd.offsets.BDay(1)).tz_localize(None).tz_localize("America/New_York") + pd.Timedelta(hours=20)
    grid = pd.date_range(start, end, freq="5min", inclusive="left")
    grid = grid[[(t.weekday() < 5 and not (t.weekday() == 4 and t.hour >= 20)) or (t.weekday() == 6 and t.hour >= 18)
                 for t in grid]]
    w = win.reindex(grid)
    sess = []
    for t in grid:
        m = t.hour * 60 + t.minute
        sess.append("regular" if 570 <= m < 960 and t.weekday() < 5 else "pre" if 240 <= m < 570
                    else "post" if 960 <= m < 1200 else "overnight")
    pct = lambda s, ref: [None if pd.isna(v) else round(v / ref - 1, 5) for v in s]
    return {"times": [t.strftime("%H:%M") for t in grid], "days": [t.strftime("%d/%m") for t in grid],
            "ts": [int(t.timestamp()) for t in grid],      # horodatage absolu : affichage dans le fuseau choisi
            "session": sess, "qqq": pct(w[ETF_TICKER], qq_ref), "nq": pct(w[FUTURES_TICKER], nq_ref),
            "ref_label": ref_t.strftime("%d/%m") + " 16:00", "qqq_ref": round(qq_ref, 2), "nq_ref": round(nq_ref, 2)}


def fetch_market_caps(tickers: list[str]) -> pd.Series:
    def cap(t: str):
        try:
            return t, float(yf.Ticker(t).fast_info["market_cap"])
        except Exception:
            return t, float("nan")
    with ThreadPoolExecutor(max_workers=16) as pool:
        return pd.Series(dict(pool.map(cap, tickers)), name="market_cap")


# ───────────────────────── Analystes ─────────────────────────
INFO_KEYS = {
    "targetMeanPrice": "target_mean", "targetMedianPrice": "target_median",
    "targetHighPrice": "target_high", "targetLowPrice": "target_low",
    "numberOfAnalystOpinions": "n_analysts", "recommendationMean": "rec_mean",
    "recommendationKey": "rec_key", "forwardPE": "pe_fwd", "trailingPE": "pe_ttm",
    "trailingPegRatio": "peg", "priceToSalesTrailing12Months": "ps",
    "profitMargins": "margin", "revenueGrowth": "rev_growth", "earningsGrowth": "eps_growth",
    "forwardEps": "eps_fwd", "trailingEps": "eps_ttm", "earningsTimestampStart": "earnings_ts",
    "shortPercentOfFloat": "short_float", "dividendYield": "div_yield",
    "debtToEquity": "debt_to_equity", "currentRatio": "current_ratio", "returnOnEquity": "roe",
    "grossMargins": "gross_margin_info", "operatingMargins": "op_margin_info",
    "heldPercentInstitutions": "inst_own", "beta": "beta_info",
}


def _records(df: pd.DataFrame | None, index_name: str | None = None) -> list[dict]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return []
    df = df.reset_index()
    if index_name:
        df = df.rename(columns={df.columns[0]: index_name})
    df = df.astype(object).where(df.notna(), None)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]) or any(isinstance(v, pd.Timestamp) for v in df[c]):
            df[c] = df[c].map(lambda v: v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else v)
    return json.loads(df.to_json(orient="records"))


def _fetch_analyst(ticker: str) -> dict:
    tk = yf.Ticker(ticker)
    out: dict = {}
    for attempt in range(2):
        try:
            info = tk.info or {}
            out.update({v: info.get(k) for k, v in INFO_KEYS.items()})
            break
        except Exception:
            time.sleep(1.5)
    try:
        out["recommendations"] = _records(tk.recommendations)
    except Exception:
        out["recommendations"] = []
    try:
        ud = tk.upgrades_downgrades
        if ud is not None and not ud.empty:
            ud = ud[ud.index >= pd.Timestamp.now() - pd.Timedelta(days=90)]
        out["actions"] = _records(ud, "date")
    except Exception:
        out["actions"] = []
    for attr, key in (("earnings_estimate", "eps_est"), ("revenue_estimate", "rev_est")):
        try:
            out[key] = _records(getattr(tk, attr), "period")
        except Exception:
            out[key] = []
    return out


def fetch_analysts(tickers: list[str], workers: int = 8) -> dict:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        data = dict(zip(tickers, pool.map(_fetch_analyst, tickers)))
    payload = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data": data}
    if sum(1 for v in data.values() if v.get("target_mean")) < 0.5 * len(tickers) and ANALYSTS_FILE.exists():
        print("  ⚠ prévisions analystes incomplètes (accès Yahoo limité ?) — fichier existant conservé")
        return load_analysts()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ANALYSTS_FILE.write_text(json.dumps(payload, default=str), encoding="utf-8")
    return payload


def load_analysts() -> dict:
    if ANALYSTS_FILE.exists():
        return json.loads(ANALYSTS_FILE.read_text(encoding="utf-8"))
    return {"updated": None, "data": {}}


def _json_age_hours(path: Path) -> float:
    """Âge d'après la date « updated » enregistrée dans le fichier (fiable après un clonage git,
    contrairement à la date de modification du fichier)."""
    if not path.exists():
        return float("inf")
    try:
        upd = json.loads(path.read_text(encoding="utf-8")).get("updated")
        return (datetime.now(timezone.utc) - datetime.fromisoformat(upd)).total_seconds() / 3600
    except Exception:
        return (time.time() - path.stat().st_mtime) / 3600


def analysts_age_hours() -> float:
    return _json_age_hours(ANALYSTS_FILE)


# ───────────────────────── Fondamentaux (états financiers trimestriels) ─────────────────────────
FIN_ROWS = {
    "income": {"revenue": ["Total Revenue", "Operating Revenue"], "gross_profit": ["Gross Profit"],
               "op_income": ["Operating Income", "Total Operating Income As Reported"],
               "net_income": ["Net Income", "Net Income Common Stockholders"],
               "ebitda": ["EBITDA", "Normalized EBITDA"], "ebit": ["EBIT"],
               "interest_expense": ["Interest Expense", "Interest Expense Non Operating"]},
    "cashflow": {"ocf": ["Operating Cash Flow"], "capex": ["Capital Expenditure"], "fcf": ["Free Cash Flow"],
                 "sbc": ["Stock Based Compensation"], "buyback": ["Repurchase Of Capital Stock"],
                 "stock_issued": ["Issuance Of Capital Stock", "Common Stock Issuance"],
                 "debt_net": ["Net Issuance Payments Of Debt"]},
    "balance": {"cash": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"],
                "debt": ["Total Debt"], "equity": ["Stockholders Equity", "Common Stock Equity"],
                "shares": ["Ordinary Shares Number", "Share Issued"],
                "deferred_rev_cur": ["Current Deferred Revenue"], "deferred_rev_nc": ["Non Current Deferred Revenue"]},
}


def _rows(df: pd.DataFrame | None, spec: dict) -> dict:
    out = {}
    if df is None or df.empty:
        return out
    df = df.loc[:, sorted(df.columns, reverse=True)].iloc[:, :8]   # du plus récent au plus ancien
    out["dates"] = [pd.Timestamp(c).strftime("%Y-%m-%d") for c in df.columns]
    for key, names in spec.items():
        row = next((n for n in names if n in df.index), None)
        out[key] = [None if row is None or pd.isna(v) else float(v)
                    for v in (df.loc[row] if row else [None] * df.shape[1])]
    return out


def _fetch_fundamental(ticker: str) -> dict:
    tk = yf.Ticker(ticker)
    out = {}
    for attr, part in (("quarterly_income_stmt", "income"), ("quarterly_cashflow", "cashflow"),
                       ("quarterly_balance_sheet", "balance")):
        for attempt in range(2):
            try:
                out[part] = _rows(getattr(tk, attr), FIN_ROWS[part])
                break
            except Exception:
                time.sleep(1.5)
    return out


def fetch_fundamentals(tickers: list[str], workers: int = 8) -> dict:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        data = dict(zip(tickers, pool.map(_fetch_fundamental, tickers)))
    payload = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data": data}
    if sum(1 for v in data.values() if v.get("cashflow", {}).get("fcf")) < 0.5 * len(tickers) and FUNDAMENTALS_FILE.exists():
        print("  ⚠ états financiers incomplets (accès Yahoo limité ?) — fichier existant conservé")
        return load_fundamentals()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FUNDAMENTALS_FILE.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def load_fundamentals() -> dict:
    if FUNDAMENTALS_FILE.exists():
        return json.loads(FUNDAMENTALS_FILE.read_text(encoding="utf-8"))
    return {"updated": None, "data": {}}


def file_age_hours(path: Path) -> float:
    if path.suffix == ".json":
        return _json_age_hours(path)
    return (time.time() - path.stat().st_mtime) / 3600 if path.exists() else float("inf")


# ───────────────────────── Contexte de marché (hors indice) ─────────────────────────
def fetch_context(period: str = "2y") -> pd.DataFrame:
    raw = download(list(CONTEXT_TICKERS), period=period, auto_adjust=True, progress=False, threads=True)
    c = raw["Close"].dropna(how="all")
    c.index = pd.to_datetime(c.index).tz_localize(None)
    return c


def load_context() -> pd.DataFrame:
    if CONTEXT_FILE.exists():
        return pd.read_csv(CONTEXT_FILE, index_col=0, parse_dates=True)
    return pd.DataFrame()


def merge_context(ctx: pd.DataFrame) -> pd.DataFrame:
    live = fetch_context("1mo")
    return live.combine_first(ctx) if not ctx.empty else live


# ───────────────────────── Orchestration ─────────────────────────
def _step(label: str, fn):
    """Exécute une étape de mise à jour ; en cas d'échec (réseau, blocage Yahoo…), on garde les données existantes."""
    try:
        return fn()
    except Exception as e:
        print(f"  ⚠ {label} : échec ({type(e).__name__}: {e}) — données existantes conservées")
        return None


def update(period: str = "2y", with_caps: bool = True, with_analysts: bool = True,
           with_fundamentals: bool = True) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("→ Composition du Nasdaq 100…")
    cons = _step("composition", fetch_constituents)
    if cons is not None and len(cons) >= 90:
        cons.to_csv(CONSTITUENTS_FILE, index=False)
        print(f"  {len(cons)} titres")
    elif CONSTITUENTS_FILE.exists():
        cons = pd.read_csv(CONSTITUENTS_FILE)
    else:
        raise RuntimeError("Composition du Nasdaq 100 indisponible (aucune copie locale).")
    tickers = cons["ticker"].tolist()

    print(f"→ Cours quotidiens ({period})…")
    res = _step("cours", lambda: fetch_prices(tickers, period))
    if res is not None:
        close, volume = res
        ok = sum(1 for t in tickers if t in close and close[t].notna().sum() > 100)
        if len(close) >= 200 and ok >= 0.8 * len(tickers):
            close.to_csv(PRICES_FILE)
            volume.to_csv(VOLUMES_FILE)
            print(f"  {len(close)} séances, du {close.index[0]:%d/%m/%Y} au {close.index[-1]:%d/%m/%Y} ({ok} titres)")
        else:
            print(f"  ⚠ téléchargement incomplet ({ok}/{len(tickers)} titres) — historique existant conservé")

    if with_caps:
        print("→ Capitalisations boursières…")
        caps = _step("capitalisations", lambda: fetch_market_caps(tickers))
        if caps is not None and caps.notna().mean() > 0.8:
            caps.to_csv(CAPS_FILE, header=True)
        else:
            print("  ⚠ capitalisations incomplètes — fichier existant conservé")
    if with_analysts:
        print("→ Prévisions des analystes (≈ 30 s)…")
        a = _step("analystes", lambda: fetch_analysts(tickers))
        if a:
            ok = sum(1 for v in a["data"].values() if v.get("target_mean"))
            print(f"  objectifs de cours pour {ok}/{len(tickers)} titres")
    print("→ Contexte (VXN, VIX, taux 10 ans, SOX, S&P 500, Anthropic)…")
    ctx = _step("contexte", lambda: fetch_context(period))
    if ctx is not None and len(ctx) > 50:
        ctx.to_csv(CONTEXT_FILE)
    if with_fundamentals:
        print("→ États financiers trimestriels (≈ 1 min)…")
        f = _step("états financiers", lambda: fetch_fundamentals(tickers))
        if f:
            ok = sum(1 for v in f["data"].values() if v.get("cashflow", {}).get("fcf"))
            print(f"  états financiers pour {ok}/{len(tickers)} titres")
    print("✓ Données à jour dans", DATA_DIR)


def history_is_stale() -> bool:
    """Historique à reconstruire (fichier absent ou pas mis à jour aujourd'hui)."""
    if not PRICES_FILE.exists():
        return True
    if datetime.fromtimestamp(PRICES_FILE.stat().st_mtime).date() < datetime.now().date():
        return True
    # Après un clonage git, la date du fichier est récente : on vérifie aussi la dernière séance enregistrée
    # (le rafraîchissement en direct ne récupère qu'un mois).
    try:
        with open(PRICES_FILE, "rb") as f:
            f.seek(-4000, 2)
            last = f.read().decode(errors="ignore").strip().splitlines()[-1].split(",")[0]
        return (datetime.now() - datetime.fromisoformat(last[:10])).days > 20
    except Exception:
        return False


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    if not PRICES_FILE.exists():
        raise SystemExit("Aucune donnée locale. Lancez d'abord : python3 ndx.py update")
    cons = pd.read_csv(CONSTITUENTS_FILE)
    close = pd.read_csv(PRICES_FILE, index_col=0, parse_dates=True)
    volume = pd.read_csv(VOLUMES_FILE, index_col=0, parse_dates=True)
    caps = (pd.read_csv(CAPS_FILE, index_col=0)["market_cap"]
            if CAPS_FILE.exists() else pd.Series(dtype=float, name="market_cap"))
    return cons, close, volume, caps
