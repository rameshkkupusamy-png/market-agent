import pandas as pd

from market_agent.portfolio import Order, Portfolio, Position
from market_agent.risk import check_limits, plan_entries, position_size
from market_agent.settings import RiskSettings, StrategySettings
from market_agent.strategy import Candidate

R = RiskSettings()
S = StrategySettings()
DAY = pd.Timestamp("2024-03-01")


def candidate(ticker, close=100.0, atr=2.0, score=0.1):
    return Candidate(ticker, DAY, close, atr, score, 2.0, True)


def test_size_risks_one_percent():
    # risk per share = 2 × ATR = 4; 1% of 10,000 = 100 → 25 shares (2,500 = 25% → capped)
    assert position_size(10_000, 10_000, 100.0, 2.0, R, S) == 10  # 10% cap: 1,000 / 100
    # wide stop: ATR 10 → risk/share 20 → 5 shares (500, under the cap)
    assert position_size(10_000, 10_000, 100.0, 10.0, R, S) == 5


def test_size_limited_by_cash():
    assert position_size(10_000, 301.0, 100.0, 10.0, R, S) == 2  # (301 − 1) / 100.1


def test_size_zero_when_nothing_fits():
    assert position_size(10_000, 50.0, 100.0, 2.0, R, S) == 0
    assert position_size(10_000, 10_000, 100.0, 0.0, R, S) == 0


def test_limits():
    held = {f"T{i}": "Tech" if i < 3 else f"S{i}" for i in range(9)}
    assert check_limits("T0", "Tech", held, R) == "already held"
    assert check_limits("NEW", "Tech", held, R) == "3 positions in Tech already"
    assert check_limits("NEW", "Energy", held, R) is None
    held["X"] = "Other"
    assert check_limits("NEW", "Energy", held, R) == "10 positions already open"


def test_plan_entries_respects_daily_limit_and_cash():
    p = Portfolio("rules-only", 10_000)
    candidates = [candidate(t) for t in ["A", "B", "C", "D"]]
    notes = plan_entries(p, candidates, 10_000, {}, R, S, DAY)
    assert [o.ticker for o in p.orders] == ["A", "B", "C"]
    assert all(o.side == "buy" and o.shares == 10 and o.atr == 2.0 for o in p.orders)
    assert notes == []


def test_plan_entries_counts_pending_orders_against_limits():
    p = Portfolio("rules-only", 10_000)
    sectors = {t: "Tech" for t in "ABCD"}
    p.positions["A"] = Position("A", "Tech", 10, DAY, 100.0, 96.0, 108.0, 100.0)
    p.orders.append(Order("B", "buy", 10, "breakout", DAY, 2.0, "Tech"))
    notes = plan_entries(p, [candidate("C"), candidate("D")], 10_000, sectors, R, S, DAY)
    assert [o.ticker for o in p.orders] == ["B", "C"]
    assert notes == ["D: 3 positions in Tech already"]


def test_halted_portfolio_opens_nothing():
    p = Portfolio("rules-only", 10_000)
    p.halted = True
    assert plan_entries(p, [candidate("A")], 10_000, {}, R, S, DAY) == ["circuit breaker on"]
    assert p.orders == []
