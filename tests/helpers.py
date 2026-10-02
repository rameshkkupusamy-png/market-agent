"""Small builders for synthetic test data."""

import pandas as pd

from market_agent.settings import Settings


def make_bars(closes, volumes=None, start="2023-01-02", spread=0.01):
    """Business-day bars: open = close, high/low = close ± spread."""
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
