"""Outcome matrix: what would have happened after entering at *every* bar.

For each bar i and each direction, a hypothetical trade is entered at the open
of bar i+1 (the first price available after the signal bar closed), with a
stop of `stop_atr` x ATR(i). Over the next `horizon` bars we record:
  * result in R for each target in TARGETS_R (target first = +T, stop first =
    -1, neither = mark-to-market at the horizon). If stop and target are both
    inside the same candle we assume the stop was hit first (conservative).
  * max favourable / adverse excursion (ATR units), and MFE before the stop.
  * bars until exit, and the spread cost in R.
Any pattern is then just a boolean mask over bars, so statistics for thousands
of condition combinations can be computed almost instantly.
"""
from __future__ import annotations

import threading

import numpy as np
import pandas as pd

from ..config import TARGETS_R

_cache: dict[tuple, pd.DataFrame] = {}
_lock = threading.Lock()
CHUNK = 20000


def _first_true(m: np.ndarray) -> np.ndarray:
    """Index of first True per row, or m.shape[1] if none."""
    any_ = m.any(axis=1)
    idx = m.argmax(axis=1)
    return np.where(any_, idx, m.shape[1])


def spread_price(df: pd.DataFrame, point: float) -> np.ndarray:
    """Per-bar spread in price units.

    Some broker histories store old spreads in different units (e.g. BTCUSD 2021-22 shows ~$390),
    so each bar's spread is capped at 5x the instrument's recent typical spread."""
    n = len(df)
    spread = df["spread"].to_numpy(float) if "spread" in df else np.zeros(n)
    ref = np.nanmedian(spread[-max(1000, n // 5):]) if n else 0.0
    if ref > 0:
        spread = np.minimum(spread, 5 * ref)
    return spread * point


def compute_outcomes(df: pd.DataFrame, horizon: int, stop_atr: float, point: float = 0.0) -> pd.DataFrame:
    n = len(df)
    H = horizon
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a = df["atr"].to_numpy(float)
    spread = spread_price(df, point)

    pad = lambda x: np.concatenate([x, np.full(H + 2, np.nan)])
    hp, lp, cp, op = pad(h), pad(l), pad(c), pad(o)
    res = {}
    for side in ("L", "S"):
        for t in TARGETS_R:
            res[f"{side}_r{t:g}"] = np.full(n, np.nan)
        res[f"{side}_mfe"] = np.full(n, np.nan)
        res[f"{side}_mae"] = np.full(n, np.nan)
        res[f"{side}_mfe_prestop"] = np.full(n, np.nan)
        res[f"{side}_bars"] = np.full(n, np.nan)
    res["cost_r"] = np.full(n, np.nan)
    steps = np.arange(H)

    for s in range(0, n, CHUNK):
        idx = np.arange(s, min(n, s + CHUNK))
        win = idx[:, None] + 1 + steps[None, :]
        wh, wl = hp[win], lp[win]
        entry = op[idx + 1]
        risk = stop_atr * a[idx]
        valid = np.isfinite(entry) & np.isfinite(risk) & (risk > 0)
        avail = np.isfinite(wh).sum(axis=1)  # bars actually available ahead
        last_close = cp[np.minimum(idx + H, n + H)]
        res["cost_r"][idx] = np.where(valid, spread[idx] / np.where(risk > 0, risk, np.nan), np.nan)

        for side, sgn in (("L", 1.0), ("S", -1.0)):
            with np.errstate(invalid="ignore"):
                if sgn > 0:
                    fav = (wh - entry[:, None]) / risk[:, None]
                    adv = (entry[:, None] - wl) / risk[:, None]
                else:
                    fav = (entry[:, None] - wl) / risk[:, None]
                    adv = (wh - entry[:, None]) / risk[:, None]
                sl_first = _first_true(adv >= 1.0)
                mtm = sgn * (last_close - entry) / risk
            fav_f = np.nan_to_num(fav, nan=-np.inf)
            adv_f = np.nan_to_num(adv, nan=-np.inf)
            mfe = fav_f.max(axis=1)
            mae = adv_f.max(axis=1)
            pre = np.where(steps[None, :] <= np.minimum(sl_first, H - 1)[:, None], fav_f, -np.inf).max(axis=1)
            res[f"{side}_mfe"][idx] = np.where(valid, mfe * stop_atr, np.nan)
            res[f"{side}_mae"][idx] = np.where(valid, mae * stop_atr, np.nan)
            res[f"{side}_mfe_prestop"][idx] = np.where(valid, pre, np.nan)
            for t in TARGETS_R:
                with np.errstate(invalid="ignore"):
                    tp_first = _first_true(fav >= t)
                win_ = tp_first < sl_first
                loss = (sl_first <= tp_first) & (sl_first < H)
                complete = avail >= H
                r = np.where(win_, t, np.where(loss, -1.0, np.where(complete, np.clip(mtm, -1.0, t), np.nan)))
                r = np.where(valid, r, np.nan)
                res[f"{side}_r{t:g}"][idx] = r
                if t == 2.0:
                    exit_bar = np.where(win_, tp_first, np.where(loss, sl_first, H - 1)) + 1
                    res[f"{side}_bars"][idx] = np.where(np.isfinite(r), exit_bar, np.nan)
    return pd.DataFrame(res, index=df.index)


def get_outcomes(key: tuple, df: pd.DataFrame, horizon: int, stop_atr: float, point: float = 0.0) -> pd.DataFrame:
    k = key + (horizon, round(stop_atr, 3), len(df))
    with _lock:
        if k in _cache:
            return _cache[k]
    out = compute_outcomes(df, horizon, stop_atr, point)
    with _lock:
        if len(_cache) > 6:
            _cache.pop(next(iter(_cache)))
        _cache[k] = out
    return out
