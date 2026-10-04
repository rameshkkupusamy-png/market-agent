"""Download each ticker at most once a day and keep it in SQLite.

Adjusted prices change for the whole history after each dividend or split, so an update replaces
the whole series instead of appending days.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

import pandas as pd

from market_agent.data.sources import EarningsSource, NoData, PriceSource
from market_agent.store import Store

log = logging.getLogger(__name__)


class PriceCache:
    def __init__(self, store: Store, source: PriceSource, today: Callable[[], date] = date.today):
        self._store = store
        self._source = source
        self._today = today

    def update(self, ticker: str, start: date, force: bool = False) -> bool:
        """Make sure the cache holds `ticker` up to today. Returns False if there is no data.

        force: download again even if it was downloaded today (the day's data was late).
        """
        today = self._today()
        stamp = today.isoformat()
        if not force and self._store.fetched_on(ticker) == stamp:
            return self._store.load_prices(ticker) is not None
        try:
            bars = self._source.fetch(ticker, pd.Timestamp(start), pd.Timestamp(today))
            if bars.empty:
                raise NoData(ticker)
        except NoData:
            # Yahoo also returns nothing on errors and rate limits: never let that wipe history.
            if self._store.load_prices(ticker) is not None:
                log.warning("%s: no price data this time, keeping the cached prices", ticker)
                return True
            log.info("%s: no price data", ticker)
            self._store.mark_missing(ticker, stamp)
            return False
        self._store.replace_prices(ticker, bars, stamp)
        return True

    def load(self, ticker: str) -> pd.DataFrame | None:
        return self._store.load_prices(ticker)


class EarningsCache:
    def __init__(
        self, store: Store, source: EarningsSource, today: Callable[[], date] = date.today
    ):
        self._store = store
        self._source = source
        self._today = today

    def update(self, ticker: str) -> None:
        """A source error propagates and leaves the stored dates untouched."""
        stamp = self._today().isoformat()
        if self._store.earnings_fetched_on(ticker) == stamp:
            return
        self._store.replace_earnings(ticker, self._source.fetch(ticker), stamp)
