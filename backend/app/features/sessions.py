"""Session, calendar and key-level features.

Sessions are defined in New York local time so they stay correct across DST:
    asia      19:00-02:00 NY
    london    02:00-08:00 NY
    new_york  08:00-16:00 NY
    off       16:00-19:00 NY
The trading day rolls over at 17:00 New York (FX convention).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SESSIONS = ["asia", "london", "new_york", "off"]
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def add_sessions(df: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
    out = {}
    ny = df["time"].dt.tz_convert("America/New_York")
    hour = ny.dt.hour + ny.dt.minute / 60.0
    out["hour_ny"] = ny.dt.hour
    sess = np.select(
        [(hour >= 19) | (hour < 2), (hour >= 2) & (hour < 8), (hour >= 8) & (hour < 16)],
        [0, 1, 2], 3)
    out["session"] = sess
    out["ln_ny_overlap"] = (hour >= 8) & (hour < 11)
    out["killzone"] = ((hour >= 2) & (hour < 5)) | ((hour >= 7) & (hour < 10))

    tday = (ny + pd.Timedelta(hours=7)).dt.tz_localize(None).dt.normalize()
    out["dow"] = tday.dt.dayofweek
    o = pd.DataFrame({"tday": tday, "high": df["high"], "low": df["low"], "open": df["open"],
                      "close": df["close"], "sess": sess, "hour": hour})

    g = o.groupby("tday")
    day_hi, day_lo, day_open = g["high"].max(), g["low"].min(), g["open"].first()
    out["pdh"] = tday.map(day_hi.shift(1)).to_numpy()
    out["pdl"] = tday.map(day_lo.shift(1)).to_numpy()
    out["day_open"] = tday.map(day_open).to_numpy()
    # intraday high/low *before* this bar (for first-touch logic)
    out["day_hi_prev"] = g["high"].transform(lambda s: s.cummax().shift(1)).to_numpy()
    out["day_lo_prev"] = g["low"].transform(lambda s: s.cummin().shift(1)).to_numpy()

    week = tday - pd.to_timedelta(tday.dt.dayofweek, unit="D")
    wk_hi = o.groupby(week)["high"].max()
    wk_lo = o.groupby(week)["low"].min()
    out["pwh"] = week.map(wk_hi.shift(1)).to_numpy()
    out["pwl"] = week.map(wk_lo.shift(1)).to_numpy()

    # Asian range: known only once the Asian session has closed (02:00 NY).
    if tf_minutes < 240:
        asia = o[o["sess"] == 0].groupby("tday")
        a_hi, a_lo = asia["high"].max(), asia["low"].min()
        after_asia = (sess == 1) | (sess == 2)
        out["asia_high"] = np.where(after_asia, tday.map(a_hi), np.nan)
        out["asia_low"] = np.where(after_asia, tday.map(a_lo), np.nan)
        # session high/low reached earlier in today's post-Asia trading
        post = o.assign(h=np.where(after_asia, o["high"], -np.inf), l=np.where(after_asia, o["low"], np.inf))
        out["post_asia_hi_prev"] = post.groupby("tday")["h"].transform(lambda s: s.cummax().shift(1)).to_numpy()
        out["post_asia_lo_prev"] = post.groupby("tday")["l"].transform(lambda s: s.cummin().shift(1)).to_numpy()
    else:
        for k in ("asia_high", "asia_low", "post_asia_hi_prev", "post_asia_lo_prev"):
            out[k] = np.nan

    f = pd.DataFrame(out, index=df.index)
    h, l, c = df["high"], df["low"], df["close"]
    # Liquidity sweeps of key levels: first breach of the level today, closing back inside.
    f["sweep_pdh"] = (h > f["pdh"]) & (c < f["pdh"]) & (f["day_hi_prev"].fillna(-np.inf) <= f["pdh"])
    f["sweep_pdl"] = (l < f["pdl"]) & (c > f["pdl"]) & (f["day_lo_prev"].fillna(np.inf) >= f["pdl"])
    f["break_pdh"] = (c > f["pdh"]) & (f["day_hi_prev"].fillna(-np.inf) <= f["pdh"])
    f["break_pdl"] = (c < f["pdl"]) & (f["day_lo_prev"].fillna(np.inf) >= f["pdl"])
    f["sweep_asia_high"] = (h > f["asia_high"]) & (c < f["asia_high"]) & (f["post_asia_hi_prev"].fillna(-np.inf) <= f["asia_high"])
    f["sweep_asia_low"] = (l < f["asia_low"]) & (c > f["asia_low"]) & (f["post_asia_lo_prev"].fillna(np.inf) >= f["asia_low"])
    f["above_day_open"] = c > f["day_open"]
    return pd.concat([df, f], axis=1)
