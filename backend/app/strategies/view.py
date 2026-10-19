"""Chart view for strategy watches (CRT / SMC), in the same shape as /api/setup_view.

Shows where the strategy stands on the latest closed candle (each step met or not), a
possible entry zone estimated from the strategy's own past trades, and those past trades.
"""
from __future__ import annotations

import numpy as np

from ..features.engine import get_features
from . import crt
from . import live as strat

SEQ = 20


def _item(label: str, met: bool, level: float | None = None, level_label: str | None = None, zone: dict | None = None) -> dict:
    return {"id": label, "label": label, "met": bool(met), "bias": "neutral", "level": None if level is None else float(level),
            "level_label": level_label, "zone": zone}


def _base(w: dict, df, direction: str, checklist: list[dict], occ: list[dict], entry_zone: dict) -> dict:
    st = w.get("stats") or {}
    row = df.iloc[-1]
    return {"symbol": w["symbol"], "tf": w["tf"], "direction": direction, "index": len(df) - 1,
            "time": int(row["time"].timestamp()), "close": float(row["close"]), "atr": float(row["atr"]),
            "checklist": checklist, "met": sum(c["met"] for c in checklist), "total": len(checklist),
            "occurrences": sorted(occ, key=lambda o: -o["time"]), "stats": (st.get("stats") or {}).get("all") or {"n": 0},
            "evidence": st.get("evidence") or {"grade": "F", "score": 0, "reasons": []}, "entry_zone": entry_zone}


def _no_zone(reason: str) -> dict:
    return {"available": False, "reason": reason}


# ------------------------------------------------------------------ CRT
def crt_view(w: dict) -> dict:
    r = strat.parse(w["plan"])
    eng = crt.CRT(w["symbol"], r["htf"])
    f, _ = get_features(w["symbol"], r["htf"])
    flags = strat.setup_filters(eng, f)
    trades = [t for t in eng.trades(r["mode"], target_mode=r["target"])
              if strat._passes(flags[(t["i"], t["direction"])], r["filters"]) and "r" in t]
    occ = [{"i": t["i"], "time": int(eng.ht[t["i"]]), "r": t["r"], "outcome": t["exit"], "oos": False, "direction": t["direction"],
            "entry": t["entry"], "stop": t["stop"], "target": t["target"], "entry_time": t["entry_time"], "exit_time": t["exit_time"]}
           for t in trades]
    i = len(eng.ht) - 1
    hi, lo, atr = float(eng.hh[i]), float(eng.hl[i]), float(eng.hatr[i])
    last = f.iloc[-1]
    trend = int(last["d1_ms_trend"]) if np.isfinite(last["d1_ms_trend"]) else 0
    direction = "long" if trend == 1 else "short" if trend == -1 else "both"
    pdp = float(last["d1_pd_pos"]) if np.isfinite(last["d1_pd_pos"]) else np.nan
    side = "low" if direction == "long" else "high"
    checklist = [
        _item(f"Range candle = last closed {r['htf']} candle (high)", True, hi, "range high"),
        _item(f"Range candle = last closed {r['htf']} candle (low)", True, lo, "range low"),
        _item("Daily structure gives a direction", direction != "both"),
    ]
    if "big_range" in r["filters"]:
        checklist.append(_item("Range ≥ 1 ATR", (hi - lo) >= atr))
    if "pd_zone" in r["filters"]:
        ok = (pdp < 0.5) if direction == "long" else (pdp > 0.5) if direction == "short" else False
        checklist.append(_item("Daily discount (buys) / premium (sells)", ok))
    checklist.append(_item(f"Next {r['htf']} candle sweeps the range {side} and closes back inside",
                           False, lo if direction == "long" else hi if direction == "short" else None, f"range {side}"))
    if direction == "both":
        return _base(w, f, direction, checklist, occ, _no_zone("The daily structure has no direction, so CRT has no side to trade yet."))
    # where past entries sat inside the range, and how far past sweeps went beyond it
    same = [t for t in trades if t["direction"] == direction]
    if len(same) < 10:
        return _base(w, f, direction, checklist, occ, _no_zone("Too few past trades in this direction to estimate an entry zone."))
    frac = np.array([((t["entry"] - t["range"][0]) if direction == "long" else (t["range"][1] - t["entry"])) / (t["range"][1] - t["range"][0])
                     for t in same])
    # how far beyond the range the stop (sweep wick + buffer) typically was, as a fraction of the range
    depth = float(np.median([((t["range"][0] - t["stop"]) if direction == "long" else (t["stop"] - t["range"][1]))
                             / (t["range"][1] - t["range"][0]) for t in same]))
    q25, q75 = np.percentile(frac, [25, 75])
    rng = hi - lo
    if direction == "long":
        z_lo, z_hi = lo + q25 * rng, lo + q75 * rng
        stop, target = lo - depth * rng, hi
    else:
        z_lo, z_hi = hi - q75 * rng, hi - q25 * rng
        stop, target = hi + depth * rng, lo
    ez = {"available": True, "top": float(z_hi), "bottom": float(z_lo), "mid": float((z_hi + z_lo) / 2), "stop": float(stop),
          "target": float(target), "stop_atr": "sweep wick", "target_r": "other side",
          "anchor": "", "n": len(same),
          "basis": f"half of the last {len(same)} {direction} CRT entries were {q25:.0%}–{q75:.0%} into the range from the swept side; "
                   f"the stop uses the typical sweep depth beyond the range"}
    return _base(w, f, direction, checklist, occ, ez)


# ------------------------------------------------------------------ SMC
def smc_view(w: dict) -> dict:
    from ..api.routes import _nearest_zone
    from ..execution.levels import levels_for_entry, signal_spec
    from .ltf_search import EXITS, collect_one, filter_flags, _b
    r = strat._smc_parse(w["plan"])
    df, rec = get_features(w["symbol"], r["tf"])
    i = len(df) - 1
    tr = collect_one(w["symbol"], r["tf"], r)
    occ = [{"i": -1, "time": int(t.time), "r": float(t.r), "outcome": t.why, "oos": bool(t.oos), "direction": t.direction,
            "entry": float(t.entry), "stop": float(t.stop), "target": float(t.target),
            "entry_time": int(t.entry_time), "exit_time": int(t.exit_time)}
           for t in tr.itertuples()]
    d1 = int(df["d1_ms_trend"].iloc[i])
    direction = "long" if d1 == 1 else "short" if d1 == -1 else "both"
    checklist = [_item("Daily structure agrees (sets the direction)", direction != "both")]
    if direction == "both":
        return _base(w, df, direction, checklist, occ, _no_zone("The daily structure has no direction yet."))
    L = direction == "long"
    sweep = (_b(df, "sweep_swing_low") | _b(df, "sweep_pdl") | _b(df, "sweep_asia_low") | _b(df, "sweep_eql")) if L else \
            (_b(df, "sweep_swing_high") | _b(df, "sweep_pdh") | _b(df, "sweep_asia_high") | _b(df, "sweep_eqh"))
    shift = (_b(df, "choch_bull") | _b(df, "mss_bull")) if L else (_b(df, "choch_bear") | _b(df, "mss_bear"))
    sw = np.flatnonzero(sweep[: i + 1])
    s_i = int(sw[-1]) if len(sw) else -10**9
    sh = np.flatnonzero(shift[: i + 1])
    c_i = int(sh[-1]) if len(sh) else -10**9
    sweep_ok = i - s_i <= SEQ
    shift_ok = sweep_ok and s_i <= c_i and c_i - s_i <= SEQ and i - c_i <= SEQ
    lvl = df["last_sl" if L else "last_sh"].iloc[s_i] if sweep_ok else df["last_sl" if L else "last_sh"].iloc[i]
    ff = filter_flags(df, r["tf"], direction)
    checklist += [
        _item(f"Liquidity sweep in the last {SEQ} candles", sweep_ok, lvl if np.isfinite(lvl) else None, "swing level"),
        _item("CHoCH / MSS after the sweep", shift_ok),
    ]
    zone = _nearest_zone(rec, df, "fvg_bull" if L else "fvg_bear", True, i, float(df["close"].iloc[i])) or \
        _nearest_zone(rec, df, "ob_bull" if L else "ob_bear", True, i, float(df["close"].iloc[i]))
    retest = "First FVG / OB retest"
    checklist.append(_item(retest, False, zone=(zone | {"label": "Retest zone · nearest untouched FVG/OB"}) if zone else None))
    if "htf_pd" in r["filters"]:
        checklist.append(_item("H4 discount (buys) / premium (sells)", bool(ff["htf_pd"][i])))
    if "killzone" in r["filters"]:
        checklist.append(_item("Kill zone (London 02-05 / New York 07-10 NY time)", bool(ff["killzone"][i])))
    if not zone:
        return _base(w, df, direction, checklist, occ, _no_zone("No untouched FVG or order block on the right side of price yet."))
    mid = (zone["top"] + zone["bottom"]) / 2
    stop, target = levels_for_entry(signal_spec(EXITS[r["exit"]], df, i, direction), mid)
    ez = {"available": True, "top": zone["top"], "bottom": zone["bottom"], "mid": mid, "stop": float(stop), "target": float(target),
          "stop_atr": "10-bar swing", "target_r": 2, "anchor": retest, "n": None,
          "basis": "the retest happens inside the nearest untouched FVG / order block; stop beyond the 10-candle swing, target 2R"}
    return _base(w, df, direction, checklist, occ, ez)


def strategy_view(w: dict) -> dict:
    return smc_view(w) if w["plan"].startswith("smc|") else crt_view(w)
