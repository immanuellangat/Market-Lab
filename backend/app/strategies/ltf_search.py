"""SMC and price-action strategies on M5 / M15 (H1 for comparison), searched honestly.

Setups (long shown; shorts mirror). All fixed BEFORE any result was seen (2026-10-07):
  SMC  smc_fvg   liquidity sweep -> bullish CHoCH/MSS within 20 bars -> first bullish-FVG retest within 20 bars of it
       smc_ob    same, but the retest is into a bullish order block
       smc_any   either retest
  PA   pa_pin       hammer / pin bar whose wick sweeps a level (swing low, previous-day low, Asian low, equal lows)
       pa_engulf    bullish engulfing within 3 bars of such a sweep
       pa_ib        inside-bar breakout: the bar after an inside bar closes above it
       pa_retest    break-and-retest: closed above the previous-day high in the last 24 bars, now dips back to it and holds
Filters (every combination):
  bias1   first higher timeframe structure agrees (H1 for M5/M15, H4 for H1)
  bias2   second higher timeframe structure agrees (H4 for M5/M15, D1 for H1)
  htf_pd  longs in the first higher timeframe's discount, shorts in its premium
  killzone London 02-05 or New York 07-10 (NY time)
  ema     same-timeframe EMA trend agrees
Exit: entry at the next open; stop beyond the 10-bar extreme (0.5-3 ATR); target 2R ("r2") or the nearest
untaken liquidity at least 1R away ("liq"). Shared simulator: stop before target in a candle, spread deducted.

Rules are ranked on in-sample data; out-of-sample results get a Benjamini-Hochberg correction across all rules.
"""
from __future__ import annotations

import itertools
import time

import numpy as np
import pandas as pd

from ..config import DEFAULT_HORIZON, HTF_CONTEXT, SYMBOLS, TIMEFRAMES
from ..data.store import bars_path
from ..discovery.search import FDR_Q, _bh
from ..execution.levels import levels_for_entry, signal_spec
from ..execution.simulate import Plan, simulate
from ..features.engine import get_features
from ..live import db
from ..research.hypothesis import WARMUP, dedupe, point_size, split_index
from ..research.outcomes import spread_price
from ..research.stats import evidence_score, summarize

KEY = "strategy_ltf_search"
TFS = ("M5", "M15", "H1")
SETUPS = ("smc_fvg", "smc_ob", "smc_any", "pa_pin", "pa_engulf", "pa_ib", "pa_retest")
FILTERS = ("bias1", "bias2", "htf_pd", "killzone", "ema")
EXITS = {"r2": Plan("next", "struct", "r2", "none"), "liq": Plan("next", "struct", "liq", "none")}
SEQ_BARS = 20
COOLDOWN = 6
MIN_IS = 150
SHRINK = 150
MIN_OOS = 50


def _b(df: pd.DataFrame, c: str) -> np.ndarray:
    return df[c].fillna(False).to_numpy(bool)


def _recent(m: np.ndarray, w: int) -> np.ndarray:
    return pd.Series(m).rolling(w, min_periods=1).max().fillna(0).to_numpy(bool)


def _last_idx(m: np.ndarray) -> np.ndarray:
    n = len(m)
    return pd.Series(np.where(m, np.arange(n), np.nan)).ffill().fillna(-10**9).to_numpy(np.int64)


def _smc(sweep: np.ndarray, shift: np.ndarray, retest: np.ndarray) -> np.ndarray:
    """Retest bars that follow a shift which itself followed a sweep (each shift used once)."""
    last_sw, last_sh = _last_idx(sweep), _last_idx(shift)
    out = np.zeros(len(sweep), bool)
    used = set()
    for r in np.flatnonzero(retest):
        c = last_sh[r - 1] if r > 0 else -10**9   # shift strictly before the retest bar
        if c < 0 or r - c > SEQ_BARS or c in used:
            continue
        s = last_sw[c]
        if s < 0 or c - s > SEQ_BARS:
            continue
        out[r] = True
        used.add(c)
    return out


def setup_masks(df: pd.DataFrame) -> dict[tuple[str, str], np.ndarray]:
    out = {}
    for d in ("long", "short"):
        L = d == "long"
        sweep = (_b(df, "sweep_swing_low") | _b(df, "sweep_pdl") | _b(df, "sweep_asia_low") | _b(df, "sweep_eql")) if L else \
                (_b(df, "sweep_swing_high") | _b(df, "sweep_pdh") | _b(df, "sweep_asia_high") | _b(df, "sweep_eqh"))
        shift = (_b(df, "choch_bull") | _b(df, "mss_bull")) if L else (_b(df, "choch_bear") | _b(df, "mss_bear"))
        fvg = _b(df, "fvg_bull_retest" if L else "fvg_bear_retest")
        ob = _b(df, "ob_bull_retest" if L else "ob_bear_retest")
        out[("smc_fvg", d)] = _smc(sweep, shift, fvg)
        out[("smc_ob", d)] = _smc(sweep, shift, ob)
        out[("smc_any", d)] = _smc(sweep, shift, fvg | ob)
        out[("pa_pin", d)] = _b(df, "hammer" if L else "shooting_star") & sweep
        out[("pa_engulf", d)] = _b(df, "bull_engulf" if L else "bear_engulf") & _recent(sweep, 3)
        h, l, c = (df[k].to_numpy(float) for k in ("high", "low", "close"))
        ib_prev = np.concatenate([[False], _b(df, "inside_bar")[:-1]])
        ph, pl = np.concatenate([[np.nan], h[:-1]]), np.concatenate([[np.nan], l[:-1]])
        out[("pa_ib", d)] = ib_prev & ((c > ph) if L else (c < pl))
        lvl = df["pdh" if L else "pdl"].to_numpy(float)
        brk = _b(df, "break_pdh" if L else "break_pdl")
        held = ((l <= lvl) & (c > lvl)) if L else ((h >= lvl) & (c < lvl))
        out[("pa_retest", d)] = _recent(brk, 24) & held & ~brk
    return out


def filter_flags(df: pd.DataFrame, tf: str, d: str) -> dict[str, np.ndarray]:
    h1, h2 = (x.lower() for x in HTF_CONTEXT[tf][:2])
    sg = 1 if d == "long" else -1
    pd1 = df[f"{h1}_pd_pos"].to_numpy(float)
    return {
        "bias1": df[f"{h1}_ms_trend"].to_numpy() == sg,
        "bias2": df[f"{h2}_ms_trend"].to_numpy() == sg,
        "htf_pd": (pd1 < 0.5) if d == "long" else (pd1 > 0.5),
        "killzone": _b(df, "killzone"),
        "ema": df["ema_trend"].to_numpy() == sg,
    }


def collect_one(symbol: str, tf: str, rule: dict) -> pd.DataFrame:
    """Trades of one rule on one market (used for the live watch's statistics)."""
    df = collect(markets=[(symbol, tf)], setups={rule["setup"]}, exits={rule["exit"]})
    for f in rule["filters"]:
        df = df[df[f]]
    return df.reset_index(drop=True)


def collect(progress=None, markets=None, setups=None, exits=None) -> pd.DataFrame:
    rows = []
    jobs = markets or [(s, tf) for tf in TFS for s in SYMBOLS if bars_path(s, tf).exists()]
    for k, (symbol, tf) in enumerate(jobs):
        if progress:
            progress(k, len(jobs), f"{symbol} {tf}")
        df, _ = get_features(symbol, tf, wait=True)
        n, H = len(df), DEFAULT_HORIZON[tf]
        sp = split_index(df)
        o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
        atr = df["atr"].to_numpy(float)
        spread = spread_price(df, point_size(symbol))
        times = df["time"].dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")
        tf_s = TIMEFRAMES[tf] * 60
        masks = setup_masks(df)
        for (setup, d), m in masks.items():
            if setups and setup not in setups:
                continue
            m = m.copy()
            m[:WARMUP] = False
            m[n - H - 1:] = False   # outcome not known yet
            ev = dedupe(np.flatnonzero(m), COOLDOWN)
            ev = ev[~((ev >= sp - H) & (ev < sp))]   # purge in-sample trades still open at the split
            ev = ev[np.isfinite(atr[ev]) & (atr[ev] > 0)]
            ff = filter_flags(df, tf, d)
            for ex, plan in EXITS.items():
                if exits and ex not in exits:
                    continue
                for i in ev:
                    entry = o[i + 1]
                    stop, tgt = levels_for_entry(signal_spec(plan, df, int(i), d), entry)
                    sl = slice(i + 1, i + 1 + H)
                    r, held, why = simulate(h[sl], l[sl], c[sl], d, entry, stop, tgt, "none", atr[i], spread[i])
                    if not np.isfinite(r):
                        continue
                    rows.append((symbol, tf, setup, d, ex, int(times[i]), float(r), float(r + spread[i] / abs(entry - stop)),
                                 why, int(times[i + 1]), int(times[i + held]) + tf_s, bool(i >= sp),
                                 float(entry), float(stop), float(tgt), *(bool(ff[f][i]) for f in FILTERS)))
    cols = ["symbol", "tf", "setup", "direction", "exit", "time", "r", "gross", "why", "entry_time", "exit_time", "oos",
            "entry", "stop", "target", *FILTERS]
    return pd.DataFrame(rows, columns=cols).sort_values("time", kind="stable").reset_index(drop=True)


def _stats(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0}
    r = d["r"].to_numpy()
    years = max((d["time"].iloc[-1] - d["time"].iloc[0]) / (365.25 * 86400), 1 / 365.25)
    s = summarize(r, 0.0, 0.0, years, gross=d["gross"].to_numpy())
    s.update(win_rate=float((r > 0).mean()), gross_r=float(d["gross"].mean()),
             wins=int((d["why"] == "target").sum()), losses=int((d["why"] == "stop").sum()))
    return s


def search(df: pd.DataFrame) -> list[dict]:
    rules = []
    for (setup, tf, ex), base in df.groupby(["setup", "tf", "exit"], sort=True):
        F = {f: base[f].to_numpy(bool) for f in FILTERS}
        oos = base["oos"].to_numpy(bool)
        for k in range(len(FILTERS) + 1):
            for combo in itertools.combinations(FILTERS, k):
                m = np.ones(len(base), bool)
                for f in combo:
                    m &= F[f]
                d = base[m]
                s_is, s_oos = _stats(d[~oos[m]]), _stats(d[oos[m]])
                n = s_is.get("n", 0)
                rules.append({"family": "SMC" if setup.startswith("smc") else "PA", "setup": setup, "tf": tf, "exit": ex,
                              "filters": list(combo), "is": s_is, "oos": s_oos,
                              "is_score": n * s_is["avg_r"] / (n + SHRINK) if n >= MIN_IS else None,
                              "oos_by_symbol": {s: {"n": len(g), "avg_r": float(g["r"].mean())} for s, g in d[oos[m]].groupby("symbol")},
                              "is_by_symbol": {s: {"n": len(g), "avg_r": float(g["r"].mean())} for s, g in d[~oos[m]].groupby("symbol")}})
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
    out = {"finished": int(time.time()), "rules_tested": len(rules), "trades": int(len(df)), "fdr_q": FDR_Q,
           "top_in_sample": ranked[:25], "validated": [r for r in rules if r["validated"]],
           "top_win_rate": sorted([r for r in rules if r["is"].get("n", 0) >= MIN_IS], key=lambda r: -r["is"]["win_rate"])[:10]}
    db.set_setting(KEY, out)
    return out
