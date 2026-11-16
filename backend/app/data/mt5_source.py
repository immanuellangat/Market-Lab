"""MetaTrader 5 data source.

Connects to the locally installed MT5 terminal (the user stays logged in there;
no credentials are stored in this project) and downloads OHLC history.

MT5 returns bar times in *broker server time*. Brokers run that clock by one of two
rules: Eastern European time (UTC+2, UTC+3 during European summer time - JustMarkets does
this) or New York time + 7h. Both put the daily close at 17:00 New York for most of the
year, but they differ by one hour in the 3-4 weeks a year between the European and the US
daylight-saving changes. Which rule applies is read from the price history itself (the hour
at which the FX week opens in those weeks), never from a single tick: a tick from a closed
market is hours old and looks exactly like a different clock offset. Everything is stored in UTC.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from ..config import SYMBOLS, TIMEFRAMES

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - platform without MT5
    mt5 = None


class MT5Error(RuntimeError):
    pass


_TF_CONST = {}
if mt5 is not None:
    _TF_CONST = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

_ZONE = {"eet": "Europe/Athens", "ny+7": "America/New_York"}
_SHIFT_H = {"eet": 0, "ny+7": 7}


def to_utc(epoch_seconds: np.ndarray, mode: str, fixed_offset_h: int = 0) -> pd.DatetimeIndex:
    """Broker server times (seconds, as MT5 reports them) -> UTC."""
    naive = pd.DatetimeIndex(pd.to_datetime(np.asarray(epoch_seconds), unit="s"))
    if mode == "fixed":
        return (naive - pd.Timedelta(hours=fixed_offset_h)).tz_localize("UTC")
    local = naive - pd.Timedelta(hours=_SHIFT_H[mode])
    return local.tz_localize(_ZONE[mode], ambiguous=np.zeros(len(local), dtype=bool),
                             nonexistent="shift_forward").tz_convert("UTC")


def from_utc(times: pd.DatetimeIndex, mode: str) -> np.ndarray:
    """UTC -> broker server times in seconds (inverse of to_utc for the two clock rules)."""
    local = pd.DatetimeIndex(times).tz_convert(_ZONE[mode]).tz_localize(None) + pd.Timedelta(hours=_SHIFT_H[mode])
    return local.to_numpy().astype("datetime64[s]").astype("int64")


def clock_rule_from_history(h1_server_times: np.ndarray) -> str | None:
    """"eet" or "ny+7", decided by the server-time hour at which the FX week opens (Sunday 17:00
    New York) in the weeks where the two rules disagree. None when the history cannot tell."""
    t = pd.Series(pd.to_datetime(np.asarray(h1_server_times), unit="s"))
    opens = t[t.diff() > pd.Timedelta(hours=30)]
    votes = {"eet": 0, "ny+7": 0}
    for o in opens:
        sunday = (o - pd.Timedelta(hours=12)).normalize()
        if sunday.dayofweek != 6:
            continue  # holiday / late open
        instant = (sunday + pd.Timedelta(hours=17)).tz_localize("America/New_York")
        eet = instant.tz_convert("Europe/Athens").tz_localize(None)
        ny7 = instant.tz_localize(None) + pd.Timedelta(hours=7)
        if eet == ny7:
            continue
        if o == eet:
            votes["eet"] += 1
        elif o == ny7:
            votes["ny+7"] += 1
    best, other = sorted(votes, key=votes.get, reverse=True)
    if votes[best] >= 3 and votes[best] >= 2 * votes[other]:
        return best
    return None


class MT5Source:
    def __init__(self) -> None:
        self.connected = False
        self.time_mode: str = "eet"  # "eet", "ny+7" or "fixed" (see module docstring)
        self.fixed_offset_h: int = 0
        self._mode_known = False  # decided once per process; reconnects must not change it
        self._symbol_map: dict[str, str] = {}
        # The MT5 Python API is not thread-safe: the live feed and manual syncs share this lock.
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ connect
    def connect(self) -> dict:
        with self.lock:
            return self._connect()

    def _connect(self) -> dict:
        if mt5 is None:
            raise MT5Error("MetaTrader5 Python package is not installed.")
        if not mt5.initialize():
            code, msg = mt5.last_error()
            raise MT5Error(
                f"Could not connect to the MT5 terminal ({code}: {msg}). "
                "Open MetaTrader 5, log in to your broker account and tick 'Save password'."
            )
        self.connected = True
        self._detect_time_mode()
        acct = mt5.account_info()
        term = mt5.terminal_info()
        return {
            "connected": True,
            "server": acct.server if acct else None,
            "company": term.company if term else None,
            "time_mode": self.time_mode,
            "fixed_offset_h": self.fixed_offset_h,
            "symbols": self.resolve_all(),
        }

    def shutdown(self) -> None:
        if mt5 is not None and self.connected:
            mt5.shutdown()
        self.connected = False

    def _detect_time_mode(self) -> None:
        """Decide the server clock rule once per process (see module docstring)."""
        if self._mode_known:
            return
        for canon in ("EURUSD", "GBPUSD", "USDJPY"):
            sym = self.resolve_symbol(canon) if canon in SYMBOLS else None
            rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 20000) if sym else None
            rule = clock_rule_from_history(rates["time"]) if rates is not None and len(rates) else None
            if rule:
                self.time_mode, self._mode_known = rule, True
                return
        # No usable FX history (new terminal): fall back to the live ticks. Several markets must show
        # the same whole-hour offset to within a minute - a stale tick from a closed market does not.
        now = time.time()
        offsets = []
        for canon in SYMBOLS:
            sym = self.resolve_symbol(canon)
            tick = mt5.symbol_info_tick(sym) if sym else None
            if tick is None or tick.time == 0:
                continue
            diff_h = (tick.time - now) / 3600.0
            if abs(diff_h - round(diff_h)) <= 0.02:
                offsets.append(round(diff_h))
        offset = max(set(offsets), key=offsets.count) if offsets else None
        if offset is None or offsets.count(offset) < 2:
            return  # markets closed: keep the current rule and try again on the next connect
        stamp = pd.Timestamp.now(tz="UTC")
        fits = [m for m in ("eet", "ny+7")
                if int(stamp.tz_convert(_ZONE[m]).utcoffset().total_seconds() // 3600) + _SHIFT_H[m] == offset]
        if not fits:
            self.time_mode, self.fixed_offset_h = "fixed", offset
        elif self.time_mode not in fits:
            self.time_mode = fits[0]
        self._mode_known = len(fits) != 2  # both fit most of the year: not decided yet

    # ------------------------------------------------------------------ symbols
    def resolve_symbol(self, canonical: str) -> str | None:
        if canonical in self._symbol_map:
            return self._symbol_map[canonical]
        aliases = SYMBOLS[canonical]["aliases"]
        all_syms = mt5.symbols_get() or []
        names = [s.name for s in all_syms]
        found = None
        # exact match first, then names starting with an alias (suffixes like .m / -ECN)
        for a in aliases:
            if a in names:
                found = a
                break
        if not found:
            for a in aliases:
                cands = sorted(n for n in names if n.upper().startswith(a))
                if cands:
                    found = min(cands, key=len)
                    break
        if found:
            mt5.symbol_select(found, True)
            self._symbol_map[canonical] = found
        return found

    def resolve_all(self) -> dict[str, str | None]:
        return {c: self.resolve_symbol(c) for c in SYMBOLS}

    # ------------------------------------------------------------------ bars
    def server_to_utc(self, epoch_seconds: np.ndarray) -> pd.DatetimeIndex:
        return to_utc(epoch_seconds, self.time_mode, self.fixed_offset_h)

    def fetch(self, canonical: str, tf: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        with self.lock:
            return self._fetch(canonical, tf, start, end)

    def alive(self) -> bool:
        """True when the terminal is reachable and logged in."""
        with self.lock:
            if mt5 is None or not self.connected:
                return False
            ti = mt5.terminal_info()
            return bool(ti and ti.connected)

    def _fetch(self, canonical: str, tf: str, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        if not self.connected:
            self._connect()
        sym = self.resolve_symbol(canonical)
        if not sym:
            raise MT5Error(f"Symbol {canonical} not found at this broker.")
        # MT5 compares request bounds with bar times in *broker server time* (UTC+2/+3 here),
        # so extend the end bound past "now"; the still-forming candle is filtered out below.
        end = (end or datetime.now(timezone.utc)) + timedelta(days=1)
        frames = []
        # Chunk the request so large M5 histories don't exceed terminal limits.
        # ~50k bars per request (M5 ~ 6 months, D1 capped at 10 years)
        chunk_days = min(3650, max(30, int(50000 * TIMEFRAMES[tf] / 1440)))
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=chunk_days), end)
            # MT5 pulls older history from the broker in the background, so the
            # first request for an old range can come back empty or partial.
            # Retry a few times while the terminal catches up.
            expected = (nxt - cur).total_seconds() / 60 / TIMEFRAMES[tf] * 5 / 7
            rates = None
            for attempt in range(4):
                rates = mt5.copy_rates_range(sym, _TF_CONST[tf], cur, nxt)
                if rates is not None and len(rates) >= 0.2 * expected:
                    break
                time.sleep(2)
            if rates is not None and len(rates):
                frames.append(pd.DataFrame(rates))
            cur = nxt
        if not frames:
            code, msg = mt5.last_error()
            raise MT5Error(f"No data returned for {sym} {tf} ({code}: {msg}).")
        raw = pd.concat(frames, ignore_index=True).drop_duplicates("time").sort_values("time")
        df = pd.DataFrame(
            {
                "time": self.server_to_utc(raw["time"].to_numpy()),
                "open": raw["open"].to_numpy(float),
                "high": raw["high"].to_numpy(float),
                "low": raw["low"].to_numpy(float),
                "close": raw["close"].to_numpy(float),
                "volume": raw["tick_volume"].to_numpy(float),
                "spread": raw["spread"].to_numpy(float),
            }
        )
        # Drop the still-forming candle: research and live detection only use closed bars.
        closed = df["time"] + pd.Timedelta(minutes=TIMEFRAMES[tf]) <= pd.Timestamp.now(tz="UTC")
        df = df[closed]
        return df.drop_duplicates("time").sort_values("time").reset_index(drop=True)

    def forming_bar(self, canonical: str, tf: str) -> dict | None:
        """The candle currently forming (display only - never used for analysis)."""
        with self.lock:
            if not self.connected:
                self._connect()
            sym = self.resolve_symbol(canonical)
            rates = mt5.copy_rates_from_pos(sym, _TF_CONST[tf], 0, 1) if sym else None
            tick = mt5.symbol_info_tick(sym) if sym else None
        if rates is None or not len(rates):
            return None
        r = rates[-1]
        t = int(self.server_to_utc(np.array([r["time"]]))[0].timestamp())
        return {"time": t, "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                "close": float(r["close"]), "bid": float(tick.bid) if tick else None,
                "closes_at": t + TIMEFRAMES[tf] * 60}

    def account(self) -> dict | None:
        """Balance / equity / currency of the logged-in account (read-only)."""
        with self.lock:
            if not self.connected:
                self._connect()
            a = mt5.account_info()
        if a is None:
            return None
        return {"balance": float(a.balance), "equity": float(a.equity), "currency": a.currency, "leverage": int(a.leverage)}

    def contract(self, canonical: str) -> dict | None:
        """Contract specification needed to convert a stop distance into a lot size."""
        with self.lock:
            if not self.connected:
                self._connect()
            sym = self.resolve_symbol(canonical)
            i = mt5.symbol_info(sym) if sym else None
        if i is None:
            return None
        return {"tick_value": float(i.trade_tick_value), "tick_size": float(i.trade_tick_size),
                "vol_min": float(i.volume_min), "vol_step": float(i.volume_step), "vol_max": float(i.volume_max)}

    def symbol_info(self, canonical: str) -> dict:
        with self.lock:
            sym = self.resolve_symbol(canonical)
            info = mt5.symbol_info(sym) if sym else None
        if info is None:
            return {}
        return {"broker_symbol": sym, "digits": info.digits, "point": info.point}


source = MT5Source()
