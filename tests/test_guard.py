import pandas as pd
import pytest

from market_agent.guard import GuardError, check_test_period
from market_agent.store import Store


def save(store, fingerprint):
    day = pd.Timestamp("2022-01-03")
    store.save_backtest(
        "test",
        day,
        day,
        fingerprint,
        {},
        {"total_return": 0.1},
        {},
        {},
        [],
        pd.Series([1.0], index=[day]),
    )


def test_test_period_guard(tmp_path):
    store = Store(tmp_path / "m.db")
    assert check_test_period(store, "aaa") == []
    save(store, "aaa")
    with pytest.raises(GuardError, match="already run with these exact settings"):
        check_test_period(store, "aaa")
    [warning] = check_test_period(store, "bbb")
    assert "run 1 time before with other settings" in warning
    store.close()
