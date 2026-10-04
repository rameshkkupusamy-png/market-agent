"""One trading day for one portfolio: the same steps in the backtest and paper trading."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pandas as pd

from market_agent.broker import Simulator
from market_agent.panel import Panel
from market_agent.portfolio import Portfolio
from market_agent.risk import plan_entries
from market_agent.settings import Settings
from market_agent.strategy import Candidate


def trade_day(
    sim: Simulator,
    portfolio: Portfolio,
    day: pd.Timestamp,
    panel: Panel,
    last_day: Callable[[str], pd.Timestamp | None],
    candidates: Sequence[Candidate],
    sectors: Mapping[str, str],
    settings: Settings,
) -> float:
    """Fill yesterday's orders, run exits, mark to market, then place orders for tomorrow."""
    sim.open(portfolio, day, panel.bar)
    sim.intraday(portfolio, day, panel.bar)
    equity = sim.close(portfolio, day, panel.bar, last_day)
    plan_entries(portfolio, candidates, equity, sectors, settings.risk, settings.strategy, day)
    return equity
