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
  Only commit/push when the user asks. Everything up to commit 7cd118f (2026-10-19, price-zone alerts) is pushed.

## Working principles the user has accepted
- Honest statistics: in-sample vs out-of-sample split (last 30% unseen), purged split, costs included,
  stop-before-target on the same candle, BH multiple-testing correction in discovery. Grades = strength of evidence, not win probability.
- Win rate is not the goal; expectancy per trade on unseen data is. Since 2026-10-18 the user explicitly wants a
  HIGHER WIN RATE too: raise it (via exits) only where unseen expectancy stays clearly positive, and say what it costs.
- Findings so far: lower-timeframe confirmation entries hurt; partials raise win rate but cut expectancy;
  robust exit improvements (pooled study over 228 standard setups): 1.5×ATR stop + trailing / 3R target (+~0.04R).
- WIN-RATE STUDY 2026-10-18 (`execution/winrate.py`, setting `winrate_study`; plans now accept any "atrK" stop / "rK" target):
  win rate is set mostly by the exit. Pooled 232 no-edge setups: 2R target 33% WR -> 0.5R target 66% WR, expectancy
  still <= 0 (high WR on a setup without edge is an illusion). Wider stops (2-3 ATR) cut cost drag in every pool.
  Extra confluence filters: 167 tests on the live watches, 0 raised win rate significantly (BH). #16 high-WR exit fails OOS.
  APPLIED 2026-10-18 (user approved): live exits are restricted to ATR stop {1,1.5,2,3} x target {1,2,3}R, no
  management, because stats/chart/similar-matches/live levels all represent exactly that (`winrate.LIVE_STOPS`,
  `exit_plan`/`plan_exit`; a watch's stop_atr/target_r fields now always match its plan, also on add).
  Same rule (best IS R with IS WR >= 55%, IS R >= +0.10) on that set: #6 & #8 -> 2ATR/1R, #7 -> 3ATR/1R.
  App win rate = target reached (stricter than "closed in profit"). Portfolio (live set, 1% risk): won 42% -> 55%
  (all) / 45% -> 56% (unseen); max DD 20.5% -> 10.2% (all), unseen ~9% both; R/trade unseen +0.38 -> +0.26;
  CAGR unseen 52% -> 34% at 1% risk, but 20%-DD-safe risk rises 1% -> 1.5% and median MC return is then equal.
  UI: Live page watch cards show the exit + past/unseen stats; "Change exit" chooser (`GET .../watchlist/{id}/exits`,
  `POST .../watchlist/{id}/exit`) with profiles "Higher win rate" / "Most profit per trade" + all 12 exits.
- HIGH-WIN-RATE DISCOVERY 2026-10-18: run #4 (2xATR stop, 1R target, 6 markets x H1/H4 x both directions):
  960 candidates -> 9 validated (all-history WR 57-75%, unseen +0.30..+0.51R; grades A x3, B x5, F x1), 383 promising.
  Pattern ids 2519 2563 2633 2779 2910 2963 3000 3102 3300. The 3 grade-A ones were added live (user approved) as
  watches #21-#23, named "(forward test)"; see Watchlist. Grade-B ones (2519 2633 2963 3000 3102) not added.
  `store.replace_patterns` now keys on target AND stop, so runs with different exits coexist (old 2R/1ATR library intact).
- Data files (bars, feature caches, meta.json) are written atomically (`store.atomic_write`): the live server used to
  overwrite bar files in place, which corrupted caches read at the same moment.

## Watchlist (live)
2026-10-17: #1 and #9–#12 had been deleted (signals table also emptied); restored from their original definitions
as #16 (= old #1, paused) and #17/#18 (= #9/#10), #19/#20 (= #11/#12). IDs below are the ORIGINAL numbers.
2026-10-18: duplicates #13/#14 (copies of #6) and #15 (copy of #7) removed (no signals). Exits switched to high win rate:
#1 EURUSD H1 long sweep_low_recent + fvg_bull_retest (1ATR/2R, paused) · #6 BTCUSD H1 long (2ATR/1R) · #7 XAUUSD H4 long (3ATR/1R) · #8 USDJPY H1 short (2ATR/1R).
#9 EURUSD H4 + #10 US100 H4: CRT "close|full" + d1_bias + pd_zone + big_range, both directions — FORWARD TEST, flagged
"unproven" (grade C per market; chosen after seeing OOS, so not validated). Strategy watches: conditions=["strategy:crt"],
plan="crt|H4|close|full|<filters>", handled by `strategies/live.py` + `LiveService._scan_strategy` (fixed stop/target levels).
#11 EURUSD H1 + #12 XAUUSD H1: SMC "smc|H1|smc_any|r2|bias2,htf_pd,killzone" (sweep -> CHoCH/MSS -> FVG/OB retest,
D1 structure agrees, H4 discount/premium, killzone; struct stop, 2R) — FORWARD TEST, "unproven" (pooled OOS +0.08R, q=1).
#21 BTCUSD H1 long (pattern 2563) · #22 EURUSD H1 short (2779) · #23 GBPUSD H1 long (2910): high-win-rate discovery
(2ATR/1R), grade A, unseen wins 67/68/76%, +0.39/+0.40/+0.50R — added 2026-10-18 as forward tests (in the name).
Portfolio with #21-#23 (1% risk, unseen): 13 trades/month, won 60%, +0.29R/trade, CAGR 56%, max DD 9%; low overlap
with the other watches (10-20% time together, weekly corr ~0). Without the CRT/SMC tests: unseen won 66%, DD 7%.

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
   - ALCHEMIST TESTED 2026-10-19 (`strategies/alchemist.py`, setting `strategy_alchemist`). User's description was
     generic (direction -> sweep -> CHoCH/BOS -> OB/FVG entry -> stop beyond sweep extreme -> next liquidity or 1:2/1:3),
     i.e. the SMC setup plus BOS, sweep-extreme stop and 3R. 108 pre-registered rules (M15/H1/H4 x htf-direction/none x
     choch/any x fvg/ob/any x r2/r3/liq), 6 markets pooled: 0 VALIDATED. Gross edge ~0 (H1 median +0.01R); M15 0/36
     positive unseen; H4 34/36 positive unseen but negative in-sample (q=1). HTF direction filter did not help.
     Stops are NOT tight: by the retest the sweep extreme is a median 3.4 ATR away (only 3% <= 1 ATR); 40% time out.
     Not wired to live/UI.
2. DONE 2026-10-07: Portfolio page (sidebar "Portfolio", `pages/PortfolioPage.tsx`, `GET/POST /api/execution/portfolio`,
   last result in setting `portfolio_last`). Includes CRT/SMC strategy watches (`portfolio._strategy_trades`).
   Result (active watches, 1% risk): all history CAGR 42% / max DD 21%; unseen-only CAGR 51% / max DD 9%;
   stress test suggests 0.75% risk for a 20% DD limit. Watchlist now also has #13/#14 (exact copies of #6, added by
   the user, flagged duplicate) and #15 (copy of #7 with baseline exit); #1 EURUSD is paused by the user. Feature caches are now written atomically and a cache that
   fails to load is rebuilt automatically (`features/engine.py`), after corrupt US100 caches broke two runs.
   2026-10-17 re-run (watches restored): all history CAGR 39% / max DD 21%; unseen-only CAGR 51% / max DD 9%.
2b. DONE 2026-10-19: chart labels for supply/demand + support/resistance zones (`features/sdsr.py`, returned by
   `/api/overlays` as `sd_zones`/`sr_zones`, layers "Supply / demand" and "Support / resistance", hover tooltips).
   S/D = 1-3 small-body base candles (body <= 0.5 ATR) then impulse (>= 0.8 ATR body, >= 1.5 ATR away within 3 bars),
   named RBR/DBR/DBD/RBD; fresh/tested/broken as of the replay bar. S/R = clusters (0.5 ATR) of >= 2 confirmed swings
   in the last 500 bars, top 4 per side by CHANCE OF BEING REACHED within DEFAULT_HORIZON bars (user request:
   "most likely to be triggered"; empirical ATR-excursion table, last 20k bars as of the bar; calibrated: predicted
   70-90% -> reached 78-79%, 90%+ -> 94-96%, 0-30% -> 13-15%), >= 1 ATR apart; touches only break ties; "flip" = both highs and lows. Drawing only —
   NOT backtested yet (no conditions/grades for these zones).
2c. DONE 2026-10-19: setups drawn as long/short POSITION boxes (TradingView position tool; user request):
   green entry->target, red entry->stop. Past trades: entry candle -> exit candle (occurrences now carry entry/stop/
   target/entry_time/exit_time: `routes._position` for patterns, CRT trade dict, SMC `collect` columns). Projected
   setup (entry zone) and selected key trade zone: drawn after the last candle; chart rightOffset 40 while shown.
   Checked: drawn exits match backtest win/loss on 782 trades.
2d. DONE 2026-10-19: S/D + S/R zones BACKTESTED. 14 conditions (category "Supply/demand", "Support/resistance":
   demand/supply_retest (+ _rev DBR/RBD, _cont RBR/DBD), in_demand/in_supply, support/resistance_test (+ _strong 3+
   touches, _flip)) computed per bar in `sdsr.add_sdsr` (FEATURE_VERSION 4 -> 5; S/D zones expire after 1500 bars;
   S/R test = fresh arrival at nearest band). Study `research/sdsr_study.py` (setting `sdsr_study`): 6 markets x
   M15/H1/H4 x 3 exits (1ATR/2R, 2ATR/1R, 1.5ATR/3R): 756 per-market + 126 pooled tests -> 0 VALIDATED (BH q<=0.10).
   Best pooled: RBR demand retest H1 1ATR/2R unseen +0.18R (random long same period -0.04R) but in-sample -0.08R,
   q=0.44 -> a lead only, nothing added live. M15 negative (costs). Chart tooltips show per-market record + verdict
   (`/zone_stats` keys demand/supply/RBR/DBR/DBD/RBD/support/resistance + `_sdsr_study`).
2e. DONE 2026-10-19: Forward-test scoreboard (sidebar "Forward tests", `live/scoreboard.py`, `GET /api/live/scoreboard`).
   Rebuilds each watch's trades (portfolio._setup_trades) on candles after "forward since" (= created; #17-#20 =
   2026-10-07 when the CRT/SMC rules were chosen; override via setting `forward_since` {id: unix}). Expected = unseen
   backtest trades before that date. Checks: binomial win rate, normal R/trade, Poisson signal count (5%); verdict
   needs 10+ trades. First run: 3 forward trades (#16 +1.95R, #18 -1.04R, #19 +1.96R), all "too early"; #7 had 0
   signals vs ~4 expected. Signals table was empty since the 2026-10-17 reset.
2f. DONE 2026-10-19: catch-up scanning for PATTERN watches (`LiveService.scan`): candles closed since the last scan
   (setting `last_scanned` {"SYM|TF": unix}, at most CATCHUP_BARS=24) are checked, cooldown respected; such signals
   carry payload.late_bars and are labelled LATE in Telegram, toasts and signal cards. First scan after install only
   checks the newest candle. CRT/SMC strategy watches still check only the latest candle (scoreboard covers them).
2g. DONE 2026-10-19: PRICE-ZONE ALERTS (`live/price_alerts.py`, table `price_alerts`, `GET/POST/DELETE
   /api/live/price_alerts`). Chart: click any zone (S/D, S/R, FVG, OB, trade zone) -> "Alert me when price reaches this
   zone"; "Alert at price" button then click a price -> level alert. Checked every live tick (LiveService.tick) against
   the forming M5 candle's high/low; after downtime against closed M5 candles (late=1). Created inside the band ->
   unarmed until price leaves. Fires once: Telegram + in-app toast (`usePriceAlertToasts`). Drawn on chart as 🔔.
3. Optional: show recently filled zones (last ~50 candles) by default on the chart.
4. DONE 2026-10-07: "View on chart" for watched setups (Live page) and discovered patterns (library row + pattern page).
   `/api/setup_view` -> checklist (met/not, price level where one exists via `_LEVEL_OF`/`_PD_FRACTION` in routes.py)
   + past occurrences. Chart: real levels = lines at price; conditions without a price = labelled dashed "marker lines"
   stacked above price (user's request); green = met, amber = not; arrows = past trades (green win / red loss).
   Strategy watches (CRT/SMC) use `/api/live/watchlist/{id}/view` (`strategies/view.py`). Each view has a
   "possible entry zone" (FVG/OB zone, or middle 50% of past entries measured from a level in ATR; none if no price
   anchor) with projected stop/target. UI check: Playwright + installed Chrome works headless (scratchpad scripts).
5. DONE 2026-10-07: key trade zones sorted by grade -> score -> distance. Risk calculator (`frontend/src/RiskCalc.tsx`,
   `/api/live/position_size`): lots from risk ($ or % balance) incl. spread, via MT5 contract (cached in setting
   `contracts`). User's MT5 balance is $0.37 — min lot exceeds sensible risk on every setup.
