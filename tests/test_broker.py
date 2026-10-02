import pandas as pd
import pytest

from market_agent.broker import Simulator
from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio, Position
from market_agent.settings import RiskSettings, StrategySettings

R = RiskSettings()
S = StrategySettings()
D0, D1, D2, D3 = pd.bdate_range("2024-03-01", periods=4)


def lookup(table):
    return lambda ticker, day: table.get((ticker, day))


def held(stop=96.0, target=108.0, entry_day=D0, shares=10):
    p = Portfolio("p", 9_000.0)
    p.positions["AAA"] = Position("AAA", "Tech", shares, entry_day, 100.1, stop, target, 100.0)
    return p


def test_buy_fills_at_next_open_with_costs_and_sets_stop_target():
    p = Portfolio("p", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0, sector="Tech"))
    Simulator(R, S).open(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100.5)}))
    pos = p.positions["AAA"]
    assert pos.entry_price == pytest.approx(100.1)
    assert p.cash == pytest.approx(10_000 - 10 * 100.1 - 1)
    assert (pos.stop, pos.target) == (96.0, 108.0)
    assert p.orders == []


def test_buy_reduced_to_available_cash():
    p = Portfolio("p", 505.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0))
    p.orders.append(Order("BBB", "buy", 10, "breakout", D0, atr=2.0))
    bars = {("AAA", D1): Bar(100, 101, 99, 100), ("BBB", D1): Bar(100, 101, 99, 100)}
    Simulator(R, S).open(p, D1, lookup(bars))
    assert p.positions["AAA"].shares == 5  # (505 - 1) / 100.1
    assert "BBB" not in p.positions
    assert p.cash >= 0


def test_buy_without_a_bar_is_dropped():
    p = Portfolio("p", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0))
    Simulator(R, S).open(p, D1, lookup({}))
    assert p.positions == {} and p.orders == []


@pytest.mark.parametrize(
    ("bar", "price", "reason"),
    [
        (Bar(97, 99, 95, 98), 96.0, "stop"),
        (Bar(100, 109, 99, 108.5), 108.0, "target"),
        (Bar(100, 110, 95, 100), 96.0, "stop"),  # both inside the range: assume the stop
        (Bar(94, 95, 93, 94), 94.0, "stop (gap)"),
        (Bar(110, 111, 109, 110), 110.0, "target (gap)"),
    ],
)
def test_exits(bar, price, reason):
    p = held()
    Simulator(R, S).intraday(p, D1, lookup({("AAA", D1): bar}))
    [trade] = p.trades
    assert trade.exit_reason == reason
    assert trade.exit_price == pytest.approx(price * (1 - R.slippage))
    assert trade.pnl == pytest.approx((trade.exit_price - 100.1) * 10 - 2 * R.commission)
    assert p.cash == pytest.approx(9_000 + 10 * trade.exit_price - 1)
    assert p.positions == {}


def test_gap_rule_does_not_apply_on_entry_day():
    p = held(entry_day=D1)
    Simulator(R, S).intraday(p, D1, lookup({("AAA", D1): Bar(94, 95, 93, 94)}))
    assert p.trades[0].exit_reason == "stop"


def test_time_exit_after_max_hold_days():
    p = held()
    p.positions["AAA"].days_held = S.max_hold_days - 1
    sim = Simulator(R, S)
    sim.close(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100)}), lambda t: D3)
    assert p.orders == [Order("AAA", "sell", 10, "time", D1)]
    sim.open(p, D2, lookup({("AAA", D2): Bar(102, 103, 101, 102)}))
    assert p.trades[0].exit_reason == "time"
    assert p.trades[0].exit_price == pytest.approx(102 * (1 - R.slippage))


def test_delisted_position_closes_at_last_close():
    p = held()
    p.positions["AAA"].last_close = 90.0
    Simulator(R, S).close(p, D2, lookup({}), lambda t: D1)
    [trade] = p.trades
    assert trade.exit_reason == "delisted"
    assert trade.exit_price == pytest.approx(90.0 * (1 - R.slippage))


def test_missing_day_without_delisting_keeps_the_position():
    p = held()
    Simulator(R, S).close(p, D1, lookup({}), lambda t: D3)
    assert "AAA" in p.positions


def test_equity_and_circuit_breaker():
    p = held()
    sim = Simulator(R, S)
    assert sim.close(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100)}), lambda t: D3) == 10_000
    assert p.peak == 10_000 and not p.halted
    p.cash = 7_000.0
    equity = sim.close(p, D2, lookup({("AAA", D2): Bar(50, 51, 49, 50)}), lambda t: D3)
    assert equity == 7_500
    assert p.halted
    assert p.events == ["2024-03-05: circuit breaker on, equity 7,500 is 25.0% below peak 10,000"]
    assert p.equity_history == [(D1, 10_000.0), (D2, 7_500.0)]
