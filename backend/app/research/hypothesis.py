"""Hypothesis testing: evaluate a combination of conditions against history
with a strict in-sample / out-of-sample split."""
from __future__ import annotations

import threading

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..config import DEFAULT_HORIZON, DEFAULT_STOP_ATR, OOS_FRACTION, TARGETS_R
from ..data.store import read_meta
from ..features.engine import get_features
from ..features.sessions import DOW, SESSIONS
from .conditions import BY_ID, available, evaluate, label_of
from .outcomes import get_outcomes
from .stats import evidence_score, summarize

WARMUP = 250
_mat_cache: dict[tuple, tuple[list[str], np.ndarray]] = {}
_mat_lock = threading.Lock()


def point_size(symbol: str) -> float:
    return float(read_meta().get("symbols", {}).get(symbol, {}).get("point", 0.0) or 0.0)


def condition_matrix(symbol: str, tf: str, df: pd.DataFrame) -> tuple[list[str], np.ndarray]:
    """All available condition masks as one (bars x conditions) boolean matrix."""
    key = (symbol, tf, len(df), df["time"].iloc[-1])
    with _mat_lock:
        if key in _mat_cache:
            return _mat_cache[key]
    conds = available(df)
    mat = np.zeros((len(df), len(conds)), bool)
    for j, c in enumerate(conds):
        mat[:, j] = np.asarray(pd.Series(c.fn(df)).fillna(False), bool)
    ids = [c.id for c in conds]
    with _mat_lock:
        if len(_mat_cache) >= 4:
            _mat_cache.pop(next(iter(_mat_cache)))
        _mat_cache[key] = (ids, mat)
    return ids, mat


def dedupe(idx: np.ndarray, cooldown: int) -> np.ndarray:
    """Keep an occurrence only if it is more than `cooldown` bars after the previous kept one."""
    if cooldown <= 0 or len(idx) == 0:
        return idx
    keep, last = [], -10**12
    for i in idx:
        if i - last > cooldown:
            keep.append(i)
            last = i
    return np.asarray(keep, dtype=int)


def split_index(df: pd.DataFrame) -> int:
    usable = len(df) - WARMUP
    return WARMUP + int(usable * (1 - OOS_FRACTION))


def _years(times: pd.Series) -> float:
    if len(times) < 2:
        return 0.0
    return max((times.iloc[-1] - times.iloc[0]).days / 365.25, 1 / 365.25)


def _breakdown(values: np.ndarray, r: np.ndarray, labels: dict | None = None, min_n: int = 1) -> list[dict]:
    rows = []
    for v in pd.unique(values):
        if pd.isna(v):
            continue
        m = values == v
        k = int(m.sum())
        if k < min_n:
            continue
        rr = r[m]
        rows.append({"key": labels.get(v, str(v)) if labels else str(v), "n": k,
                     "win_rate": float((rr > 0).mean()), "avg_r": float(rr.mean()), "total_r": float(rr.sum())})
    return sorted(rows, key=lambda x: x["key"])


def run_test(symbol: str, tf: str, conditions: list[str], direction: str, target_r: float = 2.0,
             stop_atr: float = DEFAULT_STOP_ATR, horizon: int | None = None, cooldown: int = 5,
             date_from: str | None = None, date_to: str | None = None, include_costs: bool = True,
             max_examples: int = 1500, light: bool = False) -> dict:
    if target_r not in TARGETS_R:
        raise ValueError(f"target_r must be one of {TARGETS_R}")
    if direction not in ("long", "short"):
        raise ValueError("direction must be 'long' or 'short'")
    df, _ = get_features(symbol, tf)
    horizon = horizon or DEFAULT_HORIZON[tf]
    out = get_outcomes((symbol, tf), df, horizon, stop_atr, point_size(symbol))
    side = "L" if direction == "long" else "S"
    gross_all = out[f"{side}_r{target_r:g}"].to_numpy()
    cost_all = out["cost_r"].fillna(0).to_numpy() if include_costs else np.zeros(len(df))
    net_all = gross_all - cost_all
    times = df["time"]

    valid = np.isfinite(gross_all)
    valid[:WARMUP] = False
    if date_from:
        valid &= (times >= pd.Timestamp(date_from, tz="UTC")).to_numpy()
    if date_to:
        valid &= (times <= pd.Timestamp(date_to, tz="UTC")).to_numpy()

    mask = evaluate(df, conditions) & valid
    ev = dedupe(np.flatnonzero(mask), cooldown)
    sp = split_index(df)
    split_time = times.iloc[min(sp, len(df) - 1)]
    # purge: in-sample trades must finish before the split, so no unseen prices leak into in-sample results
    purge = sp - horizon
    is_ev, oos_ev = ev[ev < purge], ev[ev >= sp]

    def base(lo, hi):
        v = valid.copy()
        v[:lo] = False
        v[hi:] = False
        return float(np.nanmean(net_all[v])) if v.any() else 0.0

    base_is, base_oos = base(0, purge), base(sp, len(df))

    def summ(e, b):
        if len(e) == 0:
            return {"n": 0}
        return summarize(net_all[e], target_r, b, _years(times.iloc[e]),
                         out[f"{side}_mfe"].to_numpy()[e], out[f"{side}_mae"].to_numpy()[e],
                         out[f"{side}_bars"].to_numpy()[e], gross_all[e])

    s_all = summ(ev, base(0, len(df)))
    s_is, s_oos = summ(is_ev, base_is), summ(oos_ev, base_oos)
    score = evidence_score(s_is, s_oos) if s_is.get("n") else {"score": 0, "grade": "F", "reasons": ["No in-sample occurrences."]}

    r = net_all[ev]
    t_ev = times.iloc[ev]
    result = {
        "symbol": symbol, "tf": tf, "direction": direction, "conditions": conditions,
        "condition_labels": [label_of(c) for c in conditions],
        "params": {"target_r": target_r, "stop_atr": stop_atr, "horizon": horizon, "cooldown": cooldown,
                   "include_costs": include_costs},
        "split_time": int(split_time.timestamp()),
        "data_range": [int(times.iloc[WARMUP].timestamp()), int(times.iloc[-1].timestamp())],
        "stats": {"all": s_all, "in_sample": s_is, "out_of_sample": s_oos},
        "evidence": score,
    }
    if len(ev) == 0 or light:
        return result

    eq = np.cumsum(r)
    result["equity"] = [{"time": int(t.timestamp()), "value": float(v), "oos": bool(i >= sp)}
                        for t, v, i in zip(t_ev, eq, ev)]
    roll = pd.Series(r).rolling(min(50, max(10, len(r) // 5)), min_periods=10).mean()
    result["rolling"] = [{"time": int(t.timestamp()), "value": float(v)} for t, v in zip(t_ev, roll) if np.isfinite(v)]

    result["breakdowns"] = {
        "session": _breakdown(df["session"].to_numpy()[ev], r, dict(enumerate(SESSIONS))),
        "dow": _breakdown(df["dow"].to_numpy()[ev], r, dict(enumerate(DOW))),
        "hour_ny": _breakdown(df["hour_ny"].to_numpy()[ev], r, {h: f"{h:02d}:00" for h in range(24)}),
        "volatility": _breakdown(df["vol_regime"].to_numpy()[ev], r, {0: "low", 1: "normal", 2: "high"}),
        "structure": _breakdown(df["ms_trend"].to_numpy()[ev], r, {1: "bullish", -1: "bearish", 0: "none"}),
        "year": _breakdown(t_ev.dt.year.to_numpy(), r),
    }
    result["decay"] = decay_check(r)
    result["best_worst"] = best_worst_filters(symbol, tf, df, ev, r, {c.lstrip("!") for c in conditions})
    result["examples"] = [
        {"i": int(i), "time": int(times.iloc[i].timestamp()), "r": float(net_all[i]),
         "outcome": "win" if gross_all[i] >= target_r - 1e-9 else "loss" if gross_all[i] <= -1 + 1e-9 else "timeout",
         "mfe_atr": float(out[f"{side}_mfe"].iloc[i]), "bars": float(out[f"{side}_bars"].iloc[i]),
         "session": SESSIONS[int(df["session"].iloc[i])], "oos": bool(i >= sp)}
        for i in ev[::-1][:max_examples]
    ]
    return result


_quick_cache: dict[tuple, dict] = {}


def quick_stats(symbol: str, tf: str, conditions: list[str], direction: str, target_r: float = 2.0,
                stop_atr: float = DEFAULT_STOP_ATR, until_index: int | None = None) -> dict:
    """Compact, cached statistics for a setup (used for zone and scanner annotations).

    until_index: only use occurrences whose outcome was already known at that bar
    (used by replay so statistics never include the future).
    """
    df, _ = get_features(symbol, tf)
    horizon = DEFAULT_HORIZON[tf]
    cutoff = None
    if until_index is not None and until_index < len(df) - 1:
        cutoff = max(WARMUP + 1, (until_index - horizon) // 250 * 250)  # coarse steps keep the cache useful
    key = (symbol, tf, tuple(conditions), direction, target_r, stop_atr, len(df), cutoff)
    if key in _quick_cache:
        return _quick_cache[key]
    date_to = str(df["time"].iloc[cutoff]) if cutoff else None
    res = run_test(symbol, tf, conditions, direction, target_r, stop_atr, date_to=date_to, max_examples=0, light=True)
    out = get_outcomes((symbol, tf), df, horizon, stop_atr, point_size(symbol))
    side = "L" if direction == "long" else "S"
    mask = evaluate(df, conditions)
    mask[:WARMUP] = False
    if cutoff:
        mask[cutoff:] = False
    ev = dedupe(np.flatnonzero(mask), 5)
    mfe_pre = out[f"{side}_mfe_prestop"].to_numpy()[ev]
    mfe_pre = mfe_pre[np.isfinite(mfe_pre)]
    s, so = res["stats"]["all"], res["stats"]["out_of_sample"]
    q = {
        "n": s.get("n", 0), "win_rate": s.get("win_rate"), "avg_r": s.get("avg_r"),
        "profit_factor": s.get("profit_factor"), "oos_n": so.get("n", 0), "oos_avg_r": so.get("avg_r"),
        "grade": res["evidence"]["grade"], "score": res["evidence"]["score"],
        # favourable move before the stop, in ATR (median / 75th pct) - used for exit zones
        "mfe_med_atr": float(np.median(mfe_pre) * stop_atr) if len(mfe_pre) else None,
        "mfe_p75_atr": float(np.percentile(mfe_pre, 75) * stop_atr) if len(mfe_pre) else None,
    }
    if len(_quick_cache) > 500:
        _quick_cache.clear()
    _quick_cache[key] = q
    return q


def decay_check(r: np.ndarray) -> dict:
    """Compare the most recent third of occurrences with everything before it."""
    n = len(r)
    if n < 30:
        return {"status": "insufficient", "message": "Fewer than 30 occurrences - cannot assess stability."}
    k = max(10, n // 3)
    old, new = r[:-k], r[-k:]
    t, p = sps.ttest_ind(new, old, equal_var=False)
    res = {"recent_n": int(k), "recent_avg_r": float(new.mean()), "earlier_avg_r": float(old.mean()), "p_value": float(p)}
    if new.mean() <= 0 < old.mean():
        res["status"], res["message"] = "broken", "The edge has disappeared in the most recent occurrences."
    elif new.mean() < 0.5 * old.mean() and old.mean() > 0:
        res["status"], res["message"] = "weakening", "Recent performance is less than half of the historical average."
    elif new.mean() > old.mean() * 1.5 and new.mean() > 0:
        res["status"], res["message"] = "improving", "Recent performance is stronger than the historical average."
    else:
        res["status"], res["message"] = "stable", "Recent performance is consistent with history."
    return res


def best_worst_filters(symbol: str, tf: str, df: pd.DataFrame, ev: np.ndarray, r: np.ndarray,
                       exclude: set[str], top: int = 6) -> dict:
    """Which additional condition most improves / degrades this setup?"""
    if len(ev) < 40:
        return {"best": [], "worst": []}
    ids, mat = condition_matrix(symbol, tf, df)
    sub = mat[ev]
    base = r.mean()
    min_n = max(15, len(ev) // 20)
    rows = []
    for j, cid in enumerate(ids):
        if cid in exclude:
            continue
        m = sub[:, j]
        k = int(m.sum())
        if k < min_n or k > len(ev) - min_n:
            continue
        avg = r[m].mean()
        shrunk = (k * avg + 30 * base) / (k + 30)
        rows.append({"id": cid, "label": BY_ID[cid].label, "n": k, "avg_r": float(avg),
                     "win_rate": float((r[m] > 0).mean()), "shrunk_r": float(shrunk), "delta": float(shrunk - base)})
    rows.sort(key=lambda x: x["delta"], reverse=True)
    return {"best": [x for x in rows[:top] if x["delta"] > 0], "worst": [x for x in rows[::-1][:top] if x["delta"] < 0]}
