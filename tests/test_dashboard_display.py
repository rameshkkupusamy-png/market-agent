import datetime

import pandas as pd

from market_agent.dashboard.display import as_percent, dates_only, value_chart


def test_as_percent():
    frame = pd.DataFrame({"total_return": [0.1234, None], "trades": [3, 4]})
    shown = as_percent(frame, ["total_return", "missing"])
    assert list(shown.columns) == ["total_return (%)", "trades"]
    assert shown["total_return (%)"].iloc[0] == 12.3


def test_dates_only():
    frame = pd.DataFrame(
        {
            "day": [pd.Timestamp("2026-10-02")],
            "exit_day": [None],
            "start": [pd.Timestamp("2015-01-02")],
            "ticker": ["NTAP"],
        }
    )
    shown = dates_only(frame)
    assert shown.at[0, "day"] == datetime.date(2026, 10, 2)
    assert shown.at[0, "start"] == datetime.date(2015, 1, 2)
    assert pd.isna(shown.at[0, "exit_day"])
    assert shown.at[0, "ticker"] == "NTAP"


def test_value_chart_does_not_start_at_zero():
    days = pd.bdate_range("2026-10-02", periods=2)
    curves = pd.DataFrame(
        {"rules-only": [10_000.0, 10_050.0], "SPY": [10_000.0, 10_020.0]}, index=days
    )
    spec = value_chart(curves).to_dict()
    assert spec["encoding"]["y"]["scale"]["zero"] is False
    assert spec["encoding"]["y"]["axis"]["format"] == ",.0f"
    assert spec["mark"]["point"] is True
    assert spec["encoding"]["color"]["field"] == "series"
