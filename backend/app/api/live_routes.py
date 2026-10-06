"""Live feed, watchlist, signals and alert settings."""
from __future__ import annotations

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
