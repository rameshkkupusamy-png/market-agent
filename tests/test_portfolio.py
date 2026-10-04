import pandas as pd

from market_agent.portfolio import (
    Order,
    Portfolio,
    Position,
    Trade,
    portfolio_from_json,
    portfolio_to_json,
)


def test_portfolio_round_trips_through_json():
    day = pd.Timestamp("2024-03-01")
    p = Portfolio(
        "rules+ai",
        8_500.5,
        peak=10_200.0,
        halted=True,
        breaker_tripped=True,
        events=["2024-03-01: circuit breaker on"],
    )
    p.positions["AAA"] = Position("AAA", "Tech", 12.5, day, 100.1, 96.0, 108.0, 101.0, 3, day)
    p.orders.append(Order("BBB", "buy", 7, "breakout", day, 1.5, "Energy", ref_close=40.0))
    p.trades.append(
        Trade("CCC", "Tech", day, 10.0, day + pd.Timedelta(days=3), 11.0, 5, "target", 3.0)
    )
    p.equity_history.append((day, 9_900.0))
    copy = portfolio_from_json(portfolio_to_json(p))
    assert copy == p
    assert isinstance(copy.positions["AAA"].entry_day, pd.Timestamp)
    assert isinstance(copy.equity_history[0], tuple)
