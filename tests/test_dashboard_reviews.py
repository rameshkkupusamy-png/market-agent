import math
from types import SimpleNamespace

import pytest

from helpers import DASHBOARD_DAYS, dashboard_db
from market_agent.dashboard.reviews import (
    REVIEW_COLUMNS,
    WHAT_IF_COLUMNS,
    headlines_used,
    review_table,
    what_if,
)
from market_agent.settings import Settings
from market_agent.store import Store

PROMPT = (
    "Candidate: AAA (Alpha), sector Tech\n\nHeadlines, newest first:\n"
    "[0] 2026-09-01 Reuters: Alpha cuts guidance\n    Weak quarter.\n"
    "[1] 2026-09-02 CNBC: Alpha hires a CFO"
)


def test_headlines_used_in_the_order_given():
    assert headlines_used(PROMPT, [1, 0]) == [
        "2026-09-02 CNBC: Alpha hires a CFO",
        "2026-09-01 Reuters: Alpha cuts guidance",
    ]


def test_headlines_used_ignores_unknown_indexes():
    assert headlines_used(PROMPT, [0, 5]) == ["2026-09-01 Reuters: Alpha cuts guidance"]
    assert headlines_used("Candidate: CCC", [0]) == []


def test_review_table(tmp_path):
    store = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    table = review_table(store)
    store.close()
    assert list(table.columns) == REVIEW_COLUMNS
    assert list(table["ticker"]) == ["CCC", "AAA"]
    aaa = table.iloc[1]
    assert (aaa["day"], aaa["verdict"], aaa["reasons"], aaa["risks"]) == (
        DASHBOARD_DAYS[-20],
        "skip",
        "Guidance cut",
        "Weak demand",
    )
    assert aaa["news"] == "2026-09-01 Reuters: Alpha cuts guidance"
    assert table["cost"].sum() == pytest.approx(0.035)


def test_review_table_when_empty(tmp_path):
    Store(tmp_path / "market.db").close()
    store = Store(tmp_path / "market.db", read_only=True)
    assert list(review_table(store).columns) == REVIEW_COLUMNS
    assert what_if(store, Settings()).empty
    store.close()


def test_what_if_follows_the_rules_trade(tmp_path):
    store = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    table = what_if(store, Settings())
    store.close()
    assert list(table.columns) == WHAT_IF_COLUMNS
    [row] = table.to_dict("records")
    assert (row["day"], row["ticker"], row["status"]) == (DASHBOARD_DAYS[-20], "AAA", "closed")
    assert row["exit_reason"] in ("target", "target (gap)")
    assert DASHBOARD_DAYS[-20] < row["exit_day"] <= DASHBOARD_DAYS[-1]
    assert row["return"] > 0


def skip(ticker):
    return SimpleNamespace(
        ticker=ticker,
        status="reviewed",
        verdict="skip",
        confidence="low",
        reasons=[],
        risks=[],
        news_used=[],
        note="",
        cost=0.01,
        model="m",
        prompt="",
        answer="{}",
    )


def test_what_if_without_later_prices(tmp_path):
    db = dashboard_db(tmp_path / "market.db")
    writer = Store(db)
    writer.save_review(DASHBOARD_DAYS[-1], skip("AAA"), late=False)  # no prices after it yet
    writer.save_review(DASHBOARD_DAYS[-5], skip("ZZZ"), late=False)  # no prices at all
    writer.close()
    store = Store(db, read_only=True)
    rows = {r["ticker"] + r["status"]: r for r in what_if(store, Settings()).to_dict("records")}
    store.close()
    assert set(rows) == {"AAAclosed", "AAAnot filled yet", "ZZZno data"}
    assert math.isnan(rows["ZZZno data"]["return"])
    assert rows["AAAnot filled yet"]["exit_reason"] == ""
