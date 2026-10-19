"""Optional Telegram delivery of signals (standard library only).

The bot token and chat id are stored only in the local SQLite settings table.
"""
from __future__ import annotations

import html
import json
import urllib.parse
import urllib.request

from . import db

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token: str, method: str, params: dict | None = None, timeout: float = 10) -> dict:
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def config() -> dict:
    return db.get_setting("telegram", {}) or {}


def configured() -> bool:
    c = config()
    return bool(c.get("token") and c.get("chat_id") and c.get("enabled", True))


def detect_chat_id(token: str) -> int | None:
    """After the user sends any message to their bot, its chat id appears in getUpdates."""
    res = _call(token, "getUpdates")
    for upd in reversed(res.get("result", [])):
        msg = upd.get("message") or upd.get("channel_post")
        if msg and msg.get("chat", {}).get("id"):
            return msg["chat"]["id"]
    return None


def send(text: str) -> None:
    c = config()
    _call(c["token"], "sendMessage", {"chat_id": c["chat_id"], "text": text, "parse_mode": "HTML",
                                      "disable_web_page_preview": "true"})


def _p(x: float) -> str:
    return f"{x:.2f}" if x >= 1000 else f"{x:.3f}" if x >= 50 else f"{x:.5f}"


def format_signal(s: dict) -> str:
    p = s["payload"]
    st = p.get("stats") or {}
    a = st.get("all", {})
    o = st.get("out_of_sample", {})
    ev = p.get("evidence") or {}
    arrow = "🟢 BUY" if s["direction"] == "long" else "🔴 SELL"
    late = p.get("late_bars") or 0
    lines = [
        *([f"⏱ <b>LATE</b>: fired {late} candle{'s' if late != 1 else ''} ago while the app was offline — check price before acting."] if late else []),
        f"<b>{arrow} {s['symbol']} {s['tf']}</b> — {html.escape(p['name'])}",
        f"Entry ≈ {_p(s['entry'])} · SL {_p(s['stop'])} · TP {_p(s['target'])} ({p['target_r']:g}R)",
    ]
    if p.get("unproven"):
        lines.append("⚠ Unproven strategy: no statistically confirmed edge yet — forward-testing.")
    for t in p.get("liquidity_targets", [])[:2]:
        lines.append(f"Liquidity: {_p(t['price'])} ({html.escape(t['kind'])}, {t['rr']:.1f}R)")
    if a.get("n"):
        lines.append(f"History: grade {ev.get('grade', '?')} · {a['n']} past · win {a['win_rate'] * 100:.0f}% · "
                     f"{a['avg_r']:+.2f}R/trade (out-of-sample {o.get('avg_r', 0):+.2f}R)")
    sim = p.get("similar")
    if sim and sim.get("k"):
        lines.append(f"Closest {sim['k']} past matches: win {sim['win_rate'] * 100:.0f}%, {sim['avg_r']:+.2f}R")
    if p.get("decay", {}).get("status") in ("weakening", "broken"):
        lines.append(f"⚠ Edge {p['decay']['status']}: {html.escape(p['decay']['message'])}")
    lines.append("<i>Historical evidence, not a guarantee.</i>")
    return "\n".join(lines)
