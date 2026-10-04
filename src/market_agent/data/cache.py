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


def utc_now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


class PriceCache:
    def __init__(
        self,
        store: Store,
        source: PriceSource,
        today: Callable[[], date] = date.today,
        now: Callable[[], pd.Timestamp] = utc_now,
    ):
        """today: the local date stamp; now: the UTC time recorded with each download."""
        self._store = store
        self._source = source
        self._today = today
        self._now = now

    def _fresh(self, ticker: str, stamp: str, fresh_after: pd.Timestamp | None) -> bool:
        if self._store.fetched_on(ticker) != stamp:
            return False
        if fresh_after is None:
            return True
        fetched_at = self._store.fetched_at(ticker)
        return fetched_at is not None and fetched_at >= fresh_after

    def update(
        self,
        ticker: str,
        start: date,
        force: bool = False,
        fresh_after: pd.Timestamp | None = None,
    ) -> bool:
        """Make sure the cache holds `ticker` up to today. Returns False if there is no data.

        force: download again even if it was downloaded today (the day's data was late).
        fresh_after: also download again if the last download was before this time (UTC), so
        bars cached before the day's data settled are replaced.
        """
        today = self._today()
        stamp = today.isoformat()
        if not force and self._fresh(ticker, stamp, fresh_after):
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
            self._store.mark_missing(ticker, stamp, self._now())
            return False
        self._store.replace_prices(ticker, bars, stamp, self._now())
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
