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


# ------------------------------------------------------------------ portfolio
class PortfolioReq(BaseModel):
    balance: float | None = None
    risk_pct: float | None = None
    max_open: int | None = None
    max_open_risk: float | None = None
    one_per_symbol: bool | None = None
    max_dd_tolerance: float | None = None
    period: str | None = None   # "all" | "unseen"


@router.get("/portfolio")
def portfolio_get():
    """Saved settings and the last simulation (if any)."""
    from ..execution import portfolio
    from ..live import db
    return _clean({"settings": portfolio.settings(), "result": db.get_setting("portfolio_last")})


@router.post("/portfolio")
def portfolio_run(req: PortfolioReq):
    """Simulate all active watched setups traded together as one account (takes ~30-60 s)."""
    from ..execution import portfolio
    from ..live import db
    if req.period not in (None, "all", "unseen"):
        raise HTTPException(400, "period must be 'all' or 'unseen'")
    new = portfolio.settings() | {k: v for k, v in req.model_dump().items() if v is not None}
    if new["risk_pct"] <= 0 or new["balance"] <= 0 or new["max_open"] < 1:
        raise HTTPException(400, "Balance, risk and max open trades must be positive.")
    if new["max_open_risk"] < new["risk_pct"] or new["max_dd_tolerance"] <= 0:
        raise HTTPException(400, "Max total open risk must be at least the risk per trade, and the drawdown limit positive.")
    portfolio.save_settings(new)
    res = portfolio.run()
    if "error" in res:
        raise HTTPException(400, res["error"])
    res = _clean(res)
    db.set_setting("portfolio_last", res)
    return res
