"""Catalog of named, explainable market conditions.

Every condition is a boolean mask over bars, evaluated at bar close using only
information available at that moment. Combinations of these conditions are
what the hypothesis tester (and later the discovery engine) evaluates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from ..features.sessions import DOW, SESSIONS


@dataclass(frozen=True)
class Condition:
    id: str
    label: str
    category: str
    bias: str  # "bull", "bear" or "neutral"
    description: str
    fn: Callable[[pd.DataFrame], pd.Series]


def recent(col: str, w: int) -> Callable:
    return lambda d: d[col].astype(bool).rolling(w, min_periods=1).max().astype(bool)


def col(name: str) -> Callable:
    return lambda d: d[name].astype(bool)


def _catalog() -> list[Condition]:
    C: list[Condition] = []

    def add(id, label, cat, bias, desc, fn):
        C.append(Condition(id, label, cat, bias, desc, fn))

    # ---------------- market structure
    add("trend_bull", "Structure bullish", "Structure", "bull", "Last break of structure was to the upside.", lambda d: d["ms_trend"] == 1)
    add("trend_bear", "Structure bearish", "Structure", "bear", "Last break of structure was to the downside.", lambda d: d["ms_trend"] == -1)
    add("bos_bull", "Bullish BOS (this bar)", "Structure", "bull", "Close above the last swing high in a bullish trend.", col("bos_bull"))
    add("bos_bear", "Bearish BOS (this bar)", "Structure", "bear", "Close below the last swing low in a bearish trend.", col("bos_bear"))
    add("choch_bull", "Bullish CHoCH (this bar)", "Structure", "bull", "First close above a swing high after a bearish trend.", col("choch_bull"))
    add("choch_bear", "Bearish CHoCH (this bar)", "Structure", "bear", "First close below a swing low after a bullish trend.", col("choch_bear"))
    add("mss_bull", "Bullish MSS", "Structure", "bull", "Bullish CHoCH delivered with a displacement candle.", col("mss_bull"))
    add("mss_bear", "Bearish MSS", "Structure", "bear", "Bearish CHoCH delivered with a displacement candle.", col("mss_bear"))
    add("bos_bull_recent", "Bullish BOS in last 10 bars", "Structure", "bull", "A bullish break of structure occurred within the last 10 bars.", recent("bos_bull", 10))
    add("bos_bear_recent", "Bearish BOS in last 10 bars", "Structure", "bear", "A bearish break of structure occurred within the last 10 bars.", recent("bos_bear", 10))
    add("choch_bull_recent", "Bullish CHoCH in last 10 bars", "Structure", "bull", "A bullish change of character within the last 10 bars.", recent("choch_bull", 10))
    add("choch_bear_recent", "Bearish CHoCH in last 10 bars", "Structure", "bear", "A bearish change of character within the last 10 bars.", recent("choch_bear", 10))
    add("hl_recent", "Higher low confirmed (last 10)", "Structure", "bull", "A higher swing low was confirmed recently.", recent("hl", 10))
    add("lh_recent", "Lower high confirmed (last 10)", "Structure", "bear", "A lower swing high was confirmed recently.", recent("lh", 10))
    add("discount", "In discount (< 50% of range)", "Structure", "bull", "Price is in the lower half of the current swing range.", lambda d: d["pd_pos"] < 0.5)
    add("premium", "In premium (> 50% of range)", "Structure", "bear", "Price is in the upper half of the current swing range.", lambda d: d["pd_pos"] > 0.5)
    add("deep_discount", "Deep discount (< 25%)", "Structure", "bull", "Price is in the bottom quarter of the swing range.", lambda d: d["pd_pos"] < 0.25)
    add("deep_premium", "Deep premium (> 75%)", "Structure", "bear", "Price is in the top quarter of the swing range.", lambda d: d["pd_pos"] > 0.75)

    # ---------------- liquidity
    add("sweep_low", "Swing-low liquidity sweep", "Liquidity", "bull", "Wick below an unswept swing low and close back above it.", col("sweep_swing_low"))
    add("sweep_high", "Swing-high liquidity sweep", "Liquidity", "bear", "Wick above an unswept swing high and close back below it.", col("sweep_swing_high"))
    add("sweep_low_recent", "Swing-low sweep in last 5 bars", "Liquidity", "bull", "Sell-side liquidity was swept within the last 5 bars.", recent("sweep_swing_low", 5))
    add("sweep_high_recent", "Swing-high sweep in last 5 bars", "Liquidity", "bear", "Buy-side liquidity was swept within the last 5 bars.", recent("sweep_swing_high", 5))
    add("sweep_pdl", "Previous-day low swept", "Liquidity", "bull", "First break below yesterday's low today, closing back above.", col("sweep_pdl"))
    add("sweep_pdh", "Previous-day high swept", "Liquidity", "bear", "First break above yesterday's high today, closing back below.", col("sweep_pdh"))
    add("sweep_pdl_recent", "PDL swept in last 12 bars", "Liquidity", "bull", "Yesterday's low was swept within the last 12 bars.", recent("sweep_pdl", 12))
    add("sweep_pdh_recent", "PDH swept in last 12 bars", "Liquidity", "bear", "Yesterday's high was swept within the last 12 bars.", recent("sweep_pdh", 12))
    add("sweep_asia_low", "Asian low swept", "Liquidity", "bull", "After Asia closes, price wicks below the Asian low and closes back above.", col("sweep_asia_low"))
    add("sweep_asia_high", "Asian high swept", "Liquidity", "bear", "After Asia closes, price wicks above the Asian high and closes back below.", col("sweep_asia_high"))
    add("sweep_asia_low_recent", "Asian low swept (last 12)", "Liquidity", "bull", "The Asian low was swept within the last 12 bars.", recent("sweep_asia_low", 12))
    add("sweep_asia_high_recent", "Asian high swept (last 12)", "Liquidity", "bear", "The Asian high was swept within the last 12 bars.", recent("sweep_asia_high", 12))
    add("sweep_eql", "Equal lows swept", "Liquidity", "bull", "A pool of equal lows was run and price closed back above.", col("sweep_eql"))
    add("sweep_eqh", "Equal highs swept", "Liquidity", "bear", "A pool of equal highs was run and price closed back below.", col("sweep_eqh"))
    add("break_pdh", "Close above previous-day high", "Liquidity", "bull", "First close above yesterday's high today.", col("break_pdh"))
    add("break_pdl", "Close below previous-day low", "Liquidity", "bear", "First close below yesterday's low today.", col("break_pdl"))

    # ---------------- imbalance / order blocks
    add("fvg_bull", "Bullish FVG formed", "Imbalance", "bull", "A 3-candle bullish fair value gap completed on this bar.", col("fvg_bull"))
    add("fvg_bear", "Bearish FVG formed", "Imbalance", "bear", "A 3-candle bearish fair value gap completed on this bar.", col("fvg_bear"))
    add("fvg_bull_disp", "Bullish FVG with displacement", "Imbalance", "bull", "Bullish FVG whose middle candle is a displacement candle.", col("fvg_bull_disp"))
    add("fvg_bear_disp", "Bearish FVG with displacement", "Imbalance", "bear", "Bearish FVG whose middle candle is a displacement candle.", col("fvg_bear_disp"))
    add("fvg_bull_retest", "Bullish FVG first retest", "Imbalance", "bull", "Price returns into an unfilled bullish FVG for the first time and holds.", col("fvg_bull_retest"))
    add("fvg_bear_retest", "Bearish FVG first retest", "Imbalance", "bear", "Price returns into an unfilled bearish FVG for the first time and holds.", col("fvg_bear_retest"))
    add("in_bull_fvg", "Trading inside bullish FVG", "Imbalance", "bull", "This bar traded inside an active bullish FVG.", col("in_bull_fvg"))
    add("in_bear_fvg", "Trading inside bearish FVG", "Imbalance", "bear", "This bar traded inside an active bearish FVG.", col("in_bear_fvg"))
    add("ob_bull_retest", "Bullish order block retest", "Order block", "bull", "First return into a bullish order block that holds.", col("ob_bull_retest"))
    add("ob_bear_retest", "Bearish order block retest", "Order block", "bear", "First return into a bearish order block that holds.", col("ob_bear_retest"))

    # ---------------- momentum / candles
    add("rsi_os", "RSI oversold (< 30)", "Momentum", "bull", "RSI(14) below 30.", lambda d: d["rsi"] < 30)
    add("rsi_ob", "RSI overbought (> 70)", "Momentum", "bear", "RSI(14) above 70.", lambda d: d["rsi"] > 70)
    add("rsi_low", "RSI below 40", "Momentum", "bull", "RSI(14) below 40.", lambda d: d["rsi"] < 40)
    add("rsi_high", "RSI above 60", "Momentum", "bear", "RSI(14) above 60.", lambda d: d["rsi"] > 60)
    add("div_bull", "Bullish RSI divergence (last 5)", "Momentum", "bull", "Lower low in price with a higher RSI low, confirmed within the last 5 bars.", recent("div_bull", 5))
    add("div_bear", "Bearish RSI divergence (last 5)", "Momentum", "bear", "Higher high in price with a lower RSI high, confirmed within the last 5 bars.", recent("div_bear", 5))
    add("disp_bull", "Bullish displacement candle", "Momentum", "bull", "Body > 1.5 ATR and > 60% of the candle range, closing up.", col("displacement_bull"))
    add("disp_bear", "Bearish displacement candle", "Momentum", "bear", "Body > 1.5 ATR and > 60% of the candle range, closing down.", col("displacement_bear"))
    add("bull_engulf", "Bullish engulfing", "Candles", "bull", "Bullish candle engulfing the previous bearish body.", col("bull_engulf"))
    add("bear_engulf", "Bearish engulfing", "Candles", "bear", "Bearish candle engulfing the previous bullish body.", col("bear_engulf"))
    add("hammer", "Hammer / bullish pin", "Candles", "bull", "Lower wick > 60% of the range with a small body.", col("hammer"))
    add("shooting_star", "Shooting star / bearish pin", "Candles", "bear", "Upper wick > 60% of the range with a small body.", col("shooting_star"))
    add("ema_bull", "EMA trend up", "Momentum", "bull", "Close > EMA50 > EMA200.", lambda d: d["ema_trend"] == 1)
    add("ema_bear", "EMA trend down", "Momentum", "bear", "Close < EMA50 < EMA200.", lambda d: d["ema_trend"] == -1)
    add("strong_up", "Strong 10-bar rally (> 2 ATR)", "Momentum", "neutral", "Price rose more than 2 ATR over the last 10 bars.", lambda d: d["roc10_atr"] > 2)
    add("strong_down", "Strong 10-bar drop (> 2 ATR)", "Momentum", "neutral", "Price fell more than 2 ATR over the last 10 bars.", lambda d: d["roc10_atr"] < -2)
    add("streak_up4", "4+ consecutive up candles", "Momentum", "neutral", "At least four consecutive bullish closes.", lambda d: d["streak"] >= 4)
    add("streak_down4", "4+ consecutive down candles", "Momentum", "neutral", "At least four consecutive bearish closes.", lambda d: d["streak"] <= -4)

    # ---------------- volatility
    add("vol_low", "Low volatility regime", "Volatility", "neutral", "ATR in the bottom third of its last 500 bars.", lambda d: d["vol_regime"] == 0)
    add("vol_normal", "Normal volatility regime", "Volatility", "neutral", "ATR in the middle third of its last 500 bars.", lambda d: d["vol_regime"] == 1)
    add("vol_high", "High volatility regime", "Volatility", "neutral", "ATR in the top third of its last 500 bars.", lambda d: d["vol_regime"] == 2)
    add("compression", "Range compression", "Volatility", "neutral", "20-bar range among the tightest 15% of the last 500 bars.", col("compression"))
    add("range_expansion", "Range-expansion candle (> 2.5 ATR)", "Volatility", "neutral", "This candle's range exceeds 2.5 ATR.", col("range_expansion"))
    add("inside_bar", "Inside bar", "Volatility", "neutral", "High/low contained within the previous candle.", col("inside_bar"))

    # ---------------- session / calendar
    for i, s in enumerate(SESSIONS):
        add(f"sess_{s}", f"{s.replace('_', ' ').title()} session", "Session", "neutral", f"Bar opens during the {s.replace('_', ' ')} session (NY-time defined).",
            (lambda k: (lambda d: d["session"] == k))(i))
    add("ln_ny_overlap", "London / NY overlap", "Session", "neutral", "08:00-11:00 New York time.", col("ln_ny_overlap"))
    add("killzone", "ICT kill zone", "Session", "neutral", "London (02-05 NY) or New York (07-10 NY) kill zone.", col("killzone"))
    for i, dname in enumerate(DOW):
        add(f"dow_{dname.lower()}", f"{dname}", "Calendar", "neutral", f"Trading day is {dname}.", (lambda k: (lambda d: d["dow"] == k))(i))
    add("above_day_open", "Above daily open", "Session", "bull", "Close above the trading day's opening price.", col("above_day_open"))
    add("below_day_open", "Below daily open", "Session", "bear", "Close below the trading day's opening price.", lambda d: ~d["above_day_open"].astype(bool))

    # ---------------- higher-timeframe context
    for p in ("h1", "h4", "d1"):
        P = p.upper()
        add(f"{p}_trend_bull", f"{P} structure bullish", "Higher timeframe", "bull", f"{P} market structure is bullish (as of the last closed {P} bar).", (lambda q: lambda d: d[f"{q}_ms_trend"] == 1)(p))
        add(f"{p}_trend_bear", f"{P} structure bearish", "Higher timeframe", "bear", f"{P} market structure is bearish (as of the last closed {P} bar).", (lambda q: lambda d: d[f"{q}_ms_trend"] == -1)(p))
        add(f"{p}_ema_bull", f"{P} EMA trend up", "Higher timeframe", "bull", f"{P} close > EMA50 > EMA200.", (lambda q: lambda d: d[f"{q}_ema_trend"] == 1)(p))
        add(f"{p}_ema_bear", f"{P} EMA trend down", "Higher timeframe", "bear", f"{P} close < EMA50 < EMA200.", (lambda q: lambda d: d[f"{q}_ema_trend"] == -1)(p))
        add(f"{p}_discount", f"{P} in discount", "Higher timeframe", "bull", f"Price in the lower half of the {P} swing range.", (lambda q: lambda d: d[f"{q}_pd_pos"] < 0.5)(p))
        add(f"{p}_premium", f"{P} in premium", "Higher timeframe", "bear", f"Price in the upper half of the {P} swing range.", (lambda q: lambda d: d[f"{q}_pd_pos"] > 0.5)(p))
        add(f"{p}_in_bull_fvg", f"{P} trading in bullish FVG", "Higher timeframe", "bull", f"The last closed {P} bar traded inside a bullish FVG.", (lambda q: lambda d: d[f"{q}_in_bull_fvg"].astype(bool))(p))
        add(f"{p}_in_bear_fvg", f"{P} trading in bearish FVG", "Higher timeframe", "bear", f"The last closed {P} bar traded inside a bearish FVG.", (lambda q: lambda d: d[f"{q}_in_bear_fvg"].astype(bool))(p))
        add(f"{p}_vol_high", f"{P} high volatility", "Higher timeframe", "neutral", f"{P} ATR in its top third.", (lambda q: lambda d: d[f"{q}_vol_regime"] == 2)(p))
    return C


CATALOG: list[Condition] = _catalog()
BY_ID: dict[str, Condition] = {c.id: c for c in CATALOG}


def available(df: pd.DataFrame) -> list[Condition]:
    """Conditions whose source columns exist for this dataset."""
    out = []
    head = df.head(5)
    for c in CATALOG:
        try:
            c.fn(head)
            out.append(c)
        except KeyError:
            pass
    return out


# A condition id prefixed with "!" means the condition is NOT true (used by discovered rules).
def base_id(cid: str) -> str:
    return cid[1:] if cid.startswith("!") else cid


def is_valid(cid: str) -> bool:
    return base_id(cid) in BY_ID


def label_of(cid: str) -> str:
    c = BY_ID.get(base_id(cid))
    if c is None:
        return cid
    return f"NOT {c.label}" if cid.startswith("!") else c.label


def evaluate(df: pd.DataFrame, ids: list[str]) -> np.ndarray:
    mask = np.ones(len(df), bool)
    for cid in ids:
        if not is_valid(cid):
            raise KeyError(f"Unknown condition '{cid}'")
        try:
            m = BY_ID[base_id(cid)].fn(df)
        except KeyError as e:
            raise KeyError(f"Condition '{cid}' needs data that is not loaded ({e}). Sync the higher timeframe too.")
        m = np.asarray(pd.Series(m).fillna(False), dtype=bool)
        mask &= ~m if cid.startswith("!") else m
    return mask
