"""SQLite storage for cached market data and backtest results."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from market_agent.data.sources import BAR_COLUMNS, EarningsHistory

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (ticker, day));
CREATE TABLE IF NOT EXISTS price_meta (
    ticker TEXT PRIMARY KEY, fetched_on TEXT, has_data INTEGER);
CREATE TABLE IF NOT EXISTS earnings (ticker TEXT, day TEXT, PRIMARY KEY (ticker, day));
CREATE TABLE IF NOT EXISTS earnings_meta (
    ticker TEXT PRIMARY KEY, fetched_on TEXT, coverage_start TEXT);
CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, period TEXT, start TEXT, end TEXT,
    fingerprint TEXT, settings TEXT, metrics TEXT, benchmark TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS backtest_trades (
    backtest_id INTEGER, ticker TEXT, entry_day TEXT, entry_price REAL, exit_day TEXT,
    exit_price REAL, shares INTEGER, exit_reason TEXT, pnl REAL);
CREATE TABLE IF NOT EXISTS backtest_equity (backtest_id INTEGER, day TEXT, equity REAL);
"""


def _day(value: pd.Timestamp) -> str:
    return value.strftime("%Y-%m-%d")


def _json_default(value: Any) -> Any:
    """Keep numpy scalars as JSON numbers; write anything else (e.g. Timestamps) as text."""
    if hasattr(value, "item") and not isinstance(value, pd.Timestamp):
        return value.item()
    return str(value)


class Store:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    # prices
    def replace_prices(self, ticker: str, bars: pd.DataFrame, fetched_on: str) -> None:
        rows = [
            (ticker, _day(day), *(float(row[c]) for c in BAR_COLUMNS))
            for day, row in bars.iterrows()
        ]
        with self._conn:
            self._conn.execute("DELETE FROM prices WHERE ticker = ?", (ticker,))
            self._conn.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            self._conn.execute(
                "INSERT OR REPLACE INTO price_meta VALUES (?, ?, 1)", (ticker, fetched_on)
            )

    def mark_missing(self, ticker: str, fetched_on: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM prices WHERE ticker = ?", (ticker,))
            self._conn.execute(
                "INSERT OR REPLACE INTO price_meta VALUES (?, ?, 0)", (ticker, fetched_on)
            )

    def fetched_on(self, ticker: str) -> str | None:
        row = self._conn.execute(
            "SELECT fetched_on FROM price_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row[0] if row else None

    def load_prices(self, ticker: str) -> pd.DataFrame | None:
        rows = self._conn.execute(
            "SELECT day, open, high, low, close, volume FROM prices WHERE ticker = ? ORDER BY day",
            (ticker,),
        ).fetchall()
        if not rows:
            return None
        frame = pd.DataFrame(rows, columns=["day", *BAR_COLUMNS])
        frame["day"] = pd.to_datetime(frame["day"])
        return frame.set_index("day")

    def missing_tickers(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT ticker FROM price_meta WHERE has_data = 0 ORDER BY ticker"
        ).fetchall()
        return [r[0] for r in rows]

    def attempted_tickers(self) -> set[str]:
        """Tickers a fetch has tried, with or without data."""
        return {r[0] for r in self._conn.execute("SELECT ticker FROM price_meta")}

    def tickers_with_prices(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT ticker FROM price_meta WHERE has_data = 1 ORDER BY ticker"
        ).fetchall()
        return [r[0] for r in rows]

    # earnings
    def replace_earnings(self, ticker: str, history: EarningsHistory, fetched_on: str) -> None:
        coverage = _day(history.coverage_start) if history.coverage_start is not None else None
        with self._conn:
            self._conn.execute("DELETE FROM earnings WHERE ticker = ?", (ticker,))
            self._conn.executemany(
                "INSERT OR IGNORE INTO earnings VALUES (?, ?)",
                [(ticker, _day(d)) for d in history.dates],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO earnings_meta VALUES (?, ?, ?)",
                (ticker, fetched_on, coverage),
            )

    def earnings_fetched_on(self, ticker: str) -> str | None:
        row = self._conn.execute(
            "SELECT fetched_on FROM earnings_meta WHERE ticker = ?", (ticker,)
        ).fetchone()
        return row[0] if row else None

    def load_earnings(self) -> dict[str, EarningsHistory]:
        dates: dict[str, list[pd.Timestamp]] = {}
        for ticker, day in self._conn.execute("SELECT ticker, day FROM earnings ORDER BY day"):
            dates.setdefault(ticker, []).append(pd.Timestamp(day))
        result = {}
        for ticker, coverage in self._conn.execute(
            "SELECT ticker, coverage_start FROM earnings_meta"
        ):
            start = pd.Timestamp(coverage) if coverage else None
            result[ticker] = EarningsHistory(dates.get(ticker, []), start)
        return result

    # backtest results
    def save_backtest(
        self,
        period: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        fingerprint: str,
        settings: dict[str, Any],
        metrics: dict[str, Any],
        benchmark: dict[str, Any],
        notes: dict[str, Any],
        trades: list[Any],
        equity: pd.Series,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO backtests (created_at, period, start, end, fingerprint, settings, "
                "metrics, benchmark, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    period,
                    _day(start),
                    _day(end),
                    fingerprint,
                    json.dumps(settings, default=_json_default),
                    json.dumps(metrics, default=_json_default),
                    json.dumps(benchmark, default=_json_default),
                    json.dumps(notes, default=_json_default),
                ),
            )
            backtest_id = cursor.lastrowid
            self._conn.executemany(
                "INSERT INTO backtest_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        backtest_id,
                        t.ticker,
                        _day(t.entry_day),
                        t.entry_price,
                        _day(t.exit_day),
                        t.exit_price,
                        t.shares,
                        t.exit_reason,
                        t.pnl,
                    )
                    for t in trades
                ],
            )
            self._conn.executemany(
                "INSERT INTO backtest_equity VALUES (?, ?, ?)",
                [(backtest_id, _day(day), float(value)) for day, value in equity.items()],
            )
        return backtest_id

    def backtest_runs(self, period: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, created_at, fingerprint, metrics, benchmark FROM backtests "
            "WHERE period = ? ORDER BY id",
            (period,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "created_at": r[1],
                "fingerprint": r[2],
                "metrics": json.loads(r[3]),
                "benchmark": json.loads(r[4]),
            }
            for r in rows
        ]
