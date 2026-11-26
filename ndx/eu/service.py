"""Assemble le système Europe pour le tableau de bord et la ligne de commande (état en mémoire + caches)."""
from __future__ import annotations

import json
import threading
import time
import traceback

import numpy as np
import pandas as pd

from . import model, situations, sources
from .desk_eu import STRATEGIES, engine as desk

LAB_FILE = sources.EU_DIR / "lab.json"


class Europe:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.data = None
        self.snap: pd.DataFrame | None = None
        self.lab: dict | None = json.loads(LAB_FILE.read_text()) if LAB_FILE.exists() else None
        self.status = "absent"
        self.loaded_t = 0.0
        self.review = None
        self.review_t = 0.0

    # ── données
    def load(self) -> None:
        with self.lock:
            d = sources.load()
            if d is None:
                self.status = "absent"
                return
            d["close"][model.BENCH] = model.ew_index(d["close"], d["volume"])
            self.data = d
            self.snap = model.snapshot(d)
            self.loaded_t = time.time()
            self.status = "prêt"
            self.review = None

    def ensure(self) -> None:
        newer = max((f.stat().st_mtime for f in (sources.BUILD_FILE, sources.PRICES_FILE) if f.exists()), default=0)
        if self.data is None or newer > self.loaded_t:
            self.load()

    def build_job(self, force: bool = False) -> None:
        """Mise à jour en arrière-plan (hebdomadaire) puis recalcul du laboratoire."""
        if self.status.startswith("mise à jour"):
            return
        if not force and sources.load() is None:
            # première construction (≈ 30 à 60 min, des centaines de rapports) : seulement à la demande de l'utilisateur
            self.status = "absent"
            return
        if not force and sources.age_days() < 7 and self.lab:
            if sources.prices_age_hours() > 18:          # cours du jour seulement
                def light():
                    try:
                        self.status = "mise à jour : cours du jour…"
                        sources.refresh_prices()
                        self.load()
                    except Exception as e:
                        traceback.print_exc()
                        self.status = f"prêt (cours non rafraîchis : {e})"
                threading.Thread(target=light, daemon=True).start()
            return

        def job():
            try:
                def log(m):
                    print(m)
                    if m.strip().startswith("→"):
                        self.status = "mise à jour : " + m.strip().lstrip("→ ").rstrip("…")
                sources.build(log)
                self.load()
                self.status = "mise à jour : laboratoire…"
                self.lab = model.backtest(self.data)
                LAB_FILE.write_text(json.dumps(self.lab, ensure_ascii=False, default=lambda x: None))
                self.status = "prêt"
            except Exception as e:
                traceback.print_exc()
                self.status = f"erreur : {type(e).__name__}: {e}"
        threading.Thread(target=job, daemon=True).start()

    def run_lab(self) -> dict:
        self.ensure()
        self.lab = model.backtest(self.data)
        LAB_FILE.write_text(json.dumps(self.lab, ensure_ascii=False, default=lambda x: None))
        return self.lab

    # ── desk
    def prices(self) -> dict[str, float]:
        if self.data is None:
            return {}
        last = self.data["close"].ffill().iloc[-1]
        return {k: float(v) for k, v in last.items() if pd.notna(v) and v > 0}

    def adv(self) -> dict[str, float]:
        if self.snap is None or self.snap.empty:
            return {}
        return {r.ticker: float(r.adv) for r in self.snap.itertuples() if pd.notna(r.adv)}

    def validated(self) -> dict[str, bool]:
        ok = bool((self.lab or {}).get("validated"))
        return {"valeur_qualite": ok}

    def order(self, body: dict, dry: bool = False) -> dict:
        self.ensure()
        prices, adv = self.prices(), self.adv()
        bench = prices.get(model.BENCH)
        if dry:
            return desk.check(body, prices, self.validated(), None, None, adv)
        res = desk.submit(body, prices, self.validated(), None, bench, adv)
        self.review = None
        return res

    def note(self, body: dict) -> dict:
        self.ensure()
        t = str(body.get("ticker", "")).upper().strip()
        res = desk.note(t, body.get("action", ""), body.get("reason", ""), body.get("actor") or "vous",
                        body.get("strategy"), self.prices().get(t), self.prices().get(model.BENCH))
        self.review = None
        return res

    # ── vue complète
    def payload(self, mark: bool = True) -> dict:
        self.ensure()
        if self.data is None:
            return {"status": self.status, "ready": False}
        S = self.snap.copy()
        close = self.data["close"]
        sf = model.special_flags(close, S)
        prices = self.prices()
        fired = desk.mark(prices, prices.get(model.BENCH)) if mark else []
        if self.review is None or time.time() - self.review_t > 300:
            self.review = desk.review(close)
            self.review_t = time.time()
        rows = []
        for _, r in S.iterrows():
            d = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()}
            d["flags"] = model.flags(r)
            d["special"] = sf.get(r["ticker"], [])
            rows.append(d)
        names = dict(zip(S["ticker"], S["name"]))
        eq = desk.equity_curve()
        info = self.data["info"]
        return {
            "ready": True, "status": self.status, "info": info,
            "rules_model": {"min_cap": model.MIN_CAP, "max_cap": model.MAX_CAP, "min_adv": model.MIN_ADV, "costs": model.COSTS},
            "factors": model.FACTORS, "rows": rows,
            "counts": {"listed": int(len(S)), "eligible": int(S["eligible"].sum()), "scored": int(S["score"].notna().sum()),
                       "with_accounts": int(S["fy_end"].notna().sum())},
            "lab": self.lab, "situations": situations.view(prices, names), "situation_types": situations.TYPES,
            "desk": {"book": desk.book(prices, desk.rules()), "rules": desk.rules(), "review": self.review,
                     "fired": fired, "strategies": [{**s, "validated": self.validated().get(s["key"], False)} for s in STRATEGIES],
                     "equity": {"date": eq["date"].tolist(), "equity": eq["equity"].tolist(), "index": eq["index"].tolist()}},
            "bench_last": prices.get(model.BENCH),
        }


EUROPE = Europe()
