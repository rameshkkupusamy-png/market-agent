"""Interfaces every market's data source implements, and the common bar format."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd

# Adjusted for later splits and dividends, so returns are continuous: signals and fills use these.
BAR_COLUMNS = ["open", "high", "low", "close", "volume"]
# As traded on the day: the minimum-price and traded-value filters use these.
TRADED_COLUMNS = ["raw_close", "raw_volume"]
PRICE_COLUMNS = [*BAR_COLUMNS, *TRADED_COLUMNS]


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


@dataclass(frozen=True)
class Headline:
    published: pd.Timestamp  # tz-aware
    source: str
    headline: str
    summary: str


class NewsSource(Protocol):
    def fetch(self, ticker: str, start: date, end: date) -> list[Headline]: ...


@dataclass(frozen=True)
class Profile:
    sector: str
    name: str = ""  # company name; empty when unknown


def to_day(index: pd.Index) -> pd.DatetimeIndex:
    """Exchange timestamps → tz-naive dates in New York time."""
    days = pd.DatetimeIndex(index)
    if days.tz is not None:
        days = days.tz_convert("America/New_York").tz_localize(None)
    return days.normalize()


def normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    bars = raw.rename(columns=str.lower)[PRICE_COLUMNS].astype(float)
    bars.index = to_day(raw.index)
    bars.index.name = "day"
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    return bars.dropna(subset=["close"])
