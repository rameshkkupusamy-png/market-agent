"""Small builders for synthetic test data."""

from types import SimpleNamespace

import pandas as pd

from market_agent.data.sources import Headline
from market_agent.portfolio import Order, Portfolio, Position, Trade, portfolio_to_json
from market_agent.reviewer import ReviewInput
from market_agent.settings import Settings
from market_agent.store import Store


def make_bars(closes, volumes=None, start="2023-01-02", spread=0.01, traded=1.0):
    """Business-day bars: open = close, high/low = close ± spread.

    As traded, prices were `traded` times the adjusted ones (e.g. 4 before a later 4:1 split).
    """
    days = pd.bdate_range(start, periods=len(closes), name="day")
    volumes = volumes if volumes is not None else [1_000_000.0] * len(closes)
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * (1 + spread) for c in closes],
            "low": [c * (1 - spread) for c in closes],
            "close": closes,
            "volume": [float(v) for v in volumes],
            "raw_close": [c * traded for c in closes],
            "raw_volume": [float(v) / traded for v in volumes],
        },
        index=days,
    )


def settings_with(**sections):
    """Settings with some fields changed, e.g. settings_with(risk={"max_positions": 2})."""
    from dataclasses import replace

    base = Settings()
    return replace(
        base,
        **{name: replace(getattr(base, name), **values) for name, values in sections.items()},
    )


# A review candidate shared by the reviewer's unit and live tests.
HEADLINE = Headline(
    pd.Timestamp("2026-10-01 14:30", tz="UTC"),
    "Reuters",
    "Nvidia wins order",
    "A large cloud order.",
)
ITEM = ReviewInput(
    ticker="NVDA",
    company="NVIDIA Corporation",
    sector="Technology",
    day=pd.Timestamp("2026-10-02"),
    close=187.62,
    sma_fast=175.4,
    sma_slow=150.25,
    volume_ratio=1.83,
    atr=5.1,
    return_63=0.214,
    next_earnings=pd.Timestamp("2026-11-19"),
    headlines=[HEADLINE],
)


DASHBOARD_DAYS = pd.bdate_range("2026-08-03", periods=40)


def dashboard_db(path):
    """A small database for the dashboard: prices, two paper days, two reviews, one backtest.

    AAA rises 1 a day from 100 and SPY 0.5 a day from 400 over DASHBOARD_DAYS. Claude skipped
    AAA 20 sessions before the end (the "what if" trade then reaches its target) and approved
    CCC on the last day. rules-only holds 10 AAA, has one closed BBB trade and a pending CCC
    order; rules+ai holds cash only.
    """
    days = DASHBOARD_DAYS
    store = Store(path)
    spy = make_bars([400 + 0.5 * i for i in range(40)], start="2026-08-03")
    store.replace_prices("SPY", spy, "2026-09-25")
    store.replace_prices(
        "AAA", make_bars([100 + i for i in range(40)], start="2026-08-03"), "2026-09-25"
    )
    d1, d2 = days[-2], days[-1]
    position = Position("AAA", "Tech", 10, days[-3], 137.137, 133.0, 145.0, 139.0, 2, d2)
    trade = Trade("BBB", "Energy", days[-12], 50.0, days[-8], 55.0, 20, "target", 98.0)
    rules = Portfolio(
        "rules-only",
        8_600.0,
        positions={"AAA": position},
        orders=[Order("CCC", "buy", 5, "breakout", d2, 2.0, "Tech", ref_close=80.0)],
        trades=[trade],
        equity_history=[(d1, 9_900.0), (d2, 10_090.0)],
        peak=10_090.0,
    )
    ai = Portfolio(
        "rules+ai", 10_000.0, equity_history=[(d1, 10_000.0), (d2, 10_000.0)], peak=10_000.0
    )
    states = {p.name: portfolio_to_json(p) for p in (rules, ai)}
    store.save_day(d1, states, "traded", "Market agent, report one", [])
    store.save_day(
        d2,
        states,
        "traded",
        f"Market agent, {d2:%Y-%m-%d} (paper trading, simulated money)",
        ["rules+ai: circuit breaker on"],
    )
    store.mark_sent(d2)
    prompt = (
        "Candidate: AAA (Alpha), sector Tech\n\nHeadlines, newest first:\n"
        "[0] 2026-09-01 Reuters: Alpha cuts guidance\n    Weak quarter.\n"
        "[1] 2026-09-02 CNBC: Alpha hires a CFO"
    )
    skip = SimpleNamespace(
        ticker="AAA",
        status="reviewed",
        verdict="skip",
        confidence="high",
        reasons=["Guidance cut"],
        risks=["Weak demand"],
        news_used=[0, 5],
        note="",
        cost=0.02,
        model="claude-opus-5-5",
        prompt=prompt,
        answer="{}",
    )
    store.save_review(days[-20], skip, late=False)
    approve = SimpleNamespace(
        ticker="CCC",
        status="reviewed",
        verdict="approve",
        confidence="medium",
        reasons=["no relevant news"],
        risks=[],
        news_used=[],
        note="",
        cost=0.015,
        model="claude-opus-5-5",
        prompt="Candidate: CCC",
        answer="{}",
    )
    store.save_review(d2, approve, late=False)
    equity = pd.Series([10_000.0 + 10 * i for i in range(40)], index=days)
    store.save_backtest(
        "tuning",
        days[0],
        days[-1],
        "abc123def456",
        {"strategy": {"volume_ratio": 1.5}, "risk": {"starting_cash": 10000.0}},
        {
            "total_return": 0.039,
            "cagr": 0.3,
            "max_drawdown": 0.0,
            "trades": 1,
            "win_rate": 1.0,
            "avg_win": 0.1,
            "avg_loss": 0.0,
            "exposure": 0.5,
        },
        {"total_return": 0.0488, "cagr": 0.35, "max_drawdown": 0.0},
        {},
        [trade],
        equity,
    )
    store.close()
    return path
