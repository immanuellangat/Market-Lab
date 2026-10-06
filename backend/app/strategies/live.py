"""Named strategies on the live watchlist.

A strategy watch is a normal watchlist row with conditions == ["strategy:crt"] and
plan == "crt|<htf>|<entry>|<target>|<filter,filter,...>". It is checked on every closed
higher-timeframe candle with the same code as the backtest; only next-open entries
("close") are supported live.
"""
from __future__ import annotations

import time

import numpy as np

from ..data.store import load_bars
from ..features import engine
from ..features.engine import get_features
from ..research.hypothesis import decay_check
from . import crt
from .crt_search import setup_filters
from .evaluate import grade

TAG = "strategy:crt"
SMC_TAG = "strategy:smc"
SMC_FILTER_TEXT = {"bias1": "H4 structure agrees", "bias2": "daily structure agrees", "htf_pd": "H4 discount/premium",
                   "killzone": "kill zone", "ema": "EMA trend agrees"}
FILTER_TEXT = {"key_time": "key-time candle", "d1_bias": "with daily bias", "pd_zone": "daily premium/discount",
               "key_level": "sweeps a key level", "big_range": "range ≥ 1 ATR"}


def is_strategy(w: dict) -> bool:
    return bool(w.get("conditions")) and w["conditions"][0] in (TAG, SMC_TAG)


def parse(plan: str) -> dict:
    name, htf, mode, tgt, filters = plan.split("|")
    if name != "crt" or htf not in crt.HTFS or mode != "close" or tgt not in crt.TARGETS:
        raise ValueError(f"unsupported strategy plan {plan!r}")
    return {"htf": htf, "mode": mode, "target": tgt, "filters": [x for x in filters.split(",") if x]}


def title(plan: str) -> str:
    if plan.startswith("smc|"):
        _, tf, setup, ex, filters = plan.split("|")
        return f"SMC {tf} sweep → CHoCH → FVG/OB retest · " + " + ".join(SMC_FILTER_TEXT[f] for f in filters.split(",") if f)
    r = parse(plan)
    return f"CRT {r['htf']} · " + " + ".join(FILTER_TEXT[f] for f in r["filters"])


def _passes(flags: dict, filters: list[str]) -> bool:
    return all(flags[f] for f in filters)


def snapshot_stats(w: dict) -> dict:
    """Backtest of this rule on this market, in the same shape as a condition watch's statistics."""
    if w["plan"].startswith("smc|"):
        return _smc_stats(w)
    r = parse(w["plan"])
    eng = crt.CRT(w["symbol"], r["htf"])
    flags = setup_filters(eng, get_features(w["symbol"], r["htf"])[0])
    trades = [t for t in eng.trades(r["mode"], target_mode=r["target"]) if _passes(flags[(t["i"], t["direction"])], r["filters"])]
    g = grade(trades, eng.ht)
    taken = [t["r"] for t in trades if "r" in t]
    return {"stats": g["stats"], "evidence": g["evidence"], "decay": decay_check(np.array(taken)),
            "split_time": g["split_time"], "as_of": int(time.time()), "signals_per_month": g["signals_per_month"]}


def check_latest(w: dict) -> list[dict]:
    """Signals on the most recent closed higher-timeframe candle (candle B of a CRT)."""
    r = parse(w["plan"])
    hdf = load_bars(w["symbol"], r["htf"]).tail(400).reset_index(drop=True)
    ldf = load_bars(w["symbol"], crt.LTF_OF[r["htf"]])
    ldf = ldf[ldf["time"] >= hdf["time"].iloc[0]].reset_index(drop=True)
    eng = crt.CRT(w["symbol"], r["htf"], hdf, ldf)
    i = len(eng.ht) - 1
    out = []
    todo = [d for (j, d) in eng.setups(start=i) if j == i]
    if not todo:
        return out
    flags = setup_filters(eng, engine.live_features(w["symbol"], r["htf"])[0])
    for d in todo:
        f = flags[(i, d)]
        if not _passes(f, r["filters"]):
            continue
        e = eng.entry(i, d, "close", r["target"])
        e["bar_time"] = int(eng.ht[i])
        e["atr"] = float(eng.hatr[i])
        e["flags"] = f
        out.append(e)
    return out


# ------------------------------------------------------------------ SMC (H1), from strategies/ltf_search.py
def _smc_parse(plan: str) -> dict:
    _, tf, setup, ex, filters = plan.split("|")
    return {"tf": tf, "setup": setup, "exit": ex, "filters": [f for f in filters.split(",") if f]}


def _smc_events(df, r: dict, d: str) -> np.ndarray:
    from .ltf_search import filter_flags, setup_masks
    m = setup_masks(df)[(r["setup"], d)].copy()
    ff = filter_flags(df, r["tf"], d)
    for f in r["filters"]:
        m &= ff[f]
    return m


def _smc_stats(w: dict) -> dict:
    """Per-market backtest of the SMC rule (same trades as the search)."""
    from ..research.stats import evidence_score
    from .ltf_search import _stats, collect_one
    r = _smc_parse(w["plan"])
    tr = collect_one(w["symbol"], r["tf"], r)
    s_is, s_oos, s_all = _stats(tr[~tr["oos"]]), _stats(tr[tr["oos"]]), _stats(tr)
    ev = evidence_score(s_is, s_oos) if s_is.get("n", 0) >= 5 else {"score": 0, "grade": "F", "reasons": ["Too few trades."]}
    return {"stats": {"all": s_all, "in_sample": s_is, "out_of_sample": s_oos}, "evidence": ev,
            "decay": decay_check(tr["r"].to_numpy()), "split_time": None, "as_of": int(time.time())}


def smc_check_latest(w: dict) -> list[dict]:
    from ..execution.levels import signal_spec
    from .ltf_search import EXITS
    r = _smc_parse(w["plan"])
    df, _ = engine.live_features(w["symbol"], r["tf"])
    i = len(df) - 1
    out = []
    for d in ("long", "short"):
        if _smc_events(df, r, d)[i]:
            out.append({"direction": d, "bar_time": int(df["time"].iloc[i].timestamp()),
                        "spec": signal_spec(EXITS[r["exit"]], df, i, d), "close": float(df["close"].iloc[i]),
                        "atr": float(df["atr"].iloc[i])})
    return out
