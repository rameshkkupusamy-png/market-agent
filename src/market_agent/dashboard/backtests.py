"""Backtest page: every saved run against the benchmark, with the settings it used."""

from __future__ import annotations

from typing import Any

import pandas as pd

from market_agent.settings import Settings
from market_agent.store import Store

RUN_COLUMNS = [
    "id",
    "period",
    "start",
    "end",
    "created_at",
    "fingerprint",
    "total_return",
    "cagr",
    "max_drawdown",
    "trades",
    "win_rate",
    "spy_total_return",
    "spy_cagr",
    "spy_max_drawdown",
]


def backtest_runs(store: Store) -> pd.DataFrame:
    rows = [
        {
            "id": r["id"],
            "period": r["period"],
            "start": r["start"],
            "end": r["end"],
            "created_at": r["created_at"],
            "fingerprint": r["fingerprint"],
            "total_return": r["metrics"].get("total_return"),
            "cagr": r["metrics"].get("cagr"),
            "max_drawdown": r["metrics"].get("max_drawdown"),
            "trades": r["metrics"].get("trades"),
            "win_rate": r["metrics"].get("win_rate"),
            "spy_total_return": r["benchmark"].get("total_return"),
            "spy_cagr": r["benchmark"].get("cagr"),
            "spy_max_drawdown": r["benchmark"].get("max_drawdown"),
        }
        for r in store.backtest_list()
    ]
    return pd.DataFrame(rows, columns=RUN_COLUMNS)


def backtest_curve(store: Store, settings: Settings, run_id: int) -> pd.DataFrame:
    equity = store.backtest_equity(run_id)
    if equity.empty:
        return pd.DataFrame()
    curve = pd.DataFrame({"strategy": equity})
    benchmark = settings.data.benchmark
    prices = store.load_prices(benchmark)
    if prices is not None:
        close = prices["close"].reindex(curve.index)
        if close.notna().any():
            curve[benchmark] = close / close.dropna().iloc[0] * equity.iloc[0]
    return curve


def backtest_settings(store: Store, run_id: int) -> dict[str, Any]:
    for run in store.backtest_list():
        if run["id"] == run_id:
            return run["settings"]
    return {}
