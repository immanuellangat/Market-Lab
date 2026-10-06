"""Key trade zones: where buys / sells have historically been taken and where
price has typically delivered (exit zones).

Everything is computed *as of* a bar index, so the same function powers the
live chart and the historical replay without leaking future information.
"""
from __future__ import annotations

import numpy as np

from ..features.engine import get_features
from .hypothesis import quick_stats

ZONE_SETUP = {
    "fvg_bull": ("fvg_bull_retest", "long", "Bullish FVG"),
    "ob_bull": ("ob_bull_retest", "long", "Bullish order block"),
    "fvg_bear": ("fvg_bear_retest", "short", "Bearish FVG"),
    "ob_bear": ("ob_bear_retest", "short", "Bearish order block"),
}
LOOKBACK = 600


def _liquidity(df, rec, i: int) -> dict:
    """Unswept liquidity above and below price as of bar i."""
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    lo_bound = max(0, i - LOOKBACK)
    above, below = [], []
    for s in rec.swings:
        if s["conf"] > i or s["i"] < lo_bound:
            continue
        if s["side"] == "high":
            seg = h[s["conf"] + 1: i + 1]
            if not len(seg) or seg.max() <= s["price"]:
                above.append({"price": float(s["price"]), "kind": "swing high", "time_i": s["i"]})
        else:
            seg = l[s["conf"] + 1: i + 1]
            if not len(seg) or seg.min() >= s["price"]:
                below.append({"price": float(s["price"]), "kind": "swing low", "time_i": s["i"]})
    for p in rec.pools:
        if p["start"] > i or (p["end"] is not None and p["end"] <= i) or p["start"] < lo_bound:
            continue
        (above if p["kind"] == "eqh" else below).append(
            {"price": float(p["level"]), "kind": "equal highs" if p["kind"] == "eqh" else "equal lows", "time_i": p["start"]})
    row = df.iloc[i]
    for col, kind in (("pdh", "previous day high"), ("pwh", "previous week high"), ("asia_high", "Asian high")):
        v = row.get(col)
        if v is not None and np.isfinite(v) and v > row["close"]:
            above.append({"price": float(v), "kind": kind, "time_i": i})
    for col, kind in (("pdl", "previous day low"), ("pwl", "previous week low"), ("asia_low", "Asian low")):
        v = row.get(col)
        if v is not None and np.isfinite(v) and v < row["close"]:
            below.append({"price": float(v), "kind": kind, "time_i": i})
    above.sort(key=lambda x: x["price"])
    below.sort(key=lambda x: -x["price"])
    return {"above": _merge(above), "below": _merge(below)}


def _merge(levels: list[dict]) -> list[dict]:
    """Combine levels at (almost) the same price: '1.1160 swing low + Asian low'."""
    out: list[dict] = []
    for lv in levels:
        if out and abs(lv["price"] - out[-1]["price"]) <= 1e-6 * max(abs(lv["price"]), 1):
            if lv["kind"] not in out[-1]["kind"]:
                out[-1]["kind"] += f" + {lv['kind']}"
        else:
            out.append(dict(lv))
    return out


def key_zones(symbol: str, tf: str, at_index: int | None = None, max_per_side: int = 3) -> dict:
    df, rec = get_features(symbol, tf)
    i = len(df) - 1 if at_index is None else max(0, min(at_index, len(df) - 1))
    row = df.iloc[i]
    close, atr = float(row["close"]), float(row["atr"])
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    liq = _liquidity(df, rec, i)
    recent_sweep_low = bool(df["sweep_swing_low"].iloc[max(0, i - 20): i + 1].any())
    recent_sweep_high = bool(df["sweep_swing_high"].iloc[max(0, i - 20): i + 1].any())
    htf_col = next((c for c in ("h4_ms_trend", "d1_ms_trend", "h1_ms_trend") if c in df.columns), None)

    buys, sells = [], []
    for z in rec.zones:
        if z["formed"] > i or (z["end"] is not None and z["end"] <= i) or z["formed"] < i - LOOKBACK:
            continue
        setup_cond, direction, label = ZONE_SETUP[z["kind"]]
        seg_l, seg_h = l[z["formed"] + 1: i + 1], h[z["formed"] + 1: i + 1]
        if direction == "long":
            if z["bottom"] > close + 0.25 * atr:
                continue
            fresh = not len(seg_l) or seg_l.min() > z["top"]
            entry, stop = z["top"], z["bottom"] - 0.25 * atr
            targets = [x for x in liq["above"] if x["price"] > entry][:3]
            conf = []
            if row["ms_trend"] == 1: conf.append("structure bullish")
            if htf_col and row[htf_col] == 1: conf.append(f"{htf_col[:2].upper()} structure bullish")
            if np.isfinite(row["last_sh"]) and np.isfinite(row["last_sl"]) and (z["top"] + z["bottom"]) / 2 < (row["last_sh"] + row["last_sl"]) / 2:
                conf.append("in discount")
            if recent_sweep_low: conf.append("sell-side liquidity recently swept")
        else:
            if z["top"] < close - 0.25 * atr:
                continue
            fresh = not len(seg_h) or seg_h.max() < z["bottom"]
            entry, stop = z["bottom"], z["top"] + 0.25 * atr
            targets = [x for x in liq["below"] if x["price"] < entry][:3]
            conf = []
            if row["ms_trend"] == -1: conf.append("structure bearish")
            if htf_col and row[htf_col] == -1: conf.append(f"{htf_col[:2].upper()} structure bearish")
            if np.isfinite(row["last_sh"]) and np.isfinite(row["last_sl"]) and (z["top"] + z["bottom"]) / 2 > (row["last_sh"] + row["last_sl"]) / 2:
                conf.append("in premium")
            if recent_sweep_high: conf.append("buy-side liquidity recently swept")
        if fresh: conf.append("untested (fresh)")
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        stats = quick_stats(symbol, tf, [setup_cond], direction, until_index=None if at_index is None else i)
        sgn = 1 if direction == "long" else -1
        stat_targets = []
        if stats.get("mfe_med_atr"):
            stat_targets.append({"price": entry + sgn * stats["mfe_med_atr"] * atr, "kind": "median historical move"})
            stat_targets.append({"price": entry + sgn * stats["mfe_p75_atr"] * atr, "kind": "75th-pct historical move"})
        item = {
            "kind": z["kind"], "label": label, "direction": direction,
            "top": float(z["top"]), "bottom": float(z["bottom"]), "start_i": int(z["start"]),
            "entry": float(entry), "stop": float(stop), "fresh": bool(fresh),
            "distance_atr": float(abs(close - entry) / atr) if atr > 0 else None,
            "targets": [dict(t, rr=float(abs(t["price"] - entry) / risk)) for t in targets],
            "stat_targets": [dict(t, rr=float(abs(t["price"] - entry) / risk)) for t in stat_targets],
            "confluence": conf, "stats": stats, "setup_condition": setup_cond,
        }
        (buys if direction == "long" else sells).append(item)

    rank = lambda z: (-len(z["confluence"]), z["distance_atr"] or 0)
    buys.sort(key=rank)
    sells.sort(key=rank)
    return {"index": i, "time": int(row["time"].timestamp()), "close": close, "atr": atr,
            "buy_zones": buys[:max_per_side], "sell_zones": sells[:max_per_side],
            "liquidity": {"above": liq["above"][:6], "below": liq["below"][:6]},
            "disclaimer": "Zones summarise historical behaviour. They are research aids, not trade instructions."}
