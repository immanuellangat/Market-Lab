"""Background job queue: discovery runs and pattern comparisons, one at a time
(large M5 tables are memory-heavy, so jobs never run in parallel)."""
from __future__ import annotations

import queue
import threading
import time
import traceback

from ..config import SYMBOLS, TIMEFRAMES
from ..data.store import bars_path
from ..research.hypothesis import run_test
from . import store
from .search import Discovery

_q: "queue.Queue[tuple]" = queue.Queue()
_cancel: set[int] = set()
_state = {"current": None, "log": []}
_started = False
_lock = threading.Lock()


def _log(msg: str) -> None:
    _state["log"] = ([f"{time.strftime('%H:%M:%S')} {msg}"] + _state["log"])[:100]


def _ensure_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_worker, daemon=True, name="discovery-worker").start()


def _worker() -> None:
    while True:
        job = _q.get()
        try:
            if job[0] == "run":
                _do_run(*job[1:])
            elif job[0] == "compare":
                _do_compare(*job[1:])
            elif job[0] == "study":
                _do_study(*job[1:])
        except Exception as e:
            traceback.print_exc()
            _log(f"job failed: {e}")
        finally:
            _state["current"] = None


# ------------------------------------------------------------------ discovery runs
def submit_run(symbols: list[str], tfs: list[str], directions: list[str], target_r: float, stop_atr: float,
               cooldown: int, min_n: int | None) -> int:
    tasks = [(s, tf, d) for s in symbols for tf in tfs for d in directions if bars_path(s, tf).exists()]
    params = {"symbols": symbols, "tfs": tfs, "directions": directions, "target_r": target_r,
              "stop_atr": stop_atr, "cooldown": cooldown, "min_n": min_n}
    rid = store.create_run(params, len(tasks))
    _ensure_worker()
    _q.put(("run", rid, tasks, params))
    return rid


def cancel_run(rid: int) -> None:
    _cancel.add(rid)


def _do_run(rid: int, tasks: list[tuple], p: dict) -> None:
    if rid in _cancel:
        store.update_run(rid, status="cancelled", finished=int(time.time()))
        return
    store.update_run(rid, status="running")
    _state["current"] = f"run #{rid}"
    summary = {"validated": 0, "promising": 0, "failed": 0, "tested": 0}
    for k, (s, tf, d) in enumerate(tasks):
        if rid in _cancel:
            store.update_run(rid, status="cancelled", finished=int(time.time()), summary=summary)
            return
        store.update_run(rid, progress=k, message=f"{s} {tf} {d}")
        try:
            disc = Discovery(s, tf, d, p["target_r"], p["stop_atr"], p["cooldown"], p["min_n"],
                             log=lambda m: (_log(m), store.update_run(rid, message=m)))
            pats = disc.run()
            store.replace_patterns(rid, s, tf, d, p["target_r"], pats, p["stop_atr"])
            summary["tested"] += disc.n_tested
            for x in pats:
                summary[x["status"]] += 1
            _log(f"{s} {tf} {d}: {sum(x['status'] == 'validated' for x in pats)} validated, "
                 f"{sum(x['status'] == 'promising' for x in pats)} promising")
        except Exception as e:
            traceback.print_exc()
            _log(f"{s} {tf} {d} failed: {e}")
    store.update_run(rid, status="done", progress=len(tasks), finished=int(time.time()), message="finished", summary=summary)


# ------------------------------------------------------------------ comparisons
def submit_compare(pid: int) -> None:
    _ensure_worker()
    store.set_compare(pid, {"status": "queued"})
    _q.put(("compare", pid))


def _quick(symbol: str, tf: str, p: dict) -> dict:
    try:
        r = run_test(symbol, tf, p["conditions"], p["direction"], p["target_r"], p["stop_atr"],
                     cooldown=p["cooldown"], max_examples=0, light=True)
    except KeyError as e:  # condition needs a higher timeframe this dataset does not have
        return {"symbol": symbol, "tf": tf, "error": str(e)}
    a, o = r["stats"]["all"], r["stats"]["out_of_sample"]
    return {"symbol": symbol, "tf": tf, "n": a.get("n", 0), "win_rate": a.get("win_rate"), "avg_r": a.get("avg_r"),
            "edge_r": a.get("edge_r"), "oos_n": o.get("n", 0), "oos_avg_r": o.get("avg_r"),
            "grade": r["evidence"]["grade"], "score": r["evidence"]["score"]}


def _do_compare(pid: int) -> None:
    p = store.get_pattern(pid)
    if not p:
        return
    _state["current"] = f"compare pattern #{pid}"
    comp = {"status": "running", "symbols": [], "timeframes": []}
    store.set_compare(pid, comp)
    for s in SYMBOLS:
        if bars_path(s, p["tf"]).exists():
            comp["symbols"].append(_quick(s, p["tf"], p))
            store.set_compare(pid, comp)
    for tf in TIMEFRAMES:
        if bars_path(p["symbol"], tf).exists():
            comp["timeframes"].append(_quick(p["symbol"], tf, p))
            store.set_compare(pid, comp)
    comp["status"] = "done"
    store.set_compare(pid, comp)


# ------------------------------------------------------------------ pooled execution study
study_state: dict = {"running": False, "progress": 0, "total": 0, "message": ""}


def submit_study(source: str = "concepts") -> None:
    _ensure_worker()
    study_state.update(running=True, progress=0, total=0, message="queued")
    _q.put(("study", source))


def _do_study(source: str = "concepts") -> None:
    from ..execution.study import run_study
    _state["current"] = "execution study"

    def prog(k, n, msg):
        study_state.update(progress=k, total=n, message=msg)
    try:
        res = run_study(progress=prog, source=source)
        _log(f"execution study finished: {res['setups']} setups, {len(res['robust'])} robust plans")
    finally:
        study_state.update(running=False, message="finished")


def state() -> dict:
    return {"current": _state["current"], "queued": _q.qsize(), "log": _state["log"][:40]}
