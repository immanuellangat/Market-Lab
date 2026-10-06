"""Stop / target rules shared by the optimiser, the pooled study and live signals.

At signal time only the signal candle and earlier data are used ("spec").
The actual levels are fixed once the entry price is known (next candle open).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .simulate import Plan

LIQ_COLS = {"long": ("last_sh", "pdh", "pwh", "asia_high"), "short": ("last_sl", "pdl", "pwl", "asia_low")}


def signal_spec(plan: Plan, df: pd.DataFrame, i: int, direction: str) -> dict:
    a = float(df["atr"].iloc[i])
    long = direction == "long"
    spec = {"plan": plan.key, "manage": plan.manage, "atr": a, "direction": direction}
    if plan.stop == "struct":
        lo = max(0, i - 9)
        raw = float(df["low"].iloc[lo: i + 1].min() - 0.1 * a) if long else float(df["high"].iloc[lo: i + 1].max() + 0.1 * a)
        spec["stop"] = {"mode": "struct", "level": raw}
    else:
        spec["stop"] = {"mode": "atr", "k": 1.5 if plan.stop == "atr1.5" else 1.0}
    if plan.target == "none":
        spec["target"] = {"mode": "none"}
    elif plan.target == "liq":
        lv = [float(df[c].iloc[i]) for c in LIQ_COLS[direction] if c in df.columns]
        spec["target"] = {"mode": "liq", "levels": [v for v in lv if np.isfinite(v)]}
    else:
        spec["target"] = {"mode": "r", "k": float(plan.target[1:])}
    return spec


def levels_for_entry(spec: dict, entry: float) -> tuple[float, float | None]:
    long = spec["direction"] == "long"
    sg = 1 if long else -1
    a = spec["atr"]
    st = spec["stop"]
    if st["mode"] == "struct":
        d = float(np.clip((entry - st["level"]) * sg, 0.5 * a, 3 * a))
    else:
        d = st["k"] * a
    stop = entry - sg * d
    t = spec["target"]
    if t["mode"] == "none":
        return stop, None
    if t["mode"] == "r":
        return stop, entry + sg * t["k"] * d
    cand = [v for v in t["levels"] if (v - entry) * sg > 1.0 * d]  # liquidity at least 1R away
    if not cand:
        return stop, entry + sg * 2 * d
    return stop, (min(cand) if long else max(cand))
