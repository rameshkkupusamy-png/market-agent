import pytest

from helpers import DASHBOARD_DAYS, dashboard_db
from market_agent.dashboard.paper import (
    ORDER_COLUMNS,
    PERFORMANCE_COLUMNS,
    POSITION_COLUMNS,
    TRADE_COLUMNS,
    closed_trades,
    equity_curves,
    latest_portfolios,
    open_positions,
    performance,
    today,
)
from market_agent.settings import Settings
from market_agent.store import Store

S = Settings()
D1, D2 = DASHBOARD_DAYS[-2], DASHBOARD_DAYS[-1]


@pytest.fixture
def store(tmp_path):
    s = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    yield s
    s.close()


@pytest.fixture
def empty(tmp_path):
    Store(tmp_path / "empty.db").close()
    s = Store(tmp_path / "empty.db", read_only=True)
    yield s
    s.close()


def test_equity_curves_against_the_benchmark(store):
    curves = equity_curves(store, S)
    assert list(curves.columns) == ["rules-only", "rules+ai", "SPY"]
    assert list(curves.index) == [D1, D2]
    assert list(curves["rules-only"]) == [9_900.0, 10_090.0]
    assert curves.at[D1, "SPY"] == 10_000.0
    assert curves.at[D2, "SPY"] == pytest.approx(10_000 * 419.5 / 419)


def test_performance(store):
    table = performance(store, S)
    assert list(table.columns) == PERFORMANCE_COLUMNS
    assert list(table.index) == ["rules-only", "rules+ai", "SPY"]
    rules = table.loc["rules-only"]
    assert rules["total_return"] == pytest.approx(0.0192)
    assert (rules["trades"], rules["win_rate"]) == (1, 1.0)
    assert rules["avg_win"] == pytest.approx(round(98 / 1001, 4))
    assert table.at["SPY", "total_return"] == pytest.approx(0.0012)
    assert table.at["SPY", "trades"] != table.at["SPY", "trades"]  # NaN: no trades


def test_positions_and_trades(store):
    positions = open_positions(store)
    assert list(positions.columns) == POSITION_COLUMNS
    [aaa] = positions.to_dict("records")
    assert (aaa["portfolio"], aaa["ticker"], aaa["shares"], aaa["days_held"]) == (
        "rules-only",
        "AAA",
        10,
        2,
    )
    assert aaa["unrealised"] == pytest.approx((139.0 - 137.137) * 10)
    trades = closed_trades(store)
    assert list(trades.columns) == TRADE_COLUMNS
    [bbb] = trades.to_dict("records")
    assert (bbb["ticker"], bbb["exit_reason"], bbb["pnl"]) == ("BBB", "target", 98.0)
    assert bbb["return"] == pytest.approx(98 / (50 * 20))


def test_today(store):
    view = today(store)
    assert (view.day, view.status, view.sent) == (D2, "traded", True)
    assert view.report.startswith(f"Market agent, {D2:%Y-%m-%d}")
    assert view.alerts == ["rules+ai: circuit breaker on"]
    assert list(view.orders.columns) == ORDER_COLUMNS
    assert view.orders.to_dict("records") == [
        {
            "portfolio": "rules-only",
            "ticker": "CCC",
            "side": "buy",
            "shares": 5,
            "reason": "breakout",
        }
    ]
    assert list(view.reviews["ticker"]) == ["CCC"]
    earlier = today(store, D1)
    assert (earlier.day, earlier.sent, earlier.report) == (D1, False, "Market agent, report one")


def test_before_the_first_run(empty):
    assert latest_portfolios(empty) == {}
    assert equity_curves(empty, S).empty
    assert performance(empty, S).empty
    assert list(open_positions(empty).columns) == POSITION_COLUMNS
    assert closed_trades(empty).empty
    assert today(empty) is None
