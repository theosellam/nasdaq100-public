"""Indicateurs techniques et de risque (vectorisés sur des séries de cours)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def period_return(s: pd.Series, days: int) -> float:
    s = s.dropna()
    if len(s) <= days:
        return np.nan
    return s.iloc[-1] / s.iloc[-1 - days] - 1


def ytd_return(s: pd.Series) -> float:
    s = s.dropna()
    prev_year = s[s.index.year < s.index[-1].year]
    return s.iloc[-1] / prev_year.iloc[-1] - 1 if len(prev_year) else np.nan


def volatility(s: pd.Series, days: int = TRADING_DAYS) -> float:
    r = s.dropna().pct_change(fill_method=None).iloc[-days:]
    return r.std() * np.sqrt(TRADING_DAYS)


def drawdown_series(s: pd.Series) -> pd.Series:
    return s / s.cummax() - 1


def max_drawdown(s: pd.Series, days: int = TRADING_DAYS) -> float:
    return drawdown_series(s.dropna().iloc[-days:]).min()


def beta(s: pd.Series, bench: pd.Series, days: int = TRADING_DAYS) -> float:
    r = pd.concat([s.pct_change(fill_method=None), bench.pct_change(fill_method=None)], axis=1).dropna().iloc[-days:]
    if len(r) < 60:
        return np.nan
    cov = np.cov(r.iloc[:, 0], r.iloc[:, 1])
    return cov[0, 1] / cov[1, 1]


def sharpe(s: pd.Series, days: int = TRADING_DAYS, rf: float = 0.04) -> float:
    r = s.dropna().pct_change(fill_method=None).iloc[-days:]
    excess = r.mean() * TRADING_DAYS - rf
    vol = r.std() * np.sqrt(TRADING_DAYS)
    return excess / vol if vol else np.nan
