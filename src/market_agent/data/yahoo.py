"""Yahoo Finance through the unofficial yfinance library. Free, personal use only."""

from __future__ import annotations

import importlib
import logging
from typing import Any

import pandas as pd

from market_agent.data.sources import EarningsHistory, NoData, normalize_bars, to_day

log = logging.getLogger(__name__)


def yahoo_symbol(ticker: str) -> str:
    """Class shares: BRK.B in index lists is BRK-B on Yahoo."""
    return ticker.replace(".", "-")


def _client(client: Any) -> Any:
    return client if client is not None else importlib.import_module("yfinance")


def _no_data_error(exc: Exception) -> bool:
    """yfinance's "ticker has no such data" error, as opposed to transport or rate-limit errors."""
    missing = importlib.import_module("yfinance.exceptions").YFTickerMissingError
    return isinstance(exc, missing)


def adjusted_and_traded(raw: pd.DataFrame, splits: pd.Series) -> pd.DataFrame:
    """Yahoo's Close and Volume allow for later splits only, Adj Close also for dividends.

    Adjusted bars scale open/high/low like yfinance's auto_adjust. As-traded prices undo every
    split after each day, from the ticker's whole split history (also splits after `end`).
    """
    days = to_day(raw.index)
    later_splits = pd.Series(1.0, index=raw.index)
    for split_day, ratio in zip(to_day(splits.index), splits, strict=True):
        if ratio > 0:
            later_splits[days < split_day] *= ratio
    factor = raw["Adj Close"] / raw["Close"]
    return pd.DataFrame(
        {
            "open": raw["Open"] * factor,
            "high": raw["High"] * factor,
            "low": raw["Low"] * factor,
            "close": raw["Adj Close"],
            "volume": raw["Volume"],
            "raw_close": raw["Close"] * later_splits,
            "raw_volume": raw["Volume"] / later_splits,
        },
        index=raw.index,
    )


class YahooPrices:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def fetch(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        source = self._yf.Ticker(yahoo_symbol(ticker))
        raw = source.history(
            start=start.date(),
            end=(end + pd.Timedelta(days=1)).date(),
            auto_adjust=False,
            actions=False,
        )
        if raw is None or raw.empty:
            raise NoData(ticker)
        bars = normalize_bars(adjusted_and_traded(raw, source.splits))
        if bars.empty:
            raise NoData(ticker)
        return bars


class YahooEarnings:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def fetch(self, ticker: str) -> EarningsHistory:
        try:
            frame = self._yf.Ticker(yahoo_symbol(ticker)).get_earnings_dates(limit=100)
        except Exception as exc:
            if not _no_data_error(exc):
                raise
            log.info("%s: no earnings dates (%s)", ticker, exc)
            return EarningsHistory([], None)
        if frame is None or frame.empty:
            return EarningsHistory([], None)
        days = sorted(set(to_day(frame.index)))
        return EarningsHistory(days, days[0])


class YahooSectors:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def sector(self, ticker: str) -> str:
        try:
            info = self._yf.Ticker(yahoo_symbol(ticker)).info or {}
        except Exception as exc:
            if not _no_data_error(exc):
                raise
            log.info("%s: no sector (%s)", ticker, exc)
            return "Unknown"
        return info.get("sector") or "Unknown"
