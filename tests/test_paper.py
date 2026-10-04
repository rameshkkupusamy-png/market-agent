import pandas as pd
import pytest

from helpers import make_bars
from market_agent.panel import Bar, Panel
from market_agent.paper import gone_lookup, restate
from market_agent.portfolio import Order, Portfolio, Position
from market_agent.settings import StrategySettings

D0 = pd.Timestamp("2024-03-01")


def lookup(table):
    return lambda ticker, day: table.get((ticker, day))


def held():
    p = Portfolio("rules-only", 9_000.0)
    p.positions["AAA"] = Position("AAA", "Tech", 10, D0, 100.0, 96.0, 108.0, 100.0, 2, D0)
    return p


def test_split_while_held_rescales_the_position():
    p = held()
    notes = restate(p, lookup({("AAA", D0): Bar(25, 25.5, 24.5, 25.0)}), 0.0)  # 4:1 split
    pos = p.positions["AAA"]
    assert pos.shares == 40
    assert (pos.entry_price, pos.stop, pos.target, pos.last_close) == (25.0, 24.0, 27.0, 25.0)
    assert pos.shares * pos.last_close == 1_000  # value unchanged
    assert notes == ["AAA: prices restated ×0.2500 (split or dividend); position adjusted"]


def test_tiny_differences_are_not_restatements():
    p = held()
    assert restate(p, lookup({("AAA", D0): Bar(100, 100, 100, 100.000001)}), 0.0) == []
    assert p.positions["AAA"].shares == 10


def test_pending_buy_is_rescaled():
    p = Portfolio("rules-only", 10_000.0)
    p.orders.append(Order("BBB", "buy", 10, "breakout", D0, atr=2.0, ref_close=100.0))
    notes = restate(p, lookup({("BBB", D0): Bar(25, 25, 25, 25.0)}), 0.0)
    [order] = p.orders
    assert (order.shares, order.atr, order.ref_close) == (40, 0.5, 25.0)
    assert notes == ["BBB: prices restated ×0.2500 (split or dividend); order adjusted"]


D1, D2 = pd.Timestamp("2024-03-04"), pd.Timestamp("2024-03-05")
SLIPPAGE = 0.001


def held_since(entry_open=50.0):
    """Bought at D0's open of 50 (plus slippage), last marked at D2's close of 60."""
    p = Portfolio("rules-only", 9_000.0)
    entry = entry_open * (1 + SLIPPAGE)
    p.positions["AAA"] = Position("AAA", "Tech", 10, D0, entry, 45.0, 70.0, 60.0, 3, D2)
    return p


def test_correction_of_the_latest_bar_only_is_not_a_split():
    p = held_since()
    bars = {("AAA", D0): Bar(50, 51, 49, 50.5), ("AAA", D2): Bar(60, 62, 58, 61.2)}  # +2%
    notes = restate(p, lookup(bars), SLIPPAGE)
    pos = p.positions["AAA"]
    assert (pos.shares, pos.entry_price, pos.stop, pos.target) == (10, 50 * 1.001, 45.0, 70.0)
    assert pos.last_close == 61.2
    assert notes == ["AAA: latest price corrected (60.00 → 61.20); position not rescaled"]


def test_dividend_restatement_rescales_and_keeps_value_and_profit():
    p = held_since()
    old = p.positions["AAA"]
    old_value, old_entry, old_shares = old.shares * old.last_close, old.entry_price, old.shares
    bars = {("AAA", D0): Bar(50 * 0.98, 51, 49, 50), ("AAA", D2): Bar(60, 62, 58, 60 * 0.98)}
    notes = restate(p, lookup(bars), SLIPPAGE)
    pos = p.positions["AAA"]
    assert pos.shares == pytest.approx(10 / 0.98) and pos.shares != int(pos.shares)
    assert pos.shares * pos.last_close == pytest.approx(old_value)
    for price in (40.0, 60.0, 75.0):
        assert (price * 0.98 - pos.entry_price) * pos.shares == pytest.approx(
            (price - old_entry) * old_shares
        )
    assert notes == ["AAA: prices restated ×0.9800 (split or dividend); position adjusted"]


def test_without_the_entry_bar_the_latest_close_decides():
    p = held_since()
    notes = restate(p, lookup({("AAA", D2): Bar(15, 15, 15, 15.0)}), SLIPPAGE)
    assert p.positions["AAA"].shares == 40
    assert notes == ["AAA: prices restated ×0.2500 (split or dividend); position adjusted"]


def test_held_share_counts_as_delisted_after_five_missing_sessions():
    sessions = pd.bdate_range("2024-01-01", periods=20)
    aaa = make_bars([50 + 0.1 * i for i in range(11)], start="2024-01-01")  # sessions 0-10
    panel = Panel({"AAA": aaa}, sessions, StrategySettings(), 0.40)
    assert gone_lookup(panel, list(sessions[:15]), 5)("AAA") is None  # 4 missing
    assert gone_lookup(panel, list(sessions[:16]), 5)("AAA") == sessions[10]  # 5 missing
    assert gone_lookup(panel, list(sessions[:16]), 5)("ZZZ") is None
