"""agent fetch | build-sectors | backtest --period tuning|test|full
| run-daily | catch-up | reset-breaker <portfolio> | report [--day YYYY-MM-DD]
| dashboard [--port N]"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from market_agent.backtest import BacktestResult, run_backtest
from market_agent.daily import DailyRun, DayResult, processable, run_days, send_results
from market_agent.data.cache import EarningsCache, PriceCache
from market_agent.data.finnhub import FinnhubNews, NoNews
from market_agent.data.sources import Profile
from market_agent.data.yahoo import YahooEarnings, YahooPrices, YahooSectors
from market_agent.earnings import EarningsCalendar
from market_agent.guard import GuardError, check_test_period
from market_agent.notify import NoTelegram, Telegram
from market_agent.panel import Panel
from market_agent.paper import PORTFOLIOS, PaperError, reset_breaker
from market_agent.reviewer import ClaudeModel, Reviewer
from market_agent.sessions import TradingCalendar
from market_agent.settings import Settings, SettingsError, load_settings
from market_agent.store import Store
from market_agent.universe import Universe, load_profiles, load_sectors, write_profiles

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


def trading_calendar(settings: Settings) -> TradingCalendar:
    start = pd.Timestamp(settings.data.history_start)
    return TradingCalendar.nyse(start, pd.Timestamp.today().normalize() + pd.Timedelta(days=7))


def news_source():
    key = os.environ.get("FINNHUB_API_KEY")
    return FinnhubNews(key) if key else NoNews()


def claude_model(settings: Settings):
    return ClaudeModel(settings.ai) if os.environ.get("ANTHROPIC_API_KEY") else None


def telegram():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    return Telegram(token, chat) if token and chat else NoTelegram()


def now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def run_process(command: list[str], env: dict[str, str]) -> int:
    """Replaced in tests."""
    return subprocess.run(command, env=env).returncode


def dashboard_command(settings: Settings, config: Path, port: int) -> int:
    app = Path(__file__).parent / "dashboard" / "app.py"
    env = {
        **os.environ,
        "MARKET_AGENT_DB": str(Path(settings.data.db_path).resolve()),
        "MARKET_AGENT_CONFIG": str(config.resolve()),
    }
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        "localhost",
        "--server.port",
        str(port),
        "--browser.gatherUsageStats",
        "false",
    ]
    print(f"Dashboard at http://localhost:{port} (Ctrl+C to stop)")
    return run_process(command, env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Swing-trading analyst")
    parser.add_argument("--config", type=Path, help=f"settings file (default {DEFAULT_CONFIG})")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="download prices and earnings dates for the universe")
    commands.add_parser("build-sectors", help="write data/sectors.csv from Yahoo")
    backtest = commands.add_parser("backtest", help="run the backtest from cached data")
    backtest.add_argument("--period", choices=PERIODS, default="tuning")
    commands.add_parser(
        "run-daily", help="trade the latest US trading day (and any missed ones) on paper"
    )
    commands.add_parser("catch-up", help="process missed trading days without waiting for data")
    reset = commands.add_parser("reset-breaker", help="turn a portfolio's circuit breaker off")
    reset.add_argument("portfolio", choices=PORTFOLIOS)
    report = commands.add_parser("report", help="show a saved daily report")
    report.add_argument("--day", type=date.fromisoformat, help="YYYY-MM-DD (default: latest)")
    dashboard = commands.add_parser("dashboard", help="open the read-only dashboard in a browser")
    dashboard.add_argument("--port", type=int, default=8501)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    load_dotenv(Path(".env"))  # the working folder only, so tests never pick up real keys

    try:
        settings = load_settings(args.config or DEFAULT_CONFIG, required=args.config is not None)
    except SettingsError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 1
    if args.command == "dashboard":
        return dashboard_command(settings, args.config or DEFAULT_CONFIG, args.port)
    store = Store(settings.data.db_path)
    try:
        if args.command == "fetch":
            return fetch(settings, store)
        if args.command == "build-sectors":
            return build_sectors(settings, store)
        if args.command == "run-daily":
            return daily_command(settings, store, wait=True)
        if args.command == "catch-up":
            return daily_command(settings, store, wait=False)
        if args.command == "reset-breaker":
            return reset_command(store, args.portfolio)
        if args.command == "report":
            return report_command(store, args.day)
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


def fetch(settings: Settings, store: Store, fresh_after: pd.Timestamp | None = None) -> int:
    """fresh_after: also download again what was downloaded before this time (UTC)."""
    prices = PriceCache(store, price_source(), now=lambda: now())
    earnings = EarningsCache(store, earnings_source())
    tickers = [*universe_tickers(settings), settings.data.benchmark]
    failed = []
    for index, ticker in enumerate(tickers, 1):
        try:
            has_data = with_retries(
                ticker,
                lambda t=ticker: prices.update(
                    t, settings.data.history_start, fresh_after=fresh_after
                ),
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
    """Keeps a ticker's earlier sector and name when the new lookup fails or says "Unknown"."""
    source = sector_source()
    path = Path(settings.data.sectors_csv)
    previous = load_profiles(path)
    profiles: dict[str, Profile] = {}
    failed = []
    for ticker in store.tickers_with_prices():
        if ticker == settings.data.benchmark:
            continue
        try:
            profile = with_retries(ticker, lambda t=ticker: source.profile(t))
        except Exception as exc:
            log.warning("%s: profile lookup failed (%s)", ticker, exc)
            failed.append(ticker)
            profile = None
        old = previous.get(ticker)
        if old is not None and (profile is None or profile.sector == "Unknown"):
            profile = Profile(old.sector, (profile.name if profile else "") or old.name)
        if profile is not None:
            profiles[ticker] = profile
    write_profiles(path, profiles)
    unknown = sum(1 for p in profiles.values() if p.sector == "Unknown")
    print(f"Sectors written for {len(profiles)} tickers ({unknown} unknown)")
    return report_failures(failed)


def refetch(settings: Settings, store: Store, tickers: list[str]) -> None:
    prices = PriceCache(store, price_source(), now=lambda: now())
    for ticker in tickers:
        try:
            with_retries(
                ticker, lambda t=ticker: prices.update(t, settings.data.history_start, True)
            )
        except Exception as exc:
            log.warning("%s: download failed again (%s)", ticker, exc)


def month_start() -> datetime:
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def settled_since(settings: Settings, calendar: TradingCalendar) -> pd.Timestamp | None:
    """When the latest processable session's data settled: downloads before then may hold
    preliminary bars."""
    settle = settings.paper.settle_minutes
    latest = processable(calendar, now(), settle)
    return None if latest is None else calendar.close(latest) + pd.Timedelta(minutes=settle)


SECRETS = ("ANTHROPIC_API_KEY", "FINNHUB_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def hide_secrets(text: str) -> str:
    for name in SECRETS:
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "<secret>")
    return text


def daily_command(settings: Settings, store: Store, wait: bool) -> int:
    """A crash still sends the days already saved, then an alert, and exits with 1."""
    results: list[DayResult] = []
    try:
        return run_daily(settings, store, wait, results)
    except Exception as exc:
        traceback.print_exc()  # kept in the scheduled task's log
        alert = (
            f"Daily run failed: {type(exc).__name__}: {hide_secrets(str(exc))}. "
            "Details are in data\\daily.log."
        )
        try:
            messenger = telegram()
            for problem in send_results(store, results, messenger):
                print(f"Telegram: {problem}", file=sys.stderr)
            messenger.send(alert)
        except Exception as send_exc:
            print(
                f"Could not send the failure alert: {type(send_exc).__name__}: "
                f"{hide_secrets(str(send_exc))}",
                file=sys.stderr,
            )
        return 1


def run_daily(settings: Settings, store: Store, wait: bool, results: list[DayResult]) -> int:
    calendar = trading_calendar(settings)
    if fetch(settings, store, fresh_after=settled_since(settings, calendar)) != 0:
        print("Some downloads failed; the data check decides whether the day can be traded.")
    reviewer = Reviewer(
        claude_model(settings), settings.ai, lambda: store.ai_spent_since(month_start())
    )
    runner = DailyRun(settings, store, reviewer, news_source())
    results = run_days(
        settings,
        store,
        calendar,
        runner,
        now=now,
        wait=wait,
        refetch=lambda tickers: refetch(settings, store, tickers),
        sleep=pause,
        results=results,
    )
    if not results:
        print("Nothing to do: every closed trading day is already processed.")
        return 0
    for result in results:
        print(f"{result.day:%Y-%m-%d}: {result.status}")
    print()
    print(results[-1].report)
    for problem in send_results(store, results, telegram()):
        print(f"Telegram: {problem}. The report is saved; see `agent report`.", file=sys.stderr)
    return 0


def reset_command(store: Store, name: str) -> int:
    try:
        print(reset_breaker(store, name))
    except PaperError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def report_command(store: Store, day: date | None) -> int:
    run = store.daily_run(pd.Timestamp(day)) if day else store.latest_daily_run()
    if run is None:
        print("No saved report for that day. Run `agent run-daily` first.", file=sys.stderr)
        return 1
    print(run["report"])
    if not run["sent"]:
        print("\n(not sent to Telegram)")
    return 0


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
