import pandas as pd

from helpers import make_bars
from market_agent.checks import check_day
from market_agent.panel import Panel
from market_agent.settings import StrategySettings

SESSIONS = pd.bdate_range("2024-01-01", periods=30)  # last session 2024-02-09


def bars():
    return make_bars([50 + 0.1 * i for i in range(30)], start="2024-01-01")


def check(missing=(), gone=(), spy=True, jump=()):
    frames = {f"T{i:02d}": bars() for i in range(25)}
    for t in missing:
        frames[t] = frames[t].drop(index=SESSIONS[-1])
    for t in gone:
        frames[t] = frames[t].iloc[:20]
    for t in jump:
        frames[t].loc[SESSIONS[-1], "close"] *= 2
    frames["SPY"] = bars() if spy else bars().iloc[:-1]
    panel = Panel(frames, SESSIONS, StrategySettings(), 0.40)
    members = {t for t in frames if t != "SPY"}
    return check_day(panel, members, SESSIONS[-1], list(SESSIONS), "SPY", 0.05, 5)


def test_complete_day_is_ok():
    result = check()
    assert (result.ok, result.eligible, result.excluded, result.reason) == (True, 25, [], None)


def test_a_few_missing_shares_are_left_out():
    result = check(missing=["T03"])  # 1 of 25 = 4%
    assert result.ok and result.excluded == ["T03"]


def test_too_many_missing_shares_stop_trading():
    result = check(missing=["T03", "T07"])  # 8%
    assert not result.ok
    assert result.reason == "2 of 25 shares have no usable price for 2024-02-09"


def test_missing_benchmark_stops_trading():
    result = check(spy=False)
    assert not result.ok and result.reason == "no SPY price for 2024-02-09"


def test_long_gone_shares_are_not_counted():
    result = check(gone=["T01", "T02"])
    assert result.ok and result.eligible == 23


def test_bad_jump_counts_as_missing():
    assert check(jump=["T05"]).excluded == ["T05"]
