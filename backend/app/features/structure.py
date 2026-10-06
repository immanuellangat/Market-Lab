"""Price-action structure engine: swings, BOS / CHoCH / MSS, liquidity sweeps,
equal highs/lows, RSI divergence, Fair Value Gaps and Order Blocks.

Strictly causal: a swing at bar j is only *known* at bar j + k (k bars to its
right must have closed). Every feature written at bar i uses only bars <= i.
Besides per-bar feature columns, the engine returns drawable records (zones,
levels, events) used by the chart overlays and the key-zones view.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MAX_ACTIVE = 40
FVG_MIN_ATR = 0.10
FVG_EXPIRY = 300
OB_EXPIRY = 500
EQ_TOL_ATR = 0.10


@dataclass
class Records:
    swings: list = field(default_factory=list)      # {i, conf, price, kind: HH/LH/HL/LL/H/L}
    events: list = field(default_factory=list)      # {i, kind, level, ref_i}
    zones: list = field(default_factory=list)       # {kind, start, end, top, bottom, touched}
    pools: list = field(default_factory=list)       # {kind: eqh/eql, start, end, level}


def _swing_flags(h: pd.Series, l: pd.Series, k: int):
    left_max = h.shift(1).rolling(k).max()
    right_max = h[::-1].shift(1).rolling(k).max()[::-1]
    left_min = l.shift(1).rolling(k).min()
    right_min = l[::-1].shift(1).rolling(k).min()[::-1]
    is_sh = ((h > left_max) & (h >= right_max)).to_numpy()
    is_sl = ((l < left_min) & (l <= right_min)).to_numpy()
    return is_sh, is_sl


def add_structure(df: pd.DataFrame, k: int = 3) -> tuple[pd.DataFrame, Records]:
    n = len(df)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    a = df["atr"].bfill().to_numpy(float)
    r = df["rsi"].to_numpy(float)
    disp_b = df["displacement_bull"].to_numpy(bool)
    disp_s = df["displacement_bear"].to_numpy(bool)
    is_sh, is_sl = _swing_flags(df["high"], df["low"], k)

    F = {name: np.zeros(n, bool) for name in (
        "swing_high_conf", "swing_low_conf", "hh", "lh", "hl", "ll",
        "bos_bull", "bos_bear", "choch_bull", "choch_bear", "mss_bull", "mss_bear",
        "sweep_swing_high", "sweep_swing_low", "eqh_formed", "eql_formed",
        "sweep_eqh", "sweep_eql", "div_bull", "div_bear",
        "fvg_bull", "fvg_bear", "fvg_bull_disp", "fvg_bear_disp",
        "fvg_bull_retest", "fvg_bear_retest", "in_bull_fvg", "in_bear_fvg",
        "ob_bull_formed", "ob_bear_formed", "ob_bull_retest", "ob_bear_retest",
    )}
    ms_trend = np.zeros(n, np.int8)
    last_sh_arr = np.full(n, np.nan)
    last_sl_arr = np.full(n, np.nan)
    fvg_size = np.full(n, np.nan)
    sweep_depth = np.full(n, np.nan)  # how far beyond the level price reached, in ATR

    R = Records()
    trend = 0
    last_sh = last_sl = np.nan
    last_sh_i = last_sl_i = -1
    prev_sh = prev_sl = None  # (price, rsi) of previous confirmed swing for divergence
    sh_broken = sl_broken = True
    highs_unswept: list[list] = []  # [level, i]
    lows_unswept: list[list] = []
    eq_pools: list[dict] = []
    fvgs: list[dict] = []
    obs: list[dict] = []

    for i in range(n):
        ai = a[i] if a[i] > 0 else (h[i] - l[i]) or 1e-9

        # ---- 1. swing confirmations (swing at j becomes known at i)
        j = i - k
        if j >= k and is_sh[j]:
            F["swing_high_conf"][i] = True
            kind = "H"
            if not np.isnan(last_sh):
                kind = "HH" if h[j] > last_sh else "LH"
                F["hh" if kind == "HH" else "lh"][i] = True
            if prev_sh is not None and h[j] > prev_sh[0] and r[j] < prev_sh[1] - 2:
                F["div_bear"][i] = True
                R.events.append({"i": i, "kind": "div_bear", "level": h[j], "ref_i": j})
            for lv in highs_unswept:
                if abs(lv[0] - h[j]) <= EQ_TOL_ATR * ai:
                    F["eqh_formed"][i] = True
                    pool = {"kind": "eqh", "start": lv[1], "end": None, "level": max(lv[0], h[j])}
                    eq_pools.append(pool)
                    R.pools.append(pool)
                    break
            R.swings.append({"i": j, "conf": i, "price": h[j], "kind": kind, "side": "high"})
            prev_sh = (h[j], r[j])
            last_sh, last_sh_i, sh_broken = h[j], j, False
            highs_unswept.append([h[j], j])
            highs_unswept = highs_unswept[-MAX_ACTIVE:]
        if j >= k and is_sl[j]:
            F["swing_low_conf"][i] = True
            kind = "L"
            if not np.isnan(last_sl):
                kind = "HL" if l[j] > last_sl else "LL"
                F["hl" if kind == "HL" else "ll"][i] = True
            if prev_sl is not None and l[j] < prev_sl[0] and r[j] > prev_sl[1] + 2:
                F["div_bull"][i] = True
                R.events.append({"i": i, "kind": "div_bull", "level": l[j], "ref_i": j})
            for lv in lows_unswept:
                if abs(lv[0] - l[j]) <= EQ_TOL_ATR * ai:
                    F["eql_formed"][i] = True
                    pool = {"kind": "eql", "start": lv[1], "end": None, "level": min(lv[0], l[j])}
                    eq_pools.append(pool)
                    R.pools.append(pool)
                    break
            R.swings.append({"i": j, "conf": i, "price": l[j], "kind": kind, "side": "low"})
            prev_sl = (l[j], r[j])
            last_sl, last_sl_i, sl_broken = l[j], j, False
            lows_unswept.append([l[j], j])
            lows_unswept = lows_unswept[-MAX_ACTIVE:]

        # ---- 2. liquidity sweeps of swing levels (wick through, close back)
        if highs_unswept:
            taken = [lv for lv in highs_unswept if h[i] > lv[0]]
            if taken:
                highs_unswept = [lv for lv in highs_unswept if h[i] <= lv[0]]
                top = max(lv[0] for lv in taken)
                if c[i] < top:
                    F["sweep_swing_high"][i] = True
                    sweep_depth[i] = (h[i] - top) / ai
                    R.events.append({"i": i, "kind": "sweep_high", "level": top,
                                     "ref_i": max(taken, key=lambda x: x[0])[1]})
        if lows_unswept:
            taken = [lv for lv in lows_unswept if l[i] < lv[0]]
            if taken:
                lows_unswept = [lv for lv in lows_unswept if l[i] >= lv[0]]
                bot = min(lv[0] for lv in taken)
                if c[i] > bot:
                    F["sweep_swing_low"][i] = True
                    sweep_depth[i] = (bot - l[i]) / ai
                    R.events.append({"i": i, "kind": "sweep_low", "level": bot,
                                     "ref_i": min(taken, key=lambda x: x[0])[1]})
        if eq_pools:
            keep = []
            for p in eq_pools:
                if p["kind"] == "eqh" and h[i] > p["level"]:
                    p["end"] = i
                    if c[i] < p["level"]:
                        F["sweep_eqh"][i] = True
                elif p["kind"] == "eql" and l[i] < p["level"]:
                    p["end"] = i
                    if c[i] > p["level"]:
                        F["sweep_eql"][i] = True
                else:
                    keep.append(p)
            eq_pools = keep[-MAX_ACTIVE:]

        # ---- 3. structure breaks (on close)
        if not sh_broken and c[i] > last_sh:
            kind = "choch_bull" if trend == -1 else "bos_bull"
            F[kind][i] = True
            if kind == "choch_bull" and disp_b[max(0, i - 2): i + 1].any():
                F["mss_bull"][i] = True
            R.events.append({"i": i, "kind": kind, "level": last_sh, "ref_i": last_sh_i})
            trend, sh_broken = 1, True
            # Order block: last bearish candle in the leg that produced the break
            lo_i = max(last_sl_i, i - 60, 0)
            cand = [x for x in range(i - 1, lo_i - 1, -1) if c[x] < o[x]]
            x = cand[0] if cand else lo_i
            obs.append({"kind": "ob_bull", "start": x, "formed": i, "end": None,
                        "top": h[x], "bottom": l[x], "touched": False})
            R.zones.append(obs[-1])
            F["ob_bull_formed"][i] = True
        elif not sl_broken and c[i] < last_sl:
            kind = "choch_bear" if trend == 1 else "bos_bear"
            F[kind][i] = True
            if kind == "choch_bear" and disp_s[max(0, i - 2): i + 1].any():
                F["mss_bear"][i] = True
            R.events.append({"i": i, "kind": kind, "level": last_sl, "ref_i": last_sl_i})
            trend, sl_broken = -1, True
            lo_i = max(last_sh_i, i - 60, 0)
            cand = [x for x in range(i - 1, lo_i - 1, -1) if c[x] > o[x]]
            x = cand[0] if cand else lo_i
            obs.append({"kind": "ob_bear", "start": x, "formed": i, "end": None,
                        "top": h[x], "bottom": l[x], "touched": False})
            R.zones.append(obs[-1])
            F["ob_bear_formed"][i] = True
        ms_trend[i] = trend
        last_sh_arr[i] = last_sh
        last_sl_arr[i] = last_sl

        # ---- 4. track existing FVGs (formed before this bar)
        if fvgs:
            keep = []
            for z in fvgs:
                if z["kind"] == "fvg_bull":
                    if l[i] <= z["top"]:
                        F["in_bull_fvg"][i] = True
                        if not z["touched"]:
                            z["touched"] = True
                            if c[i] >= z["bottom"]:
                                F["fvg_bull_retest"][i] = True
                    dead = l[i] <= z["bottom"] or c[i] < z["bottom"]
                else:
                    if h[i] >= z["bottom"]:
                        F["in_bear_fvg"][i] = True
                        if not z["touched"]:
                            z["touched"] = True
                            if c[i] <= z["top"]:
                                F["fvg_bear_retest"][i] = True
                    dead = h[i] >= z["top"] or c[i] > z["top"]
                if dead or i - z["formed"] > FVG_EXPIRY:
                    z["end"] = i
                else:
                    keep.append(z)
            fvgs = keep[-MAX_ACTIVE:]

        # ---- 5. track order blocks
        if obs:
            keep = []
            for z in obs:
                if z["formed"] == i:
                    keep.append(z)
                    continue
                if z["kind"] == "ob_bull":
                    if l[i] <= z["top"] and not z["touched"]:
                        z["touched"], z["touch_i"] = True, i
                        if c[i] >= z["bottom"]:
                            F["ob_bull_retest"][i] = True
                    dead = c[i] < z["bottom"]
                else:
                    if h[i] >= z["bottom"] and not z["touched"]:
                        z["touched"], z["touch_i"] = True, i
                        if c[i] <= z["top"]:
                            F["ob_bear_retest"][i] = True
                    dead = c[i] > z["top"]
                if dead or i - z["formed"] > OB_EXPIRY:
                    z["end"] = i
                else:
                    keep.append(z)
            obs = keep[-MAX_ACTIVE:]

        # ---- 6. new FVGs completed at this bar (3-candle imbalance)
        if i >= 2:
            if l[i] > h[i - 2] and (l[i] - h[i - 2]) >= FVG_MIN_ATR * ai:
                z = {"kind": "fvg_bull", "start": i - 1, "formed": i, "end": None,
                     "top": l[i], "bottom": h[i - 2], "touched": False}
                fvgs.append(z)
                R.zones.append(z)
                F["fvg_bull"][i] = True
                F["fvg_bull_disp"][i] = bool(disp_b[i - 1])
                fvg_size[i] = (l[i] - h[i - 2]) / ai
            elif h[i] < l[i - 2] and (l[i - 2] - h[i]) >= FVG_MIN_ATR * ai:
                z = {"kind": "fvg_bear", "start": i - 1, "formed": i, "end": None,
                     "top": l[i - 2], "bottom": h[i], "touched": False}
                fvgs.append(z)
                R.zones.append(z)
                F["fvg_bear"][i] = True
                F["fvg_bear_disp"][i] = bool(disp_s[i - 1])
                fvg_size[i] = (l[i - 2] - h[i]) / ai

    out = pd.DataFrame(F, index=df.index)
    out["ms_trend"] = ms_trend
    out["last_sh"] = last_sh_arr
    out["last_sl"] = last_sl_arr
    rng = last_sh_arr - last_sl_arr
    with np.errstate(invalid="ignore", divide="ignore"):
        pd_pos = np.where(rng > 0, (c - last_sl_arr) / rng, np.nan)
    out["pd_pos"] = np.clip(pd_pos, -0.5, 1.5)
    out["fvg_size_atr"] = fvg_size
    out["sweep_depth_atr"] = sweep_depth
    out["dist_sh_atr"] = (last_sh_arr - c) / a
    out["dist_sl_atr"] = (c - last_sl_arr) / a
    return pd.concat([df, out], axis=1), R
