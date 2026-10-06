"""Entry / exit optimisation per setup and the pooled execution study."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import SYMBOLS, TIMEFRAMES
from ..discovery import runner
from ..execution import study
from ..execution.optimize import ExecutionLab
from ..research.conditions import is_valid
from .routes import _clean

router = APIRouter(prefix="/api/execution")


class OptReq(BaseModel):
    symbol: str
    tf: str
    conditions: list[str]
    direction: str
    cooldown: int = 5


@router.post("/optimize")
def optimize(req: OptReq):
    if req.symbol not in SYMBOLS or req.tf not in TIMEFRAMES or req.direction not in ("long", "short"):
        raise HTTPException(400, "Invalid symbol, timeframe or direction")
    if not req.conditions or not all(is_valid(c) for c in req.conditions):
        raise HTTPException(400, "Invalid conditions")
    res = ExecutionLab(req.symbol, req.tf, req.conditions, req.direction, req.cooldown).optimise()
    res["recommendation"] = study.recommend(res)
    return _clean(res)


@router.post("/study")
def start_study(source: str = "concepts"):
    if source not in ("concepts", "patterns"):
        raise HTTPException(400, "source must be 'concepts' or 'patterns'")
    if runner.study_state["running"]:
        raise HTTPException(409, "The study is already running.")
    runner.submit_study(source)
    return {"started": True}


@router.get("/study")
def get_study():
    return _clean({"state": runner.study_state, "result": study.latest()})
