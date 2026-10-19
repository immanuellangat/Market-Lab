"""The Alchemist strategy (as described by the user on 2026-10-19), tested honestly.

Description given: market direction -> liquidity sweep -> CHoCH or BOS -> entry at an order block or FVG ->
stop beyond the swing / sweep extreme -> target the next liquidity, or 1:2 / 1:3.

Rules fixed BEFORE any result was seen (long shown; shorts mirror):
  direction  "htf": the first higher timeframe's structure is bullish (H1 for M15, H4 for H1, D1 for H4);
             "none": no direction filter (reference, to see whether step 1 adds anything)
  sweep      swing low, previous-day low, Asian low or equal lows swept (same detection as the rest of the app)
  confirm    "choch": bullish CHoCH / MSS within 20 bars after the sweep; "any": CHoCH, MSS or BOS
  entry      "fvg" / "ob" / "any": first retest of a bullish FVG / order block / either within 20 bars after
             the shift; entry at the next candle's open
  stop       0.1 ATR beyond the lowest low from the sweep candle to the signal candle (the sweep extreme);
             skipped if the entry is already beyond it
  target     "r2" 2R, "r3" 3R, "liq" nearest untaken liquidity at least 1R away (2R if there is none)
  timeframes M15, H1, H4; 6 markets; long + short pooled per rule.

Standards: spread deducted, stop before target inside a candle (shared simulator), oldest 70% in-sample, last 30%
unseen with in-sample trades still open at the split purged, Benjamini-Hochberg across all rules on unseen p-values.
"""
from __future__ import annotations

import itertools
import time

import numpy as np
import pandas as pd

from ..config import DEFAULT_HORIZON, HTF_CONTEXT, SYMBOLS, TIMEFRAMES
from ..data.store import bars_path
from ..discovery.search import FDR_Q, _bh
from ..execution.levels import LIQ_COLS
from ..execution.simulate import simulate
from ..features.engine import get_features
from ..live import db
from ..research.hypothesis import WARMUP, dedupe, point_size, split_index
from ..research.outcomes import spread_price
from ..research.stats import evidence_score, summarize
from .ltf_search import _b, _last_idx

KEY = "strategy_alchemist"
NAME = "Alchemist"
TFS = ("M15", "H1", "H4")
DIRS = ("htf", "none")
CONFIRMS = ("choch", "any")
ENTRIES = ("fvg", "ob", "any")
TARGETS = ("r2", "r3", "liq")
SEQ_BARS = 20
COOLDOWN = 6
MIN_OOS = 50


def signals(df: pd.DataFrame, d: str, confirm: str, entry: str) -> list[tuple[int, int]]:
    """(signal bar, sweep bar) pairs: retest after a shift that followed a sweep; each shift used once."""
    L = d == "long"
    sweep = (_b(df, "sweep_swing_low") | _b(df, "sweep_pdl") | _b(df, "sweep_asia_low") | _b(df, "sweep_eql")) if L else \
            (_b(df, "sweep_swing_high") | _b(df, "sweep_pdh") | _b(df, "sweep_asia_high") | _b(df, "sweep_eqh"))
    shift = (_b(df, "choch_bull") | _b(df, "mss_bull")) if L else (_b(df, "choch_bear") | _b(df, "mss_bear"))
    if confirm == "any":
        shift = shift | _b(df, "bos_bull" if L else "bos_bear")
    fvg, ob = _b(df, "fvg_bull_retest" if L else "fvg_bear_retest"), _b(df, "ob_bull_retest" if L else "ob_bear_retest")
    retest = fvg if entry == "fvg" else ob if entry == "ob" else (fvg | ob)
    last_sw, last_sh = _last_idx(sweep), _last_idx(shift)
    out, used = [], set()
    for r in np.flatnonzero(retest):
        c = last_sh[r - 1] if r > 0 else -10**9
        if c < 0 or r - c > SEQ_BARS or c in used:
            continue
        s = last_sw[c]
        if s < 0 or c - s > SEQ_BARS:
            continue
        out.append((int(r), int(s)))
        used.add(c)
    return out


def collect(progress=None, markets=None) -> pd.DataFrame:
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
        htf = HTF_CONTEXT[tf][0].lower()
        trend = df[f"{htf}_ms_trend"].to_numpy()
        for d, confirm, entry in itertools.product(("long", "short"), CONFIRMS, ENTRIES):
            sg = 1 if d == "long" else -1
            liq = np.column_stack([df[col].to_numpy(float) for col in LIQ_COLS[d] if col in df.columns])
            sig = [(i, s) for i, s in signals(df, d, confirm, entry) if WARMUP <= i < n - H - 1]
            keep = set(dedupe(np.array([i for i, _ in sig], dtype=np.int64), COOLDOWN).tolist()) if sig else set()
            for i, s in sig:
                if i not in keep or (sp - H <= i < sp) or not (atr[i] > 0):
                    continue
                e = o[i + 1]
                stop = (l[s: i + 1].min() - 0.1 * atr[i]) if sg > 0 else (h[s: i + 1].max() + 0.1 * atr[i])
                risk = (e - stop) * sg
                if risk <= 0:
                    continue
                cand = [v for v in liq[i] if np.isfinite(v) and (v - e) * sg > risk]
                tgts = {"r2": e + sg * 2 * risk, "r3": e + sg * 3 * risk,
                        "liq": (min(cand) if sg > 0 else max(cand)) if cand else e + sg * 2 * risk}
                sl = slice(i + 1, i + 1 + H)
                for tname, tgt in tgts.items():
                    r, held, why = simulate(h[sl], l[sl], c[sl], d, e, stop, tgt, "none", atr[i], spread[i])
                    if not np.isfinite(r):
                        continue
                    rows.append((symbol, tf, d, confirm, entry, tname, int(times[i]), float(r), float(r + spread[i] / risk),
                                 why, int(times[i + 1]), int(times[i + held]) + tf_s, bool(i >= sp), float(e), float(stop),
                                 float(tgt), float(risk / atr[i]), float(abs(tgt - e) / risk), bool(trend[i] == sg)))
    cols = ["symbol", "tf", "direction", "confirm", "entry", "target", "time", "r", "gross", "why", "entry_time",
            "exit_time", "oos", "entry_price", "stop", "target_price", "risk_atr", "rr", "htf"]
    return pd.DataFrame(rows, columns=cols).sort_values("time", kind="stable").reset_index(drop=True)


def _stats(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0}
    r = d["r"].to_numpy()
    years = max((d["time"].iloc[-1] - d["time"].iloc[0]) / (365.25 * 86400), 1 / 365.25)
    s = summarize(r, 0.0, 0.0, years, gross=d["gross"].to_numpy())
    s.update(win_rate=float((d["why"] == "target").mean()), gross_r=float(d["gross"].mean()),
             wins=int((d["why"] == "target").sum()), losses=int((d["why"] == "stop").sum()),
             median_risk_atr=float(d["risk_atr"].median()), median_rr=float(d["rr"].median()))
    return s


def run(progress=None) -> dict:
    df = collect(progress)
    rules = []
    for tf, dirf, confirm, entry, target in itertools.product(TFS, DIRS, CONFIRMS, ENTRIES, TARGETS):
        m = (df["tf"] == tf) & (df["confirm"] == confirm) & (df["entry"] == entry) & (df["target"] == target)
        if dirf == "htf":
            m &= df["htf"]
        d = df[m]
        s_is, s_oos = _stats(d[~d["oos"]]), _stats(d[d["oos"]])
        ev = evidence_score(s_is, s_oos) if s_is.get("n") else {"score": 0, "grade": "F", "reasons": []}
        per_mkt = {sym: _stats(g[g["oos"]]).get("avg_r") for sym, g in d.groupby("symbol")}
        rules.append({"id": f"alchemist|{tf}|{dirf}|{confirm}|{entry}|{target}", "tf": tf, "direction_filter": dirf,
                      "confirm": confirm, "entry": entry, "target": target, "is": s_is, "oos": s_oos,
                      "grade": ev["grade"], "score": ev["score"], "oos_by_market": per_mkt,
                      "markets_positive_oos": sum(1 for v in per_mkt.values() if v is not None and v > 0)})
    p = np.array([r["oos"].get("p_value", 1.0) if r["oos"].get("n", 0) >= MIN_OOS else 1.0 for r in rules])
    q = _bh(p)
    for r, qv in zip(rules, q):
        r["q_oos"] = float(qv)
        r["validated"] = bool(r["oos"].get("n", 0) >= MIN_OOS and qv <= FDR_Q and r["oos"].get("avg_r", 0) > 0)
    out = {"finished": int(time.time()), "strategy": NAME, "rules_tested": len(rules), "trades": int(len(df)), "fdr_q": FDR_Q,
           "rule": (f"Validated = positive unseen expectancy after costs on >= {MIN_OOS} unseen trades, surviving "
                    f"Benjamini-Hochberg (q <= {FDR_Q}) across all {len(rules)} rules."),
           "validated": [r for r in rules if r["validated"]],
           "rules": sorted(rules, key=lambda r: -(r["is"].get("shrunk_r") or -9))}
    db.set_setting(KEY, out)
    return out


def latest() -> dict | None:
    return db.get_setting(KEY)
