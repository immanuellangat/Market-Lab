"""Live feed + signal scanner.

A background thread:
  1. pulls each newly *closed* candle from MT5 (per symbol / timeframe, right after it closes),
  2. evaluates every watched setup on that candle using live_features() (recent bars only),
  3. records a signal with full context, notifies (in-app feed, optional Telegram),
  4. follows every open signal until its stop, target or time horizon is reached,
     building an honest live track record for each setup.
"""
from __future__ import annotations

import threading
import time
import traceback
from collections import deque

import numpy as np
import pandas as pd

from ..config import DEFAULT_HORIZON, SYMBOLS, TIMEFRAMES
from ..data import store
from ..data.mt5_source import MT5Error, source
from ..features import engine
from ..research.conditions import available, evaluate, label_of
from ..research.hypothesis import WARMUP, condition_matrix, dedupe, point_size, run_test
from ..research.outcomes import get_outcomes, spread_price
from ..research.zones import _liquidity
from ..execution.levels import levels_for_entry, signal_spec
from ..execution.simulate import BASELINE, Plan, simulate
from ..strategies import crt, live as strat
from . import db, notify

TICK_SECONDS = 10
GRACE_SECONDS = 4          # wait a moment after the candle closes so the broker has it
STATS_REFRESH_SECONDS = 24 * 3600
SIMILAR_K = 30


def _unix(ts) -> int:
    return int(pd.Timestamp(ts).timestamp())


def snapshot_stats(w: dict) -> dict:
    """Historical evidence for a watched setup, stored with it and refreshed daily."""
    if strat.is_strategy(w):
        return strat.snapshot_stats(w)
    res = run_test(w["symbol"], w["tf"], w["conditions"], w["direction"], w["target_r"], w["stop_atr"],
                   cooldown=w["cooldown"], max_examples=0)
    return {k: res.get(k) for k in ("stats", "evidence", "decay", "split_time", "params")} | {"as_of": int(time.time())}


class LiveService:
    def __init__(self) -> None:
        self.enabled = bool(db.get_setting("live_enabled", True))
        self.mt5_ok = False
        self.mt5_error: str | None = None
        self.last_tick: float | None = None
        self.last_bar: dict[tuple, int] = {}       # (symbol, tf) -> unix time of last stored bar
        self.next_try: dict[tuple, float] = {}     # back-off when no new candle was available
        self.log: deque[str] = deque(maxlen=200)
        self._thread: threading.Thread | None = None
        self._last_connect_try = 0.0
        self._scan_lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-service")
        self._thread.start()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        db.set_setting("live_enabled", on)

    def _run(self) -> None:
        while True:
            if self.enabled:
                try:
                    self.tick()
                except Exception as e:  # never let the loop die
                    self._log(f"error: {e}")
                    traceback.print_exc()
            time.sleep(TICK_SECONDS)

    def _log(self, msg: str) -> None:
        self.log.appendleft(f"{time.strftime('%H:%M:%S')} {msg}")

    # ------------------------------------------------------------------ main step
    def _ensure_mt5(self) -> bool:
        if source.connected and source.alive():
            self.mt5_ok, self.mt5_error = True, None
            return True
        if time.time() - self._last_connect_try < 60:
            return False
        self._last_connect_try = time.time()
        try:
            source.connect()
            self.mt5_ok, self.mt5_error = True, None
            self._log("connected to MT5")
            return True
        except MT5Error as e:
            self.mt5_ok, self.mt5_error = False, str(e)
            return False

    def _stored_last(self, symbol: str, tf: str) -> int | None:
        key = (symbol, tf)
        if key not in self.last_bar:
            p = store.bars_path(symbol, tf)
            if not p.exists():
                return None
            t = pd.read_parquet(p, columns=["time"])["time"]
            self.last_bar[key] = _unix(t.iloc[-1])
        return self.last_bar[key]

    def tick(self) -> None:
        self.last_tick = time.time()
        if not self._ensure_mt5():
            return
        now = time.time()
        updated: list[tuple[str, str]] = []
        for symbol in SYMBOLS:
            for tf, mins in TIMEFRAMES.items():
                last = self._stored_last(symbol, tf)
                if last is None:
                    continue
                key = (symbol, tf)
                next_close = last + 2 * mins * 60  # close time of the bar after the last stored one
                if now < next_close + GRACE_SECONDS or now < self.next_try.get(key, 0):
                    continue
                r = store.sync(source, symbol, tf)
                df_t = pd.read_parquet(store.bars_path(symbol, tf), columns=["time"])["time"]
                new_last = _unix(df_t.iloc[-1])
                if new_last > last:
                    self.last_bar[key] = new_last
                    self.next_try.pop(key, None)
                    updated.append(key)
                else:  # market closed / candle not published yet: back off
                    self.next_try[key] = now + min(mins * 60, 300)
        if updated:
            self._log("new candles: " + ", ".join(f"{s} {t}" for s, t in updated))
            for symbol, tf in updated:
                self.scan(symbol, tf)
            self.resolve_open()
        self._refresh_one_stale_watch()

    # ------------------------------------------------------------------ scanning
    def scan(self, symbol: str, tf: str, force: bool = False) -> list[int]:
        watches = db.list_watches(active_only=True, symbol=symbol, tf=tf)
        if not watches:
            return []
        with self._scan_lock:
            df, rec = engine.live_features(symbol, tf)
            last = len(df) - 1
            bar_time = _unix(df["time"].iloc[last])
            created = []
            for w in watches:
                if strat.is_strategy(w):
                    created += self._scan_strategy(w, force)
                    continue
                try:
                    hit = bool(evaluate(df, w["conditions"])[last])
                except KeyError as e:
                    self._log(f"watch #{w['id']} skipped: {e}")
                    continue
                if not hit:
                    continue
                prev = db.last_signal_time(w["id"])
                if prev is not None:
                    if prev >= bar_time:
                        continue
                    bars_since = int((df["time"] > pd.Timestamp(prev, unit="s", tz="UTC")).sum())
                    if bars_since <= w["cooldown"] and not force:
                        continue
                sid = self._make_signal(w, df, rec, last, bar_time)
                if sid:
                    created.append(sid)
            return created

    def _scan_strategy(self, w: dict, force: bool = False) -> list[int]:
        if w["plan"].startswith("smc|"):
            return self._scan_smc(w, force)
        try:
            hits = strat.check_latest(w)
        except Exception as e:
            self._log(f"watch #{w['id']} ({w['name']}) failed: {e}")
            return []
        out = []
        for e in hits:
            if "skip" in e:
                self._log(f"watch #{w['id']}: CRT {e['direction']} on {w['symbol']} skipped ({e['skip']})")
                continue
            prev = db.last_signal_time(w["id"])
            if prev is not None and prev >= e["bar_time"] and not force:
                continue
            sid = self._make_strategy_signal(w, e)
            if sid:
                out.append(sid)
        return out

    def _scan_smc(self, w: dict, force: bool = False) -> list[int]:
        try:
            hits = strat.smc_check_latest(w)
        except Exception as e:
            self._log(f"watch #{w['id']} ({w['name']}) failed: {e}")
            return []
        out = []
        stats = w.get("stats") or {}
        for e in hits:
            prev = db.last_signal_time(w["id"])
            if prev is not None and prev >= e["bar_time"] and not force:
                continue
            stop, target = levels_for_entry(e["spec"], e["close"])
            rr = abs(target - e["close"]) / abs(e["close"] - stop)
            payload = {
                "name": w["name"], "strategy": "SMC", "conditions": w["conditions"],
                "condition_labels": ["liquidity sweep", "CHoCH / MSS", "FVG / OB retest"]
                                    + [strat.SMC_FILTER_TEXT[f] for f in strat._smc_parse(w["plan"])["filters"]],
                "target_r": round(rr, 2), "stop_atr": None, "atr": e["atr"],
                "stop_text": "beyond the 10-bar swing", "target_text": f"{rr:.1f}R",
                "spec": e["spec"], "plan": w["plan"], "plan_text": w["name"],
                "liquidity_targets": [], "context": [],
                "stats": stats.get("stats"), "evidence": stats.get("evidence"), "decay": stats.get("decay"),
                "stats_as_of": stats.get("as_of"), "similar": None, "unproven": True,
            }
            s = {"watch_id": w["id"], "symbol": w["symbol"], "tf": w["tf"], "direction": e["direction"],
                 "bar_time": e["bar_time"], "entry": e["close"], "stop": stop, "target": target, "payload": payload}
            sid = db.add_signal(s)
            if sid is None:
                continue
            out.append(sid)
            self._log(f"SIGNAL #{sid}: {e['direction'].upper()} {w['symbol']} {w['tf']} — {w['name']}")
            if notify.configured():
                try:
                    notify.send(notify.format_signal(s))
                except Exception as ex:
                    self._log(f"telegram failed: {ex}")
        return out

    def _make_strategy_signal(self, w: dict, e: dict) -> int | None:
        stats = w.get("stats") or {}
        r = strat.parse(w["plan"])
        spec = {"plan": w["plan"], "manage": "none", "atr": e["atr"], "direction": e["direction"],
                "stop": {"mode": "fixed", "level": e["stop"]}, "target": {"mode": "fixed", "level": e["target"]}}
        payload = {
            "name": w["name"], "strategy": "CRT", "conditions": w["conditions"],
            "condition_labels": [f"CRT {r['htf']} {'swept high' if e['direction'] == 'short' else 'swept low'}"]
                                + [strat.FILTER_TEXT[f] for f in r["filters"]],
            "target_r": round(e["rr"], 2), "stop_atr": None, "atr": e["atr"],
            "stop_text": "beyond the sweep wick", "target_text": f"other side of the range · {e['rr']:.1f}R",
            "range": e["range"], "sweep": e["sweep"],
            "spec": spec, "plan": w["plan"], "plan_text": crt.describe(r["htf"], "close"),
            "liquidity_targets": [], "context": [],
            "stats": stats.get("stats"), "evidence": stats.get("evidence"), "decay": stats.get("decay"),
            "stats_as_of": stats.get("as_of"), "similar": None,
            "unproven": True,
        }
        s = {"watch_id": w["id"], "symbol": w["symbol"], "tf": r["htf"], "direction": e["direction"],
             "bar_time": e["bar_time"], "entry": e["entry"], "stop": e["stop"], "target": e["target"], "payload": payload}
        sid = db.add_signal(s)
        if sid is None:
            return None
        self._log(f"SIGNAL #{sid}: {e['direction'].upper()} {w['symbol']} {r['htf']} — {w['name']}")
        if notify.configured():
            try:
                notify.send(notify.format_signal(s))
            except Exception as ex:
                self._log(f"telegram failed: {ex}")
        return sid

    def scan_all(self) -> int:
        n = 0
        pairs = {(w["symbol"], w["tf"]) for w in db.list_watches(active_only=True)}
        for symbol, tf in pairs:
            n += len(self.scan(symbol, tf))
        return n

    def _make_signal(self, w: dict, df: pd.DataFrame, rec, last: int, bar_time: int) -> int | None:
        row = df.iloc[last]
        atr = float(row["atr"])
        entry = float(row["close"])  # the trade is taken at the next candle's open, ~this price
        sgn = 1 if w["direction"] == "long" else -1
        plan = Plan.from_key(w.get("plan") or Plan("next", "atr1" if w["stop_atr"] == 1 else "atr1.5" if w["stop_atr"] == 1.5 else "atr1",
                                                    f"r{w['target_r']:g}", "none").key)
        spec = signal_spec(plan, df, last, w["direction"])
        stop, target = levels_for_entry(spec, entry)
        risk = abs(entry - stop)

        liq = _liquidity(df, rec, last)
        side = liq["above"] if sgn > 0 else liq["below"]
        liq_targets = [{"price": t["price"], "kind": t["kind"], "rr": abs(t["price"] - entry) / risk}
                       for t in side if (t["price"] - entry) * sgn > 0][:3]

        context = []
        tail = df.iloc[-600:]
        for c in available(tail):
            try:
                if bool(pd.Series(c.fn(tail)).fillna(False).iloc[-1]):
                    context.append({"id": c.id, "label": c.label, "category": c.category, "bias": c.bias})
            except KeyError:
                pass

        stats = w.get("stats") or {}
        try:
            similar = self.similar_matches(w, {c["id"] for c in context})
        except Exception as e:
            similar = None
            self._log(f"similarity failed for watch #{w['id']}: {e}")

        payload = {
            "name": w["name"], "conditions": w["conditions"],
            "condition_labels": [label_of(c) for c in w["conditions"]],
            "target_r": w["target_r"], "stop_atr": w["stop_atr"], "atr": atr,
            "spec": spec, "plan": plan.key, "plan_text": plan.describe(),
            "liquidity_targets": liq_targets, "context": context,
            "stats": stats.get("stats"), "evidence": stats.get("evidence"), "decay": stats.get("decay"),
            "stats_as_of": stats.get("as_of"), "similar": similar,
        }
        s = {"watch_id": w["id"], "symbol": w["symbol"], "tf": w["tf"], "direction": w["direction"],
             "bar_time": bar_time, "entry": entry, "stop": stop,
             "target": target if target is not None else entry + sgn * 10 * risk, "payload": payload}
        sid = db.add_signal(s)
        if sid is None:
            return None
        self._log(f"SIGNAL #{sid}: {w['direction'].upper()} {w['symbol']} {w['tf']} â€” {w['name']}")
        if notify.configured():
            try:
                notify.send(notify.format_signal(s))
            except Exception as e:
                self._log(f"telegram failed: {e}")
        return sid

    def similar_matches(self, w: dict, current: set[str]) -> dict | None:
        """Past occurrences of this setup whose wider context most resembles the current one."""
        symbol, tf = w["symbol"], w["tf"]
        df, _ = engine.get_features(symbol, tf)
        ids, mat = condition_matrix(symbol, tf, df)
        out = get_outcomes((symbol, tf), df, DEFAULT_HORIZON[tf], w["stop_atr"], point_size(symbol))
        side = "L" if w["direction"] == "long" else "S"
        r = (out[f"{side}_r{w['target_r']:g}"] - out["cost_r"].fillna(0)).to_numpy()
        mask = evaluate(df, w["conditions"]) & np.isfinite(r)
        mask[:WARMUP] = False
        ev = dedupe(np.flatnonzero(mask), w["cooldown"])
        if len(ev) < 10:
            return None
        used = {c.lstrip("!") for c in w["conditions"]}
        ctx = [j for j, cid in enumerate(ids) if cid not in used]
        cur = np.array([ids[j] in current for j in ctx])
        A = mat[ev][:, ctx]
        inter = (A & cur).sum(axis=1)
        union = (A | cur).sum(axis=1)
        sim = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
        k = min(SIMILAR_K, max(5, len(ev) // 3))
        top = np.argsort(-sim, kind="stable")[:k]
        rt = r[ev[top]]
        times = df["time"].to_numpy()
        return {
            "k": int(k), "of": int(len(ev)),
            "win_rate": float((rt > 0).mean()), "avg_r": float(rt.mean()),
            "all_avg_r": float(r[ev].mean()), "mean_similarity": float(sim[top].mean()),
            "examples": [{"time": _unix(times[ev[j]]), "r": float(r[ev[j]]), "similarity": float(sim[j])} for j in top[:6]],
        }

    # ------------------------------------------------------------------ follow-up
    def resolve_open(self) -> None:
        for s in db.open_signals():
            try:
                self._resolve(s)
            except Exception as e:
                self._log(f"resolve #{s['id']} failed: {e}")

    def _resolve(self, s: dict) -> None:
        bars = store.load_bars(s["symbol"], s["tf"])
        t = bars["time"].dt.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64")
        i = int(np.searchsorted(t, s["bar_time"]))
        if i >= len(t) or t[i] != s["bar_time"]:
            return
        H = DEFAULT_HORIZON[s["tf"]]
        after = bars.iloc[i + 1: i + 1 + H]
        if after.empty:
            return
        p = s["payload"]
        entry = float(after["open"].iloc[0])
        spec = p.get("spec") or {  # signals created before execution plans existed
            "plan": BASELINE.key, "manage": "none", "atr": p["atr"], "direction": s["direction"],
            "stop": {"mode": "atr", "k": p["stop_atr"]}, "target": {"mode": "r", "k": p["target_r"]}}
        stop, target = levels_for_entry(spec, entry)
        if (entry - stop) * (1 if s["direction"] == "long" else -1) <= 0:  # opened beyond the stop: never filled
            db.close_signal(s["id"], "timeout", 0.0, _unix(after["time"].iloc[0]))
            return
        cost = float(spread_price(bars.iloc[i: i + 1], point_size(s["symbol"]))[0])
        h, l, c = (after[k].to_numpy(float) for k in ("high", "low", "close"))
        r, held, reason = simulate(h, l, c, s["direction"], entry, stop, target, spec["manage"], spec["atr"], cost)
        if not np.isfinite(r):
            return
        if reason == "time" and len(after) < H:
            return  # still running
        status = "win" if r > 0 else ("timeout" if reason == "time" else "loss")
        db.close_signal(s["id"], status, float(r), _unix(after["time"].iloc[held - 1]))

    def _refresh_one_stale_watch(self) -> None:
        now = time.time()
        for w in db.list_watches(active_only=True):
            if (w.get("stats_updated") or 0) < now - STATS_REFRESH_SECONDS:
                try:
                    db.update_watch(w["id"], stats=snapshot_stats(w), stats_updated=int(now))
                    self._log(f"refreshed statistics for watch #{w['id']}")
                except Exception as e:
                    db.update_watch(w["id"], stats_updated=int(now))
                    self._log(f"stats refresh failed for watch #{w['id']}: {e}")
                return  # at most one per tick: keeps the loop responsive

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "enabled": self.enabled, "mt5_ok": self.mt5_ok, "mt5_error": self.mt5_error,
            "last_tick": self.last_tick, "refreshing": engine.is_refreshing(),
            "last_bars": [{"symbol": s, "tf": tf, "time": t} for (s, tf), t in sorted(self.last_bar.items())],
            "telegram": notify.configured(), "log": list(self.log)[:50],
        }


service = LiveService()
