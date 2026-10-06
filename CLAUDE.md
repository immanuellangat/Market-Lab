# Market Lab — notes for Claude

Local price-action research platform (see README.md for features and layout).

## Environment
- Project lives in `C:\Projects\Market Lab` (moved out of OneDrive on 2026-10-06; the old
  `OneDrive\Desktop\work\ANALYSIS TOOL` copy is obsolete and may be deleted).
- Python venv: `.venv\Scripts\python.exe` (run backend commands from `backend\`).
  Tests: `..\.venv\Scripts\python -m pytest tests` (15 tests, all passing).
- Frontend: `frontend\` (Vite + React + TS + lightweight-charts). Build: `npm run build`; the backend serves `frontend\dist`.
- Server: port **8777** (`start.bat`; ports 8000/8765 are taken by the user's own `python -m http.server`).
  Restart = kill the process listening on 8777, then run `start.bat` or `.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8777` from `backend\`.
- Data/caches/SQLite (signals, watchlist, patterns, Telegram token) live in `%LOCALAPPDATA%\MarketLab\data` — NOT in the repo.
- Broker: JustMarkets via MetaTrader 5 (symbols `EURUSD.m`, `GBPUSD.m`, `USDJPY.m`, `XAUUSD.m`, `BTCUSD.m`, `US100.std`).
  Server clock = New York + 7h; MT5 request bounds are in server time (fetch extends end by +1 day).
- Shortcuts: Desktop / Start menu / Startup "Market Lab.lnk" → `start.bat` (opens Chrome `--app` window); MT5 also in Startup.
- GitHub: https://github.com/immanuellangat/Market-Lab (HTTPS remote; commit as `immanuellangat <immanuellangat9557@gmail.com>`).
  Only commit/push when the user asks. Everything up to commit fee4ba3 (2026-10-07) is pushed.

## Working principles the user has accepted
- Honest statistics: in-sample vs out-of-sample split (last 30% unseen), purged split, costs included,
  stop-before-target on the same candle, BH multiple-testing correction in discovery. Grades = strength of evidence, not win probability.
- Win rate is not the goal; expectancy per trade on unseen data is.
- Findings so far: lower-timeframe confirmation entries hurt; partials raise win rate but cut expectancy;
  robust exit improvements (pooled study over 228 standard setups): 1.5×ATR stop + trailing / 3R target (+~0.04R).

## Watchlist (live)
#1 EURUSD H1 long sweep+FVG retest (baseline exit) · #6 BTCUSD H1 long (1.5ATR/3R) · #7 XAUUSD H4 long (1.5ATR/3R) · #8 USDJPY H1 short (baseline).
#9 EURUSD H4 + #10 US100 H4: CRT "close|full" + d1_bias + pd_zone + big_range, both directions — FORWARD TEST, flagged
"unproven" (grade C per market; chosen after seeing OOS, so not validated). Strategy watches: conditions=["strategy:crt"],
plan="crt|H4|close|full|<filters>", handled by `strategies/live.py` + `LiveService._scan_strategy` (fixed stop/target levels).
#11 EURUSD H1 + #12 XAUUSD H1: SMC "smc|H1|smc_any|r2|bias2,htf_pd,killzone" (sweep -> CHoCH/MSS -> FVG/OB retest,
D1 structure agrees, H4 discount/premium, killzone; struct stop, 2R) — FORWARD TEST, "unproven" (pooled OOS +0.08R, q=1).

## Open tasks
1. **Lower-timeframe daily strategy signals (user request, in progress — nothing built yet).**
   User wants frequent M5/M15 signals from several named strategies, each signal stating its strategy,
   all backtested and graded before going live (weak ones shown but flagged).
   - CRT — BUILT & TESTED 2026-10-06 (`backend/app/strategies/crt.py`, grading in `strategies/evaluate.py`,
     results stored as setting `strategy_crt`). Rules: HTF (H1/H4) candle A range; candle B sweeps one side and
     closes inside without touching the other; entry "close" (next HTF open) or "ltf" (M5/M15 close beyond last
     swing formed since B opened, within the next HTF candle); stop sweep wick + 0.1 ATR; target opposite side; min 1R.
     RESULT: 48 variants (6 symbols x H1/H4 x close/ltf x long/short) — none validated, all graded F/D.
     Gross edge ~0 (-0.09..+0.11R) everywhere; costs make it negative (worst on H1/BTC where stops are tight).
     LTF confirmation again did not help. Not wired to live/UI yet.
     REFINEMENT SEARCH 2026-10-07 (`strategies/crt_search.py`, setting `strategy_crt_search`): published CRT filters
     (key-time 1/5/9am NY H4 + killzones, D1 bias, premium/discount, key-level sweep, range>=1ATR) x entry
     (close/ltf/limit50 = limit at 50% of sweep candle) x target (full/mid) x H1/H4 = 384 rules pooled over 6 markets.
     0 validated. Best in-sample rules collapse out-of-sample (+0.44R -> -0.35R). Mid targets reach ~60% win rate
     but ~0R expectancy. Only mild survivor: H4 + d1_bias + pd_zone + big_range (OOS +0.03R close / +0.21R limit50,
     q=1.0 - not significant). Per market: EURUSD/US100 positive IS+OOS; GBPUSD/BTCUSD negative; XAU/JPY negative IS.
     Live since 2026-10-07 as watches #9/#10 (see Watchlist).
   - SMC + PA SEARCH 2026-10-07 (`strategies/ltf_search.py`, setting `strategy_ltf_search`): 3 SMC + 4 PA setups
     (pin/engulf at swept level, inside-bar breakout, PDH/PDL break-retest) x 32 filter combos x M5/M15/H1 x 2 exits
     = 1344 rules, ~1M trades. 0 validated. M5/M15: gross ~0, costs make every setup lose (-0.1..-0.6R) — LTF is dead
     for these concepts with this broker's spreads. Best = H1 SMC (live #11/#12). No PA rule held out-of-sample.
     User asked: SMC on H1 only. APA = plain price action.
   - Alchemist: rules unknown — ask the user to describe them; do NOT invent rules.
2. Portfolio simulator (`backend/app/execution/portfolio.py`, `sizing.py`) — engine written, not wired to API/UI yet;
   lot sizing on live signals not done. A corrupted feature-cache pickle broke one run: delete
   `%LOCALAPPDATA%\MarketLab\data\features\*` entries that fail to load (they rebuild).
3. Optional: show recently filled zones (last ~50 candles) by default on the chart.
