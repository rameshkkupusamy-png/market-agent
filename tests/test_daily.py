from dataclasses import dataclass
from typing import Any

import pandas as pd

from helpers import make_bars, settings_with
from market_agent.daily import DailyRun, run_days, send_results
from market_agent.data.finnhub import NoNews
from market_agent.data.sources import EarningsHistory, Headline
from market_agent.notify import TelegramError
from market_agent.portfolio import portfolio_from_json
from market_agent.reviewer import Review
from market_agent.sessions import TradingCalendar
from market_agent.settings import Settings
from market_agent.store import Store

N = 320
SESSIONS = list(pd.bdate_range("2020-06-01", periods=N))


def aaa_bars():
    """Steady rise, then a breakout on triple volume at session 250 that runs to its target."""
    closes = [50 + 0.1 * i for i in range(250)] + [75 + 0.6 * i for i in range(N - 250)]
    volumes = [1e6] * 250 + [3e6] + [1e6] * (N - 251)
    return make_bars(closes, volumes, start="2020-06-01")


def spy_bars():
    return make_bars([400 + 0.2 * i for i in range(N)], start="2020-06-01")


def closing(day):
    return (day + pd.Timedelta(hours=20)).tz_localize("UTC")


def after_close(k):
    return closing(SESSIONS[k]) + pd.Timedelta(hours=2)


class FakeReviewer:
    def __init__(self, verdict="approve"):
        self.verdict = verdict
        self.items = []

    def review(self, item):
        self.items.append(item)
        return Review(
            item.ticker,
            "reviewed",
            self.verdict,
            "high",
            ["fine"],
            [],
            [],
            "",
            0.01,
            "fake",
            "prompt",
            "{}",
        )


@dataclass
class World:
    store: Store
    settings: Settings
    calendar: TradingCalendar
    reviewer: FakeReviewer
    runner: DailyRun


def make_world(path, frames=None, sessions=SESSIONS, verdict="approve", news: Any = None):
    path.mkdir(parents=True, exist_ok=True)
    store = Store(path / "market.db")
    for ticker, bars in (frames or {"AAA": aaa_bars(), "SPY": spy_bars()}).items():
        store.replace_prices(ticker, bars, "2026-10-01")
    store.replace_earnings("AAA", EarningsHistory([], SESSIONS[0]), "2026-10-01")
    (path / "members.csv").write_text('date,tickers\n2020-06-01,"AAA"\n', encoding="utf-8")
    settings = settings_with(
        data={"membership_csv": str(path / "members.csv"), "sectors_csv": str(path / "s.csv")}
    )
    calendar = TradingCalendar(sessions, [closing(s) for s in sessions])
    reviewer = FakeReviewer(verdict)
    runner = DailyRun(settings, store, reviewer, news or NoNews())
    return World(store, settings, calendar, reviewer, runner)


def run(world, now, wait=False, refetch=None, sleep=None):
    clock = now if callable(now) else (lambda: now)
    return run_days(
        world.settings,
        world.store,
        world.calendar,
        world.runner,
        now=clock,
        wait=wait,
        refetch=refetch or (lambda tickers: None),
        sleep=sleep or (lambda seconds: None),
    )


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += pd.Timedelta(seconds=seconds)


def state(world, day, name="rules-only"):
    return portfolio_from_json(world.store.paper_states(day)[name])


def test_first_run_processes_only_the_latest_closed_day(tmp_path):
    w = make_world(tmp_path)
    [result] = run(w, after_close(250))
    assert (result.day, result.status) == (SESSIONS[250], "traded")
    assert [o.ticker for o in state(w, SESSIONS[250]).orders] == ["AAA"]
    assert "Candidates (1):" in result.report
    assert "- AAA: approve (high): fine" in result.report
    assert w.store.daily_run(SESSIONS[250])["report"] == result.report
    assert [item.ticker for item in w.reviewer.items] == ["AAA"]


def test_second_run_on_the_same_day_does_nothing(tmp_path):
    w = make_world(tmp_path)
    run(w, after_close(250))
    assert run(w, after_close(250)) == []


def test_catch_up_matches_on_time_runs(tmp_path):
    on_time, caught_up = make_world(tmp_path / "a"), make_world(tmp_path / "b")
    for k in range(245, 276):
        run(on_time, after_close(k))
    run(caught_up, after_close(245))
    results = run(caught_up, after_close(275))
    assert len(results) == 30
    last = SESSIONS[275]
    assert on_time.store.paper_states(last) == caught_up.store.paper_states(last)
    p = state(on_time, last)
    assert p.trades and p.trades[0].ticker == "AAA"  # something actually happened
    assert on_time.store.reviews_on(SESSIONS[250])[0]["late"] is False
    assert caught_up.store.reviews_on(SESSIONS[250])[0]["late"] is True


def test_holidays_are_not_processed(tmp_path):
    sessions = [s for s in SESSIONS if s != SESSIONS[251]]
    w = make_world(tmp_path, sessions=sessions)
    run(w, after_close(250))
    results = run(w, after_close(252))
    assert [r.day for r in results] == [SESSIONS[252]]
    assert w.store.paper_states(SESSIONS[251]) == {}


def test_skipped_candidates_are_left_out_of_rules_ai(tmp_path):
    w = make_world(tmp_path, verdict="skip")
    run(w, after_close(250))
    assert [o.ticker for o in state(w, SESSIONS[250], "rules-only").orders] == ["AAA"]
    assert state(w, SESSIONS[250], "rules+ai").orders == []


def test_incomplete_latest_day_waits_then_does_not_trade(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[251])})
    run(w, after_close(250))
    clock, refetched = Clock(after_close(251)), []
    [result] = run(w, clock.now, wait=True, refetch=refetched.append, sleep=clock.sleep)
    assert result.status == "no trading"
    assert f"no SPY price for {SESSIONS[251]:%Y-%m-%d}" in result.report
    assert result.alerts == [result.report]
    assert refetched == [["SPY"]] * 8
    assert clock.t == after_close(251) + pd.Timedelta(hours=2)
    assert w.store.paper_states(SESSIONS[251]) == w.store.paper_states(SESSIONS[250])
    [next_day] = run(w, after_close(252))
    assert next_day.status == "traded"
    assert "AAA" in state(w, SESSIONS[252]).positions  # the waiting order filled


def test_late_data_is_used_when_it_arrives(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[251])})
    run(w, after_close(250))
    clock, refetched = Clock(after_close(251)), []

    def refetch(tickers):
        refetched.append(tickers)
        w.store.replace_prices("SPY", spy_bars(), "2026-10-02")

    [result] = run(w, clock.now, wait=True, refetch=refetch, sleep=clock.sleep)
    assert result.status == "traded" and len(refetched) == 1


def test_catch_up_leaves_an_incomplete_latest_day_for_later(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[251])})
    run(w, after_close(250))
    assert run(w, after_close(251), wait=False) == []
    assert w.store.latest_paper_day() == SESSIONS[250]


class FakeNews:
    def fetch(self, ticker, start, end):
        midnight = SESSIONS[250].tz_localize("America/New_York")
        return [
            Headline(midnight + pd.Timedelta(hours=19), "Late", "After the run", ""),
            Headline(midnight + pd.Timedelta(hours=9), "Early", "Before the run", ""),
        ]


def test_reviews_only_see_news_from_before_the_run(tmp_path):
    w = make_world(tmp_path, news=FakeNews())
    run(w, after_close(250))
    [item] = w.reviewer.items
    assert [h.headline for h in item.headlines] == ["Before the run"]


class FakeTelegram:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, text):
        if self.fail:
            raise TelegramError("network down")
        self.sent.append(text)


def test_reports_are_marked_sent_only_when_telegram_works(tmp_path):
    w = make_world(tmp_path)
    results = run(w, after_close(250))
    assert send_results(w.store, results, FakeTelegram(fail=True)) == ["network down"]
    assert w.store.daily_run(SESSIONS[250])["sent"] is False
    telegram = FakeTelegram()
    assert send_results(w.store, results, telegram) == []
    assert telegram.sent == [results[0].report]
    assert w.store.daily_run(SESSIONS[250])["sent"] is True


def test_long_catch_up_sends_one_summary(tmp_path):
    w = make_world(tmp_path)
    run(w, after_close(250))
    results = run(w, after_close(255))
    telegram = FakeTelegram()
    send_results(w.store, results, telegram)
    assert len(results) == 5
    assert telegram.sent[0].startswith("Caught up 4 missed trading days")
    assert telegram.sent[1] == results[-1].report


def test_long_catch_up_still_sends_no_trading_alerts(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[252])})
    run(w, after_close(250))
    results = run(w, after_close(255), wait=False)
    assert len(results) == 5
    skipped = next(r for r in results if r.day == SESSIONS[252])
    assert skipped.status == "no trading"
    telegram = FakeTelegram()
    send_results(w.store, results, telegram)
    assert telegram.sent[0].startswith("Caught up 4 missed trading days")
    assert telegram.sent[1] == results[-1].report
    assert telegram.sent[2:] == [skipped.report]
