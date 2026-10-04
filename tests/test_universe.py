import pandas as pd

from market_agent.data.sources import Profile
from market_agent.universe import (
    Universe,
    clean_ticker,
    load_profiles,
    load_sectors,
    write_profiles,
)

CSV = """date,tickers
2014-01-02,"AAPL,MSFT,TWTR"
2015-06-01,"AAPL,MSFT,BRK.B"
2020-03-02,"AAPL,BRK.B,ZZZ-201912"
"""


def universe(tmp_path):
    path = tmp_path / "members.csv"
    path.write_text(CSV, encoding="utf-8")
    return Universe.from_csv(path)


def test_clean_ticker():
    assert clean_ticker(" aapl ") == "AAPL"
    assert clean_ticker("ZZZ-201912") == "ZZZ"
    assert clean_ticker("BRK.B") == "BRK.B"


def test_members_on_a_day_use_the_latest_snapshot(tmp_path):
    u = universe(tmp_path)
    assert u.members(pd.Timestamp("2013-12-31")) == frozenset()
    assert u.members(pd.Timestamp("2014-01-02")) == {"AAPL", "MSFT", "TWTR"}
    assert u.members(pd.Timestamp("2015-05-29")) == {"AAPL", "MSFT", "TWTR"}
    assert u.members(pd.Timestamp("2015-06-01")) == {"AAPL", "MSFT", "BRK.B"}
    assert u.members(pd.Timestamp("2024-01-01")) == {"AAPL", "BRK.B", "ZZZ"}


def test_tickers_between_includes_every_snapshot_in_force(tmp_path):
    u = universe(tmp_path)
    assert u.tickers_between(pd.Timestamp("2015-01-01"), pd.Timestamp("2016-01-01")) == {
        "AAPL",
        "MSFT",
        "TWTR",
        "BRK.B",
    }


def test_profiles_round_trip(tmp_path):
    path = tmp_path / "sectors.csv"
    write_profiles(
        path,
        {
            "MSFT": Profile("Technology", "Microsoft Corporation"),
            "JPM": Profile("Financial Services", ""),
        },
    )
    assert load_sectors(path) == {"JPM": "Financial Services", "MSFT": "Technology"}
    assert load_profiles(path)["MSFT"] == Profile("Technology", "Microsoft Corporation")
    assert load_profiles(tmp_path / "none.csv") == {}


def test_sector_file_without_names(tmp_path):
    path = tmp_path / "sectors.csv"
    path.write_text("ticker,sector\nAAPL,Technology\n", encoding="utf-8")
    assert load_profiles(path) == {"AAPL": Profile("Technology", "")}
