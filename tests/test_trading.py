from types import SimpleNamespace

import pandas as pd

from market_agent.broker import Simulator
from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio
from market_agent.settings import Settings
from market_agent.strategy import Candidate
from market_agent.trading import trade_day

S = Settings()
D0, D1 = pd.bdate_range("2024-03-01", periods=2)


def test_trade_day_fills_marks_and_plans_in_order():
    p = Portfolio("rules-only", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0, ref_close=100.0))
    bars = {("AAA", D1): Bar(100, 101, 99, 100.5)}
    panel = SimpleNamespace(bar=lambda ticker, day: bars.get((ticker, day)))
    bbb = Candidate("BBB", D1, 50.0, 1.0, 0.2, 2.0, True)
    sim = Simulator(S.risk, S.strategy)
    equity = trade_day(sim, p, D1, panel, lambda t: None, [bbb], {}, S)
    assert "AAA" in p.positions
    assert [o.ticker for o in p.orders] == ["BBB"]
    assert p.equity_history == [(D1, equity)]
