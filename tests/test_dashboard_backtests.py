import pytest

from helpers import DASHBOARD_DAYS, dashboard_db
from market_agent.dashboard.backtests import (
    RUN_COLUMNS,
    backtest_curve,
    backtest_runs,
    backtest_settings,
)
from market_agent.settings import Settings
from market_agent.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    yield s
    s.close()


def test_backtest_runs(store):
    runs = backtest_runs(store)
    assert list(runs.columns) == RUN_COLUMNS
    [run] = runs.to_dict("records")
    assert (run["id"], run["period"], run["fingerprint"]) == (1, "tuning", "abc123def456")
    assert (run["total_return"], run["spy_total_return"], run["trades"]) == (0.039, 0.0488, 1)


def test_backtest_curve_against_the_benchmark(store):
    curve = backtest_curve(store, Settings(), 1)
    assert list(curve.columns) == ["strategy", "SPY"]
    assert list(curve.index) == list(DASHBOARD_DAYS)
    assert curve["SPY"].iloc[0] == 10_000.0
    assert curve["SPY"].iloc[-1] == pytest.approx(10_000 * 419.5 / 400)


def test_backtest_settings(store):
    assert backtest_settings(store, 1) == {
        "strategy": {"volume_ratio": 1.5},
        "risk": {"starting_cash": 10000.0},
    }
    assert backtest_settings(store, 99) == {}


def test_no_backtests_yet(tmp_path):
    Store(tmp_path / "empty.db").close()
    store = Store(tmp_path / "empty.db", read_only=True)
    assert backtest_runs(store).empty
    assert backtest_curve(store, Settings(), 1).empty
    store.close()
