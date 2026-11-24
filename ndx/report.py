"""Sérialisation de l'analyse en JSON + export HTML autonome du tableau de bord."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .analysis import Analysis

from .data import DATA_DIR as _DATA_DIR
REPORTS_DIR = _DATA_DIR.parent / "reports"
TEMPLATE = Path(__file__).resolve().parent / "template.html"
NY = ZoneInfo("America/New_York")


def clean(o):
    """Rend un objet sérialisable en JSON (NaN → null, types numpy → natifs)."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    if hasattr(o, "item"):
        return clean(o.item())
    return o


def series_payload(df: pd.DataFrame, fmt: str = "%Y-%m-%d") -> dict:
    return {"dates": [d.strftime(fmt) for d in df.index],
            **{c: [None if pd.isna(v) else round(float(v), 4) for v in df[c]] for c in df.columns}}


def market_status(now: datetime | None = None) -> dict:
    """État de la séance américaine (heures de New York, jours fériés non gérés)."""
    now = (now or datetime.now(NY)).astimezone(NY)
    minutes = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        state, label = "closed", "Marché fermé (week-end)"
    elif 570 <= minutes < 960:
        state, label = "open", "Marché ouvert"
    elif 240 <= minutes < 570:
        state, label = "pre", "Pré-ouverture"
    elif 960 <= minutes < 1200:
        state, label = "post", "Après-clôture"
    else:
        state, label = "closed", "Marché fermé"
    return {"state": state, "label": label, "ny_time": now.strftime("%H:%M")}


def payload(a: Analysis, intraday: pd.Series | None = None, live: bool = False) -> dict:
    stocks = a.stocks.reset_index()
    out = {
        "live": live,
        "generated": (datetime.now(ZoneInfo(os.environ["NDX_TZ"])) if os.environ.get("NDX_TZ")
                      else datetime.now()).strftime("%d/%m/%Y %H:%M:%S"),
        "generated_ts": int(datetime.now().timestamp()),   # converti dans le fuseau choisi par l'utilisateur
        "asof": a.asof.strftime("%d/%m/%Y"),
        "market": market_status(),
        "analystsUpdated": a.analysts_updated,
        "index": a.index,
        "breadth": a.breadth,
        "stocks": stocks.to_dict("records"),
        "sectors": a.sectors.reset_index().to_dict("records"),
        "indexHistory": series_payload(a.index_history[["close", "sma50", "sma200"]]),
        "breadthHistory": series_payload(a.breadth_history),
        "signals": {k: v.reset_index()[["ticker", "company"]].to_dict("records") for k, v in a.signals.items()},
        "corr": {"tickers": list(a.correlation.columns), "values": a.correlation.round(2).values.tolist()},
        "actions": a.analyst_actions[:120],
        "earnings": a.earnings,
        "context": a.context,
        "opportunities": a.opportunities,
        "strategies": a.strategies,
        "fundamentalsUpdated": a.fundamentals_updated,
    }
    if intraday is not None and len(intraday):
        out["intraday"] = {"times": [t.strftime("%H:%M") for t in intraday.index],
                           "ts": [int(t.timestamp()) for t in intraday.index],
                           "close": [round(float(v), 2) for v in intraday.values],
                           "prev_close": a.index.get("prev_close")}
    return clean(out)


def render(data: dict | None) -> str:
    return TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(data, ensure_ascii=False) if data else "null")


def build(a: Analysis, out: str | None = None) -> Path:
    """Export statique (instantané) : le fichier s'ouvre sans serveur."""
    path = Path(out) if out else REPORTS_DIR / "nasdaq100.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(payload(a)), encoding="utf-8")
    return path.resolve()
