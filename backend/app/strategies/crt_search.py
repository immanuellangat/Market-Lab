"""Search for CRT refinements, without fooling ourselves.

The candidate filters are the ones CRT traders commonly publish, fixed BEFORE looking at results:
  key_time   H4: sweep candle opens 1am / 5am / 9am New York; H1: London or NY killzone (02-05, 08-11 NY)
  d1_bias    trade with the daily market-structure trend
  pd_zone    longs in the daily discount (below 50%), shorts in the premium
  key_level  the sweep takes out a key level (prev. day / week high-low, Asia high-low, last swing) and closes back
  big_range  range candle A is at least 1 x ATR (avoids ranges that the spread eats)
Every combination of filters x entry (close / ltf / limit50) x target (full / mid) x H1/H4 is tested,
pooled over all markets and both directions. Rules are RANKED on in-sample data only; the out-of-sample
period is only read for the final report, with a Benjamini-Hochberg correction across all rules tested.
"""
from __future__ import annotations

import itertools
import time

import numpy as np
import pandas as pd

from ..config import SYMBOLS
from ..data.store import bars_path
from ..discovery.search import FDR_Q, _bh
from ..features.engine import get_features
from ..live import db
from ..research.hypothesis import WARMUP
from ..research.stats import evidence_score, summarize
from . import crt

KEY = "strategy_crt_search"
FILTERS = ("key_time", "d1_bias", "pd_zone", "key_level", "big_range")
KEY_HOURS = {"H4": {1, 5, 9}, "H1": {2, 3, 4, 8, 9, 10}}
MIN_IS = 100
SHRINK = 100
MIN_OOS = 30


def setup_filters(eng: crt.CRT, f: pd.DataFrame) -> dict[tuple[int, str], dict]:
    """Filter flags for every setup, using only information available when candle B closes.

    f: feature table of the higher timeframe (full history, or engine.live_features for live use)."""
    f = f.set_index(f["time"].dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64"))
    cols = ["hour_ny", "pdh", "pdl", "pwh", "pwl", "asia_high", "asia_low", "last_sh", "last_sl", "d1_ms_trend", "d1_pd_pos"]
    f = f[cols].reindex(eng.ht)
    v = {c: f[c].to_numpy(float) for c in cols}
    out = {}
    for i, d in eng.setups():
        short = d == "short"
        a = eng.hatr[i - 1]
        if short:
            lv = [v[c][i - 1] for c in ("pdh", "pwh", "asia_high", "last_sh")]
            lvl = any(np.isfinite(x) and eng.hh[i] > x > eng.hc[i] for x in lv)
        else:
            lv = [v[c][i - 1] for c in ("pdl", "pwl", "asia_low", "last_sl")]
            lvl = any(np.isfinite(x) and eng.hl[i] < x < eng.hc[i] for x in lv)
        tr, pdp = v["d1_ms_trend"][i], v["d1_pd_pos"][i]
        out[(i, d)] = {
            "key_time": bool(v["hour_ny"][i] in KEY_HOURS[eng.htf]),
            "d1_bias": bool(tr == (-1 if short else 1)),
            "pd_zone": bool(np.isfinite(pdp) and ((pdp > 0.5) if short else (pdp < 0.5))),
            "key_level": bool(lvl),
            "big_range": bool(np.isfinite(a) and (eng.hh[i - 1] - eng.hl[i - 1]) >= a),
        }
    return out


def collect(progress=None) -> pd.DataFrame:
    """One row per taken trade (all entry/target variants), tagged with filters and in/out-of-sample."""
    rows = []
    jobs = [(s, h) for s in SYMBOLS for h in crt.HTFS if bars_path(s, h).exists() and bars_path(s, crt.LTF_OF[h]).exists()]
    for k, (symbol, htf) in enumerate(jobs):
        if progress:
            progress(k, len(jobs), f"{symbol} {htf}")
        eng = crt.CRT(symbol, htf)
        flags = setup_filters(eng, get_features(symbol, htf)[0])
        n = len(eng.ht)
        sp = WARMUP + int((n - WARMUP) * 0.7)
        split_time = int(eng.ht[min(sp, n - 1)])
        for mode in crt.MODES:
            for tgt in crt.TARGETS:
                for t in eng.trades(mode, target_mode=tgt):
                    if "r" not in t or t["i"] < WARMUP:
                        continue
                    if t["i"] < sp and t["exit_time"] > split_time:
                        continue  # purged
                    rows.append({"symbol": symbol, "htf": htf, "mode": mode, "target": tgt, "direction": t["direction"],
                                 "time": t["signal_time"], "r": t["r"], "gross": t["gross"], "rr": t["rr"],
                                 "exit": t["exit"], "oos": t["i"] >= sp, **flags[(t["i"], t["direction"])]})
    return pd.DataFrame(rows).sort_values("time", kind="stable").reset_index(drop=True)


def _stats(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0}
    r = d["r"].to_numpy()
    years = max((d["time"].iloc[-1] - d["time"].iloc[0]) / (365.25 * 86400), 1 / 365.25)
    s = summarize(r, 0.0, 0.0, years, gross=d["gross"].to_numpy())
    s.update(win_rate=float((r > 0).mean()), wins=int((d["exit"] == "target").sum()),
             losses=int((d["exit"] == "stop").sum()), median_rr=float(d["rr"].median()),
             gross_r=float(d["gross"].mean()))
    return s


def search(df: pd.DataFrame) -> list[dict]:
    rules = []
    for htf, mode, tgt in itertools.product(crt.HTFS, crt.MODES, crt.TARGETS):
        base = df[(df["htf"] == htf) & (df["mode"] == mode) & (df["target"] == tgt)]
        for k in range(len(FILTERS) + 1):
            for combo in itertools.combinations(FILTERS, k):
                m = np.ones(len(base), bool)
                for c in combo:
                    m &= base[c].to_numpy(bool)
                d = base[m]
                s_is, s_oos = _stats(d[~d["oos"]]), _stats(d[d["oos"]])
                n = s_is.get("n", 0)
                rules.append({
                    "htf": htf, "mode": mode, "target": tgt, "filters": list(combo),
                    "is": s_is, "oos": s_oos,
                    # ranking uses in-sample only, shrunk toward 0R so small samples cannot win by luck
                    "is_score": (n * s_is["avg_r"]) / (n + SHRINK) if n >= MIN_IS else None,
                    "per_symbol_oos": {s: _stats(g) for s, g in d[d["oos"]].groupby("symbol")},
                    "_oos_dir": {dd: _stats(g) for dd, g in d[d["oos"]].groupby("direction")},
                })
    p = np.array([r["oos"].get("p_value", 1.0) if r["oos"].get("n", 0) >= MIN_OOS else 1.0 for r in rules])
    q = _bh(p)
    for r, qv in zip(rules, q):
        r["q_oos"] = float(qv)
        r["validated"] = bool(r["oos"].get("n", 0) >= MIN_OOS and qv <= FDR_Q and r["oos"]["avg_r"] > 0)
        r["evidence"] = evidence_score(r["is"], r["oos"]) if r["is"].get("n", 0) >= 5 else {"grade": "F", "score": 0}
    return rules


def run(progress=None) -> dict:
    df = collect(progress)
    rules = search(df)
    ranked = sorted([r for r in rules if r["is_score"] is not None], key=lambda r: -r["is_score"])
    by_wr = sorted([r for r in rules if r["is"].get("n", 0) >= MIN_IS], key=lambda r: -r["is"]["win_rate"])
    out = {"finished": int(time.time()), "rules_tested": len(rules), "trades": int(len(df)), "fdr_q": FDR_Q,
           "top_in_sample": ranked[:15], "top_win_rate": by_wr[:10],
           "validated": [r for r in rules if r["validated"]]}
    db.set_setting(KEY, out)
    return out
