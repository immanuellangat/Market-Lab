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
from ..features.sdsr import supply_demand, support_resistance
from ..features.sessions import SESSIONS
from ..features.structure import FVG_EXPIRY, OB_EXPIRY
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
        "atr": [_f(v) for v in d["atr"].to_numpy()],
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
        is_ob = z["kind"].startswith("ob")
        end = z["end"] if z["end"] is not None and z["end"] <= to_i else None
        t = z.get("touch_i")
        touched = t is not None and t <= to_i
        # status as it was known at to_i (replay never reveals later outcomes)
        if is_ob and touched and (end is None or t <= end):
            end, status = t, "tapped"            # an order block is spent once tapped
        elif end is not None:
            expiry = OB_EXPIRY if is_ob else FVG_EXPIRY
            status = "expired" if end - z["formed"] > expiry else ("broken" if is_ob else "filled")
        else:
            status = "partly filled" if (touched and not is_ob) else "fresh"
        if end is not None and end < from_i:
            continue
        zones.append({"kind": z["kind"], "start": z["start"], "formed": z["formed"], "end": end,
                      "top": z["top"], "bottom": z["bottom"], "status": status})
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
    sd = [{k: (_f(v) if isinstance(v, float) else v) for k, v in z.items()} for z in supply_demand(df, from_i, to_i)]
    sr = support_resistance(df, rec.swings, to_i, DEFAULT_HORIZON.get(tf, 48))
    return {"zones": zones[-400:], "swings": swings, "events": events, "pools": pools, "sd_zones": sd, "sr_zones": sr}


@router.get("/zone_stats")
def zone_stats(symbol: str, tf: str):
    """Historical first-retest record of each zone type on this market (for chart tooltips)."""
    from ..research.hypothesis import quick_stats
    _load(symbol, tf)
    setups = {"fvg_bull": ("fvg_bull_retest", "long"), "fvg_bear": ("fvg_bear_retest", "short"),
              "ob_bull": ("ob_bull_retest", "long"), "ob_bear": ("ob_bear_retest", "short")}
    return _clean({k: quick_stats(symbol, tf, [c], d) for k, (c, d) in setups.items()})


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


# conditions that refer to a price level: (column, label). Everything else is shown as a labelled marker line.
_LEVEL_OF = {
    "sweep_pdl": ("pdl", "PDL"), "sweep_pdl_recent": ("pdl", "PDL"), "break_pdl": ("pdl", "PDL"),
    "sweep_pdh": ("pdh", "PDH"), "sweep_pdh_recent": ("pdh", "PDH"), "break_pdh": ("pdh", "PDH"),
    "sweep_asia_low": ("asia_low", "Asia low"), "sweep_asia_low_recent": ("asia_low", "Asia low"),
    "sweep_asia_high": ("asia_high", "Asia high"), "sweep_asia_high_recent": ("asia_high", "Asia high"),
    "sweep_low": ("last_sl", "Swing low"), "sweep_low_recent": ("last_sl", "Swing low"),
    "sweep_high": ("last_sh", "Swing high"), "sweep_high_recent": ("last_sh", "Swing high"),
    "above_day_open": ("day_open", "Daily open"), "below_day_open": ("day_open", "Daily open"),
    "ema_bull": ("ema50", "EMA50"), "ema_bear": ("ema50", "EMA50"),
}
_PD_FRACTION = {"discount": 0.5, "premium": 0.5, "deep_discount": 0.25, "deep_premium": 0.75}
# conditions that refer to a zone: (zone kind, fresh only = first retest still possible, label)
_ZONE_OF = {
    "fvg_bull_retest": ("fvg_bull", True, "Bullish FVG"), "fvg_bear_retest": ("fvg_bear", True, "Bearish FVG"),
    "fvg_bull": ("fvg_bull", False, "Bullish FVG"), "fvg_bear": ("fvg_bear", False, "Bearish FVG"),
    "fvg_bull_disp": ("fvg_bull", False, "Bullish FVG"), "fvg_bear_disp": ("fvg_bear", False, "Bearish FVG"),
    "in_bull_fvg": ("fvg_bull", False, "Bullish FVG"), "in_bear_fvg": ("fvg_bear", False, "Bearish FVG"),
    "ob_bull_retest": ("ob_bull", True, "Bullish order block"), "ob_bear_retest": ("ob_bear", True, "Bearish order block"),
}


def _nearest_zone(rec, df, kind: str, fresh: bool, i: int, close: float) -> dict | None:
    """The active zone of this kind closest to price (for a bullish zone, preferably below price)."""
    bull = kind.endswith("bull")
    cand = [z for z in rec.zones if z["kind"] == kind and z["formed"] <= i and z["end"] is None
            and not (fresh and z.get("touched") and z.get("touch_i", 10**12) <= i)]
    if not cand:
        return None
    side = [z for z in cand if (z["top"] <= close if bull else z["bottom"] >= close)]
    pool = side or cand
    z = min(pool, key=lambda z: min(abs(close - z["top"]), abs(close - z["bottom"])))
    return {"top": float(z["top"]), "bottom": float(z["bottom"]), "start_time": int(df["time"].iloc[z["start"]].timestamp())}


def _entry_zone(df, checklist: list[dict], occ: list[dict], direction: str, stop_atr: float, target_r: float) -> dict:
    """Where the entry (next candle open after all conditions are met) is likely to be.

    FVG / order-block condition -> that zone (the retest happens inside it).
    Price-level condition       -> past entries measured from the level in ATR; the middle 50% placed around today's level.
    No price anchor             -> no zone (the entry is wherever the conditions line up)."""
    from ..research.conditions import base_id
    row = df.iloc[-1]
    atr = float(row["atr"])
    sg = 1 if direction == "long" else -1
    zone = next((c for c in checklist if c.get("zone")), None)
    out = None
    if zone:
        z = zone["zone"]
        out = {"top": z["top"], "bottom": z["bottom"], "basis": f"{zone['label']}: the retest happens inside the nearest untouched zone",
               "anchor": zone["label"], "n": None}
    else:
        lvl = next((c for c in checklist if c.get("level") is not None), None)
        if lvl:
            b = base_id(lvl["id"])
            if b in _LEVEL_OF:
                anchor = df[_LEVEL_OF[b][0]].to_numpy(float)
            else:
                fr = _PD_FRACTION[b]
                anchor = (df["last_sl"] + fr * (df["last_sh"] - df["last_sl"])).to_numpy(float)
            o, a = df["open"].to_numpy(float), df["atr"].to_numpy(float)
            idx = np.array([e["i"] for e in occ if e["i"] + 1 < len(df)], dtype=int)
            if len(idx) >= 10:
                off = (o[idx + 1] - anchor[idx]) / a[idx]
                off = off[np.isfinite(off)]
                if len(off) >= 10:
                    q25, q75 = np.percentile(off, [25, 75])
                    lo, hi = lvl["level"] + q25 * atr, lvl["level"] + q75 * atr
                    out = {"top": float(max(lo, hi)), "bottom": float(min(lo, hi)), "anchor": lvl["label"], "n": int(len(off)),
                           "basis": f"half of the last {len(off)} entries were {q25:+.2f} to {q75:+.2f} ATR from the "
                                    f"{lvl['level_label']} ({lvl['label']})"}
    if out is None:
        return {"available": False, "reason": "This pattern has no price anchor (only trend, session, volatility or "
                                              "higher-timeframe conditions): it enters wherever the conditions line up."}
    mid = (out["top"] + out["bottom"]) / 2
    stop = mid - sg * stop_atr * atr
    return out | {"available": True, "mid": mid, "stop": stop, "target": mid + sg * target_r * stop_atr * atr,
                  "stop_atr": stop_atr, "target_r": target_r}


def _position(df, i: int, direction: str, stop_atr: float, target_r: float, horizon: int) -> dict:
    """Entry / stop / target and entry / exit time of a past trade, for drawing it as a position box.
    Same rules as the backtest: enter at the next open, stop checked first when one candle hits both,
    otherwise closed after `horizon` bars."""
    n = len(df)
    if i + 1 >= n:
        return {}
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    entry, risk = float(o[i + 1]), float(stop_atr * df["atr"].iloc[i])
    if not np.isfinite(risk) or risk <= 0:
        return {}
    sg = 1 if direction == "long" else -1
    stop, target = entry - sg * risk, entry + sg * target_r * risk
    last = min(n - 1, i + horizon)
    j = last
    for k in range(i + 1, last + 1):
        hit_stop = l[k] <= stop if sg > 0 else h[k] >= stop
        hit_tgt = h[k] >= target if sg > 0 else l[k] <= target
        if hit_stop or hit_tgt:
            j = k
            break
    t = _unix(df["time"].iloc[[i + 1, j]])
    return {"entry": entry, "stop": stop, "target": target, "entry_time": int(t[0]), "exit_time": int(t[1])}


@router.get("/setup_view")
def setup_view(symbol: str, tf: str, conditions: str, direction: str = "long", target_r: float = 2.0,
               stop_atr: float = DEFAULT_STOP_ATR, cooldown: int = 5):
    """A setup drawn on the chart: which of its conditions hold on the latest closed candle (with a price
    level where the condition has one) and every past occurrence with its result."""
    from ..research.conditions import BY_ID, base_id, evaluate, label_of
    ids = [c for c in conditions.split(",") if c]
    unknown = [c for c in ids if base_id(c) not in BY_ID]
    if unknown or direction not in ("long", "short"):
        raise HTTPException(400, f"Unknown conditions {unknown}" if unknown else "direction must be long or short")
    df, rec = _load(symbol, tf)
    i = len(df) - 1
    row = df.iloc[i]
    checklist = []
    for cid in ids:
        try:
            met = bool(evaluate(df.iloc[max(0, i - 600):], [cid])[-1])
        except KeyError:
            met = False
        c = BY_ID[base_id(cid)]
        item = {"id": cid, "label": label_of(cid), "met": met, "bias": c.bias, "level": None, "level_label": None, "zone": None}
        b = base_id(cid)
        if b in _ZONE_OF:
            kind, fresh, zl = _ZONE_OF[b]
            z = _nearest_zone(rec, df, kind, fresh, i, float(row["close"]))
            if z:
                item["zone"] = z | {"label": f"{'Retest zone' if fresh else 'Zone'} · nearest {'untouched ' if fresh else ''}{zl}"}
        elif b in _LEVEL_OF and _LEVEL_OF[b][0] in df.columns and np.isfinite(row[_LEVEL_OF[b][0]]):
            item["level"], item["level_label"] = float(row[_LEVEL_OF[b][0]]), _LEVEL_OF[b][1]
        elif b in _PD_FRACTION and np.isfinite(row.get("last_sh", np.nan)) and np.isfinite(row.get("last_sl", np.nan)):
            fr = _PD_FRACTION[b]
            item["level"] = float(row["last_sl"] + fr * (row["last_sh"] - row["last_sl"]))
            item["level_label"] = f"{fr:.0%} of swing range"
        checklist.append(item)
    res = run_test(symbol, tf, ids, direction, target_r, stop_atr, cooldown=cooldown, max_examples=400)
    occ = [{"i": e["i"], "time": e["time"], "r": e["r"], "outcome": e["outcome"], "oos": e["oos"]}
           | _position(df, e["i"], direction, stop_atr, target_r, res["params"]["horizon"]) for e in res.get("examples", [])]
    return _clean({
        "symbol": symbol, "tf": tf, "direction": direction, "index": i, "time": int(row["time"].timestamp()),
        "close": float(row["close"]), "atr": float(row["atr"]), "checklist": checklist,
        "met": sum(x["met"] for x in checklist), "total": len(checklist),
        "occurrences": occ, "stats": res["stats"]["all"], "evidence": res["evidence"],
        "entry_zone": _entry_zone(df, checklist, occ, direction, stop_atr, target_r),
    })


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
