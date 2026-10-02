"""Indicators from daily bars. Every rolling window looks backwards only (no look-ahead)."""

from __future__ import annotations

import pandas as pd

from market_agent.settings import StrategySettings

INDICATOR_COLUMNS = (
    "sma_fast",
    "sma_slow",
    "atr",
    "high_close",
    "avg_volume_prev",
    "avg_value",
    "ret_rank",
)


def add_indicators(bars: pd.DataFrame, s: StrategySettings) -> pd.DataFrame:
    df = bars.copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    df["sma_fast"] = close.rolling(s.sma_fast).mean()
    df["sma_slow"] = close.rolling(s.sma_slow).mean()
    previous = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()], axis=1
    ).max(axis=1)
    df["atr"] = true_range.rolling(s.atr_days).mean()
    df["high_close"] = close.rolling(s.breakout_days).max()
    df["avg_volume_prev"] = volume.shift(1).rolling(s.volume_days).mean()
    df["avg_value"] = (close * volume).rolling(s.volume_days).mean()
    df["ret_rank"] = close / close.shift(s.rank_days) - 1
    return df


def suspicious_days(bars: pd.DataFrame, max_jump: float) -> pd.DatetimeIndex:
    """Days whose close moved more than max_jump from the previous close.

    Prices are adjusted for splits, so such a jump is usually bad data. A real jump that large
    (e.g. a takeover) is also skipped for that day, which is the cautious choice.
    """
    change = bars["close"].pct_change().abs()
    return bars.index[change > max_jump]
