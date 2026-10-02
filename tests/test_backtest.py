import pandas as pd
import pytest

from helpers import make_bars
from market_agent.backtest import compute_metrics, run_backtest
from market_agent.data.sources import EarningsHistory
from market_agent.earnings import EarningsCalendar
from market_agent.panel import Panel
from market_agent.portfolio import Trade
from market_agent.settings import Settings
from market_agent.universe import Universe

S = Settings()


def scenario(earnings_covered=True, extra_members=()):
    """AAA rises steadily with a volume spike on day 250, then keeps rising to its target."""
    n = 320
    closes = [50 + 0.1 * i for i in range(250)] + [75 + 0.6 * i for i in range(n - 250)]
    volumes = [1e6] * 250 + [3e6] + [1e6] * (n - 251)
    aaa = make_bars(closes, volumes)
    spy = make_bars([400 + 0.2 * i for i in range(n)])
    days = spy.index
    panel = Panel({"AAA": aaa, "SPY": spy}, days, S.strategy, S.data.max_daily_jump)
    universe = Universe([(days[0], frozenset({"AAA", *extra_members}))])
    coverage = days[0] if earnings_covered else None
    earnings = EarningsCalendar({"AAA": EarningsHistory([], coverage)}, list(days))
    return panel, universe, earnings, spy["close"], days


def run(**kwargs):
    panel, universe, earnings, spy, days = scenario(**kwargs)
    return run_backtest(panel, universe, earnings, {}, spy, S, days[200], days[-1])


def test_a_breakout_trade_hits_its_target():
    result = run()
    [trade] = [t for t in result.trades if t.ticker == "AAA"]
    assert trade.exit_reason in ("target", "target (gap)")
    assert trade.pnl > 0
    assert result.equity.iloc[0] == S.risk.starting_cash
    assert result.metrics["trades"] >= 1
    assert result.benchmark["total_return"] > 0
    assert set(result.benchmark) == {"total_return", "cagr", "max_drawdown"}


def test_counts_signals_without_earnings_data():
    result = run(earnings_covered=False)
    assert result.notes["signals"] >= 1
    assert result.notes["signals_without_earnings_data"] == result.notes["signals"]


def test_reports_tickers_without_data():
    result = run(extra_members=("GONE",))
    assert result.notes["tickers_without_data"] == ["GONE"]
    assert result.notes["tickers_in_universe"] == 2


def test_no_days_in_period_raises():
    panel, universe, earnings, spy, days = scenario()
    with pytest.raises(ValueError, match="No trading days"):
        run_backtest(
            panel,
            universe,
            earnings,
            {},
            spy,
            S,
            pd.Timestamp("2030-01-01"),
            pd.Timestamp("2030-12-31"),
        )


def test_compute_metrics():
    days = pd.bdate_range("2024-01-01", periods=4)
    equity = pd.Series([100.0, 120.0, 90.0, 110.0], index=days)
    day = days[0]
    trades = [
        Trade("A", "X", day, 100.0, day, 110.0, 1, "target", 8.0),
        Trade("B", "X", day, 100.0, day, 95.0, 1, "stop", -7.0),
    ]
    m = compute_metrics(equity, trades, exposure=0.5)
    assert m["total_return"] == 0.1
    assert m["max_drawdown"] == -0.25
    assert m["trades"] == 2
    assert m["win_rate"] == 0.5
    assert m["avg_win"] == 0.1
    assert m["avg_loss"] == -0.05
    assert m["exposure"] == 0.5
