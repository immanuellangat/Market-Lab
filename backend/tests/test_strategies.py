"""Named strategies: rule correctness and causality."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import crt


def test_crt_setup_rules():
    #            A      B (sweeps A high, closes inside)   C (outside bar: takes both sides)  D (sweeps C low, closes inside)
    h = np.array([10.0, 10.5, 11.0, 10.4])
    l = np.array([9.0, 9.2, 8.5, 8.0])
    c = np.array([9.5, 9.8, 9.0, 9.0])
    short, long = crt.find_setups(h, l, c)
    assert list(short) == [1]
    assert list(long) == [3]


def test_ltf_swings_are_causal():
    rng = np.random.default_rng(3)
    c = 100 + np.cumsum(rng.normal(0, 1, 600))
    h, l = c + rng.uniform(0, 1, 600), c - rng.uniform(0, 1, 600)
    full = crt.ltf_swings(h, l)
    cut = crt.ltf_swings(h[:400], l[:400])
    for k in ("sh", "sl", "sh_idx", "sl_idx"):
        assert np.allclose(full[k][:400], cut[k], equal_nan=True), k


def test_crt_trade_levels():
    """One hand-built short setup: entry at the next H1 open, stop beyond the sweep wick, target A's low."""
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    n = 40
    hc = np.full(n, 100.0)
    hdf = pd.DataFrame({"time": [t0 + pd.Timedelta(hours=k) for k in range(n)], "open": hc, "high": hc + 1,
                        "low": hc - 1, "close": hc, "spread": 0.0})
    hdf.loc[30, ["high", "close"]] = [101.5, 100.2]   # B sweeps A's high (101) and closes back inside
    hdf.loc[29, "low"] = 98.0                         # A: range 98-101
    hdf.loc[30, "low"] = 99.5                         # ... without touching A's low
    m = n * 12
    mt = [t0 + pd.Timedelta(minutes=5 * k) for k in range(m)]
    lp = np.full(m, 100.2)
    ldf = pd.DataFrame({"time": mt, "open": lp, "high": lp + 0.1, "low": lp - 0.1, "close": lp, "spread": 0.0})
    j = 31 * 12 + 3
    ldf.loc[j, "low"] = 97.9                          # target hit 3 bars after entry
    eng = crt.CRT("EURUSD", "H1", hdf, ldf)
    tr = [t for t in eng.trades("close") if t["i"] == 30]
    assert len(tr) == 1 and tr[0]["direction"] == "short"
    t = tr[0]
    assert t["entry"] == 100.2 and t["target"] == 98.0
    assert np.isclose(t["stop"], 101.5 + crt.STOP_BUFFER_ATR * eng.hatr[30])
    assert t["exit"] == "target" and np.isclose(t["r"], (100.2 - 98.0) / (t["stop"] - 100.2))


def test_smc_sequence_order():
    from app.strategies import ltf_search as ls
    n = 60
    sweep, shift, retest = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool)
    sweep[5], shift[10], retest[[12, 14]] = True, True, True      # valid: only the first retest after the shift counts
    shift[30], retest[33] = True, True                             # shift without a preceding sweep -> ignored
    sweep[40], retest[41], shift[45] = True, True, True            # retest before the shift -> ignored
    out = ls._smc(sweep, shift, retest)
    assert list(np.flatnonzero(out)) == [12]
