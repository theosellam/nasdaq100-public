"""Serveur local du tableau de bord : chaque rafraîchissement va chercher les derniers cours."""
from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from . import analysis, data, history, indicators as ind, journal, lab, portfolio, report, swing
from .themes import THEMES

LIVE_TTL = 15            # secondes : en dessous, on resservira les mêmes cours
INTRADAY_TTL = 60
ANALYSTS_MAX_AGE_H = 6   # au-delà, les prévisions analystes sont rechargées en arrière-plan
FUNDAMENTALS_MAX_AGE_H = 24


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cons, self.close, self.volume, self.caps = data.load()
        self.analysts = data.load_analysts()
        self.fundamentals = data.load_fundamentals()
        self.ctx = data.load_context()
        self.last_live = 0.0
        self.last_intraday = 0.0
        self.intraday = pd.Series(dtype=float)
        self.extended: dict = {}
        self.ext_chart: dict = {}
        self.result: analysis.Analysis | None = None
        self.cached: dict | None = None
        self.analysts_loading = False
        self.error: str | None = None
        self.pf = None
        self.pf_behavior = None
        self.pf_key = None
        self.xclose, self.xvol, self.xan, self.xfund, self.xcaps = portfolio.load_extras()
        self.lab = lab.load()
        self.lab_status = "prêt" if self.lab else "absent"
        self.journal_eval = None
        self.journal_t = 0.0
        self.pf_signals = None
        self.pf_view = None
        self.ohlcv = history.load_ohlcv()          # bougies historiques (price action)
        self.live_bars: dict | None = None         # bougies du dernier mois, barre du jour incluse
        self.swing_hist = swing.load()             # validation historique des setups
        self.swing: dict | None = None
        self.swing_journal = None
        self.swing_journal_t = 0.0

    def _portfolio(self) -> dict | None:
        """Portefeuille personnel (si des relevés sont présents dans data/private/imports)."""
        try:
            pf = portfolio.load()
        except Exception as e:
            traceback.print_exc()
            return {"error": f"Lecture des relevés impossible : {e}"}
        if not pf:
            return None
        if pf is not self.pf:                       # nouveaux relevés : on recalcule le profil
            self.pf = pf
            self.pf_behavior = None
            extras = portfolio.extra_tickers(pf, self.tickers)
            if extras and (set(extras) - set(self.xclose.columns) or portfolio.extras_age_hours() > 24):
                print("  ↻ titres détenus hors Nasdaq 100 :", " ".join(extras))
                try:
                    portfolio.fetch_extras(extras)
                    self.xclose, self.xvol, self.xan, self.xfund, self.xcaps = portfolio.load_extras()
                except Exception as e:
                    print("  ⚠ titres hors indice :", e)
        if self.pf_behavior is None and pf.get("a") is not None:
            eps = portfolio.episodes(pf["a"])
            self.pf_behavior = portfolio.behavior(pf["a"], pf["perf"], eps, self.close[data.INDEX_TICKER])
        v = portfolio.view(pf, self.result, self.close, self.xclose, self.xvol, self.xan, self.xfund, self.xcaps)
        self.pf_view = v
        if self.pf_signals is None and self.lab:
            try:
                self.pf_signals = portfolio.trades_vs_signals(pf) or {}
            except Exception as e:
                print("  ⚠ trades vs signaux :", e)
                self.pf_signals = {}
        return {**v, "behavior": self.pf_behavior, "signals": self.pf_signals}

    # ── Swing
    def _swing_frames(self) -> dict | None:
        return swing.live_frames(self.ohlcv, self.live_bars, self.tickers)

    def _swing_payload(self, pf: dict | None) -> dict:
        O = self._swing_frames()
        if O is None:
            return {"status": "Bougies historiques en cours de téléchargement (laboratoire)…"}
        total = pf.get("total") if isinstance(pf, dict) and not pf.get("error") else None
        regime = (self.result.context or {}).get("regime", {})
        self.swing = swing.screen(O, self.result.stocks, self.result.opportunities, regime, self.swing_hist,
                                  total, portfolio.rules() if total else None, self._extended_payload())
        try:
            journal.record_swing(self.swing["signals"], self.swing["asof_iso"])
            if self.swing_journal is None or time.time() - self.swing_journal_t > 600:
                self.swing_journal = journal.evaluate_swing(O)
                self.swing_journal_t = time.time()
        except Exception as e:
            print("  ⚠ journal swing :", e)
        return {**self.swing, "journal": self.swing_journal}

    # ── Laboratoire
    def _lab_payload(self) -> dict:
        if not self.lab:
            return {"status": self.lab_status}
        return {**self.lab, "status": self.lab_status}

    def lab_job(self, force_full: bool = False) -> None:
        """Construit / rafraîchit le laboratoire en arrière-plan (historique complet chaque semaine, cours chaque jour)."""
        def job():
            try:
                tickers = self.tickers
                if force_full or not history.PRICES_FILE.exists() or history.age_days(history.HIST_DIR / "build.json") > 7:
                    self.lab_status = "téléchargement de l'historique (≈ 4 min)…"
                    history.build_all(tickers)
                elif lab.LAB_FILE.exists() and history.age_days(lab.LAB_FILE) < 0.8:
                    if not history.OHLCV_FILE.exists() or not swing.SWING_FILE.exists():
                        self._swing_job(tickers)
                    return
                else:
                    self.lab_status = "mise à jour des cours historiques…"
                    history.update_recent_prices()
                self.lab_status = "calcul des tests…"
                self.lab = lab.build(tickers)
                self._swing_job(tickers)
                self.lab_status = "prêt"
                self.pf_signals = None
                self.cached = None
            except Exception as e:
                traceback.print_exc()
                self.lab_status = f"erreur : {e}"
        threading.Thread(target=job, daemon=True).start()

    def _swing_job(self, tickers: list[str]) -> None:
        """Bougies historiques à jour puis validation des setups de swing."""
        self.lab_status = "mise à jour des bougies (swing)…"
        uni = history.universe(tickers)
        self.ohlcv = history.update_ohlcv(uni) if history.OHLCV_FILE.exists() else history.fetch_ohlcv(uni)
        self.lab_status = "test des setups de swing…"
        self.swing_hist = swing.build()
        self.cached = None

    def check(self, ticker: str, amount: float) -> dict:
        if self.result is None:
            self.refresh()
        t = ticker.upper().replace(".", "-")
        if self.pf_view is None:
            return {"error": "Aucun portefeuille importé (data/private/imports)."}
        tt = "GOOGL" if t == "GOOG" else t
        op = next((o for o in self.result.opportunities if o["ticker"] == tt), None)
        stock = self.result.stocks.loc[t].to_dict() if t in self.result.stocks.index else None
        lab_row = next((r for r in (self.lab or {}).get("today", []) if r["ticker"] == tt), None)
        return portfolio.pre_trade(t, amount, self.pf_view, stock, op, lab_row)

    @property
    def tickers(self) -> list[str]:
        return self.cons["ticker"].tolist()

    # ── Cours en direct
    def refresh(self, force: bool = False) -> dict:
        with self.lock:
            now = time.time()
            if self.cached and not force and now - self.last_live < LIVE_TTL:
                return self.cached
            # Un seul téléchargement groupé (yfinance n'est pas sûr en parallèle) : indice + contexte + titres détenus
            try:
                pf = portfolio.load()
            except Exception:
                pf = None
            extras = portfolio.extra_tickers(pf, self.tickers) if pf else []
            ctx_cols = list(data.CONTEXT_TICKERS)
            try:
                lc, lv, bars = data.fetch_prices(self.tickers + ctx_cols + extras + ([portfolio.FX_TICKER] if pf else []), "1mo", ohlc=True)
                self.live_bars = bars
                ok = int(lc.reindex(columns=self.tickers).notna().any().sum())
                if ok < 0.5 * len(self.tickers):
                    raise RuntimeError(f"Yahoo Finance n'a renvoyé des cours que pour {ok}/{len(self.tickers)} titres "
                                       "(accès limité ?) — derniers cours connus affichés")
                ndx_cols = [c for c in lc.columns if c in self.tickers or c == data.INDEX_TICKER]
                self.close = lc[ndx_cols].combine_first(self.close)
                self.volume = lv[ndx_cols].combine_first(self.volume)
                cc = [c for c in ctx_cols if c in lc]
                self.ctx = lc[cc].combine_first(self.ctx) if not self.ctx.empty else lc[cc]
                xc = [c for c in extras + [portfolio.FX_TICKER] if c in lc and lc[c].notna().any()]
                if xc:
                    self.xclose = lc[xc].combine_first(self.xclose) if not self.xclose.empty else lc[xc]
                    self.xvol = lv[xc].combine_first(self.xvol) if not self.xvol.empty else lv[xc]
                self.error = None
            except Exception as e:  # réseau indisponible : on garde les derniers cours connus
                self.error = f"Cours non rafraîchis : {e}"
            if force or now - self.last_intraday > INTRADAY_TTL:
                try:
                    self.intraday = data.fetch_intraday()
                    self.last_intraday = now
                except Exception:
                    pass
            if force or now - self.last_intraday > INTRADAY_TTL or not self.extended:
                try:   # pré-ouverture / après-clôture par titre + future NQ=F (overnight)
                    self.extended = data.fetch_extended(self.tickers + extras + [data.ETF_TICKER, data.FUTURES_TICKER])
                except Exception as e:
                    print("  ⚠ cotations hors séance :", e)
                try:
                    self.ext_chart = data.fetch_session_chart() or self.ext_chart
                except Exception as e:
                    print("  ⚠ courbe hors séance :", e)
            self.last_live = now
            try:
                self.result = analysis.run(self.cons, self.close, self.volume, self.caps, self.analysts,
                                           self.fundamentals, self.ctx, self.lab)
            except Exception as e:
                # Un calcul qui échoue ne doit jamais bloquer l'affichage : on garde le dernier résultat valide
                traceback.print_exc()
                if self.cached:
                    stale = dict(self.cached)
                    stale["error"] = f"Calcul interrompu ({type(e).__name__}: {e}) — derniers résultats valides affichés"
                    return stale
                raise
            try:
                journal.record(self.result, self.close)
                if now - self.journal_t > 600 or self.journal_eval is None:
                    self.journal_eval = journal.evaluate(self.close)
                    self.journal_t = now
            except Exception as e:
                print("  ⚠ journal :", e)
            out = report.payload(self.result, self.intraday, live=True)
            try:
                out["portfolio"] = report.clean(self._portfolio())
            except Exception as e:
                traceback.print_exc()
                out["portfolio"] = {"error": f"Portefeuille : {e}"}
            out["extended"] = report.clean(self._extended_payload())
            try:
                out["swing"] = report.clean(self._swing_payload(out.get("portfolio")))
            except Exception as e:
                traceback.print_exc()
                out["swing"] = {"error": f"Swing : {type(e).__name__}: {e}"}
            out["lab"] = report.clean(self._lab_payload())
            out["journal"] = report.clean(self.journal_eval)
            out["analystsLoading"] = self.analysts_loading
            out["error"] = self.error
            self.cached = out
            return out

    def _extended_payload(self) -> dict:
        """Pré-ouverture, après-clôture et overnight (future NQ=F) pour le tableau de bord."""
        ex = self.extended or {}
        keep = ("ext", "ext_pct", "ext_session", "ext_t", "reg", "state")
        quotes = {t: {k: q.get(k) for k in keep} for t, q in ex.items() if q.get("ext") is not None}
        nq, qqq = ex.get(data.FUTURES_TICKER, {}), ex.get(data.ETF_TICKER, {})
        ch = self.ext_chart or {}
        last = lambda arr: next((v for v in reversed(arr or []) if v is not None), None)
        nq_pct, qqq_pct = last(ch.get("nq")), last(ch.get("qqq"))
        ndx_close = float(self.close[data.INDEX_TICKER].dropna().iloc[-1]) if data.INDEX_TICKER in self.close else None
        return {"quotes": quotes, "chart": ch,
                "nq": {"price": nq.get("reg"), "pct_ref": nq_pct, "t": nq.get("reg_t")},
                "qqq": {"price": qqq.get("ext") or qqq.get("reg"), "pct_ref": qqq_pct,
                        "session": qqq.get("ext_session"), "t": qqq.get("ext_t") or qqq.get("reg_t")},
                "ndx_implied": ndx_close * (1 + nq_pct) if ndx_close and nq_pct is not None else None,
                "ndx_close": ndx_close}

    # ── Analystes (arrière-plan)
    def refresh_analysts(self) -> None:
        if self.analysts_loading:
            return
        self.analysts_loading = True

        def job():
            try:
                print("  ↻ mise à jour des prévisions analystes…")
                self.analysts = data.fetch_analysts(self.tickers)
                self.cached = None
                print("  ✓ prévisions analystes à jour")
                if data.file_age_hours(data.FUNDAMENTALS_FILE) > FUNDAMENTALS_MAX_AGE_H:
                    print("  ↻ mise à jour des états financiers…")
                    self.fundamentals = data.fetch_fundamentals(self.tickers)
                    self.cached = None
                    print("  ✓ états financiers à jour")
            except Exception as e:
                print("  ⚠ analystes :", e)
            finally:
                self.analysts_loading = False
        threading.Thread(target=job, daemon=True).start()

    # ── Fiche titre
    def stock(self, ticker: str) -> dict:
        if self.result is None:
            self.refresh()
        t = ticker.upper()
        if t not in self.result.stocks.index:
            return {"error": f"{t} ne fait pas partie du Nasdaq 100"}
        s = self.close[t].dropna()
        hist = pd.DataFrame({"close": s, "sma50": ind.sma(s, 50), "sma200": ind.sma(s, 200)}).iloc[-252:]
        a = self.analysts.get("data", {}).get(t, {})
        out = {
            "ticker": t,
            "row": self.result.stocks.loc[t].to_dict(),
            "history": report.series_payload(hist),
            "recommendations": a.get("recommendations", []),
            "actions": a.get("actions", [])[:40],
            "eps_est": a.get("eps_est", []),
            "rev_est": a.get("rev_est", []),
            "opportunity": next((o for o in self.result.opportunities if o["ticker"] == ("GOOGL" if t == "GOOG" else t)), None),
            "financials": self.fundamentals.get("data", {}).get(t, {}),
        }
        try:
            O = self._swing_frames()
            if O is not None and t in O["Close"].columns:
                sig = next((x for x in (self.swing or {}).get("signals", []) if x["ticker"] == t), None)
                out["swing"] = swing.chart(O, t, sig=sig)
        except Exception:
            traceback.print_exc()
        try:
            intra = data.fetch_intraday(t, prepost=True)
            # grille fixe 4 h → 20 h : pré-ouverture, séance, après-clôture (trous comblés entre deux cotations)
            day = intra.index[-1].normalize()
            grid = pd.date_range(day + pd.Timedelta(hours=4), day + pd.Timedelta(hours=20), freq="5min", inclusive="left")
            g = intra.reindex(grid).ffill()
            g[g.index > intra.index[-1]] = float("nan")
            prev = s[s.index < day.tz_localize(None)]          # clôture de la séance précédente
            out["intraday"] = {"times": [x.strftime("%H:%M") for x in grid], "prepost": True,
                               "ts": [int(x.timestamp()) for x in grid],
                               "close": [None if pd.isna(v) else round(float(v), 2) for v in g.values],
                               "prev_close": round(float(prev.iloc[-1]), 2) if len(prev) else None}
        except Exception:
            pass
        return report.clean(out)


class Boot:
    """Démarrage en arrière-plan : le port s'ouvre tout de suite (exigé par les hébergeurs),
    les données se chargent ensuite. En attendant, l'API répond 503 et la page patiente."""
    def __init__(self) -> None:
        self.state: State | None = None
        self.status = "Démarrage…"
        self.error: str | None = None

    def run(self) -> None:
        try:
            first = not data.ANALYSTS_FILE.exists()      # premier lancement : l'analyse a besoin des prévisions des analystes
            if data.history_is_stale() or first:
                self.status = ("Premier lancement : téléchargement des cours, des analystes et des états financiers (≈ 2 à 4 min)…"
                               if first else "Téléchargement de l'historique des cours (≈ 1 min)…")
                print("→", self.status)
                try:
                    data.update(with_caps=True, with_analysts=first)
                except Exception:
                    traceback.print_exc()
                    print("  ⚠ mise à jour impossible : démarrage avec les données existantes")
            self.status = "Calcul de l'analyse…"
            st = State()
            st.refresh(force=True)
            self.state = st
            print("✓ Données prêtes")
            st.lab_job()
            if (data.analysts_age_hours() > ANALYSTS_MAX_AGE_H
                    or data.file_age_hours(data.FUNDAMENTALS_FILE) > FUNDAMENTALS_MAX_AGE_H):
                st.refresh_analysts()
        except Exception as e:
            traceback.print_exc()
            self.error = f"Échec du chargement des données : {type(e).__name__}: {e} (détails dans les journaux du serveur)"
            self.status = self.error


def make_handler(boot: Boot, password: str | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _authorized(self) -> bool:
            """Mot de passe facultatif (variable NDX_PASSWORD) : authentification HTTP Basic, identifiant libre."""
            if not password:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    _, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
                    if hmac.compare_digest(pw.encode(), password.encode()):
                        return True
                except Exception:
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Nasdaq 100", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/healthz":                 # contrôle de santé de l'hébergeur (sans mot de passe)
                self._send(200, b"ok", "text/plain")
                return
            if not self._authorized():
                return
            q = parse_qs(url.query)
            state = boot.state
            try:
                if url.path.startswith("/api/") and state is None:
                    self._json({"loading": True, "status": boot.status, "error": boot.error}, 503)
                elif url.path in ("/", "/index.html"):
                    self._send(200, report.render(None).encode(), "text/html; charset=utf-8")
                elif url.path == "/api/dashboard":
                    self._json(state.refresh(force="force" in q))
                elif url.path == "/api/stock":
                    self._json(state.stock(q.get("t", [""])[0]))
                elif url.path == "/api/check":
                    self._json(report.clean(state.check(q.get("t", [""])[0], float(q.get("amount", ["0"])[0] or 0))))
                elif url.path == "/api/lab-rebuild":
                    state.lab_job(force_full="full" in q)
                    self._json({"ok": True})
                elif url.path == "/api/positions":
                    saxo = bool(portfolio.latest_file("AggregatedAmounts_*.xlsx") or portfolio.latest_file("Holdings_*.pdf"))
                    self._json({**portfolio.load_manual(), "saxo": saxo, "themes": [
                        [k, THEMES[k]["label"] if k in THEMES else portfolio.SPACE_LABEL] for k in portfolio.THEME_CHOICES]})
                elif url.path == "/api/refresh-analysts":
                    state.refresh_analysts()
                    self._json({"ok": True})
                else:
                    self._send(404, b"Not found", "text/plain")
            except Exception as e:
                traceback.print_exc()
                self._json({"error": str(e)}, 500)
        def do_POST(self):
            if not self._authorized():
                return
            url = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 200_000:
                    self._json({"error": "Requête trop volumineuse"}, 413)
                    return
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if url.path == "/api/positions":
                    res = portfolio.save_manual(body)
                    if res.get("ok") and boot.state is not None:
                        boot.state.pf_view = None
                        boot.state.cached = None          # prochain /api/dashboard : recalcul avec les nouvelles positions
                    self._json(res, 200 if res.get("ok") else 400)
                else:
                    self._send(404, b"Not found", "text/plain")
            except Exception as e:
                traceback.print_exc()
                self._json({"error": str(e)}, 500)
    return Handler


def start(port: int = 8100, host: str = "127.0.0.1"):
    """Ouvre le port (le suivant s'il est pris) et lance le chargement des données en arrière-plan.
    Renvoie (serveur HTTP, adresse, état du démarrage) ; à l'appelant de lancer serve_forever()."""
    boot = Boot()
    password = os.environ.get("NDX_PASSWORD") or None
    httpd = None
    for p in range(port, port + 20):           # port occupé (autre copie déjà lancée) : on prend le suivant
        try:
            httpd = ThreadingHTTPServer((host, p), make_handler(boot, password))
            break
        except OSError:
            print(f"  Port {p} déjà utilisé, essai du port {p + 1}…")
    if httpd is None:
        raise SystemExit(f"Aucun port libre entre {port} et {port + 19} : fermez les autres fenêtres du tableau de bord.")
    threading.Thread(target=boot.run, daemon=True).start()
    return httpd, f"http://localhost:{p}", boot


def serve(port: int = 8100, open_browser: bool = True, host: str = "127.0.0.1") -> None:
    httpd, url, boot = start(port, host)
    password = os.environ.get("NDX_PASSWORD") or None
    print(f"✓ Tableau de bord en direct : {url}   (écoute sur {host}{', protégé par mot de passe' if password else ''} · Ctrl+C pour arrêter)")
    print("  Les données se chargent en arrière-plan ; la page s'affiche dès qu'elles sont prêtes.")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt du serveur.")
