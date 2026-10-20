"""Forward-test scoreboard: is each watched setup living up to its backtest?

Forward trades are rebuilt with the backtest engine on candles that closed AFTER the rule was chosen
("forward since"), so the record does not depend on the app having been open when a setup fired.
Expectations come from the unseen (out-of-sample) backtest trades before that date. Recorded alerts
are matched against the forward trades to show signals the app missed.

Checks (one-sided, flagged at 5%):
  * win rate: binomial probability of this few wins if the unseen win rate still held;
  * R per trade: normal approximation with the unseen trades' spread;
  * signal count: Poisson probability of this few / this many signals at the unseen rate.
"""
from __future__ import annotations

import math
import time

import numpy as np
from scipy import stats as sps

from ..config import TIMEFRAMES
from . import db

MIN_JUDGE = 10          # forward trades needed before a verdict
ALPHA = 0.05
# rules restored on 2026-10-17 were chosen on 2026-10-07 (watches #17-#20, see CLAUDE.md)
DEFAULT_SINCE = {17: 1791331200, 18: 1791331200, 19: 1791331200, 20: 1791331200}
_cache: dict = {}


def forward_since(w: dict) -> int:
    custom = db.get_setting("forward_since") or {}
    return int(custom.get(str(w["id"]), DEFAULT_SINCE.get(w["id"], w["created"])))


def _summ(r: np.ndarray, won: np.ndarray) -> dict:
    if not len(r):
        return {"n": 0}
    return {"n": int(len(r)), "wins": int(won.sum()), "win_rate": float(won.mean()), "avg_r": float(r.mean()),
            "total_r": float(r.sum())}


def score_watch(w: dict, now: float | None = None) -> dict:
    from ..execution.portfolio import _setup_trades
    now = now or time.time()
    since = forward_since(w)
    trades = sorted(_setup_trades(w), key=lambda t: t["entry_time"])
    won = lambda ts: np.array([t["exit"] == "target" if "exit" in t else t["r"] > 0 for t in ts], bool)
    r_of = lambda ts: np.array([t["r"] for t in ts], float)

    # expectation: unseen backtest trades that were known when the rule was chosen
    ref = [t for t in trades if t.get("oos") and t["exit_time"] <= since]
    exp = _summ(r_of(ref), won(ref))
    if ref:
        days = max((ref[-1]["entry_time"] - ref[0]["entry_time"]) / 86400, 1)
        exp["per_month"] = len(ref) / days * 30.44
        exp["sd_r"] = float(r_of(ref).std(ddof=1)) if len(ref) > 1 else None

    fwd = [t for t in trades if t["entry_time"] >= since]
    fwd_closed = [t for t in fwd if t["exit_time"] <= now]
    got = _summ(r_of(fwd_closed), won(fwd_closed))
    days_fwd = max((now - since) / 86400, 0.0)

    checks, verdict = [], "too early"
    if exp.get("n", 0) >= 10:
        lam = exp["per_month"] * days_fwd / 30.44
        got["expected_signals"] = lam
        if lam > 0:
            p_few = float(sps.poisson.cdf(len(fwd), lam))
            p_many = float(sps.poisson.sf(len(fwd) - 1, lam))
            if p_few < ALPHA:
                checks.append({"check": "signals", "ok": False, "p": p_few,
                               "text": f"Fewer signals than expected ({len(fwd)} vs ~{lam:.1f}): the conditions are rarer now."})
            elif p_many < ALPHA:
                checks.append({"check": "signals", "ok": False, "p": p_many,
                               "text": f"More signals than expected ({len(fwd)} vs ~{lam:.1f}): market behaviour has changed."})
            else:
                checks.append({"check": "signals", "ok": True, "p": min(p_few, p_many),
                               "text": f"{len(fwd)} signals vs ~{lam:.1f} expected: normal."})
        if got["n"]:
            p_wr = float(sps.binom.cdf(got["wins"], got["n"], exp["win_rate"]))
            checks.append({"check": "win rate", "ok": p_wr >= ALPHA, "p": p_wr,
                           "text": f"Won {got['wins']}/{got['n']} vs {exp['win_rate']:.0%} expected"
                                   + (" - worse than chance explains." if p_wr < ALPHA else ": within normal variation.")})
            if exp.get("sd_r"):
                z = (got["avg_r"] - exp["avg_r"]) / (exp["sd_r"] / math.sqrt(got["n"]))
                p_r = float(sps.norm.cdf(z))
                checks.append({"check": "R per trade", "ok": p_r >= ALPHA, "p": p_r,
                               "text": f"{got['avg_r']:+.2f}R per trade vs {exp['avg_r']:+.2f}R expected"
                                       + (" - worse than chance explains." if p_r < ALPHA else ": within normal variation.")})
        bad = [c for c in checks if not c["ok"] and c["check"] != "signals"]
        if bad:
            verdict = "underperforming"
        elif got["n"] >= MIN_JUDGE:
            verdict = "on track"
    else:
        verdict = "no expectation"
        checks.append({"check": "backtest", "ok": False, "p": None,
                       "text": "Fewer than 10 unseen backtest trades before the forward start: nothing reliable to compare with."})

    # alerts the app actually sent for these trades (signal bar closes one candle before entry)
    tf_s = TIMEFRAMES.get(w["tf"], 60) * 60
    sent = {s["bar_time"] for s in db.list_signals(limit=100000, watch_id=w["id"])}
    alerted = sum(1 for t in fwd if any(abs(t["entry_time"] - tf_s - b) <= tf_s for b in sent))
    return {
        "id": w["id"], "name": w["name"], "symbol": w["symbol"], "tf": w["tf"], "direction": w["direction"],
        "active": bool(w["active"]), "plan": w.get("plan"), "since": since, "days": days_fwd,
        "expected": exp, "forward": got, "open": len(fwd) - len(fwd_closed), "checks": checks, "verdict": verdict,
        "alerts": {"sent": len(sent), "matched": alerted, "missed": len(fwd) - alerted},
        "trades": [{"entry_time": t["entry_time"], "exit_time": t["exit_time"], "r": t["r"],
                    "direction": t.get("direction"), "exit": t.get("exit"),
                    "closed": t["exit_time"] <= now} for t in fwd][-50:],
    }


def scoreboard(force: bool = False) -> dict:
    key = "all"
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < 600:
        return hit[1]
    rows, errors = [], []
    for w in db.list_watches():
        try:
            rows.append(score_watch(w))
        except Exception as e:  # one broken watch must not hide the others
            errors.append({"id": w["id"], "error": str(e)})
    closed = [t for r in rows if r["active"] for t in r["trades"] if t["closed"]]
    rs = np.array([t["r"] for t in closed], float)
    total = {"n": int(len(rs)), "avg_r": float(rs.mean()) if len(rs) else None,
             "total_r": float(rs.sum()) if len(rs) else 0.0,
             "win_rate": float(np.mean([t["exit"] == "target" for t in closed])) if closed else None}
    out = {"as_of": int(time.time()), "watches": rows, "total": total, "errors": errors,
           "rule": (f"Forward trades are rebuilt from candles that closed after each rule was chosen. A check fails when "
                    f"the result is worse than the unseen backtest at the {ALPHA:.0%} level; verdicts need {MIN_JUDGE}+ trades.")}
    _cache[key] = (time.time(), out)
    db.set_setting("scoreboard_verdicts", {"as_of": out["as_of"], "verdicts": {str(r["id"]): r["verdict"] for r in rows}})
    return out
