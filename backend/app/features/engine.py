"""Builds the causal feature table for a symbol/timeframe, including
higher-timeframe context.

Two modes:
  * get_features(): full history, cached on disk (parquet) and in a small
    in-memory LRU. When new candles arrive the previous table keeps being
    served while a background thread rebuilds it, so requests never stall.
  * live_features(): only the most recent bars, recomputed in well under a
    second. Every feature is causal and has short memory, so values on the
    latest bars are identical to the full-history computation (verified in
    tests/test_live.py). The live scanner uses this on every candle close.
"""
from __future__ import annotations

import pickle
import threading
from collections import OrderedDict

import pandas as pd

from ..config import FEATURE_VERSION, FEATURES_DIR, HTF_CONTEXT, TIMEFRAMES
from ..data.store import atomic_write, bars_path, load_bars
from .indicators import add_indicators
from .sdsr import add_sdsr
from .sessions import add_sessions
from .structure import Records, add_structure

SWING_K = {"M5": 5, "M15": 4, "H1": 3, "H4": 3, "D1": 2}
HTF_COLS = ["ms_trend", "ema_trend", "pd_pos", "vol_regime", "rsi", "in_bull_fvg", "in_bear_fvg",
            "bos_bull", "bos_bear", "choch_bull", "choch_bear"]
HTF_BOOL = {"in_bull_fvg", "in_bear_fvg", "bos_bull", "bos_bear", "choch_bull", "choch_bear"}
LIVE_TAIL = 6000      # bars of the analysed timeframe used for live detection
LIVE_TAIL_HTF = 3000  # bars of each context timeframe
MEM_SLOTS = 4         # full feature tables kept in RAM (large M5 tables are ~250 MB)

_mem: "OrderedDict[tuple, tuple[float, pd.DataFrame, Records]]" = OrderedDict()
_lock = threading.Lock()
_refreshing: set[tuple] = set()


# ------------------------------------------------------------------ core
def compute_features(bars: pd.DataFrame, tf: str, htf_frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, Records]:
    """bars: OHLC of `tf`. htf_frames: already-computed feature tables of context timeframes."""
    df = add_indicators(bars.reset_index(drop=True))
    df = add_sessions(df, TIMEFRAMES[tf])
    df, rec = add_structure(df, SWING_K[tf])
    df = add_sdsr(df, rec.swings)
    for htf, hdf in htf_frames.items():
        df = merge_htf(df, tf, hdf, htf)
    return df, rec


def merge_htf(df: pd.DataFrame, tf: str, hdf: pd.DataFrame, htf: str) -> pd.DataFrame:
    """Attach higher-timeframe state *as it was known* when each lower-TF bar closed."""
    p = htf.lower() + "_"
    h = hdf[["time"] + HTF_COLS].copy()
    h["avail"] = h["time"] + pd.Timedelta(minutes=TIMEFRAMES[htf])
    h = h.drop(columns="time").rename(columns={c: p + c for c in HTF_COLS}).sort_values("avail")
    left = df.assign(_close=df["time"] + pd.Timedelta(minutes=TIMEFRAMES[tf]))
    merged = pd.merge_asof(left.sort_values("_close"), h, left_on="_close", right_on="avail", direction="backward")
    merged = merged.drop(columns=["_close", "avail"]).sort_values("time").reset_index(drop=True)
    for c in HTF_BOOL:  # NaN before the first HTF bar -> False, keeps the column boolean (8x less memory)
        merged[p + c] = merged[p + c].astype("boolean").fillna(False).astype(bool)
    merged[p + "ms_trend"] = merged[p + "ms_trend"].fillna(0).astype("int8")
    merged[p + "ema_trend"] = merged[p + "ema_trend"].fillna(0).astype("int8")
    merged[p + "vol_regime"] = merged[p + "vol_regime"].fillna(1).astype("int8")
    return merged


def _compute(symbol: str, tf: str) -> tuple[pd.DataFrame, Records]:
    htf = {}
    for h in HTF_CONTEXT[tf]:
        if bars_path(symbol, h).exists():
            htf[h], _ = get_features(symbol, h, wait=True)
    return compute_features(load_bars(symbol, tf), tf, htf)


# ------------------------------------------------------------------ full-history cache
def _paths(symbol: str, tf: str):
    base = FEATURES_DIR / f"{symbol}_{tf}_v{FEATURE_VERSION}"
    return base.with_suffix(".parquet"), base.with_suffix(".rec.pkl")


def _remember(key, mtime, df, rec):
    with _lock:
        _mem[key] = (mtime, df, rec)
        _mem.move_to_end(key)
        while len(_mem) > MEM_SLOTS:
            _mem.popitem(last=False)


def _build_and_store(symbol: str, tf: str, mtime: float) -> tuple[pd.DataFrame, Records]:
    df, rec = _compute(symbol, tf)
    fp, rp = _paths(symbol, tf)
    # temp file + swap: a concurrent reader never sees a half-written cache
    atomic_write(fp, lambda p: df.to_parquet(p, index=False))
    def write_rec(p):
        with open(p, "wb") as f:
            pickle.dump({"mtime": mtime, "rec": rec}, f)
    atomic_write(rp, write_rec)
    _remember((symbol, tf), mtime, df, rec)
    return df, rec


def _refresh_async(symbol: str, tf: str, mtime: float) -> None:
    key = (symbol, tf)
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def run():
        try:
            _build_and_store(symbol, tf, mtime)
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True, name=f"refresh-{symbol}-{tf}").start()


def get_features(symbol: str, tf: str, wait: bool = False) -> tuple[pd.DataFrame, Records]:
    """Full-history features. Serves the last built table immediately and refreshes
    in the background when the bars have changed (wait=True rebuilds synchronously)."""
    mtime = bars_path(symbol, tf).stat().st_mtime
    key = (symbol, tf)
    with _lock:
        hit = _mem.get(key)
        if hit:
            _mem.move_to_end(key)
    if hit and hit[0] == mtime:
        return hit[1], hit[2]
    if hit and not wait:
        _refresh_async(symbol, tf, mtime)
        return hit[1], hit[2]

    fp, rp = _paths(symbol, tf)
    if fp.exists() and rp.exists():
        try:
            with open(rp, "rb") as f:
                saved = pickle.load(f)
            df = pd.read_parquet(fp) if saved["mtime"] == mtime or not wait else None
        except Exception:   # damaged cache (e.g. an interrupted write): rebuild it
            df = saved = None
        if df is not None:
            _remember(key, saved["mtime"], df, saved["rec"])
            if saved["mtime"] != mtime:
                _refresh_async(symbol, tf, mtime)
            return df, saved["rec"]
    return _build_and_store(symbol, tf, mtime)


def is_refreshing(symbol: str | None = None) -> bool:
    with _lock:
        return any(symbol is None or k[0] == symbol for k in _refreshing)


def invalidate(symbol: str | None = None) -> None:
    """Drop in-memory tables (disk caches are re-validated by file modification time)."""
    with _lock:
        for k in list(_mem):
            if symbol is None or k[0] == symbol:
                del _mem[k]


# ------------------------------------------------------------------ live (recent bars only)
def live_features(symbol: str, tf: str, tail: int = LIVE_TAIL) -> tuple[pd.DataFrame, Records]:
    bars = load_bars(symbol, tf).iloc[-tail:]
    htf = {}
    for h in HTF_CONTEXT[tf]:
        if bars_path(symbol, h).exists():
            hb = load_bars(symbol, h).iloc[-LIVE_TAIL_HTF:]
            htf[h], _ = compute_features(hb, h, {})
    return compute_features(bars, tf, htf)
