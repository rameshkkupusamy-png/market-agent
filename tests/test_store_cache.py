import sqlite3
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from helpers import make_bars
from market_agent.data.cache import EarningsCache, PriceCache
from market_agent.data.sources import EarningsHistory, NoData, normalize_bars
from market_agent.store import Store


class FakePrices:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def fetch(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        if ticker not in self.frames:
            raise NoData(ticker)
        return self.frames[ticker]


class FakeEarnings:
    def __init__(self, histories):
        self.histories = histories
        self.calls = 0

    def fetch(self, ticker):
        self.calls += 1
        return self.histories[ticker]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "market.db")
    yield s
    s.close()


def test_normalize_bars_handles_yahoo_shape():
    index = pd.DatetimeIndex(
        ["2024-01-03 00:00", "2024-01-02 00:00", "2024-01-03 00:00", "2024-01-04 00:00"],
        tz="America/New_York",
    )
    raw = pd.DataFrame(
        {
            "Open": [2, 1, 2, 3],
            "High": [2, 1, 2, 3],
            "Low": [2, 1, 2, 3],
            "Close": [2, 1, 2, None],
            "Volume": [20, 10, 20, 30],
            "raw_close": [8, 4, 8, 12],
            "raw_volume": [5, 2.5, 5, 7.5],
        },
        index=index,
    )
    bars = normalize_bars(raw)
    assert list(bars.columns) == [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "raw_close",
        "raw_volume",
    ]
    assert bars.index.tz is None
    assert bars.index.name == "day"
    assert list(bars.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert bars["close"].dtype == float


def test_store_round_trips_prices(store):
    bars = make_bars([10, 11, 12])
    store.replace_prices("AAPL", bars, "2026-10-02")
    loaded = store.load_prices("AAPL")
    pd.testing.assert_frame_equal(loaded, bars, check_freq=False)
    assert store.fetched_on("AAPL") == "2026-10-02"
    assert store.load_prices("MSFT") is None
    assert store.tickers_with_prices() == ["AAPL"]


def test_store_round_trips_traded_prices(store):
    store.replace_prices("NVDA", make_bars([0.5, 0.6], traded=40), "2026-10-02")
    loaded = store.load_prices("NVDA")
    assert list(loaded["raw_close"]) == [20.0, 24.0]
    assert store.tickers_without_traded_prices() == []


def test_older_database_is_upgraded_and_marked_for_download(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE prices (ticker TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL, PRIMARY KEY (ticker, day));"
        "CREATE TABLE price_meta (ticker TEXT PRIMARY KEY, fetched_on TEXT, has_data INTEGER);"
        "INSERT INTO prices VALUES ('AAPL', '2026-10-01', 1, 1, 1, 1, 100);"
        "INSERT INTO price_meta VALUES ('AAPL', '2026-10-02', 1);"
        "INSERT INTO price_meta VALUES ('GONE', '2026-10-02', 0);"
    )
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.fetched_on("AAPL") is None  # the next `agent fetch` downloads it again
    assert store.fetched_on("GONE") == "2026-10-02"
    assert store.load_prices("AAPL")["raw_close"].isna().all()
    assert store.tickers_without_traded_prices() == ["AAPL"]
    store.close()


def test_replace_prices_replaces_the_whole_series(store):
    store.replace_prices("AAPL", make_bars([10, 11, 12]), "2026-10-01")
    store.replace_prices("AAPL", make_bars([5, 6]), "2026-10-02")
    assert list(store.load_prices("AAPL")["close"]) == [5.0, 6.0]


def test_price_cache_fetches_once_per_day(store):
    source = FakePrices({"AAPL": make_bars([10, 11])})
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2))
    assert cache.update("AAPL", date(2014, 1, 1)) is True
    assert cache.update("AAPL", date(2014, 1, 1)) is True
    assert len(source.calls) == 1
    ticker, start, end = source.calls[0]
    assert (start, end) == (pd.Timestamp("2014-01-01"), pd.Timestamp("2026-10-02"))
    assert list(cache.load("AAPL")["close"]) == [10.0, 11.0]


def test_price_cache_records_missing_tickers(store):
    cache = PriceCache(store, FakePrices({}), today=lambda: date(2026, 10, 2))
    assert cache.update("TWTR", date(2014, 1, 1)) is False
    assert store.missing_tickers() == ["TWTR"]
    assert cache.load("TWTR") is None


def test_earnings_cache_round_trip(store):
    history = EarningsHistory(
        [pd.Timestamp("2015-01-27"), pd.Timestamp("2015-04-27")], pd.Timestamp("2015-01-27")
    )
    source = FakeEarnings({"AAPL": history, "NEW": EarningsHistory([], None)})
    cache = EarningsCache(store, source, today=lambda: date(2026, 10, 2))
    cache.update("AAPL")
    cache.update("AAPL")
    cache.update("NEW")
    assert source.calls == 2
    loaded = store.load_earnings()
    assert loaded["AAPL"] == history
    assert loaded["NEW"] == EarningsHistory([], None)


def test_price_cache_treats_empty_frame_as_missing(store):
    source = FakePrices({"EMPTY": make_bars([10]).iloc[0:0]})
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2))
    first = cache.update("EMPTY", date(2014, 1, 1))
    repeat = cache.update("EMPTY", date(2014, 1, 1))
    assert first is False
    assert repeat is first
    assert len(source.calls) == 1
    assert store.tickers_with_prices() == []
    assert store.missing_tickers() == ["EMPTY"]


def test_backtest_round_trip_with_numpy_and_timestamp_values(store):
    trade = SimpleNamespace(
        ticker="AAPL",
        entry_day=pd.Timestamp("2024-01-02"),
        entry_price=10.0,
        exit_day=pd.Timestamp("2024-01-05"),
        exit_price=11.0,
        shares=5,
        exit_reason="target",
        pnl=5.0,
    )
    equity = pd.Series([100.0, 105.0], index=pd.bdate_range("2024-01-02", periods=2))
    metrics = {
        "trades": np.int64(3),
        "cagr": np.float64(0.12),
        "max_drawdown_day": pd.Timestamp("2024-03-01"),
    }
    benchmark = {"cagr": np.float64(0.08), "days": np.int32(250)}
    backtest_id = store.save_backtest(
        "2024",
        pd.Timestamp("2024-01-02"),
        pd.Timestamp("2024-12-31"),
        "abc123",
        {"risk": {"max_positions": 5}},
        metrics,
        benchmark,
        {},
        [trade],
        equity,
    )
    runs = store.backtest_runs("2024")
    assert [r["id"] for r in runs] == [backtest_id]
    run = runs[0]
    assert run["fingerprint"] == "abc123"
    assert run["metrics"] == {"trades": 3, "cagr": 0.12, "max_drawdown_day": "2024-03-01 00:00:00"}
    assert run["benchmark"] == {"cagr": 0.08, "days": 250}
    assert store.backtest_runs("2025") == []


def test_empty_refetch_keeps_cached_prices(store):
    store.replace_prices("AAPL", make_bars([10, 11, 12]), "2026-10-01")
    source = FakePrices({"AAPL": make_bars([10]).iloc[0:0]})
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2))
    assert cache.update("AAPL", date(2014, 1, 1)) is True
    assert list(store.load_prices("AAPL")["close"]) == [10.0, 11.0, 12.0]
    assert store.tickers_with_prices() == ["AAPL"]
    assert store.missing_tickers() == []


def test_failed_refetch_keeps_cached_prices(store):
    store.replace_prices("AAPL", make_bars([10, 11, 12]), "2026-10-01")

    class Failing:
        def fetch(self, ticker, start, end):
            raise ConnectionError("timed out")

    cache = PriceCache(store, Failing(), today=lambda: date(2026, 10, 2))
    with pytest.raises(ConnectionError):
        cache.update("AAPL", date(2014, 1, 1))
    assert list(store.load_prices("AAPL")["close"]) == [10.0, 11.0, 12.0]


def test_earnings_source_error_keeps_stored_dates(store):
    history = EarningsHistory([pd.Timestamp("2015-01-27")], pd.Timestamp("2015-01-27"))
    EarningsCache(store, FakeEarnings({"AAPL": history}), today=lambda: date(2026, 10, 1)).update(
        "AAPL"
    )

    class Failing:
        def fetch(self, ticker):
            raise ConnectionError("rate limited")

    cache = EarningsCache(store, Failing(), today=lambda: date(2026, 10, 2))
    with pytest.raises(ConnectionError):
        cache.update("AAPL")
    assert store.load_earnings()["AAPL"] == history
    assert store.earnings_fetched_on("AAPL") == "2026-10-01"


def test_paper_days_and_daily_runs(store):
    d1, d2 = pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")
    assert store.latest_paper_day() is None and store.first_paper_day() is None
    store.save_day(d1, {"rules-only": "{1}", "rules+ai": "{2}"}, "traded", "report 1", [])
    store.save_day(d2, {"rules-only": "{3}", "rules+ai": "{4}"}, "no trading", "report 2", ["!"])
    assert store.latest_paper_day() == d2 and store.first_paper_day() == d1
    assert store.paper_states(d1) == {"rules-only": "{1}", "rules+ai": "{2}"}
    run = store.daily_run(d2)
    assert (run["status"], run["report"], run["alerts"], run["sent"]) == (
        "no trading",
        "report 2",
        ["!"],
        False,
    )
    store.mark_sent(d2)
    assert store.daily_run(d2)["sent"] is True
    assert store.latest_daily_run()["day"] == d2
    store.replace_paper_state("rules-only", d2, "{5}")
    assert store.paper_states(d2)["rules-only"] == "{5}"
    assert store.daily_run(pd.Timestamp("2026-09-30")) is None
    assert store.paper_states(pd.Timestamp("2026-09-30")) == {}


def test_reviews_and_monthly_spend(store):
    day = pd.Timestamp("2026-10-02")
    review = SimpleNamespace(
        ticker="NVDA",
        status="reviewed",
        verdict="skip",
        confidence="medium",
        reasons=["Guidance cut"],
        risks=[],
        news_used=[0],
        note="",
        cost=0.04,
        model="claude-opus-5-5",
        prompt="p",
        answer="{}",
    )
    store.save_review(day, review, late=True)
    store.save_review(day, review, late=False)
    assert store.ai_spent_since(datetime(2000, 1, 1)) == pytest.approx(0.08)
    assert store.ai_spent_since(datetime(2100, 1, 1)) == 0.0
    [first, _] = store.reviews_on(day)
    assert (first["ticker"], first["verdict"], first["reasons"], first["late"]) == (
        "NVDA",
        "skip",
        ["Guidance cut"],
        True,
    )


def test_price_cache_force_downloads_again(store):
    source = FakePrices({"AAPL": make_bars([10, 11])})
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2))
    cache.update("AAPL", date(2014, 1, 1))
    cache.update("AAPL", date(2014, 1, 1), force=True)
    assert len(source.calls) == 2


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def test_price_cache_downloads_again_when_fetched_before_the_data_settled(store):
    source = FakePrices({"AAPL": make_bars([10, 11])})
    clock = {"now": utc("2026-10-02 20:30")}
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2), now=lambda: clock["now"])
    cache.update("AAPL", date(2014, 1, 1))
    assert store.fetched_at("AAPL") == utc("2026-10-02 20:30")
    cache.update("AAPL", date(2014, 1, 1), fresh_after=utc("2026-10-02 20:30"))
    assert len(source.calls) == 1  # fetched at the time asked for: fresh
    cache.update("AAPL", date(2014, 1, 1))
    assert len(source.calls) == 1  # without fresh_after the date rule alone applies
    clock["now"] = utc("2026-10-02 22:30")
    cache.update("AAPL", date(2014, 1, 1), fresh_after=utc("2026-10-02 21:00"))
    assert len(source.calls) == 2  # fetched before the data settled: downloaded again
    assert store.fetched_at("AAPL") == utc("2026-10-02 22:30")
    cache.update("AAPL", date(2014, 1, 1), fresh_after=utc("2026-10-02 21:00"))
    assert len(source.calls) == 2


def test_price_cache_records_when_a_missing_ticker_was_checked(store):
    cache = PriceCache(
        store, FakePrices({}), today=lambda: date(2026, 10, 2), now=lambda: utc("2026-10-02 22:00")
    )
    cache.update("TWTR", date(2014, 1, 1))
    assert store.fetched_at("TWTR") == utc("2026-10-02 22:00")


def test_database_without_fetch_times_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE prices (ticker TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL,"
        " volume REAL, raw_close REAL, raw_volume REAL, PRIMARY KEY (ticker, day));"
        "CREATE TABLE price_meta (ticker TEXT PRIMARY KEY, fetched_on TEXT, has_data INTEGER);"
        "INSERT INTO prices VALUES ('AAPL', '2026-10-01', 1, 1, 1, 1, 100, 1, 100);"
        "INSERT INTO price_meta VALUES ('AAPL', '2026-10-02', 1);"
    )
    conn.commit()
    conn.close()
    store = Store(path)
    columns = {row[1] for row in store._conn.execute("PRAGMA table_info(price_meta)")}
    assert "fetched_at" in columns
    assert store.fetched_on("AAPL") == "2026-10-02" and store.fetched_at("AAPL") is None
    source = FakePrices({"AAPL": make_bars([10, 11])})
    cache = PriceCache(
        store, source, today=lambda: date(2026, 10, 2), now=lambda: utc("2026-10-02 23:00")
    )
    cache.update("AAPL", date(2014, 1, 1))
    assert source.calls == []  # same date, no fresh_after: the old rule
    cache.update("AAPL", date(2014, 1, 1), fresh_after=utc("2026-10-02 21:00"))
    assert len(source.calls) == 1  # no fetch time counts as stale
    store.close()
