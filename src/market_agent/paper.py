"""Paper-trading rules that the backtest does not need (spec sections 5, 8 and 10)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import pandas as pd

from market_agent.broker import BarLookup
from market_agent.panel import Panel
from market_agent.portfolio import Portfolio

PORTFOLIOS = ("rules-only", "rules+ai")
RESTATED = 1e-4  # relative change in a stored close that counts as a split or dividend


def restate(portfolio: Portfolio, bar: BarLookup) -> list[str]:
    """Rescale what was stored in the old price scale after Yahoo restated a history.

    Shares are divided by the factor so each position keeps its value and profit.
    """
    notes = []
    for ticker, pos in portfolio.positions.items():
        b = bar(ticker, pos.marked_on) if pos.marked_on is not None else None
        if b is None or pos.last_close <= 0:
            continue
        factor = b.close / pos.last_close
        if abs(factor - 1) <= RESTATED:
            continue
        pos.entry_price *= factor
        pos.stop *= factor
        pos.target *= factor
        pos.last_close *= factor
        pos.shares /= factor
        notes.append(
            f"{ticker}: prices restated ×{factor:.4f} (split or dividend); position adjusted"
        )
    orders = []
    for order in portfolio.orders:
        b = bar(order.ticker, order.created) if order.side == "buy" else None
        if b is not None and order.ref_close > 0:
            factor = b.close / order.ref_close
            if abs(factor - 1) > RESTATED:
                order = replace(
                    order,
                    shares=math.floor(order.shares / factor),
                    atr=order.atr * factor,
                    ref_close=b.close,
                )
                notes.append(
                    f"{order.ticker}: prices restated ×{factor:.4f} (split or dividend); "
                    "order adjusted"
                )
        orders.append(order)
    portfolio.orders = orders
    return notes


def gone_lookup(
    panel: Panel, sessions: list[pd.Timestamp], n: int
) -> Callable[[str], pd.Timestamp | None]:
    """For Simulator.close: a held share counts as delisted after n sessions without a price.

    Returns its last price day then, else None. Uses data up to the processed day only.
    """
    if len(sessions) < n:
        return lambda ticker: None
    day, window_start = sessions[-1], sessions[-n]

    def last_day(ticker: str) -> pd.Timestamp | None:
        end = panel.last_bar_until(ticker, day)
        return end if end is not None and end < window_start else None

    return last_day
