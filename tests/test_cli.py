import sqlite3

import pandas as pd

from helpers import make_bars
from market_agent import cli
from market_agent.data.sources import EarningsHistory, NoData
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
    def sector(self, ticker):
        return "Technology"


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
    def sector(self, ticker):
        raise ConnectionError("timed out")


class UnknownSectors:
    def sector(self, ticker):
        return "Unknown"


def test_build_sectors_keeps_known_sectors(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["build-sectors"]) == 0
    csv = tmp_path / "data" / "sectors.csv"
    monkeypatch.setattr(cli, "sector_source", UnknownSectors)
    assert cli.main(["build-sectors"]) == 0
    assert "AAA,Technology" in csv.read_text("utf-8")
    monkeypatch.setattr(cli, "sector_source", FlakySectors)
    capsys.readouterr()
    assert cli.main(["build-sectors"]) == 1
    assert "AAA,Technology" in csv.read_text("utf-8")
    assert "Failed: 1 ticker (AAA)" in capsys.readouterr().err
