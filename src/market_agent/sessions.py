"""US trading sessions (NYSE calendar): which days the daily run processes."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

import pandas as pd


class TradingCalendar:
    def __init__(self, sessions: Sequence[pd.Timestamp], closes: Sequence[pd.Timestamp]):
        """sessions are tz-naive dates; closes are each session's closing time in UTC."""
        self.sessions = [pd.Timestamp(s) for s in sessions]
        self._closes = [pd.Timestamp(c) for c in closes]

    @classmethod
    def nyse(cls, start: pd.Timestamp, end: pd.Timestamp) -> TradingCalendar:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS", start=start, end=end)
        all_sessions = calendar.sessions
        sessions = all_sessions[(all_sessions >= start) & (all_sessions <= end)]
        return cls(list(sessions), [calendar.session_close(s) for s in sessions])

    def latest_closed(self, now: pd.Timestamp) -> pd.Timestamp | None:
        """The last session whose close is at or before `now`."""
        index = bisect_right(self._closes, now) - 1
        return self.sessions[index] if index >= 0 else None

    def close(self, session: pd.Timestamp) -> pd.Timestamp:
        """The session's closing time in UTC."""
        return self._closes[self.sessions.index(session)]

    def between(self, after: pd.Timestamp | None, until: pd.Timestamp) -> list[pd.Timestamp]:
        return [s for s in self.sessions if (after is None or s > after) and s <= until]

    def up_to(self, day: pd.Timestamp) -> list[pd.Timestamp]:
        return [s for s in self.sessions if s <= day]
