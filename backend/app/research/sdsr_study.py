"""Do supply / demand and support / resistance zones have an edge?

Each zone condition (features/sdsr.py) is traded on its own, in the zone's direction, on every
market x timeframe, under three exits (1 ATR stop / 2R, 2 ATR / 1R high win rate, 1.5 ATR / 3R).
Same standards as the research lab: costs included, stop before target inside a candle, oldest 70%
in-sample, last 30% unseen with in-sample trades still open at the split purged. Two families,
each Benjamini-Hochberg corrected on the unseen p-values:
  * per market: one test per condition x market x timeframe x exit;
  * pooled: all markets of one condition x timeframe x exit together (more trades, less noise).
Validated = positive unseen expectancy on >= MIN_OOS unseen trades with q <= FDR_Q.
"""
from __future__ import annotations

import time

import numpy as np

from ..config import DEFAULT_HORIZON, SYMBOLS
from ..data.store import bars_path
from ..discovery.search import FDR_Q, _bh
from ..features.engine import get_features
from ..live import db
from .conditions import BY_ID, evaluate
from .hypothesis import WARMUP, _years, dedupe, point_size, split_index
from .outcomes import get_outcomes
from .stats import evidence_score, summarize

KEY = "sdsr_study"
CONDITIONS = ["demand_retest", "demand_retest_rev", "demand_retest_cont", "in_demand",
              "supply_retest", "supply_retest_rev", "supply_retest_cont", "in_supply",
              "support_test", "support_test_strong", "support_flip_test",
              "resistance_test", "resistance_test_strong", "resistance_flip_test"]
TFS = ("M15", "H1", "H4")
EXITS = [(1.0, 2.0), (2.0, 1.0), (1.5, 3.0)]   # (stop ATR, target R)
COOLDOWN = 5
MIN_OOS = 20


def _trades(df, out, cond: str, direction: str, target_r: float, horizon: int):
    side = "L" if direction == "long" else "S"
    gross = out[f"{side}_r{target_r:g}"].to_numpy()
    net = gross - out["cost_r"].fillna(0).to_numpy()
    valid = np.isfinite(gross)
    valid[:WARMUP] = False
    ev = dedupe(np.flatnonzero(evaluate(df, [cond]) & valid), COOLDOWN)
    sp = split_index(df)
    is_ev, oos_ev = ev[ev < sp - horizon], ev[ev >= sp]

    def base(lo, hi):
        v = valid.copy()
        v[:lo] = False
        v[hi:] = False
        return float(np.nanmean(net[v])) if v.any() else 0.0

    times = df["time"]
    summ = lambda e, b: summarize(net[e], target_r, b, _years(times.iloc[e]), gross=gross[e]) if len(e) else {"n": 0}
    return (summ(is_ev, base(0, sp - horizon)), summ(oos_ev, base(sp, len(df))),
            net[is_ev], gross[is_ev], net[oos_ev], gross[oos_ev])


def _brief(s: dict) -> dict:
    keys = ("n", "win_rate", "avg_r", "profit_factor", "p_value", "edge_r", "trades_per_year")
    return {k: s.get(k) for k in keys if k in s}


def run(progress=None) -> dict:
    jobs = [(s, tf) for tf in TFS for s in SYMBOLS if bars_path(s, tf).exists()]
    rows, pool = [], {}
    for k, (symbol, tf) in enumerate(jobs):
        if progress:
            progress(k, len(jobs), f"{symbol} {tf}")
        df, _ = get_features(symbol, tf, wait=True)
        H = DEFAULT_HORIZON[tf]
        for stop_atr, target_r in EXITS:
            out = get_outcomes((symbol, tf), df, H, stop_atr, point_size(symbol))
            for cond in CONDITIONS:
                direction = "long" if BY_ID[cond].bias == "bull" else "short"
                s_is, s_oos, r_is, g_is, r_oos, g_oos = _trades(df, out, cond, direction, target_r, H)
                ev = evidence_score(s_is, s_oos) if s_is.get("n") else {"score": 0, "grade": "F", "reasons": []}
                rows.append({"condition": cond, "symbol": symbol, "tf": tf, "direction": direction,
                             "stop_atr": stop_atr, "target_r": target_r, "is": _brief(s_is), "oos": _brief(s_oos),
                             "grade": ev["grade"], "score": ev["score"]})
                p = pool.setdefault((cond, tf, stop_atr, target_r), {"r_is": [], "g_is": [], "r_oos": [], "g_oos": [], "markets": []})
                p["r_is"].append(r_is); p["g_is"].append(g_is); p["r_oos"].append(r_oos); p["g_oos"].append(g_oos)
                p["markets"].append({"symbol": symbol, "oos_n": s_oos.get("n", 0), "oos_r": s_oos.get("avg_r"), "is_r": s_is.get("avg_r")})

    def mark(items, get_oos):
        p = np.array([get_oos(r).get("p_value", 1.0) if get_oos(r).get("n", 0) >= MIN_OOS else 1.0 for r in items])
        q = _bh(p) if len(p) else p
        for r, qv in zip(items, q):
            so = get_oos(r)
            r["q_oos"] = float(qv)
            r["validated"] = bool(so.get("n", 0) >= MIN_OOS and qv <= FDR_Q and (so.get("avg_r") or 0) > 0)

    mark(rows, lambda r: r["oos"])
    pooled = []
    for (cond, tf, stop_atr, target_r), p in pool.items():
        cat = lambda key: np.concatenate(p[key]) if p[key] else np.array([])
        s_is = summarize(cat("r_is"), target_r, gross=cat("g_is")) if len(cat("r_is")) else {"n": 0}
        s_oos = summarize(cat("r_oos"), target_r, gross=cat("g_oos")) if len(cat("r_oos")) else {"n": 0}
        pos = sum(1 for m in p["markets"] if (m["oos_r"] or 0) > 0 and m["oos_n"] >= 10)
        pooled.append({"condition": cond, "tf": tf, "direction": "long" if BY_ID[cond].bias == "bull" else "short",
                       "stop_atr": stop_atr, "target_r": target_r, "is": _brief(s_is), "oos": _brief(s_oos),
                       "markets_positive_oos": pos, "markets": p["markets"]})
    mark(pooled, lambda r: r["oos"])
    pooled.sort(key=lambda r: -(r["oos"].get("avg_r") or -9))
    res = {"finished": int(time.time()), "tests": len(rows), "pooled_tests": len(pooled), "fdr_q": FDR_Q,
           "rule": (f"Validated = positive unseen expectancy after costs on >= {MIN_OOS} unseen trades, surviving "
                    f"Benjamini-Hochberg (q <= {FDR_Q}) across all tests of its family."),
           "validated": [r for r in rows if r["validated"]], "pooled_validated": [r for r in pooled if r["validated"]],
           "pooled": pooled, "results": rows}
    db.set_setting(KEY, res)
    return res


def latest() -> dict | None:
    return db.get_setting(KEY)
