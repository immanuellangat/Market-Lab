"""Broker server clock: rule detection from history and conversion to UTC."""
import numpy as np
import pandas as pd

from app.data.mt5_source import clock_rule_from_history, from_utc, to_utc


def _h1_server_times(rule: str) -> np.ndarray:
    """Hourly FX bars (Sunday 17:00 to Friday 17:00 New York) for 2024-2026, stamped in server time."""
    utc = pd.date_range("2024-01-01", "2026-11-14", freq="h", tz="UTC")
    ny = utc.tz_convert("America/New_York")
    week_hour = ny.dayofweek * 24 + ny.hour          # Monday 00:00 = 0
    is_open = (week_hour >= 6 * 24 + 17) | (week_hour < 4 * 24 + 17)
    return from_utc(utc[is_open], rule)


def test_rule_is_read_from_weekly_opens():
    assert clock_rule_from_history(_h1_server_times("eet")) == "eet"
    assert clock_rule_from_history(_h1_server_times("ny+7")) == "ny+7"


def test_rule_unknown_without_a_daylight_saving_change():
    t = _h1_server_times("eet")
    summer = t[(t > pd.Timestamp("2026-05-01").timestamp()) & (t < pd.Timestamp("2026-09-01").timestamp())]
    assert clock_rule_from_history(summer) is None


def test_conversion_differs_only_between_the_two_daylight_saving_changes():
    def utc_hour(server: str, rule: str) -> int:
        return to_utc(np.array([pd.Timestamp(server).timestamp()]), rule)[0].hour

    assert utc_hour("2026-07-01 12:00", "eet") == utc_hour("2026-07-01 12:00", "ny+7") == 9    # both UTC+3
    assert utc_hour("2026-11-16 12:00", "eet") == utc_hour("2026-11-16 12:00", "ny+7") == 10   # both UTC+2
    # Europe left summer time on 25 Oct 2026, the US on 1 Nov 2026
    assert utc_hour("2026-10-28 12:00", "eet") == 10
    assert utc_hour("2026-10-28 12:00", "ny+7") == 9


def test_round_trip():
    utc = pd.date_range("2026-06-01", "2026-06-10", freq="5min", tz="UTC")
    for rule in ("eet", "ny+7"):
        assert (to_utc(from_utc(utc, rule), rule) == utc).all()
