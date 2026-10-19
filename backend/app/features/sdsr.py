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

from bisect import bisect_left

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


def _sd_candidates(o, h, l, c, atr, lo: int, hi: int) -> list[dict]:
    """Supply / demand zones whose impulse completed by bar `hi` (formed = the bar it was confirmed)."""
    lo = max(MAX_BASE + 4, lo)
    body = np.abs(c - o)
    out: list[dict] = []
    a_prev = np.concatenate([[np.nan], atr[:-1]])
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero(body[lo: hi + 1] >= IMPULSE_BODY_ATR * a_prev[lo: hi + 1]) + lo
    for k in cand:
        a = atr[k - 1]
        if not np.isfinite(a) or a <= 0:
            continue
        up = c[k] > o[k]
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
            moved = c[k: min(k + 3, hi + 1)] - top
        else:
            top, bottom = float(h[sl].max()), float(np.minimum(o[sl], c[sl]).min())
            moved = bottom - c[k: min(k + 3, hi + 1)]
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
        out.append({"kind": kind, "pattern": pattern, "start": int(b0), "formed": int(formed),
                    "top": top, "bottom": bottom, "end": None, "touches": 0})
    out.sort(key=lambda z: z["formed"])
    return out


def supply_demand(df, from_i: int, to_i: int) -> list[dict]:
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    out = _sd_candidates(o, h, l, c, df["atr"].to_numpy(), from_i - SD_LOOKBACK, to_i)

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


def _sr_bands(swings: list[dict], a: float) -> list[tuple]:
    """Cluster swing prices within CLUSTER_ATR; a band needs >= 2 swings at least 10 bars apart.
    Returns (top, bottom, touches, flip, first swing bar, last swing bar)."""
    pts = sorted(swings, key=lambda s: s["price"])
    clusters: list[list[dict]] = []
    for s in pts:
        if clusters and s["price"] - clusters[-1][0]["price"] <= CLUSTER_ATR * a:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    out = []
    for cl in clusters:
        idx = sorted(s["i"] for s in cl)
        if len(cl) < 2 or idx[-1] - idx[0] < 10:   # two swings right next to each other are one reaction
            continue
        top, bottom = max(s["price"] for s in cl), min(s["price"] for s in cl)
        mid = (top + bottom) / 2
        if top - bottom < 0.2 * a:
            top, bottom = mid + 0.1 * a, mid - 0.1 * a
        out.append((float(top), float(bottom), len(cl), len({s["side"] for s in cl}) == 2, idx[0], idx[-1]))
    return out


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
    zones = []
    for top, bottom, touches, flip, first, last_i in _sr_bands(
            [s for s in swings if s["conf"] <= to_i and s["i"] >= to_i - SR_LOOKBACK], a):
        mid = (top + bottom) / 2
        sup = close >= mid
        # distance to the near edge; price already inside the band = triggered
        gap = (close - top if sup else bottom - close) / a
        p = _p_reach(down if sup else up, gap)
        if not np.isfinite(p):
            continue
        zones.append({"kind": "support" if sup else "resistance", "top": float(top), "bottom": float(bottom),
                      "start": first, "last": last_i, "touches": touches, "flip": flip,
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



# ------------------------------------------------------------------ backtestable conditions
SD_COLS = ("demand_retest", "supply_retest", "demand_retest_rev", "demand_retest_cont", "supply_retest_rev",
           "supply_retest_cont", "in_demand", "in_supply")
SR_COLS = ("support_test", "resistance_test", "support_test_strong", "resistance_test_strong",
           "support_flip_test", "resistance_flip_test")
SD_EXPIRY = SD_LOOKBACK  # a zone older than this no longer gives retest signals (as on the chart)
SR_REFRESH = 25          # rebuild S/R bands at least this often (old swings drop out of the lookback)
_VARIANT = {"DBR": "demand_retest_rev", "RBR": "demand_retest_cont", "RBD": "supply_retest_rev", "DBD": "supply_retest_cont"}


def _first(mask_of, start: int, n: int) -> int:
    """First index >= start where mask_of(slice) is True (searched in growing chunks); n if none."""
    step = 256
    while start < n:
        stop = min(n, start + step)
        hit = np.flatnonzero(mask_of(slice(start, stop)))
        if len(hit):
            return start + int(hit[0])
        start, step = stop, step * 4
    return n


def _sr_bands_fast(pts: list[tuple], a: float) -> list[tuple]:
    """Same bands as _sr_bands (top, bottom, touches, flip) from (price, is_high, bar) tuples.
    Plain Python: for ~100 swings it beats numpy's per-call overhead, and it runs on every new swing."""
    pts = sorted(pts)
    out, w = [], CLUSTER_ATR * a
    k, n = 0, len(pts)
    while k < n:
        anchor, high0, b0 = pts[k]
        j, nh, bmin, bmax = k, int(high0), b0, b0
        while j + 1 < n and pts[j + 1][0] - anchor <= w:
            j += 1
            x = pts[j]
            nh += x[1]
            if x[2] < bmin:
                bmin = x[2]
            elif x[2] > bmax:
                bmax = x[2]
        if j > k and bmax - bmin >= 10:
            top, bot, cnt = pts[j][0], anchor, j - k + 1
            if top - bot < 0.2 * a:
                mid = (top + bot) / 2
                top, bot = mid + 0.1 * a, mid - 0.1 * a
            out.append((top, bot, cnt, 0 < nh < cnt))
        k = j + 1
    return out


def add_sdsr(df, swings: list[dict]):
    """Per-bar flags for the backtester, using only what was known when each bar closed.

    demand_retest: first return into an unbroken demand zone that holds (close stays above its bottom);
    _rev = the zone was a reversal (DBR), _cont = continuation (RBR). Supply mirrors it.
    in_demand / in_supply: price is inside an unbroken zone.
    support_test: price arrives at the nearest support band below the previous close (the previous candle
    was clear of it) and closes above its bottom; _strong = band with >= 3 swings; _flip = band that has
    held both swing highs and lows. Resistance mirrors it."""
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = df["atr"].to_numpy(float)
    n = len(df)
    F = {k: np.zeros(n, bool) for k in SD_COLS + SR_COLS}

    # supply / demand: per zone, first touch and the candle that breaks it (zones are usable from formed + 1)
    for z in _sd_candidates(o, h, l, c, atr, 0, n - 1):
        top, bot, s0 = z["top"], z["bottom"], z["formed"] + 1
        lim = min(n, s0 + SD_EXPIRY)
        if z["kind"] == "demand":
            end = _first(lambda s: c[s] < bot, s0, lim)
            touch = _first(lambda s: l[s] <= top, s0, lim)
            inside = lambda s: l[s] <= top
            holds = touch < lim and c[touch] >= bot
            key, zone_col = "demand_retest", "in_demand"
        else:
            end = _first(lambda s: c[s] > top, s0, lim)
            touch = _first(lambda s: h[s] >= bot, s0, lim)
            inside = lambda s: h[s] >= bot
            holds = touch < lim and c[touch] <= top
            key, zone_col = "supply_retest", "in_supply"
        span = slice(s0, min(end + 1, lim))
        F[zone_col][span] |= inside(span)
        if touch <= end and holds:
            F[key][touch] = True
            if z["pattern"] in _VARIANT:
                F[_VARIANT[z["pattern"]]][touch] = True

    # support / resistance: bands rebuilt whenever a swing is confirmed (and every SR_REFRESH bars)
    hl, ll, cl, al = h.tolist(), l.tolist(), c.tolist(), atr.tolist()
    sw = sorted(swings, key=lambda s: s["conf"])
    conf = [s["conf"] for s in sw]
    pts_all = [(float(s["price"]), s["side"] == "high", int(s["i"])) for s in sw]
    first, nxt, bands, tops, built = 0, 0, [], [], -10**9
    for i in range(2, n):
        new = False
        while nxt < len(sw) and conf[nxt] <= i - 1:     # confirmed by the previous close
            nxt += 1
            new = True
        a = al[i - 1]
        if not (a > 0):
            continue
        if new or i - built >= SR_REFRESH:
            built = i
            while first < nxt and pts_all[first][2] < i - SR_LOOKBACK - 50:
                first += 1
            bands = _sr_bands_fast([x for x in pts_all[first:nxt] if x[2] >= i - SR_LOOKBACK], a)
            tops = [b[0] for b in bands]                   # bands come in ascending price order
        if not bands:
            continue
        pc = cl[i - 1]
        k = bisect_left(tops, pc)
        sup = bands[k - 1] if k > 0 else None              # nearest band entirely below the previous close
        if k < len(bands) and bands[k][1] <= pc:             # price is inside this band: the next one is resistance
            k += 1
        res = bands[k] if k < len(bands) else None
        if sup is not None and ll[i - 1] > sup[0] and ll[i] <= sup[0] and cl[i] >= sup[1]:
            F["support_test"][i] = True
            F["support_test_strong"][i] = sup[2] >= 3
            F["support_flip_test"][i] = sup[3]
        if res is not None and hl[i - 1] < res[1] and hl[i] >= res[1] and cl[i] <= res[0]:
            F["resistance_test"][i] = True
            F["resistance_test_strong"][i] = res[2] >= 3
            F["resistance_flip_test"][i] = res[3]
    for k, v in F.items():
        df[k] = v
    return df
