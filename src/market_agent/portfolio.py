"""Holdings, pending orders and closed trades of one simulated portfolio."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

import pandas as pd


@dataclass
class Position:
    ticker: str
    sector: str
    shares: float  # whole at entry; a dividend restatement can make it fractional
    entry_day: pd.Timestamp
    entry_price: float  # after slippage
    stop: float
    target: float
    last_close: float
    days_held: int = 0
    marked_on: pd.Timestamp | None = None  # the day last_close comes from


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    reason: str
    created: pd.Timestamp
    atr: float = 0.0
    sector: str = "Unknown"
    ref_close: float = 0.0  # the close the order was sized on (to detect restated prices)


@dataclass(frozen=True)
class Trade:
    ticker: str
    sector: str
    entry_day: pd.Timestamp
    entry_price: float
    exit_day: pd.Timestamp
    exit_price: float
    shares: float
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
    halted: bool = False  # no new positions until the owner resets it
    breaker_tripped: bool = (
        False  # this fall from the peak has been recorded; re-arms at a new peak
    )
    events: list[str] = field(default_factory=list)

    def open_sectors(self) -> dict[str, str]:
        held = {ticker: pos.sector for ticker, pos in self.positions.items()}
        pending = {o.ticker: o.sector for o in self.orders if o.side == "buy"}
        return {**held, **pending}


_DAY_FIELDS = {"entry_day", "exit_day", "created", "marked_on"}


def _plain(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    raise TypeError(f"Can't store {value!r}")


def _with_days(record: dict) -> dict:
    return {
        key: pd.Timestamp(value) if key in _DAY_FIELDS and value is not None else value
        for key, value in record.items()
    }


def portfolio_to_json(p: Portfolio) -> str:
    return json.dumps(
        {
            "name": p.name,
            "cash": p.cash,
            "peak": p.peak,
            "halted": p.halted,
            "breaker_tripped": p.breaker_tripped,
            "events": p.events,
            "positions": [asdict(pos) for pos in p.positions.values()],
            "orders": [asdict(order) for order in p.orders],
            "trades": [asdict(trade) for trade in p.trades],
            "equity_history": [[day, equity] for day, equity in p.equity_history],
        },
        default=_plain,
        sort_keys=True,
    )


def portfolio_from_json(text: str) -> Portfolio:
    data = json.loads(text)
    positions = [Position(**_with_days(record)) for record in data["positions"]]
    return Portfolio(
        name=data["name"],
        cash=data["cash"],
        positions={pos.ticker: pos for pos in positions},
        orders=[Order(**_with_days(record)) for record in data["orders"]],
        trades=[Trade(**_with_days(record)) for record in data["trades"]],
        equity_history=[(pd.Timestamp(day), equity) for day, equity in data["equity_history"]],
        peak=data["peak"],
        halted=data["halted"],
        breaker_tripped=data["breaker_tripped"],
        events=list(data["events"]),
    )
