"""Rare-event detection: is something unusual happening right now, and what
happened the few times it happened before?

Two kinds of rarity on the latest closed candle:
  * rare condition sets: an active condition (or pair of active conditions) that
    historically occurs on < 1% (pairs < 0.3%) of candles;
  * statistical extremes: a continuous measure (RSI, momentum, candle size,
    range, streak, distance from structure) beyond its 1st/99th percentile.
For each, past occurrences are listed with what price did next. These are
small samples by definition: shown for context, never graded as an edge.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from ..config import DEFAULT_HORIZON
from ..features.engine import get_features, live_features
from ..research.conditions import available, label_of
from ..research.hypothesis import WARMUP, condition_matrix, dedupe, point_size
from ..research.outcomes import get_outcomes

EXTREMES = {
    "rsi": "RSI(14)",
    "roc10_atr": "10-bar momentum (ATR)",
    "body_atr": "Candle body size (ATR)",
    "range20_atr": "20-bar range (ATR)",
    "streak": "Consecutive candles in one direction",
    "dist_sh_atr": "Distance below last swing high (ATR)",
    "dist_sl_atr": "Distance above last swing low (ATR)",
    "atr_pct": "Volatility percentile",
}


def _unix(t) -> int:
    return int(pd.Timestamp(t).timestamp())


def _outcome_summary(idx: np.ndarray, df: pd.DataFrame, out: pd.DataFrame, H: int) -> dict:
    c = df["close"].to_numpy()
    a = df["atr"].to_numpy()
    ok = idx[idx + H < len(df)]
    fwd = (c[ok + H] - c[ok]) / a[ok]
    lr = (out["L_r2"] - out["cost_r"].fillna(0)).to_numpy()[ok]
    sr = (out["S_r2"] - out["cost_r"].fillna(0)).to_numpy()[ok]
    res = {
        "n": int(len(ok)),
        "fwd_mean_atr": float(np.mean(fwd)) if len(ok) else None,
        "fwd_median_atr": float(np.median(fwd)) if len(ok) else None,
        "up_pct": float(np.mean(fwd > 0)) if len(ok) else None,
        "max_up_atr": float(np.nanmean(out["L_mfe"].to_numpy()[ok])) if len(ok) else None,
        "max_down_atr": float(np.nanmean(out["S_mfe"].to_numpy()[ok])) if len(ok) else None,
        "long_avg_r": float(np.nanmean(lr)) if len(ok) else None,
        "short_avg_r": float(np.nanmean(sr)) if len(ok) else None,
        "examples": [{"time": _unix(df["time"].iloc[i]), "fwd_atr": float(f)} for i, f in zip(ok[::-1][:10], fwd[::-1][:10])],
    }
    return res


def rare_now(symbol: str, tf: str, min_n: int = 5) -> dict:
    live, _ = live_features(symbol, tf)
    df, _ = get_features(symbol, tf)
    ids, mat = condition_matrix(symbol, tf, df)
    H = DEFAULT_HORIZON[tf]
    out = get_outcomes((symbol, tf), df, H, 1.0, point_size(symbol))
    valid = np.zeros(len(df), bool)
    valid[WARMUP:] = True
    nv = valid.sum()
    tail = live.iloc[-600:]
    active = [c.id for c in available(tail) if c.id in ids and bool(pd.Series(c.fn(tail)).fillna(False).iloc[-1])]
    col = {c: j for j, c in enumerate(ids)}
    cooldown = max(5, H // 4)
    events = []

    freq1 = {c: float(mat[valid, col[c]].mean()) for c in active}
    for c in active:
        if freq1[c] < 0.01:
            idx = dedupe(np.flatnonzero(mat[:, col[c]] & valid), cooldown)
            if len(idx) >= min_n:
                events.append({"kind": "rare condition", "conditions": [c], "labels": [label_of(c)],
                               "freq": freq1[c], **_outcome_summary(idx, df, out, H)})
    common = [c for c in active if freq1[c] >= 0.01][:30]
    for a, b in itertools.combinations(common, 2):
        m = mat[:, col[a]] & mat[:, col[b]] & valid
        f = m.sum() / nv
        if f < 0.003:
            idx = dedupe(np.flatnonzero(m), cooldown)
            if len(idx) >= min_n:
                events.append({"kind": "rare combination", "conditions": [a, b], "labels": [label_of(a), label_of(b)],
                               "freq": float(f), **_outcome_summary(idx, df, out, H)})

    last = live.iloc[-1]
    for colname, label in EXTREMES.items():
        if colname not in df.columns or not np.isfinite(last.get(colname, np.nan)):
            continue
        hist = df[colname].to_numpy()[valid]
        hist = hist[np.isfinite(hist)]
        if len(hist) < 500:
            continue
        v = float(last[colname])
        pct = float((hist < v).mean())
        if 0.01 <= pct <= 0.99:
            continue
        hi = pct > 0.99
        x = df[colname].to_numpy()
        m = (x >= v if hi else x <= v) & valid
        if m.sum() < min_n * 3:  # too extreme to have history: widen to the 0.5% tail
            q = np.quantile(hist, 0.995 if hi else 0.005)
            m = (x >= q if hi else x <= q) & valid
        idx = dedupe(np.flatnonzero(m), cooldown)
        if len(idx) >= min_n:
            events.append({"kind": "statistical extreme", "conditions": [], "feature": colname,
                           "labels": [f"{label} {'≥' if hi else '≤'} {v:.2f} ({'top' if hi else 'bottom'} {min(pct, 1 - pct) * 100:.2f}%)"],
                           "value": v, "percentile": pct, "freq": float(m.sum() / nv), **_outcome_summary(idx, df, out, H)})
    events.sort(key=lambda e: e["freq"])
    return {"symbol": symbol, "tf": tf, "time": _unix(live["time"].iloc[-1]), "horizon": H,
            "active_conditions": len(active), "events": events[:25]}
