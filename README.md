# Market Lab

A local research platform for discovering, testing and monitoring statistical edges in price action
(XAUUSD, BTCUSD, EURUSD, GBPUSD, USDJPY, US100), using history from a MetaTrader 5 terminal.

It is a research tool, not a signal service: every result is reported with in-sample vs out-of-sample
evidence, and grades measure the strength of historical evidence — not the probability of a win.

## Features

- **Chart explorer** — TradingView Lightweight Charts with detected structure (BOS / CHoCH / MSS), FVGs,
  order blocks, liquidity sweeps, equal highs/lows, session levels, key trade zones, live forming candle,
  and bar-by-bar replay that hides the future.
- **Research lab** — test any combination of 120+ conditions; win rate with confidence bounds, expectancy
  vs random entry, out-of-sample split, decay check, breakdowns, and every historical example.
- **Pattern discovery** — combination search, decision-tree rules and clustering on the oldest 70% of history,
  confirmed on the most recent 30% with a multiple-testing correction.
- **Execution study** — ~70 entry/exit plans (stops, targets, break-even, partials, trailing, lower-timeframe
  entries) compared across many setups on unseen data; only consistently robust plans are recommended.
- **Live signals** — watched setups are checked on every closed candle; alerts in-app, on the desktop and
  optionally via Telegram, with the evidence attached and an automatic live track record.
- **Rare events** — unusual conditions or statistical extremes on the latest candle, with what followed before.

## Safeguards

- All features are causal (tested: removing future bars never changes past values).
- Stop and target in the same candle count as a stop-out; spreads are deducted.
- In-sample trades that would still be open at the split are purged.
- Plan/pattern selection uses in-sample data only (tested by scrambling the out-of-sample prices).

## Setup (Windows)

Requirements: Python 3.12, Node 20+, MetaTrader 5 logged in to your broker (with *Max bars in chart* = Unlimited).

```bat
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
cd frontend && npm install && npm run build && cd ..
start.bat
```

Then open http://127.0.0.1:8777 → **Data** → *Connect to MT5* → *Download all*.

Data, caches and settings are stored in `%LOCALAPPDATA%\MarketLab` (override with `MARKETLAB_DATA`).
No broker credentials are stored by this project.

## Tests

```bat
cd backend
..\.venv\Scripts\python -m pytest tests
```

## Layout

```
backend/app/data        MT5 download, UTC conversion, Parquet storage
backend/app/features    causal detectors and higher-timeframe context
backend/app/research    conditions, outcome matrix, statistics, hypothesis tests, zones
backend/app/discovery   pattern search, rare events, background runner
backend/app/execution   trade simulator, entry/exit optimiser, pooled study, portfolio, sizing
backend/app/live        live feed, scanner, signal tracking, Telegram
frontend/src            React + TypeScript UI
```

*Historical evidence, not guarantees. Trading involves substantial risk.*
