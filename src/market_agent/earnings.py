"""Answers 'is there an earnings report on this day or in the next n trading days?'"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence

import pandas as pd

from market_agent.data.sources import EarningsHistory


class EarningsCalendar:
    def __init__(
        self, histories: Mapping[str, EarningsHistory], trading_days: Sequence[pd.Timestamp]
    ):
        self._dates = {ticker: sorted(h.dates) for ticker, h in histories.items()}
        self._coverage = {ticker: h.coverage_start for ticker, h in histories.items()}
        self._days = list(trading_days)

    def window_end(self, day: pd.Timestamp, n: int) -> pd.Timestamp:
        index = bisect_right(self._days, day) - 1 + n
        if 0 <= index < len(self._days):
            return self._days[index]
        # past the known calendar (the latest days of a run): about n trading days later
        return day + pd.Timedelta(days=math.ceil(n * 7 / 5) + 2)

    def reports_within(self, ticker: str, day: pd.Timestamp, n: int) -> bool:
        dates = self._dates.get(ticker, [])
        index = bisect_left(dates, day)
        return index < len(dates) and dates[index] <= self.window_end(day, n)

    def known(self, ticker: str, day: pd.Timestamp) -> bool:
        start = self._coverage.get(ticker)
        return start is not None and start <= day

    def next_report(self, ticker: str, day: pd.Timestamp) -> pd.Timestamp | None:
        """The first known report after `day`."""
        dates = self._dates.get(ticker, [])
        index = bisect_right(dates, day)
        return dates[index] if index < len(dates) else None
