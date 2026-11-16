"""Parquet-backed bar storage (one file per symbol/timeframe, times in UTC)."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import pandas as pd

from ..config import BARS_DIR, DATA_DIR, DEFAULT_HISTORY_START, SYMBOLS, TIMEFRAMES

META_FILE = DATA_DIR / "meta.json"


def bars_path(symbol: str, tf: str):
    return BARS_DIR / f"{symbol}_{tf}.parquet"


def load_bars(symbol: str, tf: str) -> pd.DataFrame:
    p = bars_path(symbol, tf)
    if not p.exists():
        raise FileNotFoundError(f"No data for {symbol} {tf}. Sync it from the Data page first.")
    df = pd.read_parquet(p)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def atomic_write(path, write) -> None:
    """write(tmp_path) to a temp file next to `path`, then swap it in, so a reader (another thread or the
    server process) never sees a half-written file. Windows refuses the swap while a reader has the file
    open, so it is retried briefly."""
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    write(tmp)
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                os.remove(tmp)
                raise
            time.sleep(0.1)


def save_bars(symbol: str, tf: str, df: pd.DataFrame) -> None:
    atomic_write(bars_path(symbol, tf), lambda p: df.to_parquet(p, index=False))


def merge_bars(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new
    out = pd.concat([old, new], ignore_index=True)
    return out.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)


def trim_sparse(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the continuous, full-density part of the history.

    Brokers often serve placeholder history for older periods (e.g. one bar per
    day on H1) or leave multi-week holes. Any calendar month with fewer than half
    the typical number of bars marks a break; history before the last break is
    dropped so statistics are never computed on fake or missing data.
    """
    if len(df) < 100:
        return df
    t = pd.to_datetime(df["time"], utc=True)
    month = t.dt.tz_convert(None).dt.to_period("M")
    counts = month.value_counts().sort_index()
    full = pd.period_range(counts.index[0], counts.index[-1], freq="M")
    counts = counts.reindex(full, fill_value=0)
    body = counts.iloc[1:-1]  # first/last month may legitimately be partial
    if body.empty:
        return df
    typical = body.iloc[-24:].median()
    sparse = body[body < 0.5 * typical]
    if sparse.empty:
        return df
    cut = (sparse.index[-1] + 1).to_timestamp().tz_localize("UTC")
    return df[t >= cut].reset_index(drop=True)


def inventory() -> list[dict]:
    rows = []
    for s in SYMBOLS:
        for tf in TIMEFRAMES:
            p = bars_path(s, tf)
            if p.exists():
                t = pd.read_parquet(p, columns=["time"])["time"]
                rows.append({"symbol": s, "tf": tf, "bars": len(t),
                             "start": str(pd.to_datetime(t.iloc[0], utc=True)),
                             "end": str(pd.to_datetime(t.iloc[-1], utc=True))})
            else:
                rows.append({"symbol": s, "tf": tf, "bars": 0, "start": None, "end": None})
    return rows


def sync(source, symbol: str, tf: str, start: str | None = None) -> dict:
    """Download (incrementally) bars from the source and persist them."""
    p = bars_path(symbol, tf)
    old = load_bars(symbol, tf) if p.exists() else None
    if old is not None and not old.empty and start is None:
        since = old["time"].iloc[-1].to_pydatetime() - pd.Timedelta(days=3)
    else:
        since = pd.Timestamp(start or DEFAULT_HISTORY_START, tz="UTC").to_pydatetime()
    new = source.fetch(symbol, tf, since, datetime.now(timezone.utc))
    df = trim_sparse(merge_bars(old, new))
    save_bars(symbol, tf, df)
    _write_meta(symbol, source)
    return {"symbol": symbol, "tf": tf, "bars": len(df), "added": len(df) - (0 if old is None else len(old))}


def import_csv(symbol: str, tf: str, path: str, utc_offset_hours: float = 0.0) -> dict:
    """Fallback importer: CSV with time,open,high,low,close[,volume] columns."""
    raw = pd.read_csv(path)
    raw.columns = [c.strip().lower() for c in raw.columns]
    t = pd.to_datetime(raw["time"]) - pd.Timedelta(hours=utc_offset_hours)
    df = pd.DataFrame({
        "time": t.dt.tz_localize("UTC") if t.dt.tz is None else t.dt.tz_convert("UTC"),
        "open": raw["open"].astype(float), "high": raw["high"].astype(float),
        "low": raw["low"].astype(float), "close": raw["close"].astype(float),
        "volume": raw.get("volume", pd.Series(0.0, index=raw.index)).astype(float),
        "spread": 0.0,
    })
    p = bars_path(symbol, tf)
    df = merge_bars(load_bars(symbol, tf) if p.exists() else None, df)
    save_bars(symbol, tf, df)
    return {"symbol": symbol, "tf": tf, "bars": len(df)}


def _write_meta(symbol: str, source) -> None:
    meta = read_meta()
    try:
        meta.setdefault("symbols", {})[symbol] = source.symbol_info(symbol)
        meta["time_mode"] = source.time_mode
    except Exception:
        pass
    def write(p):
        with open(p, "w") as f:
            json.dump(meta, f, indent=2)
    atomic_write(META_FILE, write)


def read_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    return {}
