"""Persistence for discovery runs and discovered patterns (same SQLite file as the live system)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from ..config import DATA_DIR

_lock = threading.Lock()
_conn = sqlite3.connect(DATA_DIR / "live.db", check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created INTEGER NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,           -- queued / running / done / failed / cancelled
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    finished INTEGER,
    summary TEXT
);
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    created INTEGER NOT NULL,
    symbol TEXT NOT NULL, tf TEXT NOT NULL, direction TEXT NOT NULL,
    target_r REAL NOT NULL, stop_atr REAL NOT NULL, cooldown INTEGER NOT NULL,
    conditions TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,           -- validated / promising / failed
    score INTEGER, grade TEXT, q_oos REAL, stability REAL,
    n_all INTEGER, avg_r REAL, win_rate REAL, oos_n INTEGER, oos_avg_r REAL, profit_factor REAL,
    data TEXT NOT NULL,             -- full statistics, description, extras
    compare TEXT                    -- cross-instrument / timeframe comparison (filled on demand)
);
CREATE INDEX IF NOT EXISTS ix_patterns_key ON patterns(symbol, tf, direction, status);
""")
_conn.commit()


def create_run(params: dict, total: int) -> int:
    with _lock:
        cur = _conn.execute("INSERT INTO runs (created, params, status, total) VALUES (?,?,?,?)",
                            (int(time.time()), json.dumps(params), "queued", total))
        _conn.commit()
        return cur.lastrowid


def update_run(rid: int, **f) -> None:
    if "summary" in f:
        f["summary"] = json.dumps(f["summary"])
    sets = ", ".join(f"{k}=?" for k in f)
    with _lock:
        _conn.execute(f"UPDATE runs SET {sets} WHERE id=?", (*f.values(), rid))
        _conn.commit()


def list_runs(limit: int = 30) -> list[dict]:
    with _lock:
        rows = _conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["summary"] = json.loads(d["summary"]) if d.get("summary") else None
        out.append(d)
    return out


def replace_patterns(run_id: int, symbol: str, tf: str, direction: str, target_r: float, pats: list[dict]) -> None:
    """A new search for the same market/direction/target supersedes the previous results."""
    now = int(time.time())
    with _lock:
        _conn.execute("DELETE FROM patterns WHERE symbol=? AND tf=? AND direction=? AND target_r=?",
                      (symbol, tf, direction, target_r))
        for p in pats:
            a, o = p["all"], p["oos"]
            _conn.execute(
                "INSERT INTO patterns (run_id, created, symbol, tf, direction, target_r, stop_atr, cooldown, conditions,"
                " source, status, score, grade, q_oos, stability, n_all, avg_r, win_rate, oos_n, oos_avg_r, profit_factor, data)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, now, p["symbol"], p["tf"], p["direction"], p["target_r"], p["stop_atr"], p["cooldown"],
                 json.dumps(p["conditions"]), p["source"], p["status"], p["score"], p["grade"], p["q_oos"],
                 p["stability"], a.get("n", 0), a.get("avg_r"), a.get("win_rate"), o.get("n", 0), o.get("avg_r"),
                 _finite(a.get("profit_factor")), json.dumps(_jsonable(p))))
        _conn.commit()


def _finite(x):
    return x if isinstance(x, (int, float)) and x == x and abs(x) != float("inf") else None


def _jsonable(o):
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, float):
        return _finite(o)
    if hasattr(o, "item"):
        return _jsonable(o.item())
    return o


def list_patterns(symbol=None, tf=None, direction=None, statuses=None, min_n=0, limit=500) -> list[dict]:
    q = "SELECT id, run_id, created, symbol, tf, direction, target_r, stop_atr, cooldown, conditions, source, status," \
        " score, grade, q_oos, stability, n_all, avg_r, win_rate, oos_n, oos_avg_r, profit_factor FROM patterns WHERE n_all>=?"
    args: list = [min_n]
    for col, v in (("symbol", symbol), ("tf", tf), ("direction", direction)):
        if v:
            q += f" AND {col}=?"
            args.append(v)
    if statuses:
        q += f" AND status IN ({','.join('?' * len(statuses))})"
        args += statuses
    q += " ORDER BY CASE status WHEN 'validated' THEN 0 WHEN 'promising' THEN 1 ELSE 2 END, score DESC, avg_r DESC LIMIT ?"
    args.append(limit)
    with _lock:
        rows = _conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["conditions"] = json.loads(d["conditions"])
        out.append(d)
    return out


def get_pattern(pid: int) -> dict | None:
    with _lock:
        r = _conn.execute("SELECT * FROM patterns WHERE id=?", (pid,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["conditions"] = json.loads(d["conditions"])
    d["data"] = json.loads(d["data"])
    d["compare"] = json.loads(d["compare"]) if d.get("compare") else None
    return d


def set_compare(pid: int, comp: dict) -> None:
    with _lock:
        _conn.execute("UPDATE patterns SET compare=? WHERE id=?", (json.dumps(_jsonable(comp)), pid))
        _conn.commit()


def counts() -> dict:
    with _lock:
        rows = _conn.execute("SELECT status, COUNT(*) FROM patterns GROUP BY status").fetchall()
    return {r[0]: r[1] for r in rows}
