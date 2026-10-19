"""Portfolio simulation of the watched setups traded together as one account.

Each setup's historical trades (with its own execution plan) are merged in time
order and replayed with account rules:
  * fixed-fractional risk: each trade risks `risk_pct` of current equity,
  * at most `max_open` trades open at once and at most `max_open_risk` % total,
  * optionally one open position per instrument.
Trades that would break a rule are skipped (and counted).

A Monte Carlo stress test then reshuffles the realised trade results thousands
of times to estimate how deep drawdowns can get through plain bad luck, and
which risk-per-trade keeps the 95th-percentile drawdown inside the user's limit.
"""
from __future__ import annotations

import time
from collections import defaultdict

import numpy as np
import pandas as pd

from ..live import db
from .optimize import ExecutionLab
from .simulate import BASELINE, Plan

DEFAULTS = {"balance": 10000.0, "risk_pct": 1.0, "max_open": 3, "max_open_risk": 3.0, "one_per_symbol": True,
            "max_dd_tolerance": 20.0, "period": "all"}
RISK_LEVELS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
MC_RUNS = 2000


def settings() -> dict:
    return DEFAULTS | (db.get_setting("portfolio") or {})


def save_settings(s: dict) -> dict:
    cur = settings() | {k: v for k, v in s.items() if k in DEFAULTS and v is not None}
    db.set_setting("portfolio", cur)
    return cur


def _strategy_trades(w: dict) -> list[dict]:
    """Historical trades of a CRT / SMC strategy watch, in the simulator's trade format."""
    from ..research.hypothesis import WARMUP
    from ..strategies import crt, live as strat
    if w["plan"].startswith("smc|"):
        from ..strategies.ltf_search import collect_one
        r = strat._smc_parse(w["plan"])
        df = collect_one(w["symbol"], r["tf"], r)
        return [{"entry_time": int(t.entry_time), "exit_time": int(t.exit_time), "r": float(t.r),
                 "oos": bool(t.oos), "exit": t.why, "symbol": w["symbol"], "direction": t.direction, "setup": w["id"]}
                for t in df.itertuples()]
    from ..features.engine import get_features
    r = strat.parse(w["plan"])
    eng = crt.CRT(w["symbol"], r["htf"])
    flags = strat.setup_filters(eng, get_features(w["symbol"], r["htf"])[0])
    n = len(eng.ht)
    sp = WARMUP + int((n - WARMUP) * 0.7)
    return [{"entry_time": t["entry_time"], "exit_time": t["exit_time"], "r": t["r"], "oos": t["i"] >= sp, "exit": t["exit"],
             "symbol": w["symbol"], "direction": t["direction"], "setup": w["id"]}
            for t in eng.trades(r["mode"], target_mode=r["target"])
            if "r" in t and t["i"] >= WARMUP and strat._passes(flags[(t["i"], t["direction"])], r["filters"])]


def _setup_trades(w: dict) -> list[dict]:
    from ..strategies.live import is_strategy
    if is_strategy(w):
        return _strategy_trades(w)
    plan = Plan.from_key(w.get("plan") or Plan("next", f"atr{w['stop_atr']:g}", f"r{w['target_r']:g}", "none").key)
    if plan.entry != "next":
        plan = BASELINE
    lab = ExecutionLab(w["symbol"], w["tf"], w["conditions"], w["direction"], w["cooldown"])
    tr = lab.trades(plan)
    for t in tr:
        t["setup"] = w["id"]
    return tr


def _max_dd(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    return float(((peak - eq) / peak).max()) if len(eq) else 0.0


def simulate_account(trades: list[dict], cfg: dict) -> dict:
    trades = sorted(trades, key=lambda t: (t["entry_time"], t["setup"]))
    equity = cfg["balance"]
    open_: list[dict] = []
    taken, skipped = [], defaultdict(int)
    curve = [(trades[0]["entry_time"] if trades else int(time.time()), equity)]

    def close_until(ts):
        nonlocal equity
        open_.sort(key=lambda x: x["exit_time"])
        while open_ and open_[0]["exit_time"] <= ts:
            t = open_.pop(0)
            equity += t["pnl"]
            curve.append((t["exit_time"], equity))

    for t in trades:
        close_until(t["entry_time"])
        if len(open_) >= cfg["max_open"]:
            skipped["max open trades"] += 1
            continue
        if cfg["one_per_symbol"] and any(o["symbol"] == t["symbol"] for o in open_):
            skipped["already in this instrument"] += 1
            continue
        open_risk = sum(o["risk_pct"] for o in open_)
        if open_risk + cfg["risk_pct"] > cfg["max_open_risk"] + 1e-9:
            skipped["max total open risk"] += 1
            continue
        risk_money = equity * cfg["risk_pct"] / 100
        tt = t | {"risk_pct": cfg["risk_pct"], "pnl": t["r"] * risk_money, "equity_before": equity}
        open_.append(tt)
        taken.append(tt)
    close_until(float("inf"))

    eq = np.array([e for _, e in curve])
    r = np.array([t["r"] for t in taken])
    first, last = curve[0][0], curve[-1][0]
    years = max((last - first) / (365.25 * 86400), 1 / 365.25)
    total = equity / cfg["balance"] - 1
    # yearly / monthly returns from the equity curve
    s = pd.Series(eq, index=pd.to_datetime([c[0] for c in curve], unit="s", utc=True))
    s = s[~s.index.duplicated(keep="last")]
    yearly = s.resample("YE").last()
    yr = (yearly / yearly.shift(1).fillna(cfg["balance"]) - 1)
    monthly = s.resample("ME").last().ffill()
    mo = (monthly / monthly.shift(1).fillna(cfg["balance"]) - 1)
    losing = 0
    worst = 0
    for x in r:
        losing = losing + 1 if x < 0 else 0
        worst = max(worst, losing)
    per_setup = defaultdict(lambda: {"trades": 0, "r": 0.0, "wins": 0, "pnl": 0.0})
    for t in taken:
        p = per_setup[t["setup"]]
        p["trades"] += 1
        p["r"] += t["r"]
        p["wins"] += t["r"] > 0
        p["pnl"] += t["pnl"]
    # drawdown duration (longest time below a previous peak)
    peak_t, longest, peak = curve[0][0], 0, curve[0][1]
    for ts, e in curve:
        if e >= peak:
            peak, peak_t = e, ts
        else:
            longest = max(longest, ts - peak_t)
    step = max(1, len(curve) // 1500)
    return {
        "start": int(first), "end": int(last), "years": years,
        "final_equity": float(equity), "total_return": float(total),
        "cagr": float((equity / cfg["balance"]) ** (1 / years) - 1) if equity > 0 else -1.0,
        "max_dd": _max_dd(eq), "longest_dd_days": longest / 86400,
        "trades": len(taken), "skipped": dict(skipped), "win_rate": float((r > 0).mean()) if len(r) else None,
        "avg_r": float(r.mean()) if len(r) else None, "worst_losing_streak": worst,
        "trades_per_month": len(taken) / max(years * 12, 1e-9),
        "profitable_months": float((mo.dropna() > 0).mean()) if len(mo.dropna()) else None,
        "yearly": [{"year": int(i.year), "return": float(v)} for i, v in yr.items() if np.isfinite(v)],
        "monthly": [{"month": i.strftime("%Y-%m"), "return": float(v)} for i, v in mo.items() if np.isfinite(v)],
        "per_setup": [{"setup": k, **v, "avg_r": v["r"] / v["trades"] if v["trades"] else None} for k, v in per_setup.items()],
        "curve": [{"time": int(c[0]), "value": float(c[1])} for c in curve[::step]] + ([{"time": int(curve[-1][0]), "value": float(curve[-1][1])}] if step > 1 else []),
        "taken_r": r.tolist(),
    }


def monte_carlo(r: np.ndarray, max_dd_tol: float, seed: int = 7) -> dict:
    """Reshuffle (bootstrap) the trade sequence to see drawdowns that bad luck could produce."""
    if len(r) < 20:
        return {"note": "Too few trades for a meaningful stress test."}
    rng = np.random.default_rng(seed)
    sims = rng.choice(r, size=(MC_RUNS, len(r)), replace=True)
    table = []
    for risk in RISK_LEVELS:
        growth = np.cumprod(1 + sims * risk / 100, axis=1)
        growth = np.clip(growth, 1e-9, None)
        peak = np.maximum.accumulate(np.concatenate([np.ones((MC_RUNS, 1)), growth], axis=1), axis=1)[:, 1:]
        dd = ((peak - growth) / peak).max(axis=1)
        final = growth[:, -1] - 1
        table.append({"risk_pct": risk, "median_return": float(np.median(final)), "p5_return": float(np.percentile(final, 5)),
                      "median_dd": float(np.median(dd)), "p95_dd": float(np.percentile(dd, 95)),
                      "p_dd_over_limit": float((dd > max_dd_tol / 100).mean()), "p_loss": float((final < 0).mean())})
    ok = [t for t in table if t["p95_dd"] <= max_dd_tol / 100]
    return {"runs": MC_RUNS, "trades_per_run": int(len(r)), "table": table,
            "suggested_risk_pct": ok[-1]["risk_pct"] if ok else None}


def overlap(trades: list[dict], ids: list[int]) -> list[dict]:
    """How often two setups are in the market at the same time, and how their results co-move."""
    by = defaultdict(list)
    for t in trades:
        by[t["setup"]].append(t)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            A, B = by.get(a, []), by.get(b, [])
            if not A or not B:
                continue
            Bs = sorted(B, key=lambda x: x["entry_time"])
            starts = np.array([x["entry_time"] for x in Bs])
            ends = np.array([x["exit_time"] for x in Bs])
            ov = sum(bool(((starts < t["exit_time"]) & (ends > t["entry_time"])).any()) for t in A)
            da = pd.Series([t["r"] for t in A], index=pd.to_datetime([t["exit_time"] for t in A], unit="s")).resample("W").sum()
            dbs = pd.Series([t["r"] for t in B], index=pd.to_datetime([t["exit_time"] for t in B], unit="s")).resample("W").sum()
            j = pd.concat([da, dbs], axis=1).fillna(0)
            corr = float(j.iloc[:, 0].corr(j.iloc[:, 1])) if len(j) > 10 else None
            out.append({"a": a, "b": b, "overlap_share": ov / len(A), "weekly_corr": corr if corr == corr else None})
    return out


def run(cfg_override: dict | None = None) -> dict:
    cfg = settings() | (cfg_override or {})
    watches = db.list_watches(active_only=True)
    if not watches:
        return {"error": "No active watched setups. Add setups from the Research lab or Discovered patterns."}
    trades, setups = [], []
    for w in watches:
        try:
            tr = _setup_trades(w)
        except Exception as e:
            setups.append({"id": w["id"], "name": w["name"], "error": str(e)})
            continue
        if cfg["period"] == "unseen":
            tr = [t for t in tr if t["oos"]]
        trades += tr
        setups.append({"id": w["id"], "name": w["name"], "symbol": w["symbol"], "tf": w["tf"], "direction": w["direction"],
                       "plan": w.get("plan") or BASELINE.key, "trades": len(tr)})
    if not trades:
        return {"error": "No historical trades for the selected period.", "setups": setups}
    acc = simulate_account(trades, cfg)
    mc = monte_carlo(np.array(acc.pop("taken_r")), cfg["max_dd_tolerance"])
    return {"config": cfg, "setups": setups, "account": acc, "monte_carlo": mc,
            "overlap": overlap(trades, [s["id"] for s in setups if "error" not in s]), "computed": int(time.time())}
