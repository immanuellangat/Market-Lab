"""Global configuration for the research platform."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Data lives outside the (OneDrive-synced) project folder: it is large and machine-specific.
_DEFAULT_DATA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MarketLab" / "data"
DATA_DIR = Path(os.environ.get("MARKETLAB_DATA", _DEFAULT_DATA))
BARS_DIR = DATA_DIR / "bars"
FEATURES_DIR = DATA_DIR / "features"
for _d in (BARS_DIR, FEATURES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Canonical instrument names. The broker may use suffixed names (e.g. "EURUSD.m"
# or "NAS100" for US100); data/mt5_source.resolve_symbol maps them automatically.
SYMBOLS: dict[str, dict] = {
    "XAUUSD": {"label": "Gold", "aliases": ["XAUUSD", "GOLD"]},
    "BTCUSD": {"label": "Bitcoin", "aliases": ["BTCUSD", "BTCUSDT"]},
    "EURUSD": {"label": "Euro / US Dollar", "aliases": ["EURUSD"]},
    "GBPUSD": {"label": "British Pound / US Dollar", "aliases": ["GBPUSD"]},
    "USDJPY": {"label": "US Dollar / Japanese Yen", "aliases": ["USDJPY"]},
    "US100": {"label": "Nasdaq 100", "aliases": ["US100", "USTEC", "NAS100", "NDX100", "NQ100", "USTECH"]},
}

# Timeframe -> minutes
TIMEFRAMES: dict[str, int] = {
    "M5": 5,
    "M15": 15,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}

# Higher timeframes used as context when analysing a given timeframe.
HTF_CONTEXT: dict[str, list[str]] = {
    "M5": ["H1", "H4"],
    "M15": ["H1", "H4"],
    "H1": ["H4", "D1"],
    "H4": ["D1"],
    "D1": [],
}

# Default history start when downloading.
DEFAULT_HISTORY_START = "2015-01-01"

# Outcome-matrix defaults: forward horizon in bars per timeframe.
DEFAULT_HORIZON: dict[str, int] = {"M5": 72, "M15": 64, "H1": 48, "H4": 30, "D1": 20}
DEFAULT_STOP_ATR = 1.0
TARGETS_R = (1.0, 2.0, 3.0)

# Fraction of history (most recent) held back as out-of-sample.
OOS_FRACTION = 0.30

# Bump to invalidate cached feature files after detector changes.
FEATURE_VERSION = 3
