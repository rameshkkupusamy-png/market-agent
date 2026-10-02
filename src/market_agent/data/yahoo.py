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


class YahooPrices:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def fetch(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        raw = self._yf.Ticker(yahoo_symbol(ticker)).history(
            start=start.date(),
            end=(end + pd.Timedelta(days=1)).date(),
            auto_adjust=True,
            actions=False,
        )
        if raw is None or raw.empty:
            raise NoData(ticker)
        bars = normalize_bars(raw)
        if bars.empty:
            raise NoData(ticker)
        return bars


class YahooEarnings:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def fetch(self, ticker: str) -> EarningsHistory:
        try:
            frame = self._yf.Ticker(yahoo_symbol(ticker)).get_earnings_dates(limit=100)
        except Exception as exc:  # yfinance raises many kinds of errors for missing data
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
            log.info("%s: no sector (%s)", ticker, exc)
            return "Unknown"
        return info.get("sector") or "Unknown"
