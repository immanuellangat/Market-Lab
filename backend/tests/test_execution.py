"""Execution layer: simulator correctness, consistency with the outcome matrix, no selection leakage."""
from __future__ import annotations

import numpy as np

from app.execution.simulate import BASELINE, Plan, simulate

from .test_core import datadir, synth_m5, write_all  # noqa: F401


def test_simulator_rules():
    h = np.array([100.6, 101.2, 102.1]); l = np.array([99.6, 100.4, 100.9]); c = np.array([100.5, 101.0, 101.5])
    assert simulate(h, l, c, "long", 100, 99, 102, "none", 1.0)[0] == 2.0
    assert simulate(h, l, c, "long", 100, 99, 102, "partial", 1.0)[0] == 1.5      # half at 1R + half at 2R
    # same candle touches stop and target -> stop first
    assert simulate(np.array([103.0]), np.array([98.0]), np.array([100.0]), "long", 100, 99, 102, "none", 1.0)[0] == -1.0
    # break-even: after +1R, a return to entry exits at 0R (stop move applies from the next candle)
    r, _, why = simulate(np.array([101.5, 100.5]), np.array([99.5, 99.9]), np.array([101.0, 100.0]), "long", 100, 99, 103, "be", 1.0)
    assert r == 0.0 and why == "breakeven"
    # short mirror
    assert simulate(-l, -h, -c, "short", -100, -99, -102, "none", 1.0)[0] == 2.0  # mirrored: high=-low


def test_baseline_plan_matches_outcome_matrix(datadir):
    from app.execution.optimize import ExecutionLab
    from app.research.outcomes import compute_outcomes
    write_all(synth_m5(days=250, seed=8))
    lab = ExecutionLab("EURUSD", "H1", ["trend_bull"], "long", cooldown=5)
    res = lab.run_plan(BASELINE)
    out = compute_outcomes(lab.df, lab.H, 1.0, 0.0)
    trades = lab.base_trades("next", "atr1")
    a = np.array([simulate(*t["path"], "long", t["entry"], t["stop"], t["entry"] + 2 * (t["entry"] - t["stop"]), "none", t["atr"], 0.0)[0]
                  for t in trades if t["taken"]])
    b = out["L_r2"].to_numpy()[[t["i"] for t in trades if t["taken"]]]
    ok = np.isfinite(b)
    assert np.allclose(a[ok], b[ok]), "simulator and outcome matrix disagree on the baseline plan"
    assert res["is"]["signals"] > 0


def test_levels_module_matches_optimiser(datadir):
    from app.execution.levels import levels_for_entry, signal_spec
    from app.execution.optimize import ExecutionLab
    write_all(synth_m5(days=250, seed=8))
    lab = ExecutionLab("EURUSD", "H1", ["trend_bull"], "long", cooldown=5)
    for key in ("next|struct|liq|none", "next|atr1.5|r3|none"):
        p = Plan.from_key(key)
        for t in lab.base_trades("next", p.stop)[:50]:
            stop, tgt = levels_for_entry(signal_spec(p, lab.df, t["i"], "long"), t["entry"])
            assert np.isclose(stop, t["stop"])
            want = t["liq"] if p.target == "liq" else t["entry"] + 3 * (t["entry"] - t["stop"])
            assert np.isclose(tgt, want)


def test_plan_choice_ignores_out_of_sample(datadir):
    """The chosen plan must depend on in-sample data only."""
    from app.execution.optimize import ExecutionLab
    write_all(synth_m5(days=250, seed=8))
    lab = ExecutionLab("EURUSD", "H1", ["trend_bull"], "long", cooldown=5)
    first = lab.optimise()["best"]["key"]
    sp = lab.sp
    rng = np.random.default_rng(0)
    for name in ("h", "l", "c", "o"):  # scramble every price after the split
        arr = getattr(lab, name).copy()
        arr[sp:] = arr[sp:] * (1 + rng.normal(0, 0.01, len(arr) - sp))
        setattr(lab, name, arr)
    if lab.ltf:
        cut = int(np.searchsorted(lab.lt, int(lab.df["time"].iloc[sp].timestamp())))
        for name in ("lh", "ll", "lc", "lo_"):
            arr = getattr(lab, name).copy()
            arr[cut:] = arr[cut:] * (1 + rng.normal(0, 0.01, len(arr) - cut))
            setattr(lab, name, arr)
    lab._base_cache.clear()
    assert lab.optimise()["best"]["key"] == first
