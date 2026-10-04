"""The data check before a day is traded (spec section 3, "Daily data checks")."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from market_agent.panel import Panel


@dataclass(frozen=True)
class DayCheck:
    ok: bool
    eligible: int
    excluded: list[str]  # eligible tickers without a usable price that day (left out)
    reason: str | None  # why nothing trades, when not ok


def check_day(
    panel: Panel,
    members: Iterable[str],
    day: pd.Timestamp,
    sessions: list[pd.Timestamp],
    benchmark: str,
    max_missing_share: float,
    alive_sessions: int,
) -> DayCheck:
    """A missing price includes a bad jump: the panel drops those days."""
    if panel.bar(benchmark, day) is None:
        return DayCheck(False, 0, [], f"no {benchmark} price for {day:%Y-%m-%d}")
    before = [d for d in sessions if d < day][-alive_sessions:]
    if not before:
        return DayCheck(True, 0, [], None)
    start, previous = before[0], before[-1]
    eligible = sorted(
        t
        for t in members
        if t != benchmark
        and (end := panel.last_bar_until(t, previous)) is not None
        and end >= start
    )
    excluded = [t for t in eligible if panel.bar(t, day) is None]
    if len(excluded) > max_missing_share * len(eligible):
        return DayCheck(
            False,
            len(eligible),
            excluded,
            f"{len(excluded)} of {len(eligible)} shares have no usable price for {day:%Y-%m-%d}",
        )
    return DayCheck(True, len(eligible), excluded, None)
