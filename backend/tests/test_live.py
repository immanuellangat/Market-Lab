"""Live mode must agree with the full-history engine, and the scanner must behave."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.features import engine
from app.research.conditions import available

from .test_core import datadir, synth_m5, write_all  # noqa: F401  (fixture re-export)


def test_live_tail_matches_full_history(datadir):
    write_all(synth_m5(days=200, seed=5))
    full, _ = engine.get_features("EURUSD", "M5", wait=True)
    live, _ = engine.live_features("EURUSD", "M5")
    assert full["time"].iloc[-1] == live["time"].iloc[-1]
    n = 300
    f, l = full.iloc[-n:].reset_index(drop=True), live.iloc[-n:].reset_index(drop=True)
    bad = []
    for c in available(f):
        a = np.asarray(pd.Series(c.fn(full)).fillna(False))[-n:]
        b = np.asarray(pd.Series(c.fn(live)).fillna(False))[-n:]
        if not np.array_equal(a.astype(bool), b.astype(bool)):
            bad.append(c.id)
    assert not bad, f"conditions differ between live and full computation: {bad}"
    for col in ("atr", "rsi", "pd_pos", "last_sh", "last_sl"):
        assert np.allclose(f[col], l[col], equal_nan=True, rtol=1e-6), col


def test_scanner_signal_lifecycle(datadir):
    from app.live import db
    from app.live.service import LiveService, snapshot_stats

    write_all(synth_m5(days=120, seed=9))
    live, _ = engine.live_features("EURUSD", "M5")
    # pick a condition that is true on the last closed bar so the scan must fire
    true_now = [c.id for c in available(live) if bool(pd.Series(c.fn(live)).fillna(False).iloc[-1])]
    assert true_now
    w = {"name": "test", "symbol": "EURUSD", "tf": "M5", "direction": "long",
         "conditions": [true_now[0]], "target_r": 2.0, "stop_atr": 1.0, "cooldown": 5}
    w["stats"] = snapshot_stats(w)
    wid = db.add_watch(w)
    svc = LiveService()
    created = svc.scan("EURUSD", "M5")
    assert len(created) == 1
    assert svc.scan("EURUSD", "M5") == []  # same candle never signals twice
    sig = db.list_signals(watch_id=wid)[0]
    assert sig["status"] == "open"
    assert sig["payload"]["context"] and sig["payload"]["stats"]["all"]["n"] >= 0

    # a signal placed far enough in the past gets resolved from subsequent candles
    bars = engine.load_bars("EURUSD", "M5")
    t = int(bars["time"].iloc[-500].timestamp())
    db.add_signal({"watch_id": wid, "symbol": "EURUSD", "tf": "M5", "direction": "long", "bar_time": t,
                   "entry": 1.1, "stop": 1.09, "target": 1.12,
                   "payload": {"stop_atr": 1.0, "atr": float(live["atr"].iloc[-1]), "target_r": 2.0}})
    svc.resolve_open()
    old = [s for s in db.list_signals(watch_id=wid) if s["bar_time"] == t][0]
    assert old["status"] in ("win", "loss", "timeout") and old["result_r"] is not None
