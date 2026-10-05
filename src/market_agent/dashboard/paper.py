"""Overview, Today and Positions pages: the paper portfolios as saved by the daily run."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from market_agent.backtest import compute_metrics
from market_agent.dashboard.reviews import REVIEW_COLUMNS, review_table
from market_agent.paper import PORTFOLIOS
from market_agent.portfolio import Portfolio, portfolio_from_json
from market_agent.settings import Settings
from market_agent.store import Store

PERFORMANCE_COLUMNS = ["total_return", "max_drawdown", "trades", "win_rate", "avg_win", "avg_loss"]
POSITION_COLUMNS = [
    "portfolio",
    "ticker",
    "sector",
    "shares",
    "entry_day",
    "entry_price",
    "stop",
    "target",
    "last_close",
    "days_held",
    "unrealised",
]
TRADE_COLUMNS = [
    "portfolio",
    "ticker",
    "sector",
    "entry_day",
    "entry_price",
    "exit_day",
    "exit_price",
    "shares",
    "exit_reason",
    "pnl",
    "return",
]
ORDER_COLUMNS = ["portfolio", "ticker", "side", "shares", "reason"]


@dataclass(frozen=True)
class TodayView:
    day: pd.Timestamp
    status: str
    report: str
    sent: bool
    alerts: list[str]
    reviews: pd.DataFrame
    orders: pd.DataFrame


def _portfolios_on(store: Store, day: pd.Timestamp) -> dict[str, Portfolio]:
    states = store.paper_states(day)
    return {name: portfolio_from_json(states[name]) for name in PORTFOLIOS if name in states}


def latest_portfolios(store: Store) -> dict[str, Portfolio]:
    day = store.latest_paper_day()
    return _portfolios_on(store, day) if day is not None else {}


def _equity(portfolio: Portfolio) -> pd.Series:
    days, values = (
        zip(*portfolio.equity_history, strict=True) if portfolio.equity_history else ((), ())
    )
    return pd.Series(values, index=pd.DatetimeIndex(days), name=portfolio.name, dtype=float)


def equity_curves(store: Store, settings: Settings) -> pd.DataFrame:
    portfolios = latest_portfolios(store)
    if not portfolios:
        return pd.DataFrame()
    curves = pd.concat([_equity(p) for p in portfolios.values()], axis=1)
    benchmark = settings.data.benchmark
    prices = store.load_prices(benchmark)
    if prices is not None:
        close = prices["close"].reindex(curves.index)
        if close.notna().any():
            first = close.dropna().iloc[0]
            curves[benchmark] = close / first * settings.risk.starting_cash
    return curves


def performance(store: Store, settings: Settings) -> pd.DataFrame:
    curves = equity_curves(store, settings)
    if curves.empty:
        return pd.DataFrame(columns=PERFORMANCE_COLUMNS)
    rows = {}
    for name, portfolio in latest_portfolios(store).items():
        metrics = compute_metrics(
            curves[name].dropna(), portfolio.trades, commission=settings.risk.commission
        )
        rows[name] = {column: metrics.get(column) for column in PERFORMANCE_COLUMNS}
    benchmark = settings.data.benchmark
    if benchmark in curves:
        metrics = compute_metrics(curves[benchmark].dropna(), None)
        rows[benchmark] = {column: metrics.get(column) for column in PERFORMANCE_COLUMNS}
    return pd.DataFrame.from_dict(rows, orient="index", columns=PERFORMANCE_COLUMNS).astype(float)


def open_positions(store: Store) -> pd.DataFrame:
    rows = [
        {
            "portfolio": name,
            "ticker": pos.ticker,
            "sector": pos.sector,
            "shares": pos.shares,
            "entry_day": pos.entry_day,
            "entry_price": pos.entry_price,
            "stop": pos.stop,
            "target": pos.target,
            "last_close": pos.last_close,
            "days_held": pos.days_held,
            "unrealised": (pos.last_close - pos.entry_price) * pos.shares,
        }
        for name, portfolio in latest_portfolios(store).items()
        for pos in portfolio.positions.values()
    ]
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


def closed_trades(store: Store) -> pd.DataFrame:
    rows = [
        {
            "portfolio": name,
            "ticker": t.ticker,
            "sector": t.sector,
            "entry_day": t.entry_day,
            "entry_price": t.entry_price,
            "exit_day": t.exit_day,
            "exit_price": t.exit_price,
            "shares": t.shares,
            "exit_reason": t.exit_reason,
            "pnl": t.pnl,
            "return": t.pnl / (t.entry_price * t.shares),
        }
        for name, portfolio in latest_portfolios(store).items()
        for t in portfolio.trades
    ]
    frame = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    return frame.sort_values("exit_day", ascending=False, kind="stable").reset_index(drop=True)


def today(store: Store, day: pd.Timestamp | None = None) -> TodayView | None:
    """A saved run (the latest by default): its report, the reviews that day and the orders
    it placed for the next open. Shows the report even when Telegram failed."""
    run = store.daily_run(day) if day is not None else store.latest_daily_run()
    if run is None:
        return None
    reviews = review_table(store)
    reviews = reviews[reviews["day"] == run["day"]].reset_index(drop=True)
    orders = [
        {
            "portfolio": name,
            "ticker": o.ticker,
            "side": o.side,
            "shares": o.shares,
            "reason": o.reason,
        }
        for name, portfolio in _portfolios_on(store, run["day"]).items()
        for o in portfolio.orders
    ]
    return TodayView(
        day=run["day"],
        status=run["status"],
        report=run["report"],
        sent=run["sent"],
        alerts=run["alerts"],
        reviews=reviews if not reviews.empty else pd.DataFrame(columns=REVIEW_COLUMNS),
        orders=pd.DataFrame(orders, columns=ORDER_COLUMNS),
    )
