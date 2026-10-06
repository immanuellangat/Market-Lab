"""Backtest grading for named strategies, held to the research lab's standards.

  * oldest 70% in-sample, most recent 30% out-of-sample (same split as the lab);
  * in-sample trades still open at the split are purged;
  * spread deducted, stop before target inside a candle (shared simulator);
  * evidence grade from the lab's evidence_score (edge measured against 0R after costs);
  * Benjamini-Hochberg across every variant tested, on the out-of-sample p-values.
"""
from __future__ import annotations

import time
from collections import Counter

import numpy as np
import pandas as pd

from ..config import SYMBOLS
from ..data.store import bars_path
from ..discovery.search import FDR_Q, _bh
from ..live import db
from ..research.hypothesis import WARMUP
from ..research.stats import evidence_score, summarize
from . import crt

KEY = "strategy_crt"
MIN_OOS = 20


def _summ(tr: list[dict], years: float) -> dict:
    if not tr:
        return {"n": 0}
    r = np.array([t["r"] for t in tr])
    s = summarize(r, 0.0, 0.0, years, gross=np.array([t["gross"] for t in tr]))
    rr = np.array([t["rr"] for t in tr])
    s["wins"] = sum(t["exit"] == "target" for t in tr)
    s["losses"] = sum(t["exit"] == "stop" for t in tr)
    s["timeouts"] = len(tr) - s["wins"] - s["losses"]
    s["win_rate"] = float((r > 0).mean())
    s["median_rr"] = float(np.median(rr))
    s["breakeven_win_rate"] = float(1 / (1 + np.median(rr)))
    return s


def _years(tr: list[dict]) -> float:
    if len(tr) < 2:
        return 0.0
    return max((tr[-1]["signal_time"] - tr[0]["signal_time"]) / (365.25 * 86400), 1 / 365.25)


def _breakdown(tr: list[dict], key) -> list[dict]:
    groups: dict = {}
    for t in tr:
        groups.setdefault(key(t), []).append(t["r"])
    return [{"key": str(k), "n": len(v), "win_rate": float(np.mean(np.array(v) > 0)), "avg_r": float(np.mean(v)),
             "total_r": float(np.sum(v))} for k, v in sorted(groups.items())]


def grade(trades: list[dict], htimes: np.ndarray) -> dict:
    n = len(htimes)
    sp = WARMUP + int((n - WARMUP) * 0.7)
    split_time = int(htimes[min(sp, n - 1)])
    usable = [t for t in trades if t["i"] >= WARMUP]
    taken = [t for t in usable if "r" in t]
    is_tr = [t for t in taken if t["i"] < sp and t["exit_time"] <= split_time]
    oos_tr = [t for t in taken if t["i"] >= sp]
    s_is, s_oos, s_all = _summ(is_tr, _years(is_tr)), _summ(oos_tr, _years(oos_tr)), _summ(taken, _years(taken))
    ev = evidence_score(s_is, s_oos) if s_is.get("n") else {"score": 0, "grade": "F", "reasons": ["No in-sample trades."]}
    ny = lambda t: pd.Timestamp(t["signal_time"], unit="s", tz="UTC").tz_convert("America/New_York")
    return {
        "setups": len(usable), "taken": len(taken),
        "skipped": dict(Counter(t["skip"] for t in usable if "skip" in t)),
        "split_time": split_time,
        "stats": {"all": s_all, "in_sample": s_is, "out_of_sample": s_oos},
        "evidence": ev,
        "signals_per_month": len(taken) / max(_years(taken) * 12, 1e-9) if taken else 0.0,
        "breakdowns": {
            "hour_ny": _breakdown(taken, lambda t: f"{ny(t).hour:02d}:00"),
            "year": _breakdown(taken, lambda t: ny(t).year),
            "rr": _breakdown(taken, lambda t: "1-1.5R" if t["rr"] < 1.5 else "1.5-2R" if t["rr"] < 2 else "2-3R" if t["rr"] < 3 else "3R+"),
        },
        "equity": [{"time": t["signal_time"], "r": round(t["r"], 4), "oos": t["i"] >= sp} for t in taken],
    }


def run_all(progress=None) -> dict:
    rows = []
    jobs = [(s, h) for s in SYMBOLS for h in crt.HTFS if bars_path(s, h).exists() and bars_path(s, crt.LTF_OF[h]).exists()]
    for k, (symbol, htf) in enumerate(jobs):
        if progress:
            progress(k, len(jobs), f"{symbol} {htf}")
        eng = crt.CRT(symbol, htf)
        for mode in crt.MODES:
            trades = eng.trades(mode)
            for d in ("long", "short"):
                g = grade([t for t in trades if t["direction"] == d], eng.ht)
                g.update(strategy=crt.NAME, symbol=symbol, htf=htf, ltf=crt.LTF_OF[htf], mode=mode, direction=d,
                         description=crt.describe(htf, mode), id=f"crt|{symbol}|{htf}|{mode}|{d}")
                rows.append(g)
    p = np.array([r["stats"]["out_of_sample"].get("p_value", 1.0) if r["stats"]["out_of_sample"].get("n", 0) >= MIN_OOS else 1.0
                  for r in rows])
    q = _bh(p) if len(p) else p
    for r, qv in zip(rows, q):
        so = r["stats"]["out_of_sample"]
        r["q_oos"] = float(qv)
        r["validated"] = bool(so.get("n", 0) >= MIN_OOS and qv <= FDR_Q and so.get("avg_r", 0) > 0)
        r["weak"] = not r["validated"] and r["evidence"]["grade"] in ("D", "F")
    out = {"finished": int(time.time()), "strategy": crt.NAME, "variants": len(rows), "fdr_q": FDR_Q,
           "rule": (f"Validated = positive out-of-sample expectancy after costs on at least {MIN_OOS} unseen trades, "
                    f"surviving a Benjamini-Hochberg correction (q <= {FDR_Q}) across all {len(rows)} variants tested."),
           "results": rows}
    db.set_setting(KEY, out)
    return out


def latest() -> dict | None:
    return db.get_setting(KEY)
