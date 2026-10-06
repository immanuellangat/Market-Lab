"""Position sizing: convert a risk amount and stop distance into a broker lot size."""
from __future__ import annotations

import math


def lots_for_risk(risk_money: float, stop_distance: float, contract: dict) -> dict:
    """Largest lot size (rounded DOWN to the broker's step) whose stop-out loses at most risk_money."""
    tick_value, tick_size = contract["tick_value"], contract["tick_size"]
    if stop_distance <= 0 or tick_value <= 0 or tick_size <= 0:
        return {"lots": None, "note": "invalid stop or contract data"}
    loss_per_lot = stop_distance / tick_size * tick_value
    raw = risk_money / loss_per_lot
    step = contract["vol_step"] or 0.01
    lots = math.floor(raw / step + 1e-9) * step
    lots = min(lots, contract["vol_max"])
    note = None
    if lots < contract["vol_min"]:
        lots = contract["vol_min"]
        note = (f"Even the minimum lot ({contract['vol_min']:g}) risks {lots * loss_per_lot:.2f}, "
                f"more than your {risk_money:.2f} risk budget.")
    return {"lots": round(lots, 4), "risk_at_stop": round(lots * loss_per_lot, 2), "loss_per_lot": round(loss_per_lot, 2), "note": note}
