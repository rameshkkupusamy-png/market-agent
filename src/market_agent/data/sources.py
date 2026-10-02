"""Interfaces every market's data source implements, and the common bar format."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


class NoData(Exception):
    """The source has no data for this ticker (unknown, delisted or renamed)."""


@dataclass(frozen=True)
class EarningsHistory:
    dates: list[pd.Timestamp]
    coverage_start: pd.Timestamp | None  # earliest date the source knows about


class PriceSource(Protocol):
    def fetch(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame: ...


class EarningsSource(Protocol):
    def fetch(self, ticker: str) -> EarningsHistory: ...


def to_day(index: pd.Index) -> pd.DatetimeIndex:
    """Exchange timestamps → tz-naive dates in New York time."""
    days = pd.DatetimeIndex(index)
    if days.tz is not None:
        days = days.tz_convert("America/New_York").tz_localize(None)
    return days.normalize()


def normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    bars = raw.rename(columns=str.lower)[BAR_COLUMNS].astype(float)
    bars.index = to_day(raw.index)
    bars.index.name = "day"
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    return bars.dropna(subset=["close"])
