"""REST API."""
from __future__ import annotations

import threading
import traceback

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import DEFAULT_HORIZON, DEFAULT_STOP_ATR, SYMBOLS, TARGETS_R, TIMEFRAMES
from ..data import store
from ..data.mt5_source import MT5Error, source
from ..features import engine
from ..features.sessions import SESSIONS
from ..research.conditions import CATALOG
from ..research.hypothesis import run_test
from ..research.zones import key_zones

router = APIRouter(prefix="/api")


def _unix(t) -> np.ndarray:
    """UTC datetimes -> unix seconds, independent of the stored resolution (ns/us/ms)."""
    return t.dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")


def _f(x):
    """JSON-safe float."""
    if x is None:
        return None
    x = float(x)
    return x if np.isfinite(x) else None


# ------------------------------------------------------------------ meta / status
@router.get("/meta")
def meta():
    return {
        "symbols": [{"id": k, "label": v["label"]} for k, v in SYMBOLS.items()],
        "timeframes": list(TIMEFRAMES),
        "targets": list(TARGETS_R),
        "default_stop_atr": DEFAULT_STOP_ATR,
        "default_horizon": DEFAULT_HORIZON,
        "sessions": SESSIONS,
        "conditions": [{"id": c.id, "label": c.label, "category": c.category, "bias": c.bias,
                        "description": c.description} for c in CATALOG],
    }


_mt5_state: dict = {"connected": False}


@router.post("/mt5/connect")
def mt5_connect():
    global _mt5_state
    try:
        _mt5_state = source.connect()
    except MT5Error as e:
        _mt5_state = {"connected": False, "error": str(e)}
    return _mt5_state


@router.get("/status")
def status():
    return {"mt5": _mt5_state, "inventory": store.inventory(), "sync": _sync_state}


# ------------------------------------------------------------------ data sync
_sync_state: dict = {"running": False, "log": [], "progress": 0, "total": 0}


class SyncReq(BaseModel):
    symbols: list[str] | None = None
    timeframes: list[str] | None = None
    start: str | None = None


def _do_sync(req: SyncReq):
    syms = req.symbols or list(SYMBOLS)
    tfs = req.timeframes or list(TIMEFRAMES)
    _sync_state.update(running=True, log=[], progress=0, total=len(syms) * len(tfs))
    try:
        if not source.connected:
            _mt5_state.update(source.connect())
        for s in syms:
            for tf in tfs:
                try:
                    r = store.sync(source, s, tf, req.start)
                    _sync_state["log"].append(f"{s} {tf}: {r['bars']:,} bars (+{r['added']:,})")
                except Exception as e:  # keep going with the rest
                    _sync_state["log"].append(f"{s} {tf}: FAILED - {e}")
                _sync_state["progress"] += 1
            engine.invalidate(s)
    except Exception as e:
        _sync_state["log"].append(f"ERROR: {e}")
    finally:
        _sync_state["running"] = False


@router.post("/sync")
def sync(req: SyncReq):
    if _sync_state["running"]:
        raise HTTPException(409, "A sync is already running.")
    threading.Thread(target=_do_sync, args=(req,), daemon=True).start()
    return {"started": True}


class CsvReq(BaseModel):
    symbol: str
    tf: str
    path: str
    utc_offset_hours: float = 0.0


@router.post("/import_csv")
def import_csv(req: CsvReq):
    r = store.import_csv(req.symbol, req.tf, req.path, req.utc_offset_hours)
    engine.invalidate(req.symbol)
    return r


# ------------------------------------------------------------------ chart data
def _load(symbol: str, tf: str):
    if symbol not in SYMBOLS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    try:
        return engine.get_features(symbol, tf)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


@router.get("/bars")
def bars(symbol: str, tf: str, count: int = 3000, end_index: int | None = None):
    df, _ = _load(symbol, tf)
    end = len(df) if end_index is None else max(1, min(end_index + 1, len(df)))
    start = max(0, end - count)
    d = df.iloc[start:end]
    t = _unix(d["time"])
    return {
        "symbol": symbol, "tf": tf, "first_index": start, "total": len(df),
        "time": t.tolist(), "open": d["open"].round(6).tolist(), "high": d["high"].round(6).tolist(),
        "low": d["low"].round(6).tolist(), "close": d["close"].round(6).tolist(),
        "volume": d["volume"].tolist(),
        "levels": {k: [_f(v) for v in d[k].to_numpy()] for k in ("pdh", "pdl", "asia_high", "asia_low") if k in d},
    }


@router.get("/index_of")
def index_of(symbol: str, tf: str, time: int):
    """Index of the last bar at or before a unix time."""
    df, _ = _load(symbol, tf)
    t = _unix(df["time"])
    return {"index": int(max(0, np.searchsorted(t, time, side="right") - 1)), "total": len(df)}


@router.get("/overlays")
def overlays(symbol: str, tf: str, from_i: int, to_i: int):
    """Drawable structures visible at to_i (nothing that was only known later)."""
    df, rec = _load(symbol, tf)
    to_i = min(to_i, len(df) - 1)

    def visible(z):
        return z["formed"] <= to_i and (z["end"] is None or z["end"] >= from_i)

    zones = []
    for z in rec.zones:
        if not visible(z):
            continue
        end = z["end"] if z["end"] is not None and z["end"] <= to_i else None
        t = z.get("touch_i")
        if z["kind"].startswith("ob") and t is not None and t <= to_i:
            end = t if end is None else min(end, t)  # an order block is spent once tapped
            if end < from_i:
                continue
        zones.append({"kind": z["kind"], "start": z["start"], "formed": z["formed"], "end": end,
                      "top": z["top"], "bottom": z["bottom"]})
    # keep the chart readable: every finished zone in view, but only the 8 most recent active ones per kind
    active = {}
    for z in reversed(zones):
        if z["end"] is None:
            active.setdefault(z["kind"], []).append(z)
    drop = {id(z) for lst in active.values() for z in lst[8:]}
    zones = [z for z in zones if id(z) not in drop]
    swings = [s for s in rec.swings if from_i <= s["i"] and s["conf"] <= to_i]
    events = [e for e in rec.events if from_i <= e["i"] <= to_i]
    pools = [{"kind": p["kind"], "start": p["start"], "level": p["level"],
              "end": p["end"] if p["end"] is not None and p["end"] <= to_i else None}
             for p in rec.pools if p["start"] <= to_i and (p["end"] is None or p["end"] >= from_i)]
    return {"zones": zones[-400:], "swings": swings, "events": events, "pools": pools}


@router.get("/zones")
def zones(symbol: str, tf: str, at_index: int | None = None):
    _load(symbol, tf)
    return key_zones(symbol, tf, at_index)


@router.get("/bar_context")
def bar_context(symbol: str, tf: str, index: int):
    """Which catalog conditions were true at this bar (explains a setup)."""
    from ..research.hypothesis import condition_matrix
    df, _ = _load(symbol, tf)
    ids, mat = condition_matrix(symbol, tf, df)
    index = max(0, min(index, len(df) - 1))
    by_id = {c.id: c for c in CATALOG}
    active = [ {"id": cid, "label": by_id[cid].label, "category": by_id[cid].category, "bias": by_id[cid].bias}
               for j, cid in enumerate(ids) if mat[index, j]]
    return {"index": index, "time": int(df["time"].iloc[index].timestamp()), "conditions": active}


# ------------------------------------------------------------------ research
class TestReq(BaseModel):
    symbol: str
    tf: str
    conditions: list[str]
    direction: str = "long"
    target_r: float = 2.0
    stop_atr: float = DEFAULT_STOP_ATR
    horizon: int | None = None
    cooldown: int = 5
    date_from: str | None = None
    date_to: str | None = None
    include_costs: bool = True


@router.post("/research/test")
def research_test(req: TestReq):
    _load(req.symbol, req.tf)
    try:
        res = run_test(**req.model_dump())
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e).strip("'\""))
    except Exception:
        traceback.print_exc()
        raise HTTPException(500, "Analysis failed - see server log.")
    return _clean(res)


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return _f(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o
