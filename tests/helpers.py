"""Small builders for synthetic test data."""

import pandas as pd

from market_agent.data.sources import Headline
from market_agent.reviewer import ReviewInput
from market_agent.settings import Settings


def make_bars(closes, volumes=None, start="2023-01-02", spread=0.01, traded=1.0):
    """Business-day bars: open = close, high/low = close ± spread.

    As traded, prices were `traded` times the adjusted ones (e.g. 4 before a later 4:1 split).
    """
    days = pd.bdate_range(start, periods=len(closes), name="day")
    volumes = volumes if volumes is not None else [1_000_000.0] * len(closes)
    closes = [float(c) for c in closes]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c * (1 + spread) for c in closes],
            "low": [c * (1 - spread) for c in closes],
            "close": closes,
            "volume": [float(v) for v in volumes],
            "raw_close": [c * traded for c in closes],
            "raw_volume": [float(v) / traded for v in volumes],
        },
        index=days,
    )


def settings_with(**sections):
    """Settings with some fields changed, e.g. settings_with(risk={"max_positions": 2})."""
    from dataclasses import replace

    base = Settings()
    return replace(
        base,
        **{name: replace(getattr(base, name), **values) for name, values in sections.items()},
    )


# A review candidate shared by the reviewer's unit and live tests.
HEADLINE = Headline(
    pd.Timestamp("2026-10-01 14:30", tz="UTC"),
    "Reuters",
    "Nvidia wins order",
    "A large cloud order.",
)
ITEM = ReviewInput(
    ticker="NVDA",
    company="NVIDIA Corporation",
    sector="Technology",
    day=pd.Timestamp("2026-10-02"),
    close=187.62,
    sma_fast=175.4,
    sma_slow=150.25,
    volume_ratio=1.83,
    atr=5.1,
    return_63=0.214,
    next_earnings=pd.Timestamp("2026-11-19"),
    headlines=[HEADLINE],
)
