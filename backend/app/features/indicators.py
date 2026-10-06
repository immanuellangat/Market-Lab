"""Causal indicator features (every value uses only data up to and including the bar)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def wilder(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return wilder(tr, n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = wilder(d.clip(lower=0), n)
    dn = wilder((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def rolling_pct_rank(x: pd.Series, window: int) -> pd.Series:
    """Percentile of the current value within the trailing window (causal)."""
    return x.rolling(window, min_periods=max(20, window // 5)).rank(pct=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    out = {}
    a = atr(df)
    out["atr"] = a
    out["atr_pct"] = rolling_pct_rank(a, 500)
    out["vol_regime"] = np.select([out["atr_pct"] < 0.33, out["atr_pct"] > 0.67], [0, 2], 1)  # 0 low,1 normal,2 high
    r = rsi(c)
    out["rsi"] = r
    out["ema50"] = c.ewm(span=50, adjust=False).mean()
    out["ema200"] = c.ewm(span=200, adjust=False).mean()
    out["ema_trend"] = np.select(
        [(c > out["ema50"]) & (out["ema50"] > out["ema200"]), (c < out["ema50"]) & (out["ema50"] < out["ema200"])],
        [1, -1], 0)
    out["roc10_atr"] = (c - c.shift(10)) / a  # momentum normalised by volatility

    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    out["body_atr"] = body / a
    out["body_ratio"] = (body / rng).fillna(0)
    out["upper_wick_ratio"] = ((h - np.maximum(o, c)) / rng).fillna(0)
    out["lower_wick_ratio"] = ((np.minimum(o, c) - l) / rng).fillna(0)
    out["displacement_bull"] = (c > o) & (body > 1.5 * a) & (out["body_ratio"] > 0.6)
    out["displacement_bear"] = (c < o) & (body > 1.5 * a) & (out["body_ratio"] > 0.6)
    out["bull_engulf"] = (c > o) & (c.shift(1) < o.shift(1)) & (c >= o.shift(1)) & (o <= c.shift(1))
    out["bear_engulf"] = (c < o) & (c.shift(1) > o.shift(1)) & (c <= o.shift(1)) & (o >= c.shift(1))
    out["hammer"] = (out["lower_wick_ratio"] > 0.6) & (out["body_ratio"] < 0.3)
    out["shooting_star"] = (out["upper_wick_ratio"] > 0.6) & (out["body_ratio"] < 0.3)

    rng20 = h.rolling(20).max() - l.rolling(20).min()
    out["range20_atr"] = rng20 / a
    out["compression"] = rolling_pct_rank(out["range20_atr"], 500) < 0.15
    out["range_expansion"] = (h - l) > 2.5 * a
    out["inside_bar"] = (h <= h.shift(1)) & (l >= l.shift(1))

    # consecutive closes in the same direction
    sign = np.sign(c - o)
    grp = (sign != sign.shift()).cumsum()
    out["streak"] = sign * sign.groupby(grp).cumcount().add(1)
    return pd.concat([df, pd.DataFrame(out, index=df.index)], axis=1)
