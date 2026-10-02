"""Holdings, pending orders and closed trades of one simulated portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


@dataclass
class Position:
    ticker: str
    sector: str
    shares: int
    entry_day: pd.Timestamp
    entry_price: float  # after slippage
    stop: float
    target: float
    last_close: float
    days_held: int = 0


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    reason: str
    created: pd.Timestamp
    atr: float = 0.0
    sector: str = "Unknown"


@dataclass(frozen=True)
class Trade:
    ticker: str
    sector: str
    entry_day: pd.Timestamp
    entry_price: float
    exit_day: pd.Timestamp
    exit_price: float
    shares: int
    exit_reason: str
    pnl: float  # after both commissions


@dataclass
class Portfolio:
    name: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    equity_history: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    peak: float = 0.0
    halted: bool = False
    events: list[str] = field(default_factory=list)

    def open_sectors(self) -> dict[str, str]:
        held = {ticker: pos.sector for ticker, pos in self.positions.items()}
        pending = {o.ticker: o.sector for o in self.orders if o.side == "buy"}
        return {**held, **pending}
