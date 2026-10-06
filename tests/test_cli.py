import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from helpers import make_bars
from market_agent import cli
from market_agent.data.finnhub import NoNews
from market_agent.data.sources import EarningsHistory, NoData, Profile
from market_agent.portfolio import portfolio_from_json, portfolio_to_json
from market_agent.sessions import TradingCalendar
from market_agent.store import Store

N = 320


def aaa_bars():
    closes = [50 + 0.1 * i for i in range(250)] + [75 + 0.6 * i for i in range(N - 250)]
    volumes = [1e6] * 250 + [3e6] + [1e6] * (N - 251)
    return make_bars(closes, volumes, start="2020-06-01")


class Prices:
    frames = {
        "AAA": aaa_bars(),
        "SPY": make_bars([400 + 0.2 * i for i in range(N)], start="2020-06-01"),
    }

    def fetch(self, ticker, start, end):
        if ticker not in self.frames:
            raise NoData(ticker)
        return self.frames[ticker]


class Earnings:
    def fetch(self, ticker):
        return EarningsHistory([], pd.Timestamp("2020-06-01"))


class Sectors:
    def profile(self, ticker):
        return Profile("Technology", f"{ticker} Corp")


def setup(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sp500_membership.csv").write_text(
        'date,tickers\n2020-06-01,"AAA,GONE"\n', encoding="utf-8"
    )
    (tmp_path / "config.yaml").write_text(
        "backtest:\n  start: 2021-03-01\n  tuning_end: 2021-06-30\n  test_start: 2021-07-01\n"
        "data:\n  history_start: 2020-06-01\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "price_source", Prices)
    monkeypatch.setattr(cli, "earnings_source", Earnings)
    monkeypatch.setattr(cli, "sector_source", Sectors)
    monkeypatch.setattr(cli, "pause", lambda seconds: None)


def write_membership(tmp_path, tickers):
    (tmp_path / "data" / "sp500_membership.csv").write_text(
        f'date,tickers\n2020-06-01,"{tickers}"\n', encoding="utf-8"
    )


def write_config(tmp_path, tuning_end="2021-06-30", test_start="2021-07-01", extra=""):
    (tmp_path / "config.yaml").write_text(
        f"backtest:\n  start: 2021-03-01\n  tuning_end: {tuning_end}\n  test_start: {test_start}\n"
        "data:\n  history_start: 2020-06-01\n" + extra,
        encoding="utf-8",
    )


def test_fetch_build_sectors_and_backtest(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    assert cli.main(["fetch"]) == 0
    out = capsys.readouterr().out
    assert "Prices: 2 tickers with data, 1 without (GONE)" in out
    assert cli.main(["build-sectors"]) == 0
    assert "AAA,Technology" in (tmp_path / "data" / "sectors.csv").read_text("utf-8")
    assert cli.main(["backtest", "--period", "tuning"]) == 0
    out = capsys.readouterr().out
    assert "Backtest (tuning) 2021-03-01 to 2021-06-30" in out
    assert "Strategy" in out and "SPY" in out
    assert "Universe members without data: 1 of 2" in out
    store = Store(tmp_path / "data" / "market.db")
    [run] = store.backtest_runs("tuning")
    store.close()
    spy = Prices.frames["SPY"]["close"].loc["2021-03-01":"2021-06-30"]
    spy_total = spy.iloc[-1] / spy.iloc[0] - 1
    assert run["benchmark"]["total_return"] == round(spy_total, 4)
    strategy_total = run["metrics"]["total_return"]
    assert f"{'Total return':16}{cli._pct(strategy_total):>12}{cli._pct(spy_total):>12}" in out


class CountingPrices(Prices):
    calls: list[str] = []

    def fetch(self, ticker, start, end):
        self.calls.append(ticker)
        return super().fetch(ticker, start, end)


def set_fetched_on(tmp_path, ticker, days_ago):
    day = (pd.Timestamp.today() - pd.Timedelta(days=days_ago)).date().isoformat()
    conn = sqlite3.connect(tmp_path / "data" / "market.db")
    conn.execute("UPDATE price_meta SET fetched_on = ? WHERE ticker = ?", (day, ticker))
    conn.commit()
    conn.close()


def test_fetch_skips_former_members_without_data(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    (tmp_path / "data" / "sp500_membership.csv").write_text(
        'date,tickers\n2020-06-01,"AAA,GONE"\n2021-01-04,"AAA,NEW"\n', encoding="utf-8"
    )
    monkeypatch.setattr(CountingPrices, "calls", [])
    monkeypatch.setattr(cli, "price_source", CountingPrices)
    assert cli.main(["fetch"]) == 0
    assert sorted(CountingPrices.calls) == ["AAA", "GONE", "NEW", "SPY"]
    # The next day: GONE left the index and never had data, so it waits; NEW is a current
    # member and is tried every day although it had no data either.
    for ticker in ["AAA", "GONE", "NEW", "SPY"]:
        set_fetched_on(tmp_path, ticker, days_ago=1)
    CountingPrices.calls.clear()
    capsys.readouterr()
    assert cli.main(["fetch"]) == 0
    assert sorted(CountingPrices.calls) == ["AAA", "NEW", "SPY"]
    out = capsys.readouterr().out
    assert "Prices: 2 tickers with data, 2 without (GONE, NEW)" in out
    assert "Skipped 1 former member without data (tried again every 30 days)" in out


def test_fetch_tries_former_members_again_after_the_recheck_interval(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    (tmp_path / "data" / "sp500_membership.csv").write_text(
        'date,tickers\n2020-06-01,"AAA,GONE"\n2021-01-04,"AAA"\n', encoding="utf-8"
    )
    write_config(tmp_path, extra="  missing_recheck_days: 7\n")
    monkeypatch.setattr(CountingPrices, "calls", [])
    monkeypatch.setattr(cli, "price_source", CountingPrices)
    assert cli.main(["fetch"]) == 0
    set_fetched_on(tmp_path, "GONE", days_ago=6)
    CountingPrices.calls.clear()
    assert cli.main(["fetch"]) == 0
    assert CountingPrices.calls == []  # GONE waits; the rest were downloaded today already
    set_fetched_on(tmp_path, "GONE", days_ago=7)
    assert cli.main(["fetch"]) == 0
    assert CountingPrices.calls == ["GONE"]


def test_backtest_refused_until_traded_prices_are_downloaded(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    assert cli.main(["fetch"]) == 0
    conn = sqlite3.connect(tmp_path / "data" / "market.db")
    conn.execute("UPDATE prices SET raw_close = NULL, raw_volume = NULL WHERE ticker = 'AAA'")
    conn.commit()
    conn.close()
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "tuning"]) == 1
    err = capsys.readouterr().err
    assert "1 ticker (AAA) has cached prices without the as-traded prices" in err
    assert "Run `agent fetch`" in err


def test_test_period_runs_once_per_settings(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["backtest", "--period", "test"]) == 0
    assert cli.main(["backtest", "--period", "test"]) == 1
    assert "already run with these exact settings" in capsys.readouterr().err


def test_backtest_without_data_explains(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    assert cli.main(["backtest", "--period", "tuning"]) == 1
    assert "Run `agent fetch` first" in capsys.readouterr().err


def test_full_period_is_guarded_like_test(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["backtest", "--period", "test"]) == 0
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "full"]) == 1
    assert "already ran the test period" in capsys.readouterr().err


def test_test_period_refused_after_full(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["backtest", "--period", "full"]) == 0
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "test"]) == 1
    assert "already ran the test period" in capsys.readouterr().err


class FlakyPrices(Prices):
    calls: list[str] = []

    def fetch(self, ticker, start, end):
        self.calls.append(ticker)
        if ticker == "BAD":
            raise ConnectionError("timed out")
        return super().fetch(ticker, start, end)


class FlakyEarnings(Earnings):
    def fetch(self, ticker):
        if ticker == "AAA":
            raise ConnectionError("rate limited")
        return super().fetch(ticker)


def test_fetch_continues_past_failing_tickers(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    write_membership(tmp_path, "AAA,BAD,GONE,OTHER")
    other = make_bars([20.0] * 10, start="2020-06-01")
    monkeypatch.setattr(FlakyPrices, "frames", {**Prices.frames, "OTHER": other})
    monkeypatch.setattr(FlakyPrices, "calls", [])
    monkeypatch.setattr(cli, "price_source", FlakyPrices)
    monkeypatch.setattr(cli, "earnings_source", FlakyEarnings)
    assert cli.main(["fetch"]) == 1
    captured = capsys.readouterr()
    assert FlakyPrices.calls.count("BAD") == cli.ATTEMPTS
    store = Store(tmp_path / "data" / "market.db")
    assert store.tickers_with_prices() == ["AAA", "OTHER", "SPY"]
    assert store.fetched_on("BAD") is None
    assert "OTHER" in store.load_earnings() and "AAA" not in store.load_earnings()
    store.close()
    assert "Failed: 2 tickers (AAA, BAD)" in captured.err


def test_test_period_refused_without_benchmark(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(Prices, "frames", {"AAA": aaa_bars()})
    cli.main(["fetch"])
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "test"]) == 1
    assert "No cached prices for the benchmark SPY" in capsys.readouterr().err


def test_test_and_full_refused_on_incomplete_cache(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    write_membership(tmp_path, "AAA,GONE,NEVER")
    capsys.readouterr()
    for period in ("test", "full"):
        assert cli.main(["backtest", "--period", period]) == 1
        err = capsys.readouterr().err
        assert "1 universe member was never fetched (NEVER)" in err
    assert cli.main(["backtest", "--period", "tuning"]) == 0
    store = Store(tmp_path / "data" / "market.db")
    assert store.backtest_runs("test") == [] and store.backtest_runs("full") == []
    store.close()


def test_tuning_end_reaching_test_start_is_refused(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    write_config(tmp_path, tuning_end="2021-07-01")
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "tuning"]) == 1
    assert "tuning_end (2021-07-01) must be before test_start (2021-07-01)" in (
        capsys.readouterr().err
    )


def test_test_start_after_cached_data_explains(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    write_config(tmp_path, test_start="2025-01-01")
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "test"]) == 1
    assert "No cached trading days in the test period" in capsys.readouterr().err


def test_missing_explicit_config_is_an_error(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    assert cli.main(["--config", "confg.yaml", "backtest", "--period", "test"]) == 1
    assert "confg.yaml" in capsys.readouterr().err
    assert not (tmp_path / "data" / "market.db").exists()


def test_warns_after_test_runs_with_other_settings(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["backtest", "--period", "test"]) == 0
    write_config(tmp_path, extra="strategy:\n  volume_ratio: 2.0\n")
    capsys.readouterr()
    assert cli.main(["backtest", "--period", "test"]) == 0
    assert "has been run 1 time before with other settings" in capsys.readouterr().out


class FlakySectors:
    def profile(self, ticker):
        raise ConnectionError("timed out")


class UnknownSectors:
    def profile(self, ticker):
        return Profile("Unknown", "")


def test_build_sectors_keeps_known_sectors(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["build-sectors"]) == 0
    csv = tmp_path / "data" / "sectors.csv"
    assert "AAA,Technology,AAA Corp" in csv.read_text("utf-8")
    monkeypatch.setattr(cli, "sector_source", UnknownSectors)
    assert cli.main(["build-sectors"]) == 0
    assert "AAA,Technology" in csv.read_text("utf-8")
    monkeypatch.setattr(cli, "sector_source", FlakySectors)
    capsys.readouterr()
    assert cli.main(["build-sectors"]) == 1
    assert "AAA,Technology" in csv.read_text("utf-8")
    assert "Failed: 1 ticker (AAA)" in capsys.readouterr().err


def test_env_file_in_the_working_folder_is_loaded(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(os, "environ", dict(os.environ))  # restored after the test
    (tmp_path / ".env").write_text("MARKET_AGENT_TEST=yes\n", encoding="utf-8")
    cli.main(["backtest"])  # fails (no data), but loads .env first
    assert os.environ["MARKET_AGENT_TEST"] == "yes"


SESSIONS = list(pd.bdate_range("2020-06-01", periods=N))


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def daily_setup(tmp_path, monkeypatch, now_index=255):
    setup(tmp_path, monkeypatch)
    telegram = FakeTelegram()
    closes = [(d + pd.Timedelta(hours=20)).tz_localize("UTC") for d in SESSIONS]
    monkeypatch.setattr(cli, "trading_calendar", lambda settings: TradingCalendar(SESSIONS, closes))
    monkeypatch.setattr(cli, "now", lambda: closes[now_index] + pd.Timedelta(hours=2))
    monkeypatch.setattr(cli, "news_source", NoNews)
    monkeypatch.setattr(cli, "claude_model", lambda settings: None)
    monkeypatch.setattr(cli, "telegram", lambda: telegram)
    return telegram


def test_run_daily_trades_reports_and_sends(tmp_path, monkeypatch, capsys):
    telegram = daily_setup(tmp_path, monkeypatch)
    day = f"{SESSIONS[255]:%Y-%m-%d}"
    assert cli.main(["run-daily"]) == 0
    assert f"{day}: traded" in capsys.readouterr().out
    assert telegram.sent[0].startswith(f"Market agent, {day}")
    assert cli.main(["run-daily"]) == 0
    assert "Nothing to do" in capsys.readouterr().out
    assert cli.main(["report"]) == 0
    assert capsys.readouterr().out.startswith(f"Market agent, {day}")
    assert cli.main(["catch-up"]) == 0


def test_report_before_any_run(tmp_path, monkeypatch, capsys):
    daily_setup(tmp_path, monkeypatch)
    assert cli.main(["report"]) == 1
    assert "No saved report" in capsys.readouterr().err


def test_reset_breaker(tmp_path, monkeypatch, capsys):
    daily_setup(tmp_path, monkeypatch)
    cli.main(["run-daily"])
    assert cli.main(["reset-breaker", "rules+ai"]) == 1
    assert "The circuit breaker is not on for rules+ai" in capsys.readouterr().err
    store = Store(tmp_path / "data" / "market.db")
    day = store.latest_paper_day()
    p = portfolio_from_json(store.paper_states(day)["rules+ai"])
    p.halted = p.breaker_tripped = True
    store.replace_paper_state("rules+ai", day, portfolio_to_json(p))
    store.close()
    assert cli.main(["reset-breaker", "rules+ai"]) == 0
    store = Store(tmp_path / "data" / "market.db")
    p = portfolio_from_json(store.paper_states(day)["rules+ai"])
    store.close()
    assert not p.halted and p.peak == p.equity_history[-1][1]
    assert p.events[-1].endswith("circuit breaker reset by the owner at equity 10,000")


def test_run_daily_downloads_again_prices_fetched_before_they_settled(tmp_path, monkeypatch):
    daily_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(CountingPrices, "calls", [])
    monkeypatch.setattr(cli, "price_source", CountingPrices)
    close = (SESSIONS[255] + pd.Timedelta(hours=20)).tz_localize("UTC")
    run_daily_at = cli.now
    monkeypatch.setattr(cli, "now", lambda: close + pd.Timedelta(minutes=10))
    assert cli.main(["fetch"]) == 0
    assert CountingPrices.calls.count("SPY") == 1
    monkeypatch.setattr(cli, "now", run_daily_at)
    assert cli.main(["run-daily"]) == 0
    assert CountingPrices.calls.count("SPY") == 2  # the early download is replaced
    assert cli.main(["catch-up"]) == 0
    assert CountingPrices.calls.count("SPY") == 2  # fetched after it settled: kept


def test_a_crash_sends_the_saved_days_and_an_alert(tmp_path, monkeypatch, capsys):
    telegram = daily_setup(tmp_path, monkeypatch, now_index=253)
    assert cli.main(["run-daily"]) == 0
    closes = [(d + pd.Timedelta(hours=20)).tz_localize("UTC") for d in SESSIONS]
    monkeypatch.setattr(cli, "now", lambda: closes[255] + pd.Timedelta(hours=2))
    monkeypatch.setenv("FINNHUB_API_KEY", "FAKEKEY")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    process, calls = cli.DailyRun.process, []

    def failing(self, market, day, check, late):
        calls.append(day)
        if len(calls) == 2:
            raise RuntimeError("boom FAKEKEY")
        return process(self, market, day, check, late)

    monkeypatch.setattr(cli.DailyRun, "process", failing)
    telegram.sent.clear()
    capsys.readouterr()
    assert cli.main(["run-daily"]) == 1
    assert calls == [SESSIONS[254], SESSIONS[255]]
    assert telegram.sent[0].startswith(f"Market agent, {SESSIONS[254]:%Y-%m-%d}")
    assert telegram.sent[-1] == (
        "Daily run failed: RuntimeError: boom <secret>. Details are in data\\daily.log."
    )
    assert not any("FAKEKEY" in text for text in telegram.sent)
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError" in err
    store = Store(tmp_path / "data" / "market.db")
    assert store.daily_run(SESSIONS[254])["sent"] is True
    store.close()


class BrokenTelegram:
    def send(self, text):
        raise ConnectionError("no network")


def test_a_crash_whose_alert_cannot_be_sent_is_printed(tmp_path, monkeypatch, capsys):
    daily_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "telegram", BrokenTelegram)

    def failing(self, market, day, check, late):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli.DailyRun, "process", failing)
    assert cli.main(["run-daily"]) == 1
    err = capsys.readouterr().err
    assert "RuntimeError: boom" in err
    assert "Could not send the failure alert: ConnectionError: no network" in err


def test_dashboard_starts_streamlit_on_localhost(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "run_process", lambda command, env: calls.append((command, env)) or 0)
    assert cli.main(["dashboard", "--port", "8600"]) == 0
    [(command, env)] = calls
    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    assert command[4].endswith("app.py") and Path(command[4]).exists()
    assert command[command.index("--server.address") + 1] == "localhost"
    assert command[command.index("--server.port") + 1] == "8600"
    assert command[command.index("--server.showEmailPrompt") + 1] == "false"
    assert env["MARKET_AGENT_DB"] == str((tmp_path / "data" / "market.db").resolve())
    assert env["MARKET_AGENT_CONFIG"] == str((tmp_path / "config.yaml").resolve())
    assert "http://localhost:8600" in capsys.readouterr().out


def test_dashboard_stops_cleanly_on_ctrl_c(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)

    def interrupted(command, env):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_process", interrupted)
    assert cli.main(["dashboard"]) == 0
    assert "Dashboard stopped." in capsys.readouterr().out
