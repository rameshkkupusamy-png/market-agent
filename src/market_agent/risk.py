"""Position sizing and portfolio limits (spec section 5)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

import pandas as pd

from market_agent.portfolio import Order, Portfolio
from market_agent.settings import RiskSettings, StrategySettings
from market_agent.strategy import Candidate


def position_size(
    equity: float,
    cash: float,
    price: float,
    atr: float,
    risk: RiskSettings,
    strategy: StrategySettings,
) -> int:
    """Shares so that a stop-out loses risk_per_trade of equity, within the size and cash caps."""
    risk_per_share = strategy.stop_atr * atr
    if not (risk_per_share > 0 and price > 0):
        return 0
    by_risk = math.floor(risk.risk_per_trade * equity / risk_per_share)
    by_cap = math.floor(risk.max_position_pct * equity / price)
    by_cash = math.floor((cash - risk.commission) / (price * (1 + risk.slippage)))
    return max(0, min(by_risk, by_cap, by_cash))


def check_limits(
    ticker: str, sector: str, open_sectors: Mapping[str, str], risk: RiskSettings
) -> str | None:
    if ticker in open_sectors:
        return "already held"
    if len(open_sectors) >= risk.max_positions:
        return f"{risk.max_positions} positions already open"
    if Counter(open_sectors.values())[sector] >= risk.max_per_sector:
        return f"{risk.max_per_sector} positions in {sector} already"
    return None


def plan_entries(
    portfolio: Portfolio,
    candidates: Sequence[Candidate],
    equity: float,
    sectors: Mapping[str, str],
    risk: RiskSettings,
    strategy: StrategySettings,
    day: pd.Timestamp,
) -> list[str]:
    """Add buy orders for the best candidates. Returns why candidates were refused."""
    if portfolio.halted:
        return ["circuit breaker on"]
    # Buy orders are created after the close and filled at the next open, so earlier buy orders
    # have already been filled when this runs; only orders added here need cash set aside.
    # Pending buys still count in open_sectors() for the position and sector limits.
    notes = []
    committed = 0.0
    slots = strategy.max_new_per_day
    for c in candidates:
        if slots == 0:
            break
        sector = sectors.get(c.ticker, "Unknown")
        refusal = check_limits(c.ticker, sector, portfolio.open_sectors(), risk)
        if refusal:
            notes.append(f"{c.ticker}: {refusal}")
            continue
        shares = position_size(equity, portfolio.cash - committed, c.close, c.atr, risk, strategy)
        if shares == 0:
            notes.append(f"{c.ticker}: not enough cash")
            continue
        portfolio.orders.append(Order(c.ticker, "buy", shares, "breakout", day, c.atr, sector))
        committed += shares * c.close * (1 + risk.slippage) + risk.commission
        slots -= 1
    return notes
