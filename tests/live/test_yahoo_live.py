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
