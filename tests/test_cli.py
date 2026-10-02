import pandas as pd

from helpers import make_bars
from market_agent import cli
from market_agent.data.sources import EarningsHistory, NoData

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
