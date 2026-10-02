"""Simulated broker shared by the backtest and paper trading (spec sections 4 and 5).

Day order: open() fills orders placed yesterday, intraday() checks stops and targets, close()
marks to market and queues time exits. Every fill pays slippage and commission.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pandas as pd

from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio, Position, Trade
from market_agent.settings import RiskSettings, StrategySettings

BarLookup = Callable[[str, pd.Timestamp], Bar | None]


class Simulator:
    def __init__(self, risk: RiskSettings, strategy: StrategySettings):
        self.risk = risk
        self.strategy = strategy

    def open(self, p: Portfolio, day: pd.Timestamp, bar: BarLookup) -> None:
        orders, p.orders = p.orders, []
        for order in orders:
            b = bar(order.ticker, day)
            if order.side == "sell":
                if order.ticker not in p.positions:
                    continue
                if b is None:
                    p.orders.append(order)  # no price today: try again tomorrow
                    continue
                self._exit(p, order.ticker, day, b.open, order.reason)
            elif b is not None:
                self._buy(p, order, day, b)

    def intraday(self, p: Portfolio, day: pd.Timestamp, bar: BarLookup) -> None:
        for ticker, pos in list(p.positions.items()):
            b = bar(ticker, day)
            if b is None:
                continue
            opened_today = pos.entry_day == day
            if not opened_today and b.open <= pos.stop:
                self._exit(p, ticker, day, b.open, "stop (gap)")
            elif not opened_today and b.open >= pos.target:
                self._exit(p, ticker, day, b.open, "target (gap)")
            elif b.low <= pos.stop:
                self._exit(p, ticker, day, pos.stop, "stop")
            elif b.high >= pos.target:
                self._exit(p, ticker, day, pos.target, "target")

    def close(
        self,
        p: Portfolio,
        day: pd.Timestamp,
        bar: BarLookup,
        last_day: Callable[[str], pd.Timestamp | None],
    ) -> float:
        selling = {o.ticker for o in p.orders if o.side == "sell"}
        for ticker, pos in list(p.positions.items()):
            b = bar(ticker, day)
            if b is None:
                end = last_day(ticker)
                if end is not None and end < day:
                    self._exit(p, ticker, day, pos.last_close, "delisted")
                continue
            pos.last_close = b.close
            pos.days_held += 1
            if pos.days_held >= self.strategy.max_hold_days and ticker not in selling:
                p.orders.append(Order(ticker, "sell", pos.shares, "time", day))
        equity = p.cash + sum(pos.shares * pos.last_close for pos in p.positions.values())
        p.equity_history.append((day, equity))
        p.peak = max(p.peak, equity)
        if not p.halted and equity <= p.peak * (1 - self.risk.breaker_drawdown):
            p.halted = True
            fall = 100 * (1 - equity / p.peak)
            p.events.append(
                f"{day:%Y-%m-%d}: circuit breaker on, equity {equity:,.0f} is {fall:.1f}% "
                f"below peak {p.peak:,.0f}"
            )
        return equity

    def _buy(self, p: Portfolio, order: Order, day: pd.Timestamp, b: Bar) -> None:
        price = b.open * (1 + self.risk.slippage)
        affordable = math.floor((p.cash - self.risk.commission) / price)
        shares = min(order.shares, affordable)
        if shares <= 0:
            return
        p.cash -= shares * price + self.risk.commission
        p.positions[order.ticker] = Position(
            ticker=order.ticker,
            sector=order.sector,
            shares=shares,
            entry_day=day,
            entry_price=price,
            stop=b.open - self.strategy.stop_atr * order.atr,
            target=b.open + self.strategy.target_atr * order.atr,
            last_close=b.open,
        )

    def _exit(
        self, p: Portfolio, ticker: str, day: pd.Timestamp, price: float, reason: str
    ) -> None:
        pos = p.positions.pop(ticker)
        fill = price * (1 - self.risk.slippage)
        p.cash += pos.shares * fill - self.risk.commission
        p.trades.append(
            Trade(
                ticker=ticker,
                sector=pos.sector,
                entry_day=pos.entry_day,
                entry_price=pos.entry_price,
                exit_day=day,
                exit_price=fill,
                shares=pos.shares,
                exit_reason=reason,
                pnl=(fill - pos.entry_price) * pos.shares - 2 * self.risk.commission,
            )
        )
