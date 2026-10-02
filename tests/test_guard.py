import pandas as pd
import pytest

from market_agent.guard import GuardError, check_test_period
from market_agent.store import Store


def save(store, fingerprint, period="test"):
    day = pd.Timestamp("2022-01-03")
    store.save_backtest(
        period,
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


def test_full_run_counts_as_a_test_period_look(tmp_path):
    store = Store(tmp_path / "m.db")
    save(store, "aaa", "full")
    with pytest.raises(GuardError, match="counts as tuning on the test period"):
        check_test_period(store, "aaa")
    [warning] = check_test_period(store, "bbb")
    assert "run 1 time before" in warning
    store.close()


def test_refusal_shows_earlier_return_as_percentage(tmp_path):
    store = Store(tmp_path / "m.db")
    save(store, "aaa")
    with pytest.raises(GuardError, match=r"\+10\.0%"):
        check_test_period(store, "aaa")
    store.close()
