"""agent fetch | build-sectors | backtest --period tuning|test|full"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

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
DEFAULT_CONFIG = Path("config.yaml")
ATTEMPTS = 3  # tries per ticker before it is listed as failed
BACKOFF_SECONDS = 5.0  # wait before the 2nd try; doubles before each later one

log = logging.getLogger(__name__)
pause = time.sleep  # replaced in tests


class PeriodError(Exception):
    """The requested backtest period can't be run with this config and cache."""


# Factories replaced in tests.
def price_source():
    return YahooPrices()


def earnings_source():
    return YahooEarnings()


def sector_source():
    return YahooSectors()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Swing-trading analyst")
    parser.add_argument("--config", type=Path, help=f"settings file (default {DEFAULT_CONFIG})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="download prices and earnings dates for the universe")
    commands.add_parser("build-sectors", help="write data/sectors.csv from Yahoo")
    backtest = commands.add_parser("backtest", help="run the backtest from cached data")
    backtest.add_argument("--period", choices=PERIODS, default="tuning")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load_dotenv(Path(".env"))  # the working folder only, so tests never pick up real keys

    try:
        settings = load_settings(args.config or DEFAULT_CONFIG, required=args.config is not None)
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


def _shown(tickers: list[str]) -> str:
    return ", ".join(tickers[:20]) + (" …" if len(tickers) > 20 else "")


def with_retries[T](ticker: str, action: Callable[[], T]) -> T:
    """Run one ticker's download, retrying with a growing wait; the last error propagates."""
    for attempt in range(1, ATTEMPTS):
        try:
            return action()
        except Exception as exc:
            log.warning("%s: %s (try %d of %d)", ticker, exc, attempt, ATTEMPTS)
            pause(BACKOFF_SECONDS * 2 ** (attempt - 1))
    return action()


def report_failures(failed: list[str]) -> int:
    if not failed:
        return 0
    noun = "ticker" if len(failed) == 1 else "tickers"
    print(
        f"Failed: {len(failed)} {noun} ({_shown(failed)}). Run the command again to retry them.",
        file=sys.stderr,
    )
    return 1


def fetch(settings: Settings, store: Store) -> int:
    prices = PriceCache(store, price_source())
    earnings = EarningsCache(store, earnings_source())
    tickers = [*universe_tickers(settings), settings.data.benchmark]
    failed = []
    for index, ticker in enumerate(tickers, 1):
        try:
            has_data = with_retries(
                ticker, lambda t=ticker: prices.update(t, settings.data.history_start)
            )
            if has_data and ticker != settings.data.benchmark:
                with_retries(ticker, lambda t=ticker: earnings.update(t))
        except Exception as exc:
            log.warning("%s: failed (%s)", ticker, exc)
            failed.append(ticker)
        if index % 50 == 0:
            print(f"  {index} of {len(tickers)} tickers")
    missing = store.missing_tickers()
    print(
        f"Prices: {len(store.tickers_with_prices())} tickers with data, "
        f"{len(missing)} without ({_shown(missing)})"
    )
    return report_failures(sorted(failed))


def build_sectors(settings: Settings, store: Store) -> int:
    """Keeps a ticker's earlier sector when the new lookup fails or says "Unknown"."""
    source = sector_source()
    path = Path(settings.data.sectors_csv)
    previous = load_sectors(path)
    sectors = {}
    failed = []
    for ticker in store.tickers_with_prices():
        if ticker == settings.data.benchmark:
            continue
        try:
            sector = with_retries(ticker, lambda t=ticker: source.sector(t))
        except Exception as exc:
            log.warning("%s: sector lookup failed (%s)", ticker, exc)
            failed.append(ticker)
            sector = None
        if sector in (None, "Unknown") and ticker in previous:
            sector = previous[ticker]
        if sector is not None:
            sectors[ticker] = sector
    write_sectors(path, sectors)
    unknown = sum(1 for s in sectors.values() if s == "Unknown")
    print(f"Sectors written for {len(sectors)} tickers ({unknown} unknown)")
    return report_failures(failed)


def period_dates(
    period: str, settings: Settings, latest: pd.Timestamp
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Only the guarded periods ("test", "full") may reach test_start."""
    b = settings.backtest
    if period == "tuning":
        if b.tuning_end >= b.test_start:
            raise PeriodError(
                f"backtest.tuning_end ({b.tuning_end}) must be before test_start "
                f"({b.test_start}), so tuning never sees the test period. Fix the config."
            )
        start, end = pd.Timestamp(b.start), pd.Timestamp(b.tuning_end)
    elif period == "test":
        start, end = pd.Timestamp(b.test_start), latest
    else:
        start, end = pd.Timestamp(b.start), latest
    if start > min(end, latest):
        raise PeriodError(
            f"No cached trading days in the {period} period ({start:%Y-%m-%d} to "
            f"{end:%Y-%m-%d}); the latest cached day is {latest:%Y-%m-%d}."
        )
    return start, end


def never_fetched(
    store: Store, universe: Universe, start: pd.Timestamp, end: pd.Timestamp
) -> list[str]:
    """Universe members of the period that `agent fetch` never tried (no record at all)."""
    attempted = store.attempted_tickers()
    return sorted(t for t in universe.tickers_between(start, end) if t not in attempted)


def backtest_command(settings: Settings, store: Store, period: str) -> int:
    benchmark = store.load_prices(settings.data.benchmark)
    if benchmark is None:
        print(
            f"No cached prices for the benchmark {settings.data.benchmark}. "
            "Run `agent fetch` first.",
            file=sys.stderr,
        )
        return 1
    stale = store.tickers_without_traded_prices()
    if stale:
        count = "1 ticker" if len(stale) == 1 else f"{len(stale)} tickers"
        verb = "has" if len(stale) == 1 else "have"
        print(
            f"{count} ({_shown(stale)}) {verb} cached prices without the as-traded prices the "
            "price and traded-value filters need. Run `agent fetch` to download them again.",
            file=sys.stderr,
        )
        return 1
    days = benchmark.index
    universe = Universe.from_csv(Path(settings.data.membership_csv))
    try:
        start, end = period_dates(period, settings, days[-1])
    except PeriodError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    warnings: list[str] = []
    if period in ("test", "full"):
        missing = never_fetched(store, universe, start, end)
        if missing:
            count = (
                "1 universe member was"
                if len(missing) == 1
                else f"{len(missing)} universe members were"
            )
            print(
                f"The cache is incomplete: {count} never fetched ({_shown(missing)}). "
                "Run `agent fetch` until it finishes without failures, then run this again. "
                "Nothing was run, so the test period is still unused.",
                file=sys.stderr,
            )
            return 1
        try:
            warnings = check_test_period(store, settings.fingerprint())
        except GuardError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    frames = {t: f for t in store.tickers_with_prices() if (f := store.load_prices(t)) is not None}
    panel = Panel(frames, days, settings.strategy, settings.data.max_daily_jump)
    earnings = EarningsCalendar(store.load_earnings(), list(days))
    sectors = load_sectors(Path(settings.data.sectors_csv))
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
        f"{n['tickers_in_universe']}. They are a mix of failed and acquired companies, so the "
        "direction of the bias is uncertain, though probably upward."
    )
    print(
        f"Signals taken without earnings dates: {n['signals_without_earnings_data']} of "
        f"{n['signals']}; bad price days skipped: {n['excluded_bad_days']}"
    )
    for event in n["circuit_breaker_events"]:
        print(f"Circuit breaker: {event}")
