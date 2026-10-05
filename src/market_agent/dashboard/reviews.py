"""AI review page data: every review, and how the skipped candidates would have done."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from market_agent.broker import Simulator
from market_agent.indicators import add_indicators
from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio
from market_agent.settings import Settings
from market_agent.store import Store

REVIEW_COLUMNS = [
    "day",
    "ticker",
    "status",
    "verdict",
    "confidence",
    "reasons",
    "risks",
    "news",
    "late",
    "model",
    "cost",
]
WHAT_IF_COLUMNS = ["day", "ticker", "status", "exit_day", "exit_reason", "return"]
WHAT_IF_SHARES = 100  # any size works: the result is a return, and cash is unlimited
HEADLINE = re.compile(r"^\[(\d+)\] (.+)$")


def headlines_used(prompt: str, indexes: list[int]) -> list[str]:
    """The numbered headline lines of a review prompt that the answer relied on."""
    numbered = {}
    for line in prompt.splitlines():
        match = HEADLINE.match(line)
        if match:
            numbered[int(match.group(1))] = match.group(2)
    return [numbered[i] for i in indexes if i in numbered]


def review_table(store: Store) -> pd.DataFrame:
    rows = [
        {
            "day": r["day"],
            "ticker": r["ticker"],
            "status": r["status"],
            "verdict": r["verdict"],
            "confidence": r["confidence"],
            "reasons": "; ".join(r["reasons"]),
            "risks": "; ".join(r["risks"]),
            "news": "; ".join(headlines_used(r["prompt"] or "", r["news_used"])),
            "late": r["late"],
            "model": r["model"],
            "cost": r["cost"],
        }
        for r in store.all_reviews()
    ]
    return pd.DataFrame(rows, columns=REVIEW_COLUMNS)


def _simulate_skip(
    store: Store, settings: Settings, ticker: str, day: pd.Timestamp
) -> dict[str, Any]:
    """The rules-only trade the skip prevented: bought at the next open, same exits."""
    result: dict[str, Any] = {
        "day": day,
        "ticker": ticker,
        "status": "no data",
        "exit_day": None,
        "exit_reason": "",
        "return": float("nan"),
    }
    bars = store.load_prices(ticker)
    if bars is None or day not in bars.index:
        return result
    atr = add_indicators(bars, settings.strategy).at[day, "atr"]
    if not atr > 0:
        return result
    later = bars.index[bars.index > day]
    if len(later) == 0:
        return {**result, "status": "not filled yet"}

    def bar(_ticker: str, when: pd.Timestamp) -> Bar | None:
        if when not in bars.index:
            return None
        row = bars.loc[when]
        return Bar(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

    sim = Simulator(settings.risk, settings.strategy)
    portfolio = Portfolio("what-if", 1e12)
    portfolio.orders.append(Order(ticker, "buy", WHAT_IF_SHARES, "breakout", day, float(atr)))
    for when in later:
        sim.open(portfolio, when, bar)
        sim.intraday(portfolio, when, bar)
        sim.close(portfolio, when, bar, lambda _t: None)
        if portfolio.trades:
            trade = portfolio.trades[0]
            return {
                **result,
                "status": "closed",
                "exit_day": trade.exit_day,
                "exit_reason": trade.exit_reason,
                "return": trade.pnl / (trade.entry_price * trade.shares),
            }
    position = portfolio.positions.get(ticker)
    if position is None:
        return {**result, "status": "not filled yet"}
    return {**result, "status": "open", "return": position.last_close / position.entry_price - 1}


def what_if(store: Store, settings: Settings) -> pd.DataFrame:
    rows = [
        _simulate_skip(store, settings, r["ticker"], r["day"])
        for r in store.all_reviews()
        if r["verdict"] == "skip"
    ]
    return pd.DataFrame(rows, columns=WHAT_IF_COLUMNS)
