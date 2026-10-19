"""Live feed, watchlist, signals and alert settings."""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import DEFAULT_STOP_ATR, SYMBOLS, TARGETS_R, TIMEFRAMES
from ..live import db, notify
from ..live.service import service, snapshot_stats
from ..research.conditions import is_valid, label_of
from .routes import _clean

router = APIRouter(prefix="/api/live")


@router.get("/status")
def status():
    return _clean(service.status())


class Toggle(BaseModel):
    enabled: bool


@router.post("/toggle")
def toggle(req: Toggle):
    service.set_enabled(req.enabled)
    return {"enabled": service.enabled}


@router.get("/candle")
def forming_candle(symbol: str, tf: str):
    """Current, still-forming candle from MT5 so the chart moves in real time (display only)."""
    from ..data.mt5_source import MT5Error, source
    if symbol not in SYMBOLS or tf not in TIMEFRAMES:
        raise HTTPException(400, "Unknown symbol or timeframe")
    try:
        bar = source.forming_bar(symbol, tf)
    except MT5Error as e:
        raise HTTPException(503, str(e))
    if bar is None:
        raise HTTPException(503, "MT5 returned no data")
    return bar


@router.get("/position_size")
def position_size(symbol: str, entry: float, stop: float, target: float | None = None,
                  risk: float | None = None, risk_pct: float | None = None):
    """Lot size so that a stop-out loses at most the risk amount (spread included), and the outcome at target.

    Uses the broker's contract (tick value / size, lot step) from MT5; the last known contract is
    remembered so the calculator still works while MT5 is offline."""
    import math
    from ..data.mt5_source import MT5Error, source
    from ..data.store import load_bars
    from ..execution.sizing import lots_for_risk
    from ..research.hypothesis import point_size
    from ..research.outcomes import spread_price
    if symbol not in SYMBOLS:
        raise HTTPException(400, "Unknown symbol")
    contracts = db.get_setting("contracts", {})
    account = None
    try:
        c = source.contract(symbol)
        if c:
            contracts[symbol] = c
            db.set_setting("contracts", contracts)
        account = source.account()
    except (MT5Error, Exception):
        pass
    c = contracts.get(symbol)
    if not c:
        raise HTTPException(503, "Contract details unknown: connect MT5 once so the calculator can learn them.")
    if risk is None:
        if risk_pct is None or not account:
            raise HTTPException(400, "Enter a risk amount (or a % of balance while MT5 is connected).")
        risk = account["balance"] * risk_pct / 100
    if risk <= 0:
        raise HTTPException(400, "Risk must be positive.")
    dist = abs(entry - stop)
    if dist <= 0:
        raise HTTPException(400, "Stop must differ from entry.")
    bars = load_bars(symbol, "H1").tail(500)
    spread = float(np.median(spread_price(bars, point_size(symbol))))   # typical spread in price
    size = lots_for_risk(risk, dist + spread, c)
    lots = size["lots"]
    per_price = lots / c["tick_size"] * c["tick_value"]                # money per 1.0 price move at this size
    out = {"symbol": symbol, "direction": "long" if stop < entry else "short", "risk": round(risk, 2),
           "lots": lots, "loss_at_stop": round((dist + spread) * per_price, 2), "spread": spread,
           "spread_cost": round(spread * per_price, 2), "contract": c, "account": account, "note": size.get("note"),
           "min_lot_risk": round(c["vol_min"] / c["tick_size"] * c["tick_value"] * (dist + spread), 2)}
    if target is not None:
        reward = abs(target - entry)
        out["profit_at_target"] = round((reward - spread) * per_price, 2)
        out["rr"] = round(reward / dist, 2)
    if account and account["balance"] > 0:
        out["risk_pct_of_balance"] = round(100 * out["loss_at_stop"] / account["balance"], 1)
    return _clean(out)


@router.post("/scan_now")
def scan_now():
    """Re-check all watched setups on the latest closed candle (never duplicates a signal)."""
    return {"new_signals": service.scan_all()}


# ------------------------------------------------------------------ watchlist
class WatchReq(BaseModel):
    symbol: str
    tf: str
    conditions: list[str]
    direction: str
    target_r: float = 2.0
    stop_atr: float = DEFAULT_STOP_ATR
    cooldown: int = 5
    name: str | None = None
    plan: str | None = None   # execution plan key, e.g. "next|struct|liq|none"


@router.post("/watchlist")
def add_watch(req: WatchReq):
    if req.symbol not in SYMBOLS or req.tf not in TIMEFRAMES or req.direction not in ("long", "short"):
        raise HTTPException(400, "Invalid symbol, timeframe or direction")
    if req.target_r not in TARGETS_R or not req.conditions:
        raise HTTPException(400, "Invalid target or empty conditions")
    unknown = [c for c in req.conditions if not is_valid(c)]
    if unknown:
        raise HTTPException(400, f"Unknown conditions: {unknown}")
    if req.plan:
        from ..execution.simulate import Plan
        try:
            pl = Plan.from_key(req.plan)
        except ValueError:
            raise HTTPException(400, "Invalid execution plan")
        if pl.entry != "next":
            raise HTTPException(400, "Lower-timeframe entry plans are research-only: they did not beat simple entries in testing.")
    w = req.model_dump()
    from ..execution.winrate import plan_exit
    ex = plan_exit(req.plan)
    if ex:  # the statistics must describe the exit that is actually traded
        w["stop_atr"], w["target_r"] = ex
    w["name"] = req.name or " + ".join(label_of(c) for c in req.conditions)
    try:
        w["stats"] = snapshot_stats(w)
    except Exception as e:
        raise HTTPException(400, f"Could not evaluate this setup: {e}")
    wid = db.add_watch(w)
    return _clean(db.get_watch(wid))


@router.get("/watchlist")
def list_watch():
    rows = db.list_watches()
    for w in rows:
        w["record"] = db.watch_record(w["id"])
    return _clean(rows)


class WatchPatch(BaseModel):
    active: bool | None = None
    name: str | None = None


@router.patch("/watchlist/{wid}")
def patch_watch(wid: int, req: WatchPatch):
    if not db.get_watch(wid):
        raise HTTPException(404, "Not found")
    fields = {k: (int(v) if isinstance(v, bool) else v) for k, v in req.model_dump().items() if v is not None}
    db.update_watch(wid, **fields)
    return _clean(db.get_watch(wid))


@router.post("/watchlist/{wid}/refresh")
def refresh_watch(wid: int):
    import time
    w = db.get_watch(wid)
    if not w:
        raise HTTPException(404, "Not found")
    db.update_watch(wid, stats=snapshot_stats(w), stats_updated=int(time.time()))
    return _clean(db.get_watch(wid))


@router.get("/watchlist/{wid}/exits")
def watch_exits(wid: int):
    """Every live exit for this watch with its past and unseen results, and the two suggested profiles."""
    from ..execution.winrate import exit_profiles
    from ..strategies.live import is_strategy
    w = db.get_watch(wid)
    if not w:
        raise HTTPException(404, "Not found")
    if is_strategy(w):
        raise HTTPException(400, "Strategy watches use their own fixed exit rules.")
    return _clean(exit_profiles(w))


class ExitReq(BaseModel):
    stop_atr: float
    target_r: float


@router.post("/watchlist/{wid}/exit")
def set_exit(wid: int, req: ExitReq):
    """Switch a watch to another live exit; its statistics are recomputed for that exit."""
    import time
    from ..execution.winrate import LIVE_STOPS, exit_plan
    from ..strategies.live import is_strategy
    w = db.get_watch(wid)
    if not w:
        raise HTTPException(404, "Not found")
    if is_strategy(w):
        raise HTTPException(400, "Strategy watches use their own fixed exit rules.")
    if req.stop_atr not in LIVE_STOPS or req.target_r not in TARGETS_R:
        raise HTTPException(400, f"Stop must be one of {LIVE_STOPS} x ATR and target one of {TARGETS_R} R.")
    w.update(stop_atr=req.stop_atr, target_r=req.target_r, plan=exit_plan(req.stop_atr, req.target_r))
    db.update_watch(wid, stop_atr=w["stop_atr"], target_r=w["target_r"], plan=w["plan"],
                    stats=snapshot_stats(w), stats_updated=int(time.time()))
    return _clean(db.get_watch(wid))


@router.get("/watchlist/{wid}/view")
def watch_view(wid: int):
    """Chart view of a strategy watch (CRT / SMC): steps met now, possible entry zone, past trades."""
    from ..strategies.live import is_strategy
    from ..strategies.view import strategy_view
    w = db.get_watch(wid)
    if not w:
        raise HTTPException(404, "Not found")
    if not is_strategy(w):
        raise HTTPException(400, "Use /api/setup_view for condition-based setups")
    return _clean(strategy_view(w))


@router.delete("/watchlist/{wid}")
def delete_watch(wid: int):
    db.delete_watch(wid)
    return {"deleted": wid}


# ------------------------------------------------------------------ signals
@router.get("/signals")
def signals(since_id: int = 0, limit: int = 200, watch_id: int | None = None):
    return _clean(db.list_signals(since_id, limit, watch_id))


# ------------------------------------------------------------------ telegram
class TgReq(BaseModel):
    token: str | None = None
    chat_id: int | None = None
    enabled: bool | None = None


@router.get("/telegram")
def tg_get():
    c = notify.config()
    tok = c.get("token") or ""
    return {"configured": notify.configured(), "has_token": bool(tok), "token_hint": (tok[:6] + "…") if tok else "",
            "chat_id": c.get("chat_id"), "enabled": c.get("enabled", True)}


@router.post("/telegram")
def tg_set(req: TgReq):
    c = notify.config()
    for k, v in req.model_dump().items():
        if v is not None:
            c[k] = v.strip() if isinstance(v, str) else v
    db.set_setting("telegram", c)
    return tg_get()


@router.post("/telegram/detect")
def tg_detect():
    c = notify.config()
    if not c.get("token"):
        raise HTTPException(400, "Save the bot token first.")
    try:
        cid = notify.detect_chat_id(c["token"])
    except Exception as e:
        raise HTTPException(400, f"Telegram rejected the request: {e}")
    if cid is None:
        raise HTTPException(404, "No messages found. Open your bot in Telegram, press Start / send it any message, then try again.")
    c["chat_id"] = cid
    db.set_setting("telegram", c)
    return tg_get()


@router.post("/telegram/test")
def tg_test():
    if not notify.configured():
        raise HTTPException(400, "Telegram is not fully configured yet.")
    try:
        notify.send("✅ Market Lab is connected. Signals for your watched setups will arrive here.")
    except Exception as e:
        raise HTTPException(400, f"Sending failed: {e}")
    return {"sent": True}
