"""Supply / demand zones and support / resistance zones for the chart.

Supply / demand: a base of 1-3 small-bodied candles followed by an impulsive move
away (>= IMPULSE_ATR within 3 candles). Demand = base before a rally, supply =
base before a drop. Named by the move into the base: RBR / DBR (demand),
DBD / RBD (supply). The zone is the base: wick extreme to body edge.

Support / resistance: prices where several confirmed swing highs / lows cluster
(within CLUSTER_ATR). Below price = support, above = resistance; a zone holding
both swing highs and swing lows has flipped role at least once. Bands are ranked by
how likely price is to reach them within the timeframe's horizon, measured from
how far price has actually travelled (in ATR) over that many bars in the past.

Everything is computed as of bar `to_i` (nothing known only later), so replay
shows exactly what a trader could have drawn at that moment.
"""
from __future__ import annotations

import numpy as np

BASE_BODY_ATR = 0.5      # a base candle's body is at most this many ATR
IMPULSE_BODY_ATR = 0.8   # the first candle leaving the base
IMPULSE_ATR = 1.5        # distance price must travel from the zone within 3 candles
MAX_BASE = 3
SD_LOOKBACK = 1500       # bars before the view start that may still hold an active zone
SD_ACTIVE_PER_KIND = 6
SR_LOOKBACK = 500        # swings older than this (bars before to_i) are ignored
CLUSTER_ATR = 0.5
SR_PER_SIDE = 4
SR_MIN_GAP_ATR = 1.0     # chosen S/R bands on one side are at least this far apart
REACH_SAMPLE = 20000     # past bars used to estimate how likely price is to reach a band


def supply_demand(df, from_i: int, to_i: int) -> list[dict]:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = df["atr"].to_numpy()
    lo = max(MAX_BASE + 4, from_i - SD_LOOKBACK)
    body = np.abs(c - o)
    out: list[dict] = []
    for k in range(lo, to_i + 1):
        a = atr[k - 1]
        if not np.isfinite(a) or a <= 0:
            continue
        up = c[k] - o[k] >= IMPULSE_BODY_ATR * a
        down = o[k] - c[k] >= IMPULSE_BODY_ATR * a
        if not (up or down):
            continue
        b1 = k - 1
        b0 = b1
        while b0 > k - 1 - MAX_BASE and body[b0] <= BASE_BODY_ATR * a:
            b0 -= 1
        b0 += 1
        if b0 > b1:       # the candle before the impulse is not a base
            continue
        sl = slice(b0, b1 + 1)
        if up:
            top, bottom = float(np.maximum(o[sl], c[sl]).max()), float(l[sl].min())
            moved = c[k: min(k + 3, to_i + 1)] - top
        else:
            top, bottom = float(h[sl].max()), float(np.minimum(o[sl], c[sl]).min())
            moved = bottom - c[k: min(k + 3, to_i + 1)]
        hit = np.nonzero(moved >= IMPULSE_ATR * a)[0]
        if not len(hit) or top <= bottom:
            continue
        formed = k + int(hit[0])
        leg = c[b0 - 1] - c[b0 - 4]
        into = "R" if leg > 0.5 * a else "D" if leg < -0.5 * a else None
        kind = "demand" if up else "supply"
        pattern = (f"{into}B{'R' if up else 'D'}" if into else "base")
        # skip a zone that mostly overlaps a same-kind zone still unbroken when this one formed
        def alive(z):
            seg = c[z["formed"] + 1: formed + 1]
            return not (seg < z["bottom"]).any() if z["kind"] == "demand" else not (seg > z["top"]).any()
        if any(z["kind"] == kind and min(top, z["top"]) - max(bottom, z["bottom"]) > 0.5 * (top - bottom) and alive(z)
               for z in out[-20:]):
            continue
        out.append({"kind": kind, "pattern": pattern, "start": b0, "formed": formed,
                    "top": top, "bottom": bottom, "end": None, "touches": 0})

    # status as of to_i: touched = price came back into the zone; broken = a close beyond its far side
    for z in out:
        seg = slice(z["formed"] + 1, to_i + 1)
        if z["kind"] == "demand":
            inside = l[seg] <= z["top"]
            brk = np.nonzero(c[seg] < z["bottom"])[0]
        else:
            inside = h[seg] >= z["bottom"]
            brk = np.nonzero(c[seg] > z["top"])[0]
        if len(brk):
            z["end"] = z["formed"] + 1 + int(brk[0])
            inside = inside[: int(brk[0]) + 1]
        z["touches"] = int(inside[0]) + int(np.sum(inside[1:] & ~inside[:-1])) if len(inside) else 0
        z["status"] = "broken" if z["end"] is not None else ("fresh" if z["touches"] == 0 else "tested")

    out = [z for z in out if z["end"] is None or z["end"] >= from_i]
    # keep the chart readable: the most recent unbroken zones per kind
    keep, count = [], {"demand": 0, "supply": 0}
    for z in reversed(out):
        if z["end"] is None:
            if count[z["kind"]] >= SD_ACTIVE_PER_KIND:
                continue
            count[z["kind"]] += 1
        keep.append(z)
    return keep[::-1][-300:]


def _reach_table(df, to_i: int, horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """How far price travelled up / down (in ATR) within `horizon` bars after each past bar, using only
    windows that had finished by to_i. Sorted, so P(reach d ATR) = share of values >= d."""
    lo = max(0, to_i - REACH_SAMPLE - horizon)
    h, l = df["high"].to_numpy()[lo: to_i + 1], df["low"].to_numpy()[lo: to_i + 1]
    c, a = df["close"].to_numpy()[lo: to_i + 1], df["atr"].to_numpy()[lo: to_i + 1]
    if len(c) <= horizon + 10:
        return np.array([]), np.array([])
    wh = np.lib.stride_tricks.sliding_window_view(h[1:], horizon).max(axis=1)
    wl = np.lib.stride_tricks.sliding_window_view(l[1:], horizon).min(axis=1)
    base, atr = c[: len(wh)], a[: len(wh)]
    ok = np.isfinite(atr) & (atr > 0)
    return np.sort((wh - base)[ok] / atr[ok]), np.sort((base - wl)[ok] / atr[ok])


def _p_reach(table: np.ndarray, d: float) -> float:
    if d <= 0:
        return 1.0
    return float(1 - np.searchsorted(table, d, side="left") / len(table)) if len(table) else float("nan")


def support_resistance(df, swings: list[dict], to_i: int, horizon: int = 48) -> list[dict]:
    """S/R bands ranked by how likely price is to reach them within `horizon` bars (empirical, this market)."""
    a = float(df["atr"].iloc[to_i])
    close = float(df["close"].iloc[to_i])
    if not np.isfinite(a) or a <= 0:
        return []
    up, down = _reach_table(df, to_i, horizon)
    pts = sorted((s for s in swings if s["conf"] <= to_i and s["i"] >= to_i - SR_LOOKBACK),
                 key=lambda s: s["price"])
    clusters: list[list[dict]] = []
    for s in pts:
        if clusters and s["price"] - clusters[-1][0]["price"] <= CLUSTER_ATR * a:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    zones = []
    for cl in clusters:
        idx = sorted(s["i"] for s in cl)
        if len(cl) < 2 or idx[-1] - idx[0] < 10:   # two swings right next to each other are one reaction
            continue
        top, bottom = max(s["price"] for s in cl), min(s["price"] for s in cl)
        mid = (top + bottom) / 2
        if top - bottom < 0.2 * a:
            top, bottom = mid + 0.1 * a, mid - 0.1 * a
        sup = close >= mid
        # distance to the near edge; price already inside the band = triggered
        gap = (close - top if sup else bottom - close) / a
        p = _p_reach(down if sup else up, gap)
        if not np.isfinite(p):
            continue
        zones.append({"kind": "support" if sup else "resistance", "top": float(top), "bottom": float(bottom),
                      "start": idx[0], "last": idx[-1], "touches": len(cl), "flip": len({s["side"] for s in cl}) == 2,
                      "distance_atr": float(max(gap, 0.0)), "inside": bool(gap <= 0),
                      "p_reach": p, "horizon": horizon, "sample": int(len(up))})
    out = []
    for kind in ("support", "resistance"):
        side = [z for z in zones if z["kind"] == kind]
        # most likely to be reached first; among equals, the one tested most often
        side.sort(key=lambda z: (-z["p_reach"], -z["touches"]))
        picked: list[dict] = []
        for z in side:   # a band right next to an already chosen one is the same level
            mid = (z["top"] + z["bottom"]) / 2
            if all(abs(mid - (p["top"] + p["bottom"]) / 2) >= SR_MIN_GAP_ATR * a for p in picked):
                picked.append(z)
            if len(picked) == SR_PER_SIDE:
                break
        out += picked
    return out
