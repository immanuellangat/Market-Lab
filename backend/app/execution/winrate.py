"""Win-rate research: how far can win rate be raised without giving up expectancy?

Two levers, both chosen on the in-sample period only and then checked on unseen data:

A. Exit frontier: wider stops / closer targets / break-even raise win rate mechanically.
   Every plan in STOPS x TARGETS x MANAGE is run on each setup; the trade-off between
   win rate and R per trade is reported per setup and pooled over the standard concept setups.
B. Confluence filters: one extra condition added to a watched setup. A filter is kept only if
   it raises the in-sample win rate significantly (one-sided two-proportion z-test,
   Benjamini-Hochberg across all filters tried) without lowering in-sample expectancy.

Result stored as setting "winrate_study".
"""
from __future__ import annotations

import math
import time

import numpy as np

from ..config import TARGETS_R
from ..discovery.search import FDR_Q, _bh
from ..features.engine import get_features
from ..live import db
from ..research.conditions import available, base_id, label_of
from .optimize import ExecutionLab
from .simulate import BASELINE, Plan
from .study import concept_setups

KEY = "winrate_study"
STOPS = ("atr1", "atr1.5", "atr2", "atr3", "struct")
TARGETS = ("r0.5", "r0.75", "r1", "r1.5", "r2", "r3")
MANAGE = ("none", "be")
MIN_IS = 40            # in-sample trades a filtered setup needs
MAX_EXP_DROP = 0.05    # a "higher win rate" plan may give up at most this much in-sample R per trade


def plans() -> list[Plan]:
    return [Plan("next", s, t, m) for s in STOPS for t in TARGETS for m in MANAGE]


def _cell(r: dict) -> dict:
    return {"n": r.get("taken", 0), "win_rate": r.get("win_rate"), "avg_r": r.get("avg_r") if r.get("taken") else None}


def _frontier(lab: ExecutionLab) -> dict:
    return {pl.key: {"is": _cell(res["is"]), "oos": _cell(res["oos"])} for pl in plans() for res in [lab.run_plan(pl)]}


def _pick(front: dict, current: str) -> dict | None:
    """Highest in-sample win rate whose in-sample expectancy is within MAX_EXP_DROP of the current plan (and > 0)."""
    cur = front.get(current)
    if not cur or cur["is"]["avg_r"] is None:
        return None
    floor = max(cur["is"]["avg_r"] - MAX_EXP_DROP, 0.0)
    ok = [(k, v) for k, v in front.items() if v["is"]["avg_r"] is not None and v["is"]["avg_r"] >= floor and v["is"]["n"] >= MIN_IS]
    if not ok:
        return None
    k, v = max(ok, key=lambda kv: (kv[1]["is"]["win_rate"], kv[1]["is"]["avg_r"]))
    return {"key": k, "description": Plan.from_key(k).describe(), **v}


def _ztest(w1: int, n1: int, w0: int, n0: int) -> float:
    """One-sided p-value that win rate 1 > win rate 0."""
    if n1 == 0 or n0 == 0:
        return 1.0
    p = (w1 + w0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    if se == 0:
        return 1.0
    z = (w1 / n1 - w0 / n0) / se
    return 0.5 * math.erfc(z / math.sqrt(2))


def _filters(w: dict, plan: Plan) -> list[dict]:
    df, _ = get_features(w["symbol"], w["tf"])
    have = {base_id(c) for c in w["conditions"]}
    base = ExecutionLab(w["symbol"], w["tf"], w["conditions"], w["direction"], w["cooldown"]).run_plan(plan)
    b_is = base["is"]
    bw, bn = round((b_is.get("win_rate") or 0) * b_is.get("taken", 0)), b_is.get("taken", 0)
    rows = []
    for c in available(df):
        if c.id in have:
            continue
        try:
            r = ExecutionLab(w["symbol"], w["tf"], w["conditions"] + [c.id], w["direction"], w["cooldown"]).run_plan(plan)
        except Exception:
            continue
        s_is, s_oos = r["is"], r["oos"]
        n = s_is.get("taken", 0)
        if n < MIN_IS or n >= bn:
            continue
        wins = round(s_is["win_rate"] * n)
        # compare against the trades the filter REMOVED (independent samples)
        p = _ztest(wins, n, bw - wins, bn - n)
        rows.append({"id": c.id, "label": label_of(c.id), "p": p, "is": _cell(s_is), "oos": _cell(s_oos)})
    if rows:
        q = _bh(np.array([x["p"] for x in rows]))
        for x, qq in zip(rows, q):
            x["q"] = float(qq)
    keep = [x for x in rows if x["q"] <= FDR_Q and (x["is"]["avg_r"] or -9) >= (b_is.get("avg_r") or 0)]
    keep.sort(key=lambda x: -x["is"]["win_rate"])
    return [{"tried": len(rows), "base": {"is": _cell(b_is), "oos": _cell(base["oos"])}}] + keep[:10]


def _pool(fronts: list[dict]) -> list[dict]:
    out = []
    for k in fronts[0]:
        row = {"key": k, "description": Plan.from_key(k).describe()}
        for part in ("is", "oos"):
            cells = [f[k][part] for f in fronts if f[k][part]["n"] and f[k][part]["avg_r"] is not None]
            n = sum(c["n"] for c in cells)
            row[part] = {"n": n,
                         "win_rate": sum(c["win_rate"] * c["n"] for c in cells) / n if n else None,
                         "avg_r": sum(c["avg_r"] * c["n"] for c in cells) / n if n else None}
        out.append(row)
    out.sort(key=lambda r: -(r["oos"]["win_rate"] or 0))
    return out


def run(progress=None) -> dict:
    watches = [w for w in db.list_watches() if not (w.get("conditions") or [""])[0].startswith("strategy:")]
    # identical rule + plan copies add nothing
    seen, uniq = set(), []
    for w in watches:
        k = (w["symbol"], w["tf"], w["direction"], tuple(w["conditions"]), w.get("plan") or BASELINE.key)
        if k not in seen:
            seen.add(k)
            uniq.append(w)
    pool = concept_setups()
    total = len(uniq) + len(pool)
    per_watch = []
    for k, w in enumerate(uniq):
        if progress:
            progress(k, total, f"watch #{w['id']}")
        lab = ExecutionLab(w["symbol"], w["tf"], w["conditions"], w["direction"], w["cooldown"])
        front = _frontier(lab)
        current = w.get("plan") or BASELINE.key
        pick = _pick(front, current)
        filt = _filters(w, Plan.from_key(pick["key"] if pick else current))
        per_watch.append({"id": w["id"], "name": w["name"], "symbol": w["symbol"], "tf": w["tf"], "direction": w["direction"],
                          "current": {"key": current, **front[current]} if current in front else {"key": current},
                          "pick": pick, "filters": filt[1:], "filters_tried": filt[0]["tried"],
                          "filter_base": filt[0]["base"], "frontier": front})
    fronts = []
    for k, p in enumerate(pool):
        if progress:
            progress(len(uniq) + k, total, f"{p['symbol']} {p['tf']} {p['id']}")
        try:
            fronts.append(_frontier(ExecutionLab(p["symbol"], p["tf"], p["conditions"], p["direction"], p["cooldown"])))
        except Exception:
            continue
    out = {"finished": int(time.time()), "watches": per_watch, "pooled": _pool(fronts), "pooled_setups": len(fronts),
           "rules": {"max_exp_drop": MAX_EXP_DROP, "min_is": MIN_IS, "fdr_q": FDR_Q}}
    db.set_setting(KEY, out)
    return out


# ------------------------------------------------------------------ live exit choice per watch
# Live exits are limited to an ATR stop and a 1/2/3R target: those are what the statistics, the chart,
# the "similar past matches" and the live levels all represent exactly, so every number shown agrees.
LIVE_STOPS = (1.0, 1.5, 2.0, 3.0)
MIN_WR = 0.55      # "higher win rate": in-sample win rate at least this ...
MIN_R = 0.10       # ... and in-sample profit per trade at least this
_exit_cache: dict[tuple, list] = {}


def exit_plan(stop_atr: float, target_r: float) -> str:
    return Plan("next", f"atr{stop_atr:g}", f"r{target_r:g}", "none").key


def plan_exit(key: str | None) -> tuple[float, float] | None:
    """(stop_atr, target_r) of a plan that is a plain live exit, else None."""
    if not key:
        return None
    p = Plan.from_key(key)
    if p.entry != "next" or p.manage != "none" or not p.stop.startswith("atr") or not p.target.startswith("r"):
        return None
    s, t = p.stop_atr, float(p.target[1:])
    return (s, t) if s in LIVE_STOPS and t in TARGETS_R else None


def _brief(s: dict) -> dict:
    return {"n": s.get("n", 0), "win_rate": s.get("win_rate"), "avg_r": s.get("avg_r")}


def exit_profiles(w: dict) -> dict:
    """Every live exit of a condition watch with in-sample and unseen results, plus two profiles chosen on
    in-sample data only: the best profit per trade, and the best profit per trade among high-win-rate exits."""
    from ..research.hypothesis import run_test
    df, _ = get_features(w["symbol"], w["tf"])
    key = (w["symbol"], w["tf"], tuple(w["conditions"]), w["direction"], w["cooldown"], len(df))
    if key not in _exit_cache:
        rows = []
        for s in LIVE_STOPS:
            for t in TARGETS_R:
                res = run_test(w["symbol"], w["tf"], w["conditions"], w["direction"], t, s, cooldown=w["cooldown"],
                               max_examples=0, light=True)
                st, plan = res["stats"], exit_plan(s, t)
                rows.append({"stop_atr": s, "target_r": t, "plan": plan, "description": Plan.from_key(plan).describe(),
                             "is": _brief(st["in_sample"]), "oos": _brief(st["out_of_sample"]), "grade": res["evidence"]["grade"]})
        if len(_exit_cache) > 50:
            _exit_cache.clear()
        _exit_cache[key] = rows
    rows = _exit_cache[key]
    ok = [r for r in rows if r["is"]["n"] >= MIN_IS and r["is"]["avg_r"] is not None]
    best = max(ok, key=lambda r: r["is"]["avg_r"], default=None)
    high = max((r for r in ok if r["is"]["win_rate"] >= MIN_WR and r["is"]["avg_r"] >= MIN_R),
               key=lambda r: r["is"]["avg_r"], default=None)
    cur = plan_exit(w.get("plan")) if w.get("plan") else (w["stop_atr"], w["target_r"])
    return {"exits": rows, "high_wr": high, "best_r": best,
            "current": next((r for r in rows if cur and (r["stop_atr"], r["target_r"]) == cur), None),
            "rule": f"Both choices are made on the older 70% of history only (the newest 30% is the unseen check). "
                    f"Higher win rate = most profit per trade among exits that won at least {MIN_WR:.0%} of past trades "
                    f"and made at least {MIN_R:+.2f}R per trade. Win = target reached; costs included."}
