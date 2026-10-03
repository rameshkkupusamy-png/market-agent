import pandas as pd
import pytest

from helpers import make_bars, settings_with
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
    m = compute_metrics(equity, trades, exposure=0.5, commission=1.0)
    assert m["total_return"] == 0.1
    assert m["max_drawdown"] == -0.25
    assert m["trades"] == 2
    assert m["win_rate"] == 0.5
    assert m["avg_win"] == round(8.0 / 101.0, 4)  # net pnl over entry cost incl. commission
    assert m["avg_loss"] == round(-7.0 / 101.0, 4)
    assert m["exposure"] == 0.5


def test_win_and_loss_use_net_pnl():
    day = pd.Timestamp("2024-01-01")
    equity = pd.Series([100.0, 100.0], index=pd.bdate_range(day, periods=2))
    # price rose 0.1%, but commissions make the trade a loss: it is averaged as a loss
    trade = Trade("A", "X", day, 100.0, day, 100.1, 10, "time", 1.0 - 2.0)
    m = compute_metrics(equity, [trade], commission=1.0)
    assert m["win_rate"] == 0.0
    assert m["avg_win"] == 0.0
    assert m["avg_loss"] == round(-1.0 / 1001.0, 4)


def test_bad_days_counted_only_for_members_in_the_period():
    n = 320
    closes = [50 + 0.1 * i for i in range(n)]
    closes[10] = closes[10] * 3  # warm-up spike: outside the period
    closes[260] = closes[260] * 3  # inside the period
    aaa = make_bars(closes)
    other = make_bars(closes)  # not a member
    spy = make_bars([400 + 0.2 * i for i in range(n)])
    days = spy.index
    panel = Panel({"AAA": aaa, "OTHER": other, "SPY": spy}, days, S.strategy, 0.4)
    universe = Universe([(days[0], frozenset({"AAA"}))])
    earnings = EarningsCalendar({}, list(days))
    result = run_backtest(panel, universe, earnings, {}, spy["close"], S, days[200], days[-1])
    assert panel.excluded["AAA"] == [days[10], days[11], days[260], days[261]]
    assert len(panel.excluded["OTHER"]) == 4
    assert result.notes["excluded_bad_days"] == 2  # the jump up and back down on days 260-261


def test_circuit_breaker_is_recorded_but_does_not_stop_trading():
    panel, universe, earnings, spy, days = scenario()
    settings = settings_with(risk={"breaker_drawdown": 0.0})  # trips on the first day
    result = run_backtest(panel, universe, earnings, {}, spy, settings, days[200], days[-1])
    assert result.notes["circuit_breaker_events"][0].startswith(f"{days[200]:%Y-%m-%d}: ")
    assert [t.ticker for t in result.trades] == ["AAA"]
