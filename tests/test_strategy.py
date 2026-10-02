import pandas as pd

from helpers import make_bars
from market_agent.data.sources import EarningsHistory
from market_agent.earnings import EarningsCalendar
from market_agent.panel import Panel
from market_agent.settings import StrategySettings
from market_agent.strategy import screen

S = StrategySettings()
N = 260


def breakout(start=50.0, step=0.1, last_volume=2e6):
    """Steady rise (always a 20-day high), normal volume, then a volume spike on the last day."""
    closes = [start + step * i for i in range(N)]
    volumes = [1e6] * (N - 1) + [last_volume]
    return make_bars(closes, volumes)


def run(frames, earnings=None, members=None):
    days = next(iter(frames.values())).index
    panel = Panel(frames, days, S, 0.40)
    histories = earnings or {t: EarningsHistory([], days[0]) for t in frames}
    calendar = EarningsCalendar(histories, list(days))
    day = days[-1]
    return screen(day, panel.snapshot(day), members or set(frames), calendar, S)


def test_breakout_on_volume_is_a_candidate():
    [candidate] = run({"AAA": breakout()})
    assert candidate.ticker == "AAA"
    assert candidate.volume_ratio == 2.0
    assert candidate.close == 50 + 0.1 * (N - 1)
    assert candidate.atr > 0
    assert candidate.earnings_known is True


def test_needs_enough_volume():
    assert run({"AAA": breakout(last_volume=1.4e6)}) == []


def test_needs_an_uptrend():
    falling = make_bars([100 - 0.1 * i for i in range(N - 1)] + [100.0], [1e6] * (N - 1) + [2e6])
    assert run({"AAA": falling}) == []


def test_needs_price_and_liquidity():
    cheap = breakout(start=5.0, step=0.01)
    assert run({"AAA": cheap}) == []
    thin = make_bars([50 + 0.1 * i for i in range(N)], [1e5] * (N - 1) + [2e5])
    assert run({"AAA": thin}) == []


def test_only_universe_members():
    assert run({"AAA": breakout()}, members={"BBB"}) == []


def test_earnings_within_five_trading_days_blocks_entry():
    bars = breakout()
    days = bars.index
    soon = EarningsHistory([days[-1] + pd.offsets.BDay(3)], days[0])
    later = EarningsHistory([days[-1] + pd.offsets.BDay(8)], days[0])
    assert run({"AAA": bars}, earnings={"AAA": soon}) == []
    assert len(run({"AAA": bars}, earnings={"AAA": later})) == 1


def test_candidate_records_missing_earnings_coverage():
    [candidate] = run({"AAA": breakout()}, earnings={"AAA": EarningsHistory([], None)})
    assert candidate.earnings_known is False


def test_ranked_by_three_month_return():
    slow = breakout(step=0.05)
    fast = breakout(step=0.2)
    assert [c.ticker for c in run({"SLOW": slow, "FAST": fast})] == ["FAST", "SLOW"]


def test_reports_within_uses_trading_days():
    days = list(pd.bdate_range("2024-01-01", periods=30))
    cal = EarningsCalendar({"A": EarningsHistory([days[10]], days[0])}, days)
    assert cal.reports_within("A", days[5], 5) is True
    assert cal.reports_within("A", days[4], 5) is False
    assert cal.reports_within("A", days[10], 5) is True
    assert cal.reports_within("A", days[11], 5) is False
    assert cal.known("A", days[0]) is True
    assert cal.known("B", days[0]) is False
