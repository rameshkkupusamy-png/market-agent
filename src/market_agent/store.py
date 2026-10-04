"""SQLite storage for cached market data and backtest results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from market_agent.data.sources import PRICE_COLUMNS, EarningsHistory

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT, day TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
    raw_close REAL, raw_volume REAL, PRIMARY KEY (ticker, day));
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
CREATE TABLE IF NOT EXISTS paper_states (
    portfolio TEXT, day TEXT, state TEXT, PRIMARY KEY (portfolio, day));
CREATE TABLE IF NOT EXISTS daily_runs (
    day TEXT PRIMARY KEY, created_at TEXT, status TEXT, report TEXT, alerts TEXT, sent INTEGER);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, day TEXT, ticker TEXT, status TEXT,
    verdict TEXT, confidence TEXT, reasons TEXT, risks TEXT, news_used TEXT, note TEXT,
    late INTEGER, model TEXT, prompt TEXT, answer TEXT, cost REAL);
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
        self._add_traded_prices()

    def _add_traded_prices(self) -> None:
        """Databases from before as-traded prices: add the columns, download prices again."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(prices)")}
        if "raw_close" in columns:
            return
        with self._conn:
            self._conn.execute("ALTER TABLE prices ADD COLUMN raw_close REAL")
            self._conn.execute("ALTER TABLE prices ADD COLUMN raw_volume REAL")
            self._conn.execute("UPDATE price_meta SET fetched_on = NULL WHERE has_data = 1")

    def close(self) -> None:
        self._conn.close()

    # prices
    def replace_prices(self, ticker: str, bars: pd.DataFrame, fetched_on: str) -> None:
        rows = [
            (ticker, _day(day), *(float(row[c]) for c in PRICE_COLUMNS))
            for day, row in bars.iterrows()
        ]
        with self._conn:
            self._conn.execute("DELETE FROM prices WHERE ticker = ?", (ticker,))
            self._conn.executemany(
                f"INSERT INTO prices (ticker, day, {', '.join(PRICE_COLUMNS)}) "
                f"VALUES ({', '.join('?' * (len(PRICE_COLUMNS) + 2))})",
                rows,
            )
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
            f"SELECT day, {', '.join(PRICE_COLUMNS)} FROM prices WHERE ticker = ? ORDER BY day",
            (ticker,),
        ).fetchall()
        if not rows:
            return None
        frame = pd.DataFrame(rows, columns=["day", *PRICE_COLUMNS])
        frame["day"] = pd.to_datetime(frame["day"])
        return frame.set_index("day").astype(float)

    def tickers_without_traded_prices(self) -> list[str]:
        """Tickers cached before as-traded prices were stored (or kept from such a cache)."""
        rows = self._conn.execute(
            "SELECT DISTINCT ticker FROM prices WHERE raw_close IS NULL ORDER BY ticker"
        ).fetchall()
        return [r[0] for r in rows]

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

    # paper trading
    def save_day(
        self,
        day: pd.Timestamp,
        states: Mapping[str, str],
        status: str,
        report: str,
        alerts: list[str],
    ) -> None:
        """Both portfolios and the day's run record, in one transaction."""
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO paper_states VALUES (?, ?, ?)",
                [(name, _day(day), state) for name, state in states.items()],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_runs VALUES (?, ?, ?, ?, ?, 0)",
                (
                    _day(day),
                    datetime.now().isoformat(timespec="seconds"),
                    status,
                    report,
                    json.dumps(alerts),
                ),
            )

    def _paper_day(self, sql: str) -> pd.Timestamp | None:
        value = self._conn.execute(sql).fetchone()[0]
        return pd.Timestamp(value) if value else None

    def latest_paper_day(self) -> pd.Timestamp | None:
        return self._paper_day("SELECT MAX(day) FROM paper_states")

    def first_paper_day(self) -> pd.Timestamp | None:
        return self._paper_day("SELECT MIN(day) FROM paper_states")

    def paper_states(self, day: pd.Timestamp) -> dict[str, str]:
        rows = self._conn.execute(
            "SELECT portfolio, state FROM paper_states WHERE day = ?", (_day(day),)
        )
        return dict(rows.fetchall())

    def replace_paper_state(self, portfolio: str, day: pd.Timestamp, state: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE paper_states SET state = ? WHERE portfolio = ? AND day = ?",
                (state, portfolio, _day(day)),
            )

    def _run(self, row: tuple | None) -> dict[str, Any] | None:
        if row is None:
            return None
        day, created_at, status, report, alerts, sent = row
        return {
            "day": pd.Timestamp(day),
            "created_at": created_at,
            "status": status,
            "report": report,
            "alerts": json.loads(alerts),
            "sent": bool(sent),
        }

    def daily_run(self, day: pd.Timestamp) -> dict[str, Any] | None:
        return self._run(
            self._conn.execute(
                "SELECT day, created_at, status, report, alerts, sent FROM daily_runs "
                "WHERE day = ?",
                (_day(day),),
            ).fetchone()
        )

    def latest_daily_run(self) -> dict[str, Any] | None:
        return self._run(
            self._conn.execute(
                "SELECT day, created_at, status, report, alerts, sent FROM daily_runs "
                "ORDER BY day DESC LIMIT 1"
            ).fetchone()
        )

    def mark_sent(self, day: pd.Timestamp) -> None:
        with self._conn:
            self._conn.execute("UPDATE daily_runs SET sent = 1 WHERE day = ?", (_day(day),))

    # AI reviews
    def save_review(self, day: pd.Timestamp, review: Any, late: bool) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO reviews (created_at, day, ticker, status, verdict, confidence, "
                "reasons, risks, news_used, note, late, model, prompt, answer, cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    _day(day),
                    review.ticker,
                    review.status,
                    review.verdict,
                    review.confidence,
                    json.dumps(review.reasons),
                    json.dumps(review.risks),
                    json.dumps(review.news_used),
                    review.note,
                    int(late),
                    review.model,
                    review.prompt,
                    review.answer,
                    review.cost,
                ),
            )

    def ai_spent_since(self, since: datetime) -> float:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM reviews WHERE created_at >= ?",
            (since.isoformat(timespec="seconds"),),
        ).fetchone()
        return float(row[0])

    def reviews_on(self, day: pd.Timestamp) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ticker, status, verdict, confidence, reasons, risks, news_used, note, late, "
            "model, cost FROM reviews WHERE day = ? ORDER BY id",
            (_day(day),),
        ).fetchall()
        keys = (
            "ticker",
            "status",
            "verdict",
            "confidence",
            "reasons",
            "risks",
            "news_used",
            "note",
            "late",
            "model",
            "cost",
        )
        result = []
        for row in rows:
            record = dict(zip(keys, row, strict=True))
            for key in ("reasons", "risks", "news_used"):
                record[key] = json.loads(record[key])
            record["late"] = bool(record["late"])
            result.append(record)
        return result
