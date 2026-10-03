"""Reaches Yahoo Finance. Run with: pytest -m live"""

import pandas as pd
import pytest

from market_agent.data.yahoo import YahooEarnings, YahooPrices

pytestmark = pytest.mark.live


def test_real_prices_and_earnings():
    bars = YahooPrices().fetch("AAPL", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-03-01"))
    assert len(bars) > 30
    assert (bars["high"] >= bars["low"]).all()
    assert YahooEarnings().fetch("AAPL").dates


def test_traded_prices_undo_later_splits():
    # NVDA split 4:1 in 2021 and 10:1 in 2024; on 2015-01-02 it closed at $20.13
    bars = YahooPrices().fetch("NVDA", pd.Timestamp("2015-01-02"), pd.Timestamp("2015-01-05"))
    first = bars.iloc[0]
    assert first["raw_close"] == pytest.approx(20.13, abs=0.01)
    assert first["close"] < 1  # adjusted for both splits
    assert first["raw_close"] * first["raw_volume"] == pytest.approx(
        first["raw_close"] / 40 * first["raw_volume"] * 40
    )
