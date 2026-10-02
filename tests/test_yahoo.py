import pandas as pd
import pytest
from yfinance.exceptions import YFEarningsDateMissing, YFRateLimitError

from market_agent.data.sources import NoData
from market_agent.data.yahoo import YahooEarnings, YahooPrices, YahooSectors, yahoo_symbol


class FakeTicker:
    def __init__(self, history=None, earnings=None, info=None, error=None):
        self._history = history
        self._earnings = earnings
        self._info = info or {}
        self._error = error
        self.history_args = None

    @property
    def info(self):
        if self._error:
            raise self._error
        return self._info

    def history(self, **kwargs):
        self.history_args = kwargs
        return self._history if self._history is not None else pd.DataFrame()

    def get_earnings_dates(self, limit):
        if self._error:
            raise self._error
        return self._earnings


class FakeYf:
    def __init__(self, tickers):
        self.tickers = tickers
        self.requested = []

    def Ticker(self, symbol):  # noqa: N802  same name as yfinance
        self.requested.append(symbol)
        return self.tickers[symbol]


def test_yahoo_symbol_uses_dashes():
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("AAPL") == "AAPL"


def test_prices_are_requested_adjusted_and_normalized():
    raw = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [5.0]},
        index=pd.DatetimeIndex(["2024-01-02"], tz="America/New_York"),
    )
    ticker = FakeTicker(history=raw)
    yf = FakeYf({"BRK-B": ticker})
    bars = YahooPrices(yf).fetch("BRK.B", pd.Timestamp("2014-01-01"), pd.Timestamp("2024-01-02"))
    assert yf.requested == ["BRK-B"]
    assert ticker.history_args["auto_adjust"] is True
    assert str(ticker.history_args["start"]) == "2014-01-01"
    assert str(ticker.history_args["end"]) == "2024-01-03"  # yfinance's end is exclusive
    assert list(bars.index) == [pd.Timestamp("2024-01-02")]


def test_no_prices_raises_no_data():
    with pytest.raises(NoData):
        YahooPrices(FakeYf({"TWTR": FakeTicker()})).fetch(
            "TWTR", pd.Timestamp("2014-01-01"), pd.Timestamp("2024-01-02")
        )


def test_earnings_dates_become_days_with_coverage():
    frame = pd.DataFrame(
        {"EPS Estimate": [1.0, 1.0]},
        index=pd.DatetimeIndex(
            ["2024-04-30 16:30", "2015-01-27 16:30"], tz="America/New_York", name="Earnings Date"
        ),
    )
    history = YahooEarnings(FakeYf({"AAPL": FakeTicker(earnings=frame)})).fetch("AAPL")
    assert history.dates == [pd.Timestamp("2015-01-27"), pd.Timestamp("2024-04-30")]
    assert history.coverage_start == pd.Timestamp("2015-01-27")


@pytest.mark.parametrize(
    "ticker", [FakeTicker(earnings=None), FakeTicker(error=YFEarningsDateMissing("AAPL"))]
)
def test_no_earnings_data_gives_empty_history(ticker):
    history = YahooEarnings(FakeYf({"AAPL": ticker})).fetch("AAPL")
    assert history.dates == [] and history.coverage_start is None


@pytest.mark.parametrize("error", [YFRateLimitError(), ConnectionError("timed out")])
def test_earnings_transport_errors_propagate(error):
    with pytest.raises(type(error)):
        YahooEarnings(FakeYf({"AAPL": FakeTicker(error=error)})).fetch("AAPL")


def test_sector():
    yf = FakeYf({"AAPL": FakeTicker(info={"sector": "Technology"}), "X": FakeTicker(info={})})
    assert YahooSectors(yf).sector("AAPL") == "Technology"
    assert YahooSectors(yf).sector("X") == "Unknown"


def test_sector_transport_errors_propagate():
    yf = FakeYf({"AAPL": FakeTicker(error=YFRateLimitError())})
    with pytest.raises(YFRateLimitError):
        YahooSectors(yf).sector("AAPL")
