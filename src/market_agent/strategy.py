"""The breakout strategy: which shares to buy on day D (spec section 4)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from market_agent.earnings import EarningsCalendar
from market_agent.settings import StrategySettings

REQUIRED = [
    "close",
    "raw_close",
    "volume",
    "sma_fast",
    "sma_slow",
    "atr",
    "high_close",
    "avg_volume_prev",
    "avg_value",
    "ret_rank",
]


@dataclass(frozen=True)
class Candidate:
    ticker: str
    day: pd.Timestamp
    close: float
    atr: float
    score: float  # 63-day return, used for ranking
    volume_ratio: float
    earnings_known: bool


def screen(
    day: pd.Timestamp,
    snapshot: pd.DataFrame,
    members: Iterable[str],
    earnings: EarningsCalendar,
    s: StrategySettings,
) -> list[Candidate]:
    snap = snapshot.loc[snapshot.index.intersection(sorted(set(members)))]
    snap = snap.dropna(subset=REQUIRED)
    signal = (
        (snap["raw_close"] > s.min_price)  # as traded: adjusted prices can be far lower
        & (snap["avg_value"] > s.min_traded_value)
        & (snap["close"] > snap["sma_slow"])
        & (snap["sma_fast"] > snap["sma_slow"])
        & (snap["close"] >= snap["high_close"])
        & (snap["avg_volume_prev"] > 0)
        & (snap["volume"] >= s.volume_ratio * snap["avg_volume_prev"])
    )
    candidates = []
    for ticker, row in snap[signal].iterrows():
        if earnings.reports_within(ticker, day, s.earnings_buffer_days):
            continue
        candidates.append(
            Candidate(
                ticker=str(ticker),
                day=day,
                close=float(row["close"]),
                atr=float(row["atr"]),
                score=float(row["ret_rank"]),
                volume_ratio=round(float(row["volume"] / row["avg_volume_prev"]), 2),
                earnings_known=earnings.known(str(ticker), day),
            )
        )
    candidates.sort(key=lambda c: (-c.score, c.ticker))
    return candidates
