"""Price-zone alerts: tell the user when price reaches a zone or level they marked on the chart.

An alert is a price band [bottom, top] (a level has top == bottom) and fires once, when price trades into it.
Checked every live tick against the forming M5 candle (so a quick wick counts) and, after downtime, against
the M5 candles closed since the last check (those alerts are marked late). An alert created while price is
already inside its band is "unarmed": it only fires after price has left the band and come back.
"""
from __future__ import annotations

import html
import time

import numpy as np
import pandas as pd

from ..data import store
from . import db, notify

BAR_S = 300                 # M5
MAX_CATCHUP_S = 3 * 86400   # look back at most this far after downtime
LATE_S = 600                # an alert found on a candle that closed this long ago is "late"


def _inside(price: float, a: dict) -> bool:
    return a["bottom"] <= price <= a["top"]


def current_price(source, symbol: str) -> float | None:
    try:
        bar = source.forming_bar(symbol, "M5")
    except Exception:
        bar = None
    if bar:
        return bar["bid"] or bar["close"]
    p = store.bars_path(symbol, "M5")
    if p.exists():
        return float(pd.read_parquet(p, columns=["close"])["close"].iloc[-1])
    return None


def create(source, symbol: str, tf: str | None, label: str, top: float, bottom: float) -> dict:
    top, bottom = max(top, bottom), min(top, bottom)
    price = current_price(source, symbol)
    armed = price is None or not (bottom <= price <= top)
    aid = db.add_price_alert({"symbol": symbol, "tf": tf, "label": label, "top": top, "bottom": bottom,
                              "armed_at": int(time.time()) if armed else None})
    return next(a for a in db.list_price_alerts(symbol=symbol) if a["id"] == aid) | {"price": price}


def _events(source, symbol: str, since: int) -> list[dict]:
    """Candles to check, oldest first: closed M5 candles overlapping [since, now], then the forming one."""
    out = []
    p = store.bars_path(symbol, "M5")
    start = max(since, int(time.time()) - MAX_CATCHUP_S) - BAR_S
    if p.exists() and since < time.time() - BAR_S:     # only after a gap: normally the forming candle is enough
        df = pd.read_parquet(p, columns=["time", "high", "low", "close"],
                             filters=[("time", ">=", pd.Timestamp(start, unit="s", tz="UTC"))])
        t = df["time"].dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")
        for k in range(len(df)):
            out.append({"start": int(t[k]), "end": int(t[k]) + BAR_S, "high": float(df["high"].iat[k]),
                        "low": float(df["low"].iat[k]), "last": float(df["close"].iat[k]), "live": False})
    try:
        bar = source.forming_bar(symbol, "M5")
    except Exception:
        bar = None
    if bar and (not out or bar["time"] >= out[-1]["end"]):
        out.append({"start": bar["time"], "end": int(time.time()), "high": bar["high"], "low": bar["low"],
                    "last": bar["bid"] or bar["close"], "live": True})
    return out


def check(source) -> list[dict]:
    """Fire every active alert whose band price has reached. Returns the alerts that fired."""
    active = db.list_price_alerts(status="active")
    fired, now = [], int(time.time())
    for symbol in {a["symbol"] for a in active}:
        mine = [a for a in active if a["symbol"] == symbol]
        events = _events(source, symbol, min(a["last_checked"] or a["created"] for a in mine))
        for a in mine:
            armed_at, hit = a["armed_at"], None
            for e in events:
                if e["end"] <= (a["last_checked"] or a["created"]) - BAR_S:
                    continue
                if armed_at is None:                       # waiting for price to leave the band first
                    if not _inside(e["last"], a):
                        armed_at = e["end"]
                    continue
                if e["end"] <= armed_at:
                    continue
                # a candle that began before the alert was armed may hold older prices: use only its last price
                lo, hi = (e["low"], e["high"]) if e["start"] >= armed_at else (e["last"], e["last"])
                if hi >= a["bottom"] and lo <= a["top"]:
                    hit = e
                    break
            if hit:
                price = float(min(max(hit["last"], a["bottom"]), a["top"]))
                late = int(not hit["live"] and now - hit["end"] > LATE_S)
                db.update_price_alert(a["id"], status="triggered", triggered_at=now, trigger_price=price,
                                      late=late, armed_at=armed_at, last_checked=now)
                fired.append(a | {"trigger_price": price, "late": late, "hit_time": hit["end"]})
            else:
                db.update_price_alert(a["id"], armed_at=armed_at, last_checked=now)
    for a in fired:
        band = f"{a['bottom']:.5g}" if a["top"] == a["bottom"] else f"{a['bottom']:.5g}–{a['top']:.5g}"
        db.add_notification("price_alert", f"{'LATE · ' if a['late'] else ''}🔔 {a['symbol']} reached {a['label']}",
                            f"Price ≈ {a['trigger_price']:.5g} · zone {band}" + (f" · drawn on {a['tf']}" if a.get("tf") else "")
                            + (". Reached while the app was offline: check price now." if a["late"] else ""),
                            symbol=a["symbol"], tf=a.get("tf"), ref_id=a["id"], level="warn" if a["late"] else "info",
                            link={"page": "chart", "symbol": a["symbol"], "tf": a.get("tf") or "H1", "time": a["hit_time"]})
        if notify.configured():
            try:
                notify.send(format_alert(a))
            except Exception:
                pass
    return fired


def _p(x: float) -> str:
    return f"{x:.5f}".rstrip("0").rstrip(".") if abs(x) < 50 else f"{x:.2f}"


def format_alert(a: dict) -> str:
    band = _p(a["top"]) if a["top"] == a["bottom"] else f"{_p(a['bottom'])}–{_p(a['top'])}"
    lines = [f"🔔 <b>{a['symbol']}</b> reached <b>{html.escape(a['label'])}</b> ({band})",
             f"Price ≈ {_p(a['trigger_price'])}" + (f" · drawn on {a['tf']}" if a.get("tf") else "")]
    if a.get("late"):
        lines.insert(0, "⏱ <b>LATE</b>: price reached it while the app was offline — check price now.")
    lines.append("<i>A zone being reached is not a signal by itself.</i>")
    return "\n".join(lines)
