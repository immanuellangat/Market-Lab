"""Candle-by-candle trade simulation for entry/exit plans.

Conservative rules (same as the outcome matrix):
  * within a candle the stop is checked before anything favourable happens,
    so a candle touching both stop and target counts as a stop-out;
  * stop adjustments (break-even, trailing) take effect from the NEXT candle;
  * a trade still open at the time limit is closed at that candle's close.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit


@dataclass(frozen=True)
class Plan:
    entry: str = "next"      # "next": next candle open | "ltf": wait for lower-timeframe structure break
    stop: str = "atr1"       # "atr1" | "atr1.5" | "struct" (beyond recent swing) | "ltf_swing"
    target: str = "r2"       # "r1.5" | "r2" | "r3" | "liq" (nearest untaken liquidity) | "none"
    manage: str = "none"     # "none" | "be" (break-even at 1R) | "partial" (half at 1R + BE) | "trail"

    @property
    def key(self) -> str:
        return f"{self.entry}|{self.stop}|{self.target}|{self.manage}"

    @staticmethod
    def from_key(k: str) -> "Plan":
        e, s, t, m = k.split("|")
        return Plan(e, s, t, m)

    def describe(self) -> str:
        e = {"next": "Enter at next candle open", "ltf": "Wait for a lower-timeframe break of structure, then enter"}[self.entry]
        s = {"atr1": "stop 1×ATR", "atr1.5": "stop 1.5×ATR", "struct": "stop beyond the recent swing",
             "ltf_swing": "stop beyond the last confirmed lower-timeframe swing",
             "ltf_pullback": "stop beyond the pullback low/high made since the signal"}[self.stop]
        t = {"r1.5": "target 1.5R", "r2": "target 2R", "r3": "target 3R", "liq": "target the nearest untaken liquidity",
             "none": "no fixed target"}[self.target]
        m = {"none": "", "be": ", stop to break-even at +1R", "partial": ", bank half at +1R and move stop to break-even",
             "trail": ", trail the stop 1.5×ATR behind price after +1R"}[self.manage]
        return f"{e}; {s}; {t}{m}."


BASELINE = Plan()


MANAGE_CODE = {"none": 0, "be": 1, "partial": 2, "trail": 3}
REASONS = ("stop", "breakeven", "trail", "target", "time", "invalid")


@njit(cache=True)
def _sim(h, l, c, long, entry, stop, target, manage, atr, cost):
    risk = (entry - stop) if long else (stop - entry)
    if not (risk > 0):
        return np.nan, 0, 5
    one_r = entry + risk if long else entry - risk
    has_target = not np.isnan(target)
    cur_stop = stop
    pos = 1.0
    realized = 0.0
    moved_be = False
    partial_done = False
    trailing = False
    best = entry
    trail_dist = 1.5 * atr
    n = h.shape[0]
    for k in range(n):
        hi = h[k]
        lo = l[k]
        hit_stop = (lo <= cur_stop) if long else (hi >= cur_stop)
        if hit_stop:  # stop checked first: conservative
            r = ((cur_stop - entry) if long else (entry - cur_stop)) / risk
            code = 0 if r < -1e-9 else (1 if abs(r) <= 1e-9 else 2)
            return realized + pos * r - cost / risk, k + 1, code
        fav = hi if long else lo
        reached_1r = (fav >= one_r) if long else (fav <= one_r)
        if manage == 2 and (not partial_done) and reached_1r:
            realized += 0.5
            pos = 0.5
            partial_done = True
        if has_target:
            hit_t = (fav >= target) if long else (fav <= target)
            if hit_t:
                r = ((target - entry) if long else (entry - target)) / risk
                return realized + pos * r - cost / risk, k + 1, 3
        if reached_1r and (manage == 1 or manage == 2) and not moved_be:
            cur_stop = entry
            moved_be = True
        if manage == 3:
            if reached_1r:
                trailing = True
            if trailing:
                if long:
                    best = max(best, hi)
                    cur_stop = max(cur_stop, best - trail_dist)
                else:
                    best = min(best, lo)
                    cur_stop = min(cur_stop, best + trail_dist)
    if n == 0:
        return np.nan, 0, 5
    last = c[n - 1]
    r = ((last - entry) if long else (entry - last)) / risk
    return realized + pos * r - cost / risk, n, 4


def simulate(h: np.ndarray, l: np.ndarray, c: np.ndarray, direction: str, entry: float, stop: float,
             target: float | None, manage: str, atr: float, cost: float = 0.0) -> tuple[float, int, str]:
    """Simulate one trade over the candles that follow entry (index 0 = entry candle, entered at its open).

    Returns (R multiple after costs, candles held, exit reason)."""
    r, held, code = _sim(np.ascontiguousarray(h, np.float64), np.ascontiguousarray(l, np.float64),
                         np.ascontiguousarray(c, np.float64), direction == "long", float(entry), float(stop),
                         np.nan if target is None else float(target), MANAGE_CODE[manage], float(atr), float(cost))
    return float(r), int(held), REASONS[code]
