"""Discovery runs, discovered patterns, comparisons and rare events."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import DEFAULT_STOP_ATR, SYMBOLS, TARGETS_R, TIMEFRAMES
from ..data.store import bars_path
from ..discovery import rare, runner, store
from .routes import _clean

router = APIRouter(prefix="/api/discovery")


class RunReq(BaseModel):
    symbols: list[str]
    tfs: list[str]
    directions: list[str] = ["long", "short"]
    target_r: float = 2.0
    stop_atr: float = DEFAULT_STOP_ATR
    cooldown: int = 5
    min_n: int | None = None


@router.post("/runs")
def start_run(req: RunReq):
    if not req.symbols or not req.tfs or not req.directions:
        raise HTTPException(400, "Pick at least one instrument, timeframe and direction.")
    if any(s not in SYMBOLS for s in req.symbols) or any(t not in TIMEFRAMES for t in req.tfs):
        raise HTTPException(400, "Unknown instrument or timeframe")
    if any(d not in ("long", "short") for d in req.directions) or req.target_r not in TARGETS_R:
        raise HTTPException(400, "Invalid direction or target")
    rid = runner.submit_run(req.symbols, req.tfs, req.directions, req.target_r, req.stop_atr, req.cooldown, req.min_n)
    return {"run_id": rid}


@router.get("/runs")
def runs():
    return {"runs": store.list_runs(), "worker": runner.state(), "counts": store.counts()}


@router.post("/runs/{rid}/cancel")
def cancel(rid: int):
    runner.cancel_run(rid)
    return {"cancelled": rid}


@router.get("/patterns")
def patterns(symbol: str | None = None, tf: str | None = None, direction: str | None = None,
             status: str | None = "validated,promising", min_n: int = 0):
    sts = [s for s in (status or "").split(",") if s] or None
    return _clean(store.list_patterns(symbol, tf, direction, sts, min_n))


@router.get("/patterns/{pid}")
def pattern(pid: int):
    p = store.get_pattern(pid)
    if not p:
        raise HTTPException(404, "Pattern not found")
    return _clean(p)


@router.post("/patterns/{pid}/compare")
def compare(pid: int):
    if not store.get_pattern(pid):
        raise HTTPException(404, "Pattern not found")
    runner.submit_compare(pid)
    return {"queued": pid}


@router.get("/rare")
def rare_events(symbol: str, tf: str):
    if symbol not in SYMBOLS or tf not in TIMEFRAMES or not bars_path(symbol, tf).exists():
        raise HTTPException(400, "Unknown or missing dataset")
    return _clean(rare.rare_now(symbol, tf))
