"""Core correctness tests: no look-ahead, outcome labelling, end-to-end research run."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data import store
from app.features import engine
from app.research import outcomes


def synth_m5(days: int = 160, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    t = pd.date_range("2023-01-02", periods=days * 288, freq="5min", tz="UTC")
    t = t[t.dayofweek < 5]
    hour = t.hour.to_numpy()
    vol = np.where((hour >= 7) & (hour < 17), 1.6, 0.7) * 0.0003
    ret = rng.standard_t(4, len(t)) * vol / 1.4
    close = 1.10 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[1.10], close[:-1]])
    wick = np.abs(rng.normal(0, 1, (2, len(t)))) * vol * 1.10
    high = np.maximum(open_, close) + wick[0]
    low = np.minimum(open_, close) - wick[1]
    return pd.DataFrame({"time": t, "open": open_, "high": high, "low": low, "close": close,
                         "volume": 100.0, "spread": 10.0})


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    r = df.set_index("time").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "spread": "mean"})
    return r.dropna().reset_index()


@pytest.fixture
def datadir(tmp_path, monkeypatch):
    (tmp_path / "bars").mkdir()
    (tmp_path / "features").mkdir()
    monkeypatch.setattr(store, "BARS_DIR", tmp_path / "bars")
    monkeypatch.setattr(store, "META_FILE", tmp_path / "meta.json")
    monkeypatch.setattr(engine, "FEATURES_DIR", tmp_path / "features")
    engine.invalidate()
    return tmp_path


def write_all(m5: pd.DataFrame, cut: pd.Timestamp | None = None):
    for tf, rule, mins in (("M5", None, 5), ("H1", "1h", 60), ("H4", "4h", 240)):
        d = m5 if rule is None else resample(m5, rule)
        if cut is not None:  # keep only bars that had closed by `cut`
            d = d[d["time"] + pd.Timedelta(minutes=mins) <= cut]
        store.save_bars("EURUSD", tf, d.reset_index(drop=True))
    engine.invalidate()


def test_no_lookahead(datadir):
    m5 = synth_m5()
    write_all(m5)
    full, _ = engine.get_features("EURUSD", "M5", wait=True)
    k = 25000
    cut = m5["time"].iloc[k - 1] + pd.Timedelta(minutes=5)
    write_all(m5.iloc[:k], cut)
    part, _ = engine.get_features("EURUSD", "M5", wait=True)
    assert len(part) == k
    a, b = full.iloc[:k].reset_index(drop=True), part.reset_index(drop=True)
    bad = []
    for c in a.columns:
        x, y = a[c], b[c]
        if pd.api.types.is_numeric_dtype(x) or pd.api.types.is_bool_dtype(x):
            same = np.allclose(x.astype(float), y.astype(float), equal_nan=True, rtol=1e-9, atol=1e-12)
        else:
            same = x.equals(y)
        if not same:
            bad.append(c)
    assert not bad, f"Columns that changed when future bars were removed (look-ahead): {bad}"


def test_outcome_labels():
    # entry at next open = 100, ATR = 1 -> stop 99, 2R target 102
    df = pd.DataFrame({
        "open":  [100, 100, 100.5, 101.0, 101.5],
        "high":  [100, 100.6, 101.2, 102.1, 101.6],
        "low":   [100, 99.6, 100.4, 100.9, 101.0],
        "close": [100, 100.5, 101.0, 101.5, 101.2],
        "atr":   [1.0, 1, 1, 1, 1], "spread": 0.0,
    }, dtype=float)
    out = outcomes.compute_outcomes(df, horizon=3, stop_atr=1.0)
    assert out.loc[0, "L_r2"] == 2.0          # target hit on 3rd bar after entry, stop never touched
    assert out.loc[0, "L_r1"] == 1.0
    assert out.loc[0, "S_r2"] == -1.0         # short: high reached 101 -> stopped
    # same-bar ambiguity resolves as a loss
    df2 = df.copy()
    df2.loc[1, ["high", "low"]] = [103.0, 98.0]
    assert outcomes.compute_outcomes(df2, 3, 1.0).loc[0, "L_r2"] == -1.0


def test_research_end_to_end(datadir):
    from app.research.hypothesis import run_test
    from app.research.zones import key_zones
    write_all(synth_m5(seed=3))
    res = run_test("EURUSD", "M5", ["fvg_bull_retest", "sess_london"], "long")
    s = res["stats"]
    assert s["all"]["n"] == s["in_sample"]["n"] + s["out_of_sample"]["n"] > 0
    assert 0 <= res["evidence"]["score"] <= 100
    # Random-walk data must not show a strong edge
    assert res["evidence"]["grade"] in ("C", "D", "F")
    z = key_zones("EURUSD", "M5")
    assert "buy_zones" in z and "sell_zones" in z
    z2 = key_zones("EURUSD", "M5", at_index=20000)
    assert z2["index"] == 20000
