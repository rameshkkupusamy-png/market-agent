"""The daily paper-trading run and catch-up (spec section 8)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from market_agent.broker import Simulator
from market_agent.checks import DayCheck, check_day
from market_agent.data.sources import NewsSource, Profile
from market_agent.earnings import EarningsCalendar
from market_agent.notify import TelegramError
from market_agent.panel import Panel
from market_agent.paper import PORTFOLIOS, gone_lookup, restate
from market_agent.portfolio import Portfolio, portfolio_from_json, portfolio_to_json
from market_agent.report import PortfolioLine, build_report, fills_on
from market_agent.reviewer import Review, ReviewInput
from market_agent.sessions import TradingCalendar
from market_agent.settings import Settings
from market_agent.store import Store
from market_agent.strategy import Candidate, screen
from market_agent.trading import trade_day
from market_agent.universe import Universe, load_profiles

# News published after 18:00 New York time on day D could not have been seen by D's run.
NEWS_CUTOFF_HOURS = 18
SUMMARY_AFTER = 3  # a catch-up of more days than this sends one summary, not every report


@dataclass(frozen=True)
class DayResult:
    day: pd.Timestamp
    status: str  # "traded" | "no trading"
    report: str
    alerts: list[str]


@dataclass(frozen=True)
class Market:
    panel: Panel
    sessions: list[pd.Timestamp]
    universe: Universe
    earnings: EarningsCalendar
    profiles: dict[str, Profile]


def load_market(store: Store, settings: Settings, sessions: list[pd.Timestamp]) -> Market:
    """Everything in the cache up to the last session given. Days are processed one by one,
    and every step reads only data up to its own day, so later days do not leak in."""
    frames = {t: f for t in store.tickers_with_prices() if (f := store.load_prices(t)) is not None}
    return Market(
        panel=Panel(
            frames, pd.DatetimeIndex(sessions), settings.strategy, settings.data.max_daily_jump
        ),
        sessions=list(sessions),
        universe=Universe.from_csv(Path(settings.data.membership_csv)),
        earnings=EarningsCalendar(store.load_earnings(), list(sessions)),
        profiles=load_profiles(Path(settings.data.sectors_csv)),
    )


def check_market(market: Market, settings: Settings, day: pd.Timestamp) -> DayCheck:
    return check_day(
        market.panel,
        market.universe.members(day),
        day,
        [d for d in market.sessions if d <= day],
        settings.data.benchmark,
        settings.paper.max_missing_share,
        settings.paper.delisted_after_days,
    )


def review_warnings(reviews: Mapping[str, Review], late: bool) -> list[str]:
    out = []
    missed = [r for r in reviews.values() if r.status == "not reviewed"]
    failed = [r for r in reviews.values() if r.status == "failed"]
    if missed:
        out.append(f"{len(missed)} not reviewed ({missed[0].note}); treated as approved")
    if failed:
        out.append(f"{len(failed)} reviews failed; flagged and still bought by rules+ai")
    if late and reviews:
        out.append("Reviewed late (catch-up): these verdicts did not exist before the orders")
    return out


class DailyRun:
    def __init__(self, settings: Settings, store: Store, reviewer: Any, news: NewsSource):
        """reviewer: anything with review(ReviewInput) -> Review."""
        self.settings = settings
        self.store = store
        self.reviewer = reviewer
        self.news = news

    def _previous_states(self) -> dict[str, str]:
        previous = self.store.latest_paper_day()
        if previous is not None:
            return self.store.paper_states(previous)
        cash = self.settings.risk.starting_cash
        return {name: portfolio_to_json(Portfolio(name, cash)) for name in PORTFOLIOS}

    def process(self, market: Market, day: pd.Timestamp, check: DayCheck, late: bool) -> DayResult:
        s = self.settings
        states = self._previous_states()
        if not check.ok:
            text = (
                f"No trading on {day:%Y-%m-%d}: {check.reason}. "
                "Orders placed earlier wait for the next trading day."
            )
            self.store.save_day(day, states, "no trading", text, [text])
            return DayResult(day, "no trading", text, [text])

        portfolios = {name: portfolio_from_json(states[name]) for name in PORTFOLIOS}
        warnings: list[str] = []
        alerts: list[str] = []
        if check.excluded:
            warnings.append(
                f"{len(check.excluded)} shares had no usable price and were left out today: "
                + ", ".join(check.excluded)
            )
        candidates = screen(
            day,
            market.panel.snapshot(day),
            market.universe.members(day),
            market.earnings,
            s.strategy,
        )
        reviews = self._review(market, day, candidates[: s.ai.max_reviews_per_day], late, warnings)
        warnings += review_warnings(reviews, late)
        if any(r.note.startswith("monthly cap") for r in reviews.values()):
            alerts.append(
                f"Claude's monthly cap of US${s.ai.monthly_cap:.2f} is reached: candidates are "
                "not reviewed until next month."
            )

        sim = Simulator(s.risk, s.strategy)  # halts on the circuit breaker
        sessions = [d for d in market.sessions if d <= day]
        last_day = gone_lookup(market.panel, sessions, s.paper.delisted_after_days)
        sectors = {ticker: p.sector for ticker, p in market.profiles.items()}
        fills: list[str] = []
        for name, portfolio in portfolios.items():
            notes = restate(portfolio, market.panel.bar, s.risk.slippage)
            warnings += [f"{name}: {note}" for note in notes]
            was_halted = portfolio.halted
            taken = candidates
            if name == "rules+ai":
                taken = [c for c in candidates if self._verdict(reviews, c) != "skip"]
            trade_day(sim, portfolio, day, market.panel, last_day, taken, sectors, s)
            fills += fills_on(portfolio, day)
            if portfolio.halted and not was_halted:
                alerts.append(
                    f"{name}: {portfolio.events[-1]}. No new positions until "
                    f"`agent reset-breaker {name}`."
                )

        first = self.store.first_paper_day() or day
        spy_return = self._spy_return(market, first, day)
        lines = [
            PortfolioLine(
                name, p.equity_history[-1][1], s.risk.starting_cash, len(p.positions), p.halted
            )
            for name, p in portfolios.items()
        ]
        report = build_report(day, lines, spy_return, first, candidates, reviews, fills, warnings)
        new_states = {name: portfolio_to_json(p) for name, p in portfolios.items()}
        self.store.save_day(day, new_states, "traded", report, alerts)
        return DayResult(day, "traded", report, alerts)

    @staticmethod
    def _verdict(reviews: Mapping[str, Review], candidate: Candidate) -> str:
        review = reviews.get(candidate.ticker)
        return review.verdict if review is not None else "approve"

    def _spy_return(self, market: Market, first: pd.Timestamp, day: pd.Timestamp) -> float:
        benchmark = self.settings.data.benchmark
        start, end = market.panel.bar(benchmark, first), market.panel.bar(benchmark, day)
        return end.close / start.close - 1 if start and end else 0.0

    def _review(
        self,
        market: Market,
        day: pd.Timestamp,
        candidates: list[Candidate],
        late: bool,
        warnings: list[str],
    ) -> dict[str, Review]:
        if not candidates:
            return {}
        ai = self.settings.ai
        snap = market.panel.snapshot(day)
        cutoff = (day + pd.Timedelta(hours=NEWS_CUTOFF_HOURS)).tz_localize("America/New_York")
        start = (day - pd.Timedelta(days=ai.news_days)).date()
        reviews = {}
        for c in candidates:
            try:
                found = self.news.fetch(c.ticker, start, day.date())
            except Exception as exc:
                warnings.append(f"News for {c.ticker} unavailable ({exc})")
                found = []
            headlines = [h for h in found if h.published <= cutoff][: ai.max_headlines]
            profile = market.profiles.get(c.ticker, Profile("Unknown", ""))
            row = snap.loc[c.ticker]
            item = ReviewInput(
                ticker=c.ticker,
                company=profile.name or c.ticker,
                sector=profile.sector,
                day=day,
                close=c.close,
                sma_fast=float(row["sma_fast"]),
                sma_slow=float(row["sma_slow"]),
                volume_ratio=c.volume_ratio,
                atr=c.atr,
                return_63=c.score,
                next_earnings=market.earnings.next_report(c.ticker, day),
                headlines=headlines,
            )
            review = self.reviewer.review(item)
            self.store.save_review(day, review, late)
            reviews[c.ticker] = review
        return reviews


def processable(
    calendar: TradingCalendar, now: pd.Timestamp, settle_minutes: float
) -> pd.Timestamp | None:
    """The latest session whose data has settled: closed at least settle_minutes ago.

    Bars fetched soon after the close may still be preliminary."""
    return calendar.latest_closed(now - pd.Timedelta(minutes=settle_minutes))


def pending_days(
    store: Store, calendar: TradingCalendar, now: pd.Timestamp, settle_minutes: float
) -> list[pd.Timestamp]:
    """Settled sessions not processed yet. The very first run starts with the latest one."""
    latest = processable(calendar, now, settle_minutes)
    if latest is None:
        return []
    last = store.latest_paper_day()
    return [latest] if last is None else calendar.between(last, latest)


def run_days(
    settings: Settings,
    store: Store,
    calendar: TradingCalendar,
    runner: DailyRun,
    now: Callable[[], pd.Timestamp],
    wait: bool,
    refetch: Callable[[list[str]], None],
    sleep: Callable[[float], None],
    results: list[DayResult] | None = None,
) -> list[DayResult]:
    """Process every pending day in order. Only the latest day can be waited for; with
    wait=False an incomplete latest day is left for the next run (its data may still come).

    results: a list to append each day's result to as it is saved (also returned), so a
    caller still has the finished days if a later one fails."""
    results = [] if results is None else results
    days = pending_days(store, calendar, now(), settings.paper.settle_minutes)
    if not days:
        return results
    deadline = now() + pd.Timedelta(hours=settings.paper.retry_hours)
    every = settings.paper.retry_every_minutes
    market = load_market(store, settings, calendar.up_to(days[-1]))
    for day in days:
        latest = day == days[-1]
        check = check_market(market, settings, day)
        while not check.ok and latest and wait and now() < deadline:
            print(
                f"Data for {day:%Y-%m-%d} is incomplete ({check.reason}); "
                f"trying again in {every:g} minutes"
            )
            sleep(every * 60)
            refetch([*check.excluded, settings.data.benchmark])
            market = load_market(store, settings, calendar.up_to(days[-1]))
            check = check_market(market, settings, day)
        if not check.ok and latest and not wait:
            print(
                f"Data for {day:%Y-%m-%d} is not complete yet ({check.reason}); "
                "the next run will process it."
            )
            break
        results.append(runner.process(market, day, check, late=not latest))
    return results


def send_results(store: Store, results: list[DayResult], telegram: Any) -> list[str]:
    """Send the reports and alerts; marks each report sent. Returns what went wrong."""
    messages: list[tuple[pd.Timestamp | None, str]] = []
    shown = results
    if len(results) > SUMMARY_AFTER:
        missed = ", ".join(f"{r.day:%Y-%m-%d}" for r in results[:-1])
        messages.append(
            (
                None,
                f"Caught up {len(results) - 1} missed trading days ({missed}). Their "
                "reports are saved: `agent report --day YYYY-MM-DD`.",
            )
        )
        shown = results[-1:]
    messages += [(r.day, r.report) for r in shown]
    sent_reports = {r.report for r in shown}
    messages += [(None, alert) for r in results for alert in r.alerts if alert not in sent_reports]
    for day, text in messages:
        try:
            telegram.send(text)
        except TelegramError as exc:
            return [str(exc)]
        if day is not None:
            store.mark_sent(day)
    return []
