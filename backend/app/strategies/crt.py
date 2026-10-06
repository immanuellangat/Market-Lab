"""Candle Range Theory (CRT).

Rules (agreed with the user on 2026-10-06; fixed before any test was run):
  * Range: the high/low of a finished higher-timeframe candle A (H1 or H4).
  * Sweep: the next candle B trades beyond one side of A's range and closes back inside it,
    without touching the other side.
  * Direction: against the sweep (swept high -> short, swept low -> long).
  * Entry - two variants, tested side by side:
      "close": at the open of the candle after B, no confirmation;
      "ltf":   during the candle after B, wait for a lower-timeframe (M5 for H1, M15 for H4)
               market-structure shift: a close beyond the last confirmed swing that formed
               since B opened; enter at the next lower-timeframe open.
      "limit50": a limit order at 50% of B, valid during the next HTF candle.
  * Stop: beyond B's sweep wick (+0.1 x ATR of the higher timeframe).
  * Target: the opposite side of A's range ("full", min 1R) or its midpoint ("mid", min 0.5R).
  * The setup is dropped if, before entry, price trades beyond the sweep wick (failed sweep)
    or reaches the target, or if the target is less than MIN_RR x risk away.

Trades are simulated on the lower-timeframe path with the shared simulator
(stop before target inside a candle, spread deducted).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DEFAULT_HORIZON, TIMEFRAMES
from ..data.store import load_bars
from ..execution.simulate import simulate
from ..research.hypothesis import point_size
from ..research.outcomes import spread_price

NAME = "CRT"
HTFS = ("H1", "H4")
LTF_OF = {"H1": "M5", "H4": "M15"}
MODES = ("close", "ltf", "limit50")
TARGETS = ("full", "mid")
MIN_RR = {"full": 1.0, "mid": 0.5}
STOP_BUFFER_ATR = 0.1
SWING_K = 2
ATR_N = 14


def describe(htf: str, mode: str) -> str:
    entry = ("enter at the next candle open" if mode == "close" else
             f"enter after a {LTF_OF[htf]} structure shift during the next {htf} candle")
    return (f"CRT {htf}: previous {htf} candle's range swept by the next candle, which closes back inside; "
            f"{entry}; stop beyond the sweep wick; target the opposite side of the range.")


def _unix(times: pd.Series) -> np.ndarray:
    return times.dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")


def _atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = ATR_N) -> np.ndarray:
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, min_periods=n, adjust=False).mean().to_numpy()


def find_setups(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Indices of candle B for short (swept high) and long (swept low) CRT setups."""
    ah = np.concatenate([[np.nan], h[:-1]])
    al = np.concatenate([[np.nan], l[:-1]])
    short = (h > ah) & (c < ah) & (c > al) & (l > al)
    long = (l < al) & (c > al) & (c < ah) & (h < ah)
    return np.flatnonzero(short), np.flatnonzero(long)


def ltf_swings(h: np.ndarray, l: np.ndarray, k: int = SWING_K) -> dict[str, np.ndarray]:
    """Last confirmed swing high/low (level and the bar it formed on), as known at each bar's close.

    A swing at bar j needs k bars on each side, so it only becomes known at bar j + k."""
    n = len(h)
    is_sh = np.ones(n, bool)
    is_sl = np.ones(n, bool)
    for d in range(1, k + 1):
        is_sh[:d] = is_sl[:d] = False
        is_sh[n - d:] = is_sl[n - d:] = False
        is_sh[d:] &= h[d:] > h[:-d]
        is_sl[d:] &= l[d:] < l[:-d]
        is_sh[:-d] &= h[:-d] >= h[d:]
        is_sl[:-d] &= l[:-d] <= l[d:]
    out = {}
    for name, flag, src in (("sh", is_sh, h), ("sl", is_sl, l)):
        idx = pd.Series(np.where(flag, np.arange(n, dtype=float), np.nan)).shift(k).ffill().to_numpy()
        out[name + "_idx"] = np.nan_to_num(idx, nan=-1).astype(np.int64)
        out[name] = pd.Series(np.where(flag, src, np.nan)).shift(k).ffill().to_numpy()
    return out


class CRT:
    """All CRT trades for one market and higher timeframe (both directions)."""

    def __init__(self, symbol: str, htf: str, hdf: pd.DataFrame | None = None, ldf: pd.DataFrame | None = None):
        self.symbol, self.htf, self.ltf = symbol, htf, LTF_OF[htf]
        hdf = load_bars(symbol, htf) if hdf is None else hdf
        ldf = load_bars(symbol, self.ltf) if ldf is None else ldf
        self.hdf = hdf.reset_index(drop=True)
        self.ht = _unix(self.hdf["time"])
        self.hh, self.hl, self.hc = (self.hdf[k].to_numpy(float) for k in ("high", "low", "close"))
        self.hatr = _atr(self.hh, self.hl, self.hc)
        self.lt = _unix(ldf["time"])
        self.lo, self.lh, self.ll, self.lc = (ldf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.lspread = spread_price(ldf, point_size(symbol))
        self.sw = ltf_swings(self.lh, self.ll)
        self.tf_s = TIMEFRAMES[htf] * 60
        self.horizon = DEFAULT_HORIZON[htf] * TIMEFRAMES[htf] // TIMEFRAMES[self.ltf]

    def setups(self, start: int = 1) -> list[tuple[int, str]]:
        s, lg = find_setups(self.hh, self.hl, self.hc)
        ev = [(int(i), "short") for i in s if i >= start] + [(int(i), "long") for i in lg if i >= start]
        return sorted(ev)

    def entry(self, i: int, direction: str, mode: str, target_mode: str = "full") -> dict:
        """Entry for the setup whose sweep candle B is HTF bar i.

        entry_j == len(ltf) means the entry is the still-forming lower-timeframe candle (live use)."""
        short = direction == "short"
        sweep = self.hh[i] if short else self.hl[i]
        target = self.hl[i - 1] if short else self.hh[i - 1]
        if target_mode == "mid":
            target = (self.hh[i - 1] + self.hl[i - 1]) / 2
        a = self.hatr[i]
        if not np.isfinite(a):
            return {"i": i, "direction": direction, "signal_time": int(self.ht[i] + self.tf_s), "skip": "warm-up"}
        stop = sweep + STOP_BUFFER_ATR * a if short else sweep - STOP_BUFFER_ATR * a
        t_close = self.ht[i] + self.tf_s
        j0 = int(np.searchsorted(self.lt, t_close))
        base = {"i": i, "direction": direction, "signal_time": int(t_close), "sweep": float(sweep),
                "stop": float(stop), "target": float(target), "range": [float(self.hl[i - 1]), float(self.hh[i - 1])]}
        limit = None
        if mode == "close":
            entry_j = j0
        elif mode == "limit50":
            # limit order at 50% of the sweep candle, valid during the next HTF candle
            limit = (self.hh[i] + self.hl[i]) / 2
            jw = int(np.searchsorted(self.lt, t_close + self.tf_s))
            entry_j = None
            for j in range(j0, jw):
                if (self.lh[j] > sweep) if short else (self.ll[j] < sweep):
                    return base | {"skip": "failed sweep"}
                if (self.ll[j] <= target) if short else (self.lh[j] >= target):
                    return base | {"skip": "target reached before entry"}
                if (self.lh[j] >= limit) if short else (self.ll[j] <= limit):
                    entry_j = j
                    limit = max(limit, self.lo[j]) if short else min(limit, self.lo[j])  # gap through: open fill
                    break
            if entry_j is None:
                return base | {"skip": "waiting" if jw >= len(self.lt) else "limit not filled"}
        else:
            jb = int(np.searchsorted(self.lt, self.ht[i]))
            jw = int(np.searchsorted(self.lt, t_close + self.tf_s))
            entry_j = None
            for j in range(j0, jw):
                if (self.lh[j] > sweep) if short else (self.ll[j] < sweep):
                    return base | {"skip": "failed sweep"}
                if (self.ll[j] <= target) if short else (self.lh[j] >= target):
                    return base | {"skip": "target reached before entry"}
                lvl, idx = (self.sw["sl"][j], self.sw["sl_idx"][j]) if short else (self.sw["sh"][j], self.sw["sh_idx"][j])
                if idx >= jb and np.isfinite(lvl) and ((self.lc[j] < lvl) if short else (self.lc[j] > lvl)):
                    entry_j = j + 1
                    break
            if entry_j is None:
                if jw >= len(self.lt):  # the window has not finished yet (live)
                    return base | {"skip": "waiting"}
                return base | {"skip": "no shift"}
        if entry_j >= len(self.lt):
            # live: entry is the forming candle; use the last close as the expected price
            return base | self._levels(float(self.lc[-1]), stop, target, short, target_mode) | {"entry_j": entry_j, "pending": True,
                                                                                    "cost": float(self.lspread[-1])}
        entry = float(self.lo[entry_j]) if limit is None else float(limit)
        return base | self._levels(entry, stop, target, short, target_mode) | {"entry_j": entry_j, "entry_time": int(self.lt[entry_j]),
                                                                  "cost": float(self.lspread[max(entry_j - 1, 0)])}

    @staticmethod
    def _levels(entry: float, stop: float, target: float, short: bool, target_mode: str = "full") -> dict:
        risk = (stop - entry) if short else (entry - stop)
        reward = (entry - target) if short else (target - entry)
        if risk <= 0:
            return {"entry": entry, "skip": "price beyond stop"}
        if reward / risk < MIN_RR[target_mode]:
            return {"entry": entry, "skip": f"target under {MIN_RR[target_mode]:g}R"}
        return {"entry": entry, "rr": float(reward / risk)}

    def trades(self, mode: str, start: int = 1, target_mode: str = "full") -> list[dict]:
        out = []
        for i, d in self.setups(start):
            if i + 1 >= len(self.ht) and mode == "close":
                continue
            e = self.entry(i, d, mode, target_mode)
            if "skip" not in e and not e.get("pending"):
                j = e["entry_j"]
                path = slice(j, j + self.horizon)
                r, held, why = simulate(self.lh[path], self.ll[path], self.lc[path], d, e["entry"], e["stop"],
                                        e["target"], "none", float(self.hatr[i]), e["cost"])
                if held == 0 or (why == "time" and held < self.horizon):
                    e["skip"] = "still open"
                else:
                    cost_r = e["cost"] / abs(e["entry"] - e["stop"])
                    e.update(r=float(r), gross=float(r + cost_r), exit=why, held=int(held),
                             exit_time=int(self.lt[j + held - 1] + TIMEFRAMES[self.ltf] * 60))
            out.append(e)
        return out
