"""Day × ticker tables of prices and indicators, so one day's screen is a single row lookup."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from market_agent.data.sources import BAR_COLUMNS
from market_agent.indicators import INDICATOR_COLUMNS, add_indicators, suspicious_days
from market_agent.settings import StrategySettings

COLUMNS = (*BAR_COLUMNS, *INDICATOR_COLUMNS)


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


class Panel:
    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        days: pd.DatetimeIndex,
        strategy: StrategySettings,
        max_jump: float,
    ):
        self.days = pd.DatetimeIndex(days)
        self.excluded: dict[str, list[pd.Timestamp]] = {}
        self._last_day: dict[str, pd.Timestamp] = {}
        enriched = {}
        for ticker, bars in frames.items():
            bad = suspicious_days(bars, max_jump)
            if len(bad):
                self.excluded[ticker] = list(bad)
            clean = bars.drop(index=bad)
            if clean.empty:
                continue
            self._last_day[ticker] = clean.index[-1]
            enriched[ticker] = add_indicators(clean, strategy).reindex(self.days)
        self.tickers = set(enriched)
        self._wide = {
            column: pd.DataFrame(
                {ticker: df[column] for ticker, df in enriched.items()}, index=self.days
            )
            for column in COLUMNS
        }

    def snapshot(self, day: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame({column: wide.loc[day] for column, wide in self._wide.items()})

    def bar(self, ticker: str, day: pd.Timestamp) -> Bar | None:
        if ticker not in self.tickers or day not in self.days:
            return None
        values = [self._wide[c].at[day, ticker] for c in ("open", "high", "low", "close")]
        if any(math.isnan(v) for v in values):
            return None
        return Bar(*(float(v) for v in values))

    def last_day(self, ticker: str) -> pd.Timestamp | None:
        return self._last_day.get(ticker)
