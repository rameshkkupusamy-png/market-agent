"""agent fetch | build-sectors | backtest --period tuning|test|full"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from market_agent.backtest import BacktestResult, run_backtest
from market_agent.data.cache import EarningsCache, PriceCache
from market_agent.data.yahoo import YahooEarnings, YahooPrices, YahooSectors
from market_agent.earnings import EarningsCalendar
from market_agent.guard import GuardError, check_test_period
from market_agent.panel import Panel
from market_agent.settings import Settings, SettingsError, load_settings
from market_agent.store import Store
from market_agent.universe import Universe, load_sectors, write_sectors

PERIODS = ("tuning", "test", "full")


# Factories replaced in tests.
def price_source():
    return YahooPrices()


def earnings_source():
    return YahooEarnings()


def sector_source():
    return YahooSectors()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Swing-trading analyst")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="download prices and earnings dates for the universe")
    commands.add_parser("build-sectors", help="write data/sectors.csv from Yahoo")
    backtest = commands.add_parser("backtest", help="run the backtest from cached data")
    backtest.add_argument("--period", choices=PERIODS, default="tuning")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    try:
        settings = load_settings(args.config)
    except SettingsError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 1
    store = Store(settings.data.db_path)
    try:
        if args.command == "fetch":
            return fetch(settings, store)
        if args.command == "build-sectors":
            return build_sectors(settings, store)
        return backtest_command(settings, store, args.period)
    finally:
        store.close()


def universe_tickers(settings: Settings) -> list[str]:
    universe = Universe.from_csv(Path(settings.data.membership_csv))
    start = pd.Timestamp(settings.data.history_start)
    return sorted(universe.tickers_between(start, pd.Timestamp(date.today())))


def fetch(settings: Settings, store: Store) -> int:
    prices = PriceCache(store, price_source())
    earnings = EarningsCache(store, earnings_source())
    tickers = [*universe_tickers(settings), settings.data.benchmark]
    for index, ticker in enumerate(tickers, 1):
        if prices.update(ticker, settings.data.history_start) and ticker != settings.data.benchmark:
            earnings.update(ticker)
        if index % 50 == 0:
            print(f"  {index} of {len(tickers)} tickers")
    missing = store.missing_tickers()
    shown = ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")
    print(
        f"Prices: {len(store.tickers_with_prices())} tickers with data, "
        f"{len(missing)} without ({shown})"
    )
    return 0


def build_sectors(settings: Settings, store: Store) -> int:
    source = sector_source()
    sectors = {ticker: source.sector(ticker) for ticker in store.tickers_with_prices()}
    sectors.pop(settings.data.benchmark, None)
    write_sectors(Path(settings.data.sectors_csv), sectors)
    unknown = sum(1 for s in sectors.values() if s == "Unknown")
    print(f"Sectors written for {len(sectors)} tickers ({unknown} unknown)")
    return 0


def period_dates(
    period: str, settings: Settings, latest: pd.Timestamp
) -> tuple[pd.Timestamp, pd.Timestamp]:
    b = settings.backtest
    if period == "tuning":
        return pd.Timestamp(b.start), pd.Timestamp(b.tuning_end)
    if period == "test":
        return pd.Timestamp(b.test_start), latest
    return pd.Timestamp(b.start), latest


def backtest_command(settings: Settings, store: Store, period: str) -> int:
    benchmark = store.load_prices(settings.data.benchmark)
    if benchmark is None:
        print("No cached data. Run `agent fetch` first.", file=sys.stderr)
        return 1
    warnings: list[str] = []
    if period == "test":
        try:
            warnings = check_test_period(store, settings.fingerprint())
        except GuardError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    frames = {t: f for t in store.tickers_with_prices() if (f := store.load_prices(t)) is not None}
    days = benchmark.index
    panel = Panel(frames, days, settings.strategy, settings.data.max_daily_jump)
    universe = Universe.from_csv(Path(settings.data.membership_csv))
    earnings = EarningsCalendar(store.load_earnings(), list(days))
    sectors = load_sectors(Path(settings.data.sectors_csv))
    start, end = period_dates(period, settings, days[-1])
    result = run_backtest(
        panel, universe, earnings, sectors, benchmark["close"], settings, start, end
    )
    store.save_backtest(
        period,
        result.start,
        result.end,
        settings.fingerprint(),
        {"strategy": asdict(settings.strategy), "risk": asdict(settings.risk)},
        result.metrics,
        result.benchmark,
        result.notes,
        result.trades,
        result.equity,
    )
    print_result(period, result)
    for warning in warnings:
        print(f"Warning: {warning}")
    return 0


def _pct(value: float) -> str:
    return f"{100 * value:+.1f}%"


def print_result(period: str, r: BacktestResult) -> None:
    m, b, n = r.metrics, r.benchmark, r.notes
    print(f"Backtest ({period}) {r.start:%Y-%m-%d} to {r.end:%Y-%m-%d}")
    print(f"{'':16}{'Strategy':>12}{'SPY':>12}")
    for key, label in (
        ("total_return", "Total return"),
        ("cagr", "Per year"),
        ("max_drawdown", "Worst fall"),
    ):
        print(f"{label:16}{_pct(m[key]):>12}{_pct(b[key]):>12}")
    print(
        f"Trades {m['trades']}, win rate {100 * m['win_rate']:.0f}%, "
        f"average win {_pct(m['avg_win'])}, average loss {_pct(m['avg_loss'])}, "
        f"invested {100 * m['exposure']:.0f}% of days, {r.open_positions} still open"
    )
    print(
        f"Universe members without data: {len(n['tickers_without_data'])} of "
        f"{n['tickers_in_universe']} (results are biased upwards by roughly that share)"
    )
    print(
        f"Signals taken without earnings dates: {n['signals_without_earnings_data']} of "
        f"{n['signals']}; bad price days skipped: {n['excluded_bad_days']}"
    )
    for event in n["circuit_breaker_events"]:
        print(f"Circuit breaker: {event}")
