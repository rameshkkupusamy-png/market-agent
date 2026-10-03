"""Replays history day by day through the same strategy, risk and simulator as paper trading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from statistics import mean
from typing import Any

import pandas as pd

from market_agent.broker import Simulator
from market_agent.earnings import EarningsCalendar
from market_agent.panel import Panel
from market_agent.portfolio import Portfolio, Trade
from market_agent.risk import plan_entries
from market_agent.settings import Settings
from market_agent.strategy import screen
from market_agent.universe import Universe


@dataclass
class BacktestResult:
    start: pd.Timestamp
    end: pd.Timestamp
    equity: pd.Series
    trades: list[Trade]
    metrics: dict[str, Any]
    benchmark: dict[str, Any]
    notes: dict[str, Any]
    open_positions: int


def compute_metrics(
    equity: pd.Series,
    trades: list[Trade] | None,
    exposure: float | None = None,
    commission: float = 0.0,
) -> dict[str, Any]:
    total = equity.iloc[-1] / equity.iloc[0] - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0
    drawdown = (equity / equity.cummax() - 1).min()
    metrics: dict[str, Any] = {
        "total_return": round(float(total), 4),
        "cagr": round(float(cagr), 4),
        "max_drawdown": round(float(drawdown), 4),
    }
    if trades is not None:
        # net pnl as a share of the entry cost (shares at the slipped price plus commission)
        returns = [t.pnl / (t.entry_price * t.shares + commission) for t in trades]
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        metrics.update(
            trades=len(trades),
            win_rate=round(len(wins) / len(trades), 4) if trades else 0.0,
            avg_win=round(mean(wins), 4) if wins else 0.0,
            avg_loss=round(mean(losses), 4) if losses else 0.0,
            exposure=round(float(exposure or 0.0), 4),
        )
    return metrics


def run_backtest(
    panel: Panel,
    universe: Universe,
    earnings: EarningsCalendar,
    sectors: Mapping[str, str],
    benchmark_close: pd.Series,
    settings: Settings,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> BacktestResult:
    days = [day for day in panel.days if start <= day <= end]
    if not days:
        raise ValueError(f"No trading days between {start:%Y-%m-%d} and {end:%Y-%m-%d}")
    sim = Simulator(settings.risk, settings.strategy, halt_on_breaker=False)
    portfolio = Portfolio("rules-only", settings.risk.starting_cash)
    signals = without_earnings = exposed_days = 0

    for day in days:
        sim.open(portfolio, day, panel.bar)
        sim.intraday(portfolio, day, panel.bar)
        equity = sim.close(portfolio, day, panel.bar, panel.last_day)
        if portfolio.positions:
            exposed_days += 1
        candidates = screen(
            day, panel.snapshot(day), universe.members(day), earnings, settings.strategy
        )
        signals += len(candidates)
        without_earnings += sum(not c.earnings_known for c in candidates)
        plan_entries(portfolio, candidates, equity, sectors, settings.risk, settings.strategy, day)

    equity_series = pd.Series(dict(portfolio.equity_history), name="equity")
    bench = benchmark_close.loc[days[0] : days[-1]]
    bench_equity = bench / bench.iloc[0] * settings.risk.starting_cash
    members = universe.tickers_between(days[0], days[-1])
    bad_days = sum(
        1
        for ticker, bad in panel.excluded.items()
        if ticker in members and ticker != settings.data.benchmark
        for day in bad
        if days[0] <= day <= days[-1]
    )
    return BacktestResult(
        start=days[0],
        end=days[-1],
        equity=equity_series,
        trades=list(portfolio.trades),
        metrics=compute_metrics(
            equity_series, portfolio.trades, exposed_days / len(days), settings.risk.commission
        ),
        benchmark=compute_metrics(bench_equity, None),
        notes={
            "signals": signals,
            "signals_without_earnings_data": without_earnings,
            "tickers_in_universe": len(members),
            "tickers_without_data": sorted(t for t in members if t not in panel.tickers),
            "excluded_bad_days": bad_days,
            "circuit_breaker_events": list(portfolio.events),
        },
        open_positions=len(portfolio.positions),
    )
