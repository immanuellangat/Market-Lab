"""Entry / exit optimisation for a setup, without leaking into the result.

For every occurrence of the setup we build trades under ~60 execution plans
(entry method x stop x target x trade management). The best plan is chosen
using the IN-SAMPLE period only; its out-of-sample result is then reported
next to the baseline plan's out-of-sample result. If the "improvement" does
not survive on unseen data, the report says so.

Lower-timeframe entries: after a signal on the setup's timeframe, wait up to
WAIT_BARS of those candles for a break of structure in the trade direction on
the lower timeframe, enter at the next lower-timeframe open, and place the stop
beyond the lower-timeframe swing. If price first breaks the setup's structural
invalidation level, or no confirmation comes, the trade is skipped (0R).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DEFAULT_HORIZON, TIMEFRAMES
from ..data.store import bars_path
from ..features.engine import get_features
from ..research.conditions import evaluate, label_of
from ..research.hypothesis import WARMUP, dedupe, point_size, split_index
from ..research.outcomes import spread_price
from ..research.stats import summarize
from .simulate import BASELINE, Plan, simulate

LTF_OF = {"D1": "H1", "H4": "M15", "H1": "M5", "M15": "M5"}
WAIT_BARS = 3
MAX_LTF_PATH = 2000
SHRINK = 30
TARGETS = ("r1.5", "r2", "r3", "liq")
MANAGE = ("none", "be", "partial")


def plan_grid(has_ltf: bool) -> list[Plan]:
    plans = [Plan("next", s, t, m) for s in ("atr1", "atr1.5", "struct") for t in TARGETS for m in MANAGE]
    plans += [Plan("next", s, "none", "trail") for s in ("atr1", "atr1.5", "struct")]
    if has_ltf:
        plans += [Plan("ltf", s, t, m) for s in ("ltf_pullback", "ltf_swing", "atr1") for t in TARGETS for m in MANAGE]
        plans += [Plan("ltf", s, "none", "trail") for s in ("ltf_pullback", "ltf_swing", "atr1")]
    return plans


class ExecutionLab:
    def __init__(self, symbol: str, tf: str, conditions: list[str], direction: str, cooldown: int = 5):
        self.symbol, self.tf, self.conditions, self.direction, self.cooldown = symbol, tf, conditions, direction, cooldown
        self.long = direction == "long"
        df, _ = get_features(symbol, tf)  # serve cached tables; never rebuild mid-study
        self.df = df
        n = len(df)
        self.H = DEFAULT_HORIZON[tf]
        mask = evaluate(df, conditions)
        mask[:WARMUP] = False
        mask[n - 2:] = False
        self.events = dedupe(np.flatnonzero(mask), cooldown)
        self.sp = split_index(df)
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.atr = df["atr"].to_numpy(float)
        self.spread = spread_price(df, point_size(symbol))
        self.levels = [df[k].to_numpy(float) for k in ("last_sh", "pdh", "pwh", "asia_high")] if self.long else \
                      [df[k].to_numpy(float) for k in ("last_sl", "pdl", "pwl", "asia_low")]
        self.ltf = LTF_OF.get(tf) if LTF_OF.get(tf) and bars_path(symbol, LTF_OF[tf]).exists() else None
        if self.ltf:
            lf, _ = get_features(symbol, self.ltf)
            self.lt = lf["time"].dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")
            self.lo_, self.lh, self.ll, self.lc = (lf[k].to_numpy(float) for k in ("open", "high", "low", "close"))
            self.latr = lf["atr"].to_numpy(float)
            self.lsw = lf["last_sl" if self.long else "last_sh"].to_numpy(float)
            conf = (lf["bos_bull"] | lf["choch_bull"]) if self.long else (lf["bos_bear"] | lf["choch_bear"])
            self.lconf = conf.to_numpy(bool)
            self.lspread = spread_price(lf, point_size(symbol))
        self.t = self.df["time"].dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")
        self._base_cache: dict[tuple, list] = {}

    # ------------------------------------------------------------------ entries
    def _struct_stop(self, i: int, entry: float) -> float:
        a = self.atr[i]
        if self.long:
            s = self.l[max(0, i - 9): i + 1].min() - 0.1 * a
            d = float(np.clip(entry - s, 0.5 * a, 3 * a))
            return entry - d
        s = self.h[max(0, i - 9): i + 1].max() + 0.1 * a
        d = float(np.clip(s - entry, 0.5 * a, 3 * a))
        return entry + d

    def base_trades(self, entry_mode: str, stop_mode: str) -> list[dict]:
        """Entry, stop and price path per occurrence (shared by all targets / management rules)."""
        key = (entry_mode, stop_mode)
        if key in self._base_cache:
            return self._base_cache[key]
        sg = 1 if self.long else -1
        trades = []
        for i in self.events:
            a = self.atr[i]
            if not np.isfinite(a) or a <= 0:
                continue
            if self.sp - self.H <= i < self.sp:
                continue  # purged: this trade would still be open when the unseen period starts
            tr = {"i": int(i), "oos": bool(i >= self.sp), "taken": False}
            if entry_mode == "next":
                entry = self.o[i + 1]
                stop = entry - sg * Plan("next", stop_mode).stop_atr * a if stop_mode.startswith("atr") else self._struct_stop(i, entry)
                tr.update(taken=True, entry=entry, stop=stop, atr=a, cost=self.spread[i],
                          path=(self.h[i + 1: i + 1 + self.H], self.l[i + 1: i + 1 + self.H], self.c[i + 1: i + 1 + self.H]))
            else:
                tr.update(self._ltf_entry(i, stop_mode))
            if tr["taken"]:
                risk = abs(tr["entry"] - tr["stop"])
                lv = [x[i] for x in self.levels]
                cand = [v for v in lv if np.isfinite(v) and (v - tr["entry"]) * sg > 1.0 * risk]
                tr["liq"] = (min(cand) if self.long else max(cand)) if cand else tr["entry"] + sg * 2 * risk
            trades.append(tr)
        self._base_cache[key] = trades
        return trades

    def _ltf_entry(self, i: int, stop_mode: str) -> dict:
        sg = 1 if self.long else -1
        tf_s = TIMEFRAMES[self.tf] * 60
        t0 = self.t[i] + tf_s                      # signal candle closes
        j0 = int(np.searchsorted(self.lt, t0))     # first lower-timeframe candle after the signal
        w = WAIT_BARS * TIMEFRAMES[self.tf] // TIMEFRAMES[self.ltf]
        inval = self._struct_stop(i, self.c[i])
        for j in range(j0, min(j0 + w, len(self.lt) - 2)):
            if (self.ll[j] <= inval) if self.long else (self.lh[j] >= inval):
                return {"taken": False, "skip": "invalidated"}
            if self.lconf[j]:
                entry = self.lo_[j + 1]
                la = self.latr[j]
                if stop_mode == "ltf_pullback":  # beyond the pullback extreme made since the signal
                    ext = self.ll[j0: j + 1].min() if self.long else self.lh[j0: j + 1].max()
                    stop = ext - sg * 0.1 * la
                elif stop_mode == "ltf_swing" and np.isfinite(self.lsw[j]):
                    stop = self.lsw[j] - sg * 0.1 * la
                else:
                    stop = entry - sg * la
                if (entry - stop) * sg < 0.3 * la:
                    stop = entry - sg * 0.3 * la
                horizon = min(MAX_LTF_PATH, self.H * TIMEFRAMES[self.tf] // TIMEFRAMES[self.ltf])
                e = j + 1
                return {"taken": True, "entry": entry, "stop": stop, "atr": la, "cost": self.lspread[j],
                        "path": (self.lh[e: e + horizon], self.ll[e: e + horizon], self.lc[e: e + horizon])}
        return {"taken": False, "skip": "no confirmation"}

    # ------------------------------------------------------------------ evaluation
    def run_plan(self, plan: Plan) -> dict:
        trades = self.base_trades(plan.entry, plan.stop)
        sg = 1 if self.long else -1
        out = []
        for tr in trades:
            if not tr["taken"]:
                out.append((tr["i"], tr["oos"], 0.0, False))
                continue
            risk = abs(tr["entry"] - tr["stop"])
            if plan.target == "none":
                tgt = None
            elif plan.target == "liq":
                tgt = tr["liq"]
            else:
                tgt = tr["entry"] + sg * float(plan.target[1:]) * risk
            h, l, c = tr["path"]
            r, _, _ = simulate(h, l, c, self.direction, tr["entry"], tr["stop"], tgt, plan.manage, tr["atr"], tr["cost"])
            out.append((tr["i"], tr["oos"], r, True))
        return self._summ(plan, out)

    def trades(self, plan: Plan) -> list[dict]:
        """Every historical trade under `plan` with entry/exit timestamps (for portfolio simulation).
        Only next-candle entries (the plans allowed live)."""
        if plan.entry != "next":
            raise ValueError("portfolio simulation supports next-candle entry plans only")
        sg = 1 if self.long else -1
        tf_s = TIMEFRAMES[self.tf] * 60
        out = []
        for tr in self.base_trades("next", plan.stop):
            if not tr["taken"]:
                continue
            risk = abs(tr["entry"] - tr["stop"])
            tgt = None if plan.target == "none" else tr["liq"] if plan.target == "liq" else tr["entry"] + sg * float(plan.target[1:]) * risk
            h, l, c = tr["path"]
            r, held, why = simulate(h, l, c, self.direction, tr["entry"], tr["stop"], tgt, plan.manage, tr["atr"], tr["cost"])
            if not np.isfinite(r) or held == 0:
                continue
            i = tr["i"]
            out.append({"entry_time": int(self.t[i + 1]), "exit_time": int(self.t[min(i + held, len(self.t) - 1)] + tf_s),
                        "r": float(r), "oos": tr["oos"], "exit": why, "symbol": self.symbol, "direction": self.direction})
        return out

    def _summ(self, plan: Plan, out: list) -> dict:
        res = {"key": plan.key, "plan": plan.__dict__, "description": plan.describe()}
        for name, sel in (("is", False), ("oos", True)):
            rows = [x for x in out if x[1] == sel and np.isfinite(x[2])]
            taken = np.array([x[2] for x in rows if x[3]])
            signals = len(rows)
            s = summarize(taken, 0.0) if len(taken) else {"n": 0}
            s.update(signals=signals, taken=int(len(taken)),
                     per_signal_r=float(sum(x[2] for x in rows) / signals) if signals else None,
                     win_rate=float((taken > 0).mean()) if len(taken) else None)
            res[name] = s
        return res

    def optimise(self) -> dict:
        plans = plan_grid(self.ltf is not None)
        results = [self.run_plan(p) for p in plans]
        base = next(r for r in results if r["key"] == BASELINE.key)

        def score(r):  # chosen on the in-sample period ONLY, shrunk toward the baseline
            s, b = r["is"], base["is"]
            if not s.get("taken") or s["taken"] < 30 or s.get("per_signal_r") is None:
                return -np.inf
            k = s["signals"]
            return (k * s["per_signal_r"] + SHRINK * (b.get("per_signal_r") or 0)) / (k + SHRINK)

        for r in results:
            r["is_score"] = float(score(r)) if np.isfinite(score(r)) else None
        ranked = sorted(results, key=lambda r: -(r["is_score"] if r["is_score"] is not None else -1e9))
        best = ranked[0]
        bo, bb = best["oos"], base["oos"]
        verdict = "insufficient"
        if bo.get("signals", 0) >= 15 and bo.get("per_signal_r") is not None and bb.get("per_signal_r") is not None:
            diff = bo["per_signal_r"] - bb["per_signal_r"]
            verdict = "improved" if diff > 0.05 else ("similar" if diff > -0.05 else "worse")
        return {
            "symbol": self.symbol, "tf": self.tf, "ltf": self.ltf, "direction": self.direction,
            "conditions": self.conditions, "labels": [label_of(c) for c in self.conditions],
            "occurrences": int(len(self.events)), "split_time": int(self.df["time"].iloc[self.sp].timestamp()),
            "baseline": base, "best": best, "verdict": verdict,
            "ranked": ranked[:15], "n_plans": len(results),
        }
