"""Trade statistics with sample-size-aware reliability measures."""
from __future__ import annotations

import math

import numpy as np
from scipy import stats as sps

SHRINK_M = 50  # pseudo-trades pulling small samples toward the baseline


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / den)


def max_drawdown(r: np.ndarray) -> float:
    if len(r) == 0:
        return 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float((peak - eq).max())


def max_losing_streak(r: np.ndarray) -> int:
    best = cur = 0
    for x in r:
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return best


def summarize(r: np.ndarray, target: float, baseline: float | None = None, years: float | None = None,
              mfe: np.ndarray | None = None, mae: np.ndarray | None = None, bars: np.ndarray | None = None,
              gross: np.ndarray | None = None) -> dict:
    """r: net R per trade in chronological order. gross: R before costs (for win/loss classification)."""
    r = np.asarray(r, float)
    n = len(r)
    g = r if gross is None else np.asarray(gross, float)
    if n == 0:
        return {"n": 0}
    wins = int(np.sum(g >= target - 1e-9))
    losses = int(np.sum(g <= -1 + 1e-9))
    timeouts = n - wins - losses
    mean = float(r.mean())
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    base = 0.0 if baseline is None else baseline
    t = (mean - base) / (sd / math.sqrt(n)) if n > 1 and sd > 0 else 0.0
    p = float(1 - sps.t.cdf(t, df=n - 1)) if n > 1 else 1.0
    out = {
        "n": n,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": wins / n,
        "win_rate_lb": wilson_lower(wins, n),
        "breakeven_win_rate": 1 / (1 + target),
        "avg_r": mean,
        "median_r": float(np.median(r)),
        "total_r": float(r.sum()),
        "profit_factor": float(pos / neg) if neg > 0 else (float("inf") if pos > 0 else 0.0),
        "max_dd_r": max_drawdown(r),
        "max_losing_streak": max_losing_streak(r),
        "sharpe_per_trade": mean / sd if sd > 0 else 0.0,
        "baseline_r": baseline,
        "edge_r": mean - base,
        "t_stat": t,
        "p_value": p,
        "shrunk_r": (n * mean + SHRINK_M * base) / (n + SHRINK_M),
        "trades_per_year": n / years if years else None,
    }
    if n >= 10:
        rng = np.random.default_rng(7)
        boots = rng.choice(r, size=(min(1000, max(200, 200000 // n)), n), replace=True).mean(axis=1)
        out["avg_r_ci"] = [float(np.percentile(boots, 5)), float(np.percentile(boots, 95))]
    if mfe is not None and len(mfe):
        out["avg_mfe_atr"] = float(np.nanmean(mfe))
        out["avg_mae_atr"] = float(np.nanmean(mae))
    if bars is not None and len(bars):
        out["avg_bars"] = float(np.nanmean(bars))
    return out


def evidence_score(is_s: dict, oos_s: dict) -> dict:
    """Combine significance, sample size and out-of-sample agreement into 0-100.

    This measures how strong the *historical evidence* is. It is not a
    probability that the next trade wins.
    """
    n = is_s.get("n", 0)
    if n < 5:
        return {"score": 0, "grade": "F", "reasons": ["Too few occurrences to say anything."]}
    reasons = []
    sig = max(0.0, 1 - 2 * is_s["p_value"]) if is_s["edge_r"] > 0 else 0.0
    reasons.append(f"In-sample edge vs. random entry: {is_s['edge_r']:+.3f}R (p={is_s['p_value']:.3f}).")
    sample = min(1.0, math.log10(max(n, 1)) / math.log10(500))
    reasons.append(f"Sample size factor {sample:.2f} from {n} in-sample occurrences.")
    on = oos_s.get("n", 0)
    if on < 15:
        oos = 0.5
        reasons.append(f"Out-of-sample unproven: only {on} occurrences.")
    elif oos_s["edge_r"] > 0 and oos_s["avg_r"] > 0:
        oos = 1.0 if oos_s["edge_r"] >= 0.5 * max(is_s["edge_r"], 1e-9) else 0.8
        reasons.append(f"Out-of-sample confirms: {oos_s['avg_r']:+.3f}R over {on} trades.")
    elif oos_s["avg_r"] > 0:
        oos = 0.5
        reasons.append("Out-of-sample positive but no better than random entry.")
    else:
        oos = 0.15
        reasons.append(f"Out-of-sample FAILED: {oos_s['avg_r']:+.3f}R over {on} trades.")
    score = round(100 * sig * sample * oos)
    grade = "A" if score >= 75 else "B" if score >= 55 else "C" if score >= 35 else "D" if score >= 15 else "F"
    return {"score": score, "grade": grade, "reasons": reasons,
            "components": {"significance": sig, "sample": sample, "oos": oos}}
