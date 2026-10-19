"""SQLite storage for the watchlist, generated signals and live settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time

from ..config import DATA_DIR

_DB = DATA_DIR / "live.db"
_lock = threading.Lock()
_conn = sqlite3.connect(_DB, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created INTEGER NOT NULL,
    name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    tf TEXT NOT NULL,
    direction TEXT NOT NULL,
    conditions TEXT NOT NULL,
    target_r REAL NOT NULL,
    stop_atr REAL NOT NULL,
    cooldown INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    stats TEXT,
    stats_updated INTEGER
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    tf TEXT NOT NULL,
    direction TEXT NOT NULL,
    bar_time INTEGER NOT NULL,
    detected_at INTEGER NOT NULL,
    entry REAL, stop REAL, target REAL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    result_r REAL,
    closed_at INTEGER,
    UNIQUE (watch_id, bar_time)
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS price_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    tf TEXT,                          -- timeframe the zone was drawn on (context only)
    label TEXT NOT NULL,
    top REAL NOT NULL, bottom REAL NOT NULL,   -- a price level has top == bottom
    status TEXT NOT NULL DEFAULT 'active',     -- active / triggered / cancelled
    armed_at INTEGER,                 -- NULL while price is still inside the zone it was created in
    last_checked INTEGER,
    triggered_at INTEGER, trigger_price REAL, late INTEGER NOT NULL DEFAULT 0
);
""")
_conn.commit()
# migration: execution plan per watched setup (added with the execution study)
if "plan" not in {r[1] for r in _conn.execute("PRAGMA table_info(watchlist)").fetchall()}:
    _conn.execute("ALTER TABLE watchlist ADD COLUMN plan TEXT")
    _conn.commit()

WATCH_JSON = ("conditions", "stats")


def _row(r: sqlite3.Row | None, json_cols=()) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for c in json_cols:
        if d.get(c):
            d[c] = json.loads(d[c])
    return d


# ------------------------------------------------------------------ watchlist
def add_watch(w: dict) -> int:
    with _lock:
        cur = _conn.execute(
            "INSERT INTO watchlist (created,name,symbol,tf,direction,conditions,target_r,stop_atr,cooldown,stats,stats_updated,plan)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(time.time()), w["name"], w["symbol"], w["tf"], w["direction"], json.dumps(w["conditions"]),
             w["target_r"], w["stop_atr"], w["cooldown"], json.dumps(w.get("stats")), int(time.time()), w.get("plan")))
        _conn.commit()
        return cur.lastrowid


def list_watches(active_only: bool = False, symbol: str | None = None, tf: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM watchlist WHERE 1=1", []
    if active_only:
        q += " AND active=1"
    if symbol:
        q += " AND symbol=?"; args.append(symbol)
    if tf:
        q += " AND tf=?"; args.append(tf)
    with _lock:
        rows = _conn.execute(q + " ORDER BY id", args).fetchall()
    return [_row(r, WATCH_JSON) for r in rows]


def get_watch(wid: int) -> dict | None:
    with _lock:
        return _row(_conn.execute("SELECT * FROM watchlist WHERE id=?", (wid,)).fetchone(), WATCH_JSON)


def update_watch(wid: int, **fields) -> None:
    if not fields:
        return
    for c in WATCH_JSON:
        if c in fields:
            fields[c] = json.dumps(fields[c])
    sets = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        _conn.execute(f"UPDATE watchlist SET {sets} WHERE id=?", (*fields.values(), wid))
        _conn.commit()


def delete_watch(wid: int) -> None:
    with _lock:
        _conn.execute("DELETE FROM watchlist WHERE id=?", (wid,))
        _conn.commit()


# ------------------------------------------------------------------ signals
def add_signal(s: dict) -> int | None:
    """Returns the new id, or None if this setup already signalled on this bar."""
    with _lock:
        try:
            cur = _conn.execute(
                "INSERT INTO signals (watch_id,symbol,tf,direction,bar_time,detected_at,entry,stop,target,payload)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (s["watch_id"], s["symbol"], s["tf"], s["direction"], s["bar_time"], int(time.time()),
                 s["entry"], s["stop"], s["target"], json.dumps(s["payload"])))
        except sqlite3.IntegrityError:
            return None
        _conn.commit()
        return cur.lastrowid


def list_signals(since_id: int = 0, limit: int = 200, watch_id: int | None = None) -> list[dict]:
    q, args = "SELECT * FROM signals WHERE id>?", [since_id]
    if watch_id is not None:
        q += " AND watch_id=?"; args.append(watch_id)
    with _lock:
        rows = _conn.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [_row(r, ("payload",)) for r in rows]


def open_signals() -> list[dict]:
    with _lock:
        rows = _conn.execute("SELECT * FROM signals WHERE status='open'").fetchall()
    return [_row(r, ("payload",)) for r in rows]


def last_signal_time(watch_id: int) -> int | None:
    with _lock:
        r = _conn.execute("SELECT MAX(bar_time) FROM signals WHERE watch_id=?", (watch_id,)).fetchone()
    return r[0]


def close_signal(sid: int, status: str, result_r: float, closed_at: int) -> None:
    with _lock:
        _conn.execute("UPDATE signals SET status=?, result_r=?, closed_at=? WHERE id=?", (status, result_r, closed_at, sid))
        _conn.commit()


def watch_record(watch_id: int) -> dict:
    """Live track record of a watched setup (signals generated since it was added)."""
    with _lock:
        rows = _conn.execute("SELECT status, result_r FROM signals WHERE watch_id=?", (watch_id,)).fetchall()
    closed = [r["result_r"] for r in rows if r["status"] != "open" and r["result_r"] is not None]
    return {
        "signals": len(rows),
        "open": sum(1 for r in rows if r["status"] == "open"),
        "wins": sum(1 for r in rows if r["status"] == "win"),
        "losses": sum(1 for r in rows if r["status"] == "loss"),
        "avg_r": sum(closed) / len(closed) if closed else None,
        "total_r": sum(closed) if closed else 0.0,
    }


# ------------------------------------------------------------------ settings
def get_setting(key: str, default=None):
    with _lock:
        r = _conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def set_setting(key: str, value) -> None:
    with _lock:
        _conn.execute("INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, json.dumps(value)))
        _conn.commit()


# ------------------------------------------------------------------ price alerts
def add_price_alert(a: dict) -> int:
    with _lock:
        cur = _conn.execute("INSERT INTO price_alerts (created,symbol,tf,label,top,bottom,armed_at,last_checked)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (int(time.time()), a["symbol"], a.get("tf"), a["label"], a["top"], a["bottom"],
                             a.get("armed_at"), int(time.time())))
        _conn.commit()
        return cur.lastrowid


def list_price_alerts(symbol: str | None = None, status: str | None = None, triggered_since: int | None = None,
                      limit: int = 200) -> list[dict]:
    q, args = "SELECT * FROM price_alerts WHERE 1=1", []
    if symbol:
        q += " AND symbol=?"; args.append(symbol)
    if status:
        q += " AND status=?"; args.append(status)
    if triggered_since is not None:
        q += " AND triggered_at > ?"; args.append(triggered_since)
    q += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with _lock:
        return [dict(r) for r in _conn.execute(q, args).fetchall()]


def update_price_alert(aid: int, **fields) -> None:
    if not fields:
        return
    with _lock:
        _conn.execute(f"UPDATE price_alerts SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), aid))
        _conn.commit()
