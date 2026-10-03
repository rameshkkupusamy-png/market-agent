import math

import pandas as pd

from helpers import make_bars
from market_agent.indicators import add_indicators, suspicious_days
from market_agent.panel import Bar, Panel
from market_agent.settings import StrategySettings

S = StrategySettings()


def rising(n=260, start=50.0, step=0.1):
    return [start + step * i for i in range(n)]


def test_indicator_values():
    bars = make_bars([10, 11, 12, 13, 14], volumes=[100, 200, 300, 400, 500])
    s = StrategySettings(
        sma_fast=2, sma_slow=3, atr_days=2, breakout_days=3, volume_days=2, rank_days=2
    )
    df = add_indicators(bars, s)
    last = df.iloc[-1]
    assert last["sma_fast"] == 13.5
    assert last["sma_slow"] == 13.0
    assert last["high_close"] == 14.0
    assert last["avg_volume_prev"] == 350.0  # days 3 and 4, not today
    assert last["avg_value"] == (13 * 400 + 14 * 500) / 2
    assert math.isclose(last["ret_rank"], 14 / 12 - 1)
    # true range on the last day: max(high-low, |high-prev close|, |low-prev close|)
    tr_last = max(14.14 - 13.86, abs(14.14 - 13), abs(13.86 - 13))
    tr_prev = max(13.13 - 12.87, abs(13.13 - 12), abs(12.87 - 12))
    assert math.isclose(last["atr"], (tr_last + tr_prev) / 2)


def test_traded_value_uses_traded_prices():
    # adjusted 10 with volume 100, but it really traded at 30 (shares 100 / 3 after the split)
    bars = make_bars([10, 10], volumes=[100, 100], traded=3)
    bars["raw_volume"] = [100.0, 100.0]  # e.g. dividends only: volume unchanged
    df = add_indicators(bars, StrategySettings(volume_days=2))
    assert df.iloc[-1]["avg_value"] == 3_000


def test_indicators_never_look_ahead():
    bars = make_bars(rising(300), volumes=[1e6 + 1000 * (i % 7) for i in range(300)])
    full = add_indicators(bars, S)
    for cut in (210, 250, 299):
        partial = add_indicators(bars.iloc[: cut + 1], S)
        pd.testing.assert_series_equal(partial.iloc[-1], full.iloc[cut], check_names=False)


def test_suspicious_days_flags_big_jumps():
    bars = make_bars([10, 10.5, 16, 15.5, 15])
    assert list(suspicious_days(bars, 0.40)) == [bars.index[2]]


def test_panel_snapshot_and_bar():
    a = make_bars(rising(260))
    b = make_bars(rising(260, start=20))
    panel = Panel({"AAA": a, "BBB": b}, a.index, S, 0.40)
    day = a.index[-1]
    snap = panel.snapshot(day)
    assert set(snap.index) == {"AAA", "BBB"}
    assert snap.loc["AAA", "close"] == a["close"].iloc[-1]
    assert not math.isnan(snap.loc["AAA", "sma_slow"])
    assert panel.bar("AAA", day) == Bar(
        a["open"].iloc[-1], a["high"].iloc[-1], a["low"].iloc[-1], a["close"].iloc[-1]
    )
    assert panel.bar("ZZZ", day) is None
    assert panel.tickers == {"AAA", "BBB"}


def test_panel_excludes_suspicious_days_and_knows_last_day():
    closes = rising(30)
    closes[10] = closes[10] * 1.5  # one bad spike (+50%); the fall back next day is −33%
    a = make_bars(closes)
    short = make_bars(rising(20))
    panel = Panel({"AAA": a, "SHORT": short}, a.index, S, 0.40)
    assert panel.bar("AAA", a.index[10]) is None
    assert panel.excluded == {"AAA": [a.index[10]]}
    assert panel.last_day("SHORT") == short.index[-1]
    assert panel.bar("SHORT", a.index[25]) is None
    assert panel.last_day("NOPE") is None
