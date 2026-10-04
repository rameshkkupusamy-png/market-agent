import pandas as pd

from market_agent.sessions import TradingCalendar

DAYS = [pd.Timestamp(d) for d in ["2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"]]
CLOSES = [pd.Timestamp(f"{d:%Y-%m-%d} 21:00", tz="UTC") for d in DAYS]
CLOSES[2] = pd.Timestamp("2026-11-27 18:00", tz="UTC")  # early close after Thanksgiving
CAL = TradingCalendar(DAYS, CLOSES)


def test_latest_closed_session():
    assert CAL.latest_closed(pd.Timestamp("2026-11-24 20:59", tz="UTC")) is None
    assert CAL.latest_closed(pd.Timestamp("2026-11-24 21:00", tz="UTC")) == DAYS[0]
    assert CAL.latest_closed(pd.Timestamp("2026-11-26 22:30", tz="UTC")) == DAYS[1]  # holiday
    assert CAL.latest_closed(pd.Timestamp("2026-11-27 18:30", tz="UTC")) == DAYS[2]


def test_between_and_up_to():
    assert CAL.between(DAYS[0], DAYS[2]) == DAYS[1:3]
    assert CAL.between(None, DAYS[1]) == DAYS[:2]
    assert CAL.up_to(DAYS[1]) == DAYS[:2]


def test_nyse_calendar_knows_holidays_and_early_closes():
    cal = TradingCalendar.nyse(pd.Timestamp("2026-11-01"), pd.Timestamp("2026-12-31"))
    assert pd.Timestamp("2026-11-26") not in cal.sessions
    assert cal.latest_closed(pd.Timestamp("2026-11-27 18:30", tz="UTC")) == pd.Timestamp(
        "2026-11-27"
    )
