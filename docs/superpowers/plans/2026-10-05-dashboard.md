# Market agent: dashboard implementation plan (plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only, local Streamlit dashboard (`agent dashboard`) with the five pages from spec section 9: Overview, Today, Positions and trades, AI review (with the "what if" for skipped candidates), and Backtest.

**Architecture:** Data shaping lives in plain functions under `market_agent/dashboard/` (`reviews.py`, `paper.py`, `backtests.py`) that read the SQLite `Store` and return pandas DataFrames, so they are tested without Streamlit. A thin `dashboard/app.py` script renders those frames with Streamlit; it opens the database read-only, so it can stay open while the 06:30 run writes. `agent dashboard` launches Streamlit on localhost with the database path in an environment variable. Docker (the rest of the original plan 3) is left out: the agent runs on the owner's PC with Task Scheduler, and Docker only matters for a later move to a cloud server.

**Tech Stack:** Python 3.12, pandas, SQLite, Streamlit (>= 1.40; 1.65 current) with `streamlit.testing.v1.AppTest` for app tests, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-02-market-agent-design.md` (section 9 "Dashboard", section 10 "Telegram fails → report saved, visible on dashboard"). Plans 1 and 2: `docs/superpowers/plans/2026-10-02-data-and-backtest.md`, `docs/superpowers/plans/2026-10-03-paper-trading.md`.

## Global Constraints

- Python `>=3.12`; ruff line length 100, rules `E,F,I,UP,B`; run `ruff check --fix .` and `ruff format .` before every commit. CI runs bare `pytest`: tests share fixtures through `tests/helpers.py` (imported as `helpers`), never `from tests...`.
- Dashboard (spec §9): Streamlit, localhost, read-only. Pages: 1 Overview: equity curves of both portfolios vs SPY; return, max drawdown, win rate, average win/loss, trades. 2 Today: candidates, AI verdicts with reasons and news used, orders for tomorrow. 3 Positions and trades: open (entry, stop, target, days held) and closed (result, exit reason). 4 AI review: every review, and how the skipped candidates would have done ("what if"). 5 Backtest: tuning and test periods vs SPY, with the settings used.
- Spec §9/§10: if Telegram fails, the report is still saved and shown on the dashboard.
- The dashboard never writes to the database and never reaches the network; it binds to `localhost` only.
- The portfolios are named `rules-only` and `rules+ai`; the benchmark is `settings.data.benchmark` (default `SPY`); starting cash is `settings.risk.starting_cash` (default 10,000).
- Normal tests use no network.
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The owner opens the dashboard while the 06:30 run is writing.** Expected: the dashboard never writes, never creates tables, never migrates, and can't block the run. Pinned by: Task 1 `test_read_only_store_refuses_writes_and_creates_nothing`, Task 5 `test_dashboard_never_changes_the_database`.
2. **The dashboard is opened before the first `run-daily` (only prices and backtests exist).** Expected: every page renders with a plain "nothing yet" message, no traceback. Pinned by: Task 5 `test_pages_before_any_paper_run`.
3. **`agent dashboard` is started from the wrong folder, so the database file doesn't exist.** Expected: a clear message saying where it looked, no traceback, no new empty database created. Pinned by: Task 5 `test_missing_database_is_explained`.
4. **Claude skipped a candidate on the latest day (no later prices yet) or one whose prices are gone.** Expected: its "what if" row says "not filled yet" / "no data" instead of crashing the page. Pinned by: Task 2 `test_what_if_without_later_prices`.
5. **A stored review names a headline number that isn't in its prompt.** Expected: that index is ignored; the known headlines still show. Pinned by: Task 2 `test_headlines_used_ignores_unknown_indexes`.

---

## File structure

```
src/market_agent/
  store.py                 MOD  Store(path, read_only=False); paper_days(), all_reviews(),
                                backtest_list(), backtest_equity(id)
  dashboard/__init__.py    NEW  (empty)
  dashboard/reviews.py     NEW  review_table(), headlines_used(), what_if()
  dashboard/paper.py       NEW  latest_portfolios(), equity_curves(), performance(),
                                open_positions(), closed_trades(), today()
  dashboard/backtests.py   NEW  backtest_runs(), backtest_curve(), backtest_settings()
  dashboard/app.py         NEW  the Streamlit script (five pages)
  cli.py                   MOD  `agent dashboard [--port]`
tests/
  helpers.py               MOD  dashboard_db(path): a small database for all dashboard tests
  test_store_cache.py      MOD
  test_dashboard_reviews.py, test_dashboard_paper.py, test_dashboard_backtests.py,
  test_dashboard_app.py    NEW
pyproject.toml             MOD  streamlit dependency
README.md, CLAUDE.md       MOD
```

---

### Task 1: Read-only store, dashboard queries and the shared test database

**Files:**
- Modify: `src/market_agent/store.py`, `tests/helpers.py`
- Test: `tests/test_store_cache.py`

**Interfaces:**
- Produces:
  - `Store(path: Path | str, read_only: bool = False)`. Read-only: the file must exist (`FileNotFoundError` otherwise); opened with SQLite `mode=ro`; no schema, no migrations, no folder creation.
  - `Store.paper_days() -> list[pd.Timestamp]` (ascending, distinct days in `paper_states`).
  - `Store.all_reviews() -> list[dict]`, newest day first, then newest id. Keys: `day` (Timestamp), `ticker`, `status`, `verdict`, `confidence`, `reasons`, `risks`, `news_used` (lists), `note`, `late` (bool), `model`, `prompt`, `answer`, `cost`, `created_at`.
  - `Store.backtest_list() -> list[dict]`, newest first. Keys: `id`, `created_at`, `period`, `start`, `end` (Timestamps), `fingerprint`, `settings`, `metrics`, `benchmark`, `notes` (decoded JSON).
  - `Store.backtest_equity(backtest_id: int) -> pd.Series` (index = day Timestamps, name `"equity"`).
  - `helpers.dashboard_db(path: Path) -> Path`: builds the database described in Step 1 and returns its path.

- [ ] **Step 1: Add the shared test database to `tests/helpers.py`**

Add these imports to the top of `tests/helpers.py` (merge with the existing ones; ruff sorts them):

```python
from types import SimpleNamespace

from market_agent.portfolio import Order, Portfolio, Position, Trade, portfolio_to_json
from market_agent.store import Store
```

and append:

```python
DASHBOARD_DAYS = pd.bdate_range("2026-08-03", periods=40)


def dashboard_db(path):
    """A small database for the dashboard: prices, two paper days, two reviews, one backtest.

    AAA rises 1 a day from 100 and SPY 0.5 a day from 400 over DASHBOARD_DAYS. Claude skipped
    AAA 20 sessions before the end (the "what if" trade then reaches its target) and approved
    CCC on the last day. rules-only holds 10 AAA, has one closed BBB trade and a pending CCC
    order; rules+ai holds cash only.
    """
    days = DASHBOARD_DAYS
    store = Store(path)
    spy = make_bars([400 + 0.5 * i for i in range(40)], start="2026-08-03")
    store.replace_prices("SPY", spy, "2026-09-25")
    store.replace_prices(
        "AAA", make_bars([100 + i for i in range(40)], start="2026-08-03"), "2026-09-25"
    )
    d1, d2 = days[-2], days[-1]
    position = Position("AAA", "Tech", 10, days[-3], 137.137, 133.0, 145.0, 139.0, 2, d2)
    trade = Trade("BBB", "Energy", days[-12], 50.0, days[-8], 55.0, 20, "target", 98.0)
    rules = Portfolio(
        "rules-only",
        8_600.0,
        positions={"AAA": position},
        orders=[Order("CCC", "buy", 5, "breakout", d2, 2.0, "Tech", ref_close=80.0)],
        trades=[trade],
        equity_history=[(d1, 9_900.0), (d2, 10_090.0)],
        peak=10_090.0,
    )
    ai = Portfolio(
        "rules+ai", 10_000.0, equity_history=[(d1, 10_000.0), (d2, 10_000.0)], peak=10_000.0
    )
    states = {p.name: portfolio_to_json(p) for p in (rules, ai)}
    store.save_day(d1, states, "traded", "Market agent, report one", [])
    store.save_day(
        d2,
        states,
        "traded",
        f"Market agent, {d2:%Y-%m-%d} (paper trading, simulated money)",
        ["rules+ai: circuit breaker on"],
    )
    store.mark_sent(d2)
    prompt = (
        "Candidate: AAA (Alpha), sector Tech\n\nHeadlines, newest first:\n"
        "[0] 2026-09-01 Reuters: Alpha cuts guidance\n    Weak quarter.\n"
        "[1] 2026-09-02 CNBC: Alpha hires a CFO"
    )
    skip = SimpleNamespace(
        ticker="AAA",
        status="reviewed",
        verdict="skip",
        confidence="high",
        reasons=["Guidance cut"],
        risks=["Weak demand"],
        news_used=[0, 5],
        note="",
        cost=0.02,
        model="claude-opus-5-5",
        prompt=prompt,
        answer="{}",
    )
    store.save_review(days[-20], skip, late=False)
    approve = SimpleNamespace(
        ticker="CCC",
        status="reviewed",
        verdict="approve",
        confidence="medium",
        reasons=["no relevant news"],
        risks=[],
        news_used=[],
        note="",
        cost=0.015,
        model="claude-opus-5-5",
        prompt="Candidate: CCC",
        answer="{}",
    )
    store.save_review(d2, approve, late=False)
    equity = pd.Series([10_000.0 + 10 * i for i in range(40)], index=days)
    store.save_backtest(
        "tuning",
        days[0],
        days[-1],
        "abc123def456",
        {"strategy": {"volume_ratio": 1.5}, "risk": {"starting_cash": 10000.0}},
        {
            "total_return": 0.039,
            "cagr": 0.3,
            "max_drawdown": 0.0,
            "trades": 1,
            "win_rate": 1.0,
            "avg_win": 0.1,
            "avg_loss": 0.0,
            "exposure": 0.5,
        },
        {"total_return": 0.0488, "cagr": 0.35, "max_drawdown": 0.0},
        {},
        [trade],
        equity,
    )
    store.close()
    return path
```

(`ruff format` will reflow the `SimpleNamespace(...)` and dict literals; that's fine.)

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_store_cache.py` (`sqlite3` and `pytest` are already imported; add
`from helpers import DASHBOARD_DAYS, dashboard_db`):

```python
def test_read_only_store_refuses_writes_and_creates_nothing(tmp_path):
    db = dashboard_db(tmp_path / "market.db")
    before = db.read_bytes()
    store = Store(db, read_only=True)
    assert store.latest_paper_day() == DASHBOARD_DAYS[-1]
    with pytest.raises(sqlite3.OperationalError):
        store.mark_sent(DASHBOARD_DAYS[-2])
    store.close()
    assert db.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        Store(tmp_path / "nothing" / "market.db", read_only=True)
    assert not (tmp_path / "nothing").exists()


def test_dashboard_queries(tmp_path):
    store = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    assert store.paper_days() == [DASHBOARD_DAYS[-2], DASHBOARD_DAYS[-1]]
    reviews = store.all_reviews()
    assert [(r["day"], r["ticker"], r["verdict"]) for r in reviews] == [
        (DASHBOARD_DAYS[-1], "CCC", "approve"),
        (DASHBOARD_DAYS[-20], "AAA", "skip"),
    ]
    assert reviews[1]["news_used"] == [0, 5] and reviews[1]["prompt"].startswith("Candidate: AAA")
    assert reviews[1]["late"] is False
    [run] = store.backtest_list()
    assert (run["id"], run["period"], run["fingerprint"]) == (1, "tuning", "abc123def456")
    assert (run["start"], run["end"]) == (DASHBOARD_DAYS[0], DASHBOARD_DAYS[-1])
    assert run["settings"] == {
        "strategy": {"volume_ratio": 1.5},
        "risk": {"starting_cash": 10000.0},
    }
    assert run["metrics"]["total_return"] == 0.039 and run["benchmark"]["total_return"] == 0.0488
    equity = store.backtest_equity(1)
    assert list(equity.index) == list(DASHBOARD_DAYS)
    assert equity.iloc[0] == 10_000.0 and equity.name == "equity"
    store.close()
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_store_cache.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'read_only'`, `AttributeError: 'Store' object has no attribute 'paper_days'`).

- [ ] **Step 4: Implement in `store.py`**

Replace `__init__` with:

```python
    def __init__(self, path: Path | str, read_only: bool = False):
        """read_only: for the dashboard — the file must exist; nothing is created or migrated,
        and SQLite refuses every write, so it can stay open while the daily run writes."""
        if read_only:
            path = Path(path)
            if not path.exists():
                raise FileNotFoundError(f"No database at {path}")
            self._conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            return
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.executescript(SCHEMA)
        self._add_traded_prices()
        self._add_fetch_times()
```

(keep the two existing migration calls exactly as they are today).

Add, next to the other paper and review methods:

```python
def paper_days(self) -> list[pd.Timestamp]:
    rows = self._conn.execute("SELECT DISTINCT day FROM paper_states ORDER BY day")
    return [pd.Timestamp(r[0]) for r in rows.fetchall()]


def all_reviews(self) -> list[dict[str, Any]]:
    rows = self._conn.execute(
        "SELECT day, ticker, status, verdict, confidence, reasons, risks, news_used, note, "
        "late, model, prompt, answer, cost, created_at FROM reviews "
        "ORDER BY day DESC, id DESC"
    ).fetchall()
    keys = (
        "day",
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
        "prompt",
        "answer",
        "cost",
        "created_at",
    )
    result = []
    for row in rows:
        record = dict(zip(keys, row, strict=True))
        record["day"] = pd.Timestamp(record["day"])
        for key in ("reasons", "risks", "news_used"):
            record[key] = json.loads(record[key])
        record["late"] = bool(record["late"])
        result.append(record)
    return result


def backtest_list(self) -> list[dict[str, Any]]:
    rows = self._conn.execute(
        "SELECT id, created_at, period, start, end, fingerprint, settings, metrics, "
        "benchmark, notes FROM backtests ORDER BY id DESC"
    ).fetchall()
    return [
        {
            "id": r[0],
            "created_at": r[1],
            "period": r[2],
            "start": pd.Timestamp(r[3]),
            "end": pd.Timestamp(r[4]),
            "fingerprint": r[5],
            "settings": json.loads(r[6]),
            "metrics": json.loads(r[7]),
            "benchmark": json.loads(r[8]),
            "notes": json.loads(r[9]),
        }
        for r in rows
    ]


def backtest_equity(self, backtest_id: int) -> pd.Series:
    rows = self._conn.execute(
        "SELECT day, equity FROM backtest_equity WHERE backtest_id = ? ORDER BY day",
        (backtest_id,),
    ).fetchall()
    return pd.Series(
        [r[1] for r in rows],
        index=pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows]),
        name="equity",
        dtype=float,
    )
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest -q` then `.venv/Scripts/pytest.exe -q` (bare, as CI runs it)
Expected: all pass both ways.

- [ ] **Step 6: Commit**

```bash
git add src/market_agent/store.py tests/helpers.py tests/test_store_cache.py
git commit -m "Add a read-only store and the queries the dashboard needs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: AI review views and the "what if" for skipped candidates

**Files:**
- Create: `src/market_agent/dashboard/__init__.py` (empty), `src/market_agent/dashboard/reviews.py`
- Test: `tests/test_dashboard_reviews.py`

**Interfaces:**
- Consumes: `Store.all_reviews()`, `Store.load_prices(ticker)` (Task 1 / plan 1); `Simulator` (broker.py), `add_indicators` (indicators.py), `Bar` (panel.py), `Order`, `Portfolio` (portfolio.py), `Settings`.
- Produces:
  - `REVIEW_COLUMNS = ["day", "ticker", "status", "verdict", "confidence", "reasons", "risks", "news", "late", "model", "cost"]`
  - `review_table(store) -> pd.DataFrame` with exactly `REVIEW_COLUMNS`, newest first; `reasons`/`risks` joined with `"; "`, `news` = the used headlines joined with `"; "`.
  - `headlines_used(prompt: str, indexes: list[int]) -> list[str]`.
  - `WHAT_IF_COLUMNS = ["day", "ticker", "status", "exit_day", "exit_reason", "return"]`
  - `what_if(store, settings) -> pd.DataFrame` with exactly `WHAT_IF_COLUMNS`, one row per `skip` review, `status` in `"closed" | "open" | "not filled yet" | "no data"`, `return` a fraction (0.05 = +5%).

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard_reviews.py`:

```python
import math
from types import SimpleNamespace

import pytest

from helpers import DASHBOARD_DAYS, dashboard_db
from market_agent.dashboard.reviews import (
    REVIEW_COLUMNS,
    WHAT_IF_COLUMNS,
    headlines_used,
    review_table,
    what_if,
)
from market_agent.settings import Settings
from market_agent.store import Store

PROMPT = (
    "Candidate: AAA (Alpha), sector Tech\n\nHeadlines, newest first:\n"
    "[0] 2026-09-01 Reuters: Alpha cuts guidance\n    Weak quarter.\n"
    "[1] 2026-09-02 CNBC: Alpha hires a CFO"
)


def test_headlines_used_in_the_order_given():
    assert headlines_used(PROMPT, [1, 0]) == [
        "2026-09-02 CNBC: Alpha hires a CFO",
        "2026-09-01 Reuters: Alpha cuts guidance",
    ]


def test_headlines_used_ignores_unknown_indexes():
    assert headlines_used(PROMPT, [0, 5]) == ["2026-09-01 Reuters: Alpha cuts guidance"]
    assert headlines_used("Candidate: CCC", [0]) == []


def test_review_table(tmp_path):
    store = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    table = review_table(store)
    store.close()
    assert list(table.columns) == REVIEW_COLUMNS
    assert list(table["ticker"]) == ["CCC", "AAA"]
    aaa = table.iloc[1]
    assert (aaa["day"], aaa["verdict"], aaa["reasons"], aaa["risks"]) == (
        DASHBOARD_DAYS[-20],
        "skip",
        "Guidance cut",
        "Weak demand",
    )
    assert aaa["news"] == "2026-09-01 Reuters: Alpha cuts guidance"
    assert table["cost"].sum() == pytest.approx(0.035)


def test_review_table_when_empty(tmp_path):
    Store(tmp_path / "market.db").close()
    store = Store(tmp_path / "market.db", read_only=True)
    assert list(review_table(store).columns) == REVIEW_COLUMNS
    assert what_if(store, Settings()).empty
    store.close()


def test_what_if_follows_the_rules_trade(tmp_path):
    store = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    table = what_if(store, Settings())
    store.close()
    assert list(table.columns) == WHAT_IF_COLUMNS
    [row] = table.to_dict("records")
    assert (row["day"], row["ticker"], row["status"]) == (DASHBOARD_DAYS[-20], "AAA", "closed")
    assert row["exit_reason"] in ("target", "target (gap)")
    assert DASHBOARD_DAYS[-20] < row["exit_day"] <= DASHBOARD_DAYS[-1]
    assert row["return"] > 0


def skip(ticker):
    return SimpleNamespace(
        ticker=ticker,
        status="reviewed",
        verdict="skip",
        confidence="low",
        reasons=[],
        risks=[],
        news_used=[],
        note="",
        cost=0.01,
        model="m",
        prompt="",
        answer="{}",
    )


def test_what_if_without_later_prices(tmp_path):
    db = dashboard_db(tmp_path / "market.db")
    writer = Store(db)
    writer.save_review(DASHBOARD_DAYS[-1], skip("AAA"), late=False)  # no prices after it yet
    writer.save_review(DASHBOARD_DAYS[-5], skip("ZZZ"), late=False)  # no prices at all
    writer.close()
    store = Store(db, read_only=True)
    rows = {r["ticker"] + r["status"]: r for r in what_if(store, Settings()).to_dict("records")}
    store.close()
    assert set(rows) == {"AAAclosed", "AAAnot filled yet", "ZZZno data"}
    assert math.isnan(rows["ZZZno data"]["return"])
    assert rows["AAAnot filled yet"]["exit_reason"] == ""
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_reviews.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.dashboard'`.

- [ ] **Step 3: Implement**

`src/market_agent/dashboard/__init__.py`: empty file.

`src/market_agent/dashboard/reviews.py`:

```python
"""AI review page data: every review, and how the skipped candidates would have done."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from market_agent.broker import Simulator
from market_agent.indicators import add_indicators
from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio
from market_agent.settings import Settings
from market_agent.store import Store

REVIEW_COLUMNS = [
    "day",
    "ticker",
    "status",
    "verdict",
    "confidence",
    "reasons",
    "risks",
    "news",
    "late",
    "model",
    "cost",
]
WHAT_IF_COLUMNS = ["day", "ticker", "status", "exit_day", "exit_reason", "return"]
WHAT_IF_SHARES = 100  # any size works: the result is a return, and cash is unlimited
HEADLINE = re.compile(r"^\[(\d+)\] (.+)$")


def headlines_used(prompt: str, indexes: list[int]) -> list[str]:
    """The numbered headline lines of a review prompt that the answer relied on."""
    numbered = {}
    for line in prompt.splitlines():
        match = HEADLINE.match(line)
        if match:
            numbered[int(match.group(1))] = match.group(2)
    return [numbered[i] for i in indexes if i in numbered]


def review_table(store: Store) -> pd.DataFrame:
    rows = [
        {
            "day": r["day"],
            "ticker": r["ticker"],
            "status": r["status"],
            "verdict": r["verdict"],
            "confidence": r["confidence"],
            "reasons": "; ".join(r["reasons"]),
            "risks": "; ".join(r["risks"]),
            "news": "; ".join(headlines_used(r["prompt"] or "", r["news_used"])),
            "late": r["late"],
            "model": r["model"],
            "cost": r["cost"],
        }
        for r in store.all_reviews()
    ]
    return pd.DataFrame(rows, columns=REVIEW_COLUMNS)


def _simulate_skip(
    store: Store, settings: Settings, ticker: str, day: pd.Timestamp
) -> dict[str, Any]:
    """The rules-only trade the skip prevented: bought at the next open, same exits."""
    result: dict[str, Any] = {
        "day": day,
        "ticker": ticker,
        "status": "no data",
        "exit_day": None,
        "exit_reason": "",
        "return": float("nan"),
    }
    bars = store.load_prices(ticker)
    if bars is None or day not in bars.index:
        return result
    atr = add_indicators(bars, settings.strategy).at[day, "atr"]
    if not atr > 0:
        return result
    later = bars.index[bars.index > day]
    if len(later) == 0:
        return {**result, "status": "not filled yet"}

    def bar(_ticker: str, when: pd.Timestamp) -> Bar | None:
        if when not in bars.index:
            return None
        row = bars.loc[when]
        return Bar(float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]))

    sim = Simulator(settings.risk, settings.strategy)
    portfolio = Portfolio("what-if", 1e12)
    portfolio.orders.append(Order(ticker, "buy", WHAT_IF_SHARES, "breakout", day, float(atr)))
    for when in later:
        sim.open(portfolio, when, bar)
        sim.intraday(portfolio, when, bar)
        sim.close(portfolio, when, bar, lambda _t: None)
        if portfolio.trades:
            trade = portfolio.trades[0]
            return {
                **result,
                "status": "closed",
                "exit_day": trade.exit_day,
                "exit_reason": trade.exit_reason,
                "return": trade.pnl / (trade.entry_price * trade.shares),
            }
    position = portfolio.positions.get(ticker)
    if position is None:
        return {**result, "status": "not filled yet"}
    return {**result, "status": "open", "return": position.last_close / position.entry_price - 1}


def what_if(store: Store, settings: Settings) -> pd.DataFrame:
    rows = [
        _simulate_skip(store, settings, r["ticker"], r["day"])
        for r in store.all_reviews()
        if r["verdict"] == "skip"
    ]
    return pd.DataFrame(rows, columns=WHAT_IF_COLUMNS)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_reviews.py -q`, then the full suite both ways (`python -m pytest -q`, `pytest.exe -q`) and ruff.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/dashboard tests/test_dashboard_reviews.py
git commit -m "Add the AI review views and the what-if for skipped candidates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Paper trading views (overview, today, positions, trades)

**Files:**
- Create: `src/market_agent/dashboard/paper.py`
- Test: `tests/test_dashboard_paper.py`

**Interfaces:**
- Consumes: `Store.paper_days`, `paper_states`, `daily_run`, `latest_daily_run`, `load_prices` (Tasks 1 and plan 2); `portfolio_from_json`; `compute_metrics` (backtest.py); `review_table`, `REVIEW_COLUMNS` (Task 2); `PORTFOLIOS` (paper.py of plan 2).
- Produces:
  - `latest_portfolios(store) -> dict[str, Portfolio]` (`{}` before the first run)
  - `equity_curves(store, settings) -> pd.DataFrame` — index = paper days; one column per portfolio, plus the benchmark (`settings.data.benchmark`) scaled to `settings.risk.starting_cash` on the first day; empty frame before the first run.
  - `PERFORMANCE_COLUMNS = ["total_return", "max_drawdown", "trades", "win_rate", "avg_win", "avg_loss"]`; `performance(store, settings) -> pd.DataFrame`, index = portfolio names then the benchmark (trade columns empty for the benchmark).
  - `POSITION_COLUMNS = ["portfolio", "ticker", "sector", "shares", "entry_day", "entry_price", "stop", "target", "last_close", "days_held", "unrealised"]`; `open_positions(store)`.
  - `TRADE_COLUMNS = ["portfolio", "ticker", "sector", "entry_day", "entry_price", "exit_day", "exit_price", "shares", "exit_reason", "pnl", "return"]`; `closed_trades(store)`, newest exit first.
  - `ORDER_COLUMNS = ["portfolio", "ticker", "side", "shares", "reason"]`
  - `TodayView(day, status, report, sent, alerts, reviews, orders)` (frozen dataclass; `reviews` with `REVIEW_COLUMNS`, `orders` with `ORDER_COLUMNS`) and `today(store, day=None) -> TodayView | None` (latest run when `day` is None).

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard_paper.py`:

```python
import pytest

from helpers import DASHBOARD_DAYS, dashboard_db
from market_agent.dashboard.paper import (
    ORDER_COLUMNS,
    PERFORMANCE_COLUMNS,
    POSITION_COLUMNS,
    TRADE_COLUMNS,
    closed_trades,
    equity_curves,
    latest_portfolios,
    open_positions,
    performance,
    today,
)
from market_agent.settings import Settings
from market_agent.store import Store

S = Settings()
D1, D2 = DASHBOARD_DAYS[-2], DASHBOARD_DAYS[-1]


@pytest.fixture
def store(tmp_path):
    s = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    yield s
    s.close()


@pytest.fixture
def empty(tmp_path):
    Store(tmp_path / "empty.db").close()
    s = Store(tmp_path / "empty.db", read_only=True)
    yield s
    s.close()


def test_equity_curves_against_the_benchmark(store):
    curves = equity_curves(store, S)
    assert list(curves.columns) == ["rules-only", "rules+ai", "SPY"]
    assert list(curves.index) == [D1, D2]
    assert list(curves["rules-only"]) == [9_900.0, 10_090.0]
    assert curves.at[D1, "SPY"] == 10_000.0
    assert curves.at[D2, "SPY"] == pytest.approx(10_000 * 419.5 / 419)


def test_performance(store):
    table = performance(store, S)
    assert list(table.columns) == PERFORMANCE_COLUMNS
    assert list(table.index) == ["rules-only", "rules+ai", "SPY"]
    rules = table.loc["rules-only"]
    assert rules["total_return"] == pytest.approx(0.0192)
    assert (rules["trades"], rules["win_rate"]) == (1, 1.0)
    assert rules["avg_win"] == pytest.approx(round(98 / 1001, 4))
    assert table.at["SPY", "total_return"] == pytest.approx(0.0012)
    assert table.at["SPY", "trades"] != table.at["SPY", "trades"]  # NaN: no trades


def test_positions_and_trades(store):
    positions = open_positions(store)
    assert list(positions.columns) == POSITION_COLUMNS
    [aaa] = positions.to_dict("records")
    assert (aaa["portfolio"], aaa["ticker"], aaa["shares"], aaa["days_held"]) == (
        "rules-only",
        "AAA",
        10,
        2,
    )
    assert aaa["unrealised"] == pytest.approx((139.0 - 137.137) * 10)
    trades = closed_trades(store)
    assert list(trades.columns) == TRADE_COLUMNS
    [bbb] = trades.to_dict("records")
    assert (bbb["ticker"], bbb["exit_reason"], bbb["pnl"]) == ("BBB", "target", 98.0)
    assert bbb["return"] == pytest.approx(98 / (50 * 20))


def test_today(store):
    view = today(store)
    assert (view.day, view.status, view.sent) == (D2, "traded", True)
    assert view.report.startswith(f"Market agent, {D2:%Y-%m-%d}")
    assert view.alerts == ["rules+ai: circuit breaker on"]
    assert list(view.orders.columns) == ORDER_COLUMNS
    assert view.orders.to_dict("records") == [
        {
            "portfolio": "rules-only",
            "ticker": "CCC",
            "side": "buy",
            "shares": 5,
            "reason": "breakout",
        }
    ]
    assert list(view.reviews["ticker"]) == ["CCC"]
    earlier = today(store, D1)
    assert (earlier.day, earlier.sent, earlier.report) == (D1, False, "Market agent, report one")


def test_before_the_first_run(empty):
    assert latest_portfolios(empty) == {}
    assert equity_curves(empty, S).empty
    assert performance(empty, S).empty
    assert list(open_positions(empty).columns) == POSITION_COLUMNS
    assert closed_trades(empty).empty
    assert today(empty) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_paper.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.dashboard.paper'`.

- [ ] **Step 3: Implement `src/market_agent/dashboard/paper.py`**

```python
"""Overview, Today and Positions pages: the paper portfolios as saved by the daily run."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from market_agent.backtest import compute_metrics
from market_agent.dashboard.reviews import REVIEW_COLUMNS, review_table
from market_agent.paper import PORTFOLIOS
from market_agent.portfolio import Portfolio, portfolio_from_json
from market_agent.settings import Settings
from market_agent.store import Store

PERFORMANCE_COLUMNS = ["total_return", "max_drawdown", "trades", "win_rate", "avg_win", "avg_loss"]
POSITION_COLUMNS = [
    "portfolio",
    "ticker",
    "sector",
    "shares",
    "entry_day",
    "entry_price",
    "stop",
    "target",
    "last_close",
    "days_held",
    "unrealised",
]
TRADE_COLUMNS = [
    "portfolio",
    "ticker",
    "sector",
    "entry_day",
    "entry_price",
    "exit_day",
    "exit_price",
    "shares",
    "exit_reason",
    "pnl",
    "return",
]
ORDER_COLUMNS = ["portfolio", "ticker", "side", "shares", "reason"]


@dataclass(frozen=True)
class TodayView:
    day: pd.Timestamp
    status: str
    report: str
    sent: bool
    alerts: list[str]
    reviews: pd.DataFrame
    orders: pd.DataFrame


def _portfolios_on(store: Store, day: pd.Timestamp) -> dict[str, Portfolio]:
    states = store.paper_states(day)
    return {name: portfolio_from_json(states[name]) for name in PORTFOLIOS if name in states}


def latest_portfolios(store: Store) -> dict[str, Portfolio]:
    day = store.latest_paper_day()
    return _portfolios_on(store, day) if day is not None else {}


def _equity(portfolio: Portfolio) -> pd.Series:
    days, values = (
        zip(*portfolio.equity_history, strict=True) if portfolio.equity_history else ((), ())
    )
    return pd.Series(values, index=pd.DatetimeIndex(days), name=portfolio.name, dtype=float)


def equity_curves(store: Store, settings: Settings) -> pd.DataFrame:
    portfolios = latest_portfolios(store)
    if not portfolios:
        return pd.DataFrame()
    curves = pd.concat([_equity(p) for p in portfolios.values()], axis=1)
    benchmark = settings.data.benchmark
    prices = store.load_prices(benchmark)
    if prices is not None:
        close = prices["close"].reindex(curves.index)
        if close.notna().any():
            first = close.dropna().iloc[0]
            curves[benchmark] = close / first * settings.risk.starting_cash
    return curves


def performance(store: Store, settings: Settings) -> pd.DataFrame:
    curves = equity_curves(store, settings)
    if curves.empty:
        return pd.DataFrame(columns=PERFORMANCE_COLUMNS)
    rows = {}
    for name, portfolio in latest_portfolios(store).items():
        metrics = compute_metrics(
            curves[name].dropna(), portfolio.trades, commission=settings.risk.commission
        )
        rows[name] = {column: metrics.get(column) for column in PERFORMANCE_COLUMNS}
    benchmark = settings.data.benchmark
    if benchmark in curves:
        metrics = compute_metrics(curves[benchmark].dropna(), None)
        rows[benchmark] = {column: metrics.get(column) for column in PERFORMANCE_COLUMNS}
    return pd.DataFrame.from_dict(rows, orient="index", columns=PERFORMANCE_COLUMNS).astype(float)


def open_positions(store: Store) -> pd.DataFrame:
    rows = [
        {
            "portfolio": name,
            "ticker": pos.ticker,
            "sector": pos.sector,
            "shares": pos.shares,
            "entry_day": pos.entry_day,
            "entry_price": pos.entry_price,
            "stop": pos.stop,
            "target": pos.target,
            "last_close": pos.last_close,
            "days_held": pos.days_held,
            "unrealised": (pos.last_close - pos.entry_price) * pos.shares,
        }
        for name, portfolio in latest_portfolios(store).items()
        for pos in portfolio.positions.values()
    ]
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


def closed_trades(store: Store) -> pd.DataFrame:
    rows = [
        {
            "portfolio": name,
            "ticker": t.ticker,
            "sector": t.sector,
            "entry_day": t.entry_day,
            "entry_price": t.entry_price,
            "exit_day": t.exit_day,
            "exit_price": t.exit_price,
            "shares": t.shares,
            "exit_reason": t.exit_reason,
            "pnl": t.pnl,
            "return": t.pnl / (t.entry_price * t.shares),
        }
        for name, portfolio in latest_portfolios(store).items()
        for t in portfolio.trades
    ]
    frame = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    return frame.sort_values("exit_day", ascending=False, kind="stable").reset_index(drop=True)


def today(store: Store, day: pd.Timestamp | None = None) -> TodayView | None:
    """A saved run (the latest by default): its report, the reviews that day and the orders
    it placed for the next open. Shows the report even when Telegram failed."""
    run = store.daily_run(day) if day is not None else store.latest_daily_run()
    if run is None:
        return None
    reviews = review_table(store)
    reviews = reviews[reviews["day"] == run["day"]].reset_index(drop=True)
    orders = [
        {
            "portfolio": name,
            "ticker": o.ticker,
            "side": o.side,
            "shares": o.shares,
            "reason": o.reason,
        }
        for name, portfolio in _portfolios_on(store, run["day"]).items()
        for o in portfolio.orders
    ]
    return TodayView(
        day=run["day"],
        status=run["status"],
        report=run["report"],
        sent=run["sent"],
        alerts=run["alerts"],
        reviews=reviews if not reviews.empty else pd.DataFrame(columns=REVIEW_COLUMNS),
        orders=pd.DataFrame(orders, columns=ORDER_COLUMNS),
    )
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_paper.py -q`, then the full suite both ways and ruff.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/dashboard/paper.py tests/test_dashboard_paper.py
git commit -m "Add the paper trading views for the dashboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Backtest views

**Files:**
- Create: `src/market_agent/dashboard/backtests.py`
- Test: `tests/test_dashboard_backtests.py`

**Interfaces:**
- Consumes: `Store.backtest_list()`, `Store.backtest_equity(id)`, `Store.load_prices` (Task 1).
- Produces:
  - `RUN_COLUMNS = ["id", "period", "start", "end", "created_at", "fingerprint", "total_return", "cagr", "max_drawdown", "trades", "win_rate", "spy_total_return", "spy_cagr", "spy_max_drawdown"]`
  - `backtest_runs(store) -> pd.DataFrame` with `RUN_COLUMNS`, newest first.
  - `backtest_curve(store, settings, run_id) -> pd.DataFrame`: index = days; columns `"strategy"` and the benchmark (scaled to the strategy's first value).
  - `backtest_settings(store, run_id) -> dict` (the settings stored with the run; `{}` if unknown).

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard_backtests.py`:

```python
import pytest

from helpers import DASHBOARD_DAYS, dashboard_db
from market_agent.dashboard.backtests import (
    RUN_COLUMNS,
    backtest_curve,
    backtest_runs,
    backtest_settings,
)
from market_agent.settings import Settings
from market_agent.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(dashboard_db(tmp_path / "market.db"), read_only=True)
    yield s
    s.close()


def test_backtest_runs(store):
    runs = backtest_runs(store)
    assert list(runs.columns) == RUN_COLUMNS
    [run] = runs.to_dict("records")
    assert (run["id"], run["period"], run["fingerprint"]) == (1, "tuning", "abc123def456")
    assert (run["total_return"], run["spy_total_return"], run["trades"]) == (0.039, 0.0488, 1)


def test_backtest_curve_against_the_benchmark(store):
    curve = backtest_curve(store, Settings(), 1)
    assert list(curve.columns) == ["strategy", "SPY"]
    assert list(curve.index) == list(DASHBOARD_DAYS)
    assert curve["SPY"].iloc[0] == 10_000.0
    assert curve["SPY"].iloc[-1] == pytest.approx(10_000 * 419.5 / 400)


def test_backtest_settings(store):
    assert backtest_settings(store, 1) == {
        "strategy": {"volume_ratio": 1.5},
        "risk": {"starting_cash": 10000.0},
    }
    assert backtest_settings(store, 99) == {}


def test_no_backtests_yet(tmp_path):
    Store(tmp_path / "empty.db").close()
    store = Store(tmp_path / "empty.db", read_only=True)
    assert backtest_runs(store).empty
    assert backtest_curve(store, Settings(), 1).empty
    store.close()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_backtests.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.dashboard.backtests'`.

- [ ] **Step 3: Implement `src/market_agent/dashboard/backtests.py`**

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_backtests.py -q`, then the full suite both ways and ruff.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/dashboard/backtests.py tests/test_dashboard_backtests.py
git commit -m "Add the backtest views for the dashboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The Streamlit app

**Files:**
- Create: `src/market_agent/dashboard/app.py`
- Modify: `pyproject.toml`
- Test: `tests/test_dashboard_app.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4; `load_settings` (settings.py).
- Produces: `src/market_agent/dashboard/app.py`, a Streamlit script that reads the environment variables `MARKET_AGENT_DB` (default `data/market.db`) and `MARKET_AGENT_CONFIG` (default `config.yaml`), and the constant `PAGES = ("Overview", "Today", "Positions and trades", "AI review", "Backtest")`. Each page starts with `st.header(<page name>)`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"streamlit>=1.40",` to `dependencies`, then run
`.venv/Scripts/python.exe -m pip install -e ".[dev]"`.

- [ ] **Step 2: Write the failing tests**

`tests/test_dashboard_app.py`:

```python
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import market_agent.dashboard
from helpers import dashboard_db
from market_agent.store import Store

APP = Path(market_agent.dashboard.__file__).parent / "app.py"
PAGES = ("Overview", "Today", "Positions and trades", "AI review", "Backtest")


def open_page(monkeypatch, db, page=None):
    monkeypatch.setenv("MARKET_AGENT_DB", str(db))
    monkeypatch.setenv("MARKET_AGENT_CONFIG", str(Path(db).parent / "no-config.yaml"))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    if page is not None:
        at.sidebar.radio[0].set_value(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders(tmp_path, monkeypatch, page):
    at = open_page(monkeypatch, dashboard_db(tmp_path / "market.db"), page)
    assert not at.exception
    assert at.header[0].value == page


def test_today_shows_the_saved_report(tmp_path, monkeypatch):
    at = open_page(monkeypatch, dashboard_db(tmp_path / "market.db"), "Today")
    assert any(code.value.startswith("Market agent, 2026-09-25") for code in at.code)
    assert any("circuit breaker" in w.value for w in at.warning)


@pytest.mark.parametrize("page", PAGES)
def test_pages_before_any_paper_run(tmp_path, monkeypatch, page):
    Store(tmp_path / "market.db").close()  # an empty database: no prices, runs or reviews
    at = open_page(monkeypatch, tmp_path / "market.db", page)
    assert not at.exception
    assert at.header[0].value == page


def test_missing_database_is_explained(tmp_path, monkeypatch):
    db = tmp_path / "elsewhere" / "market.db"
    at = open_page(monkeypatch, db)
    assert not at.exception
    assert "No database at" in at.error[0].value
    assert not db.parent.exists()


def test_dashboard_never_changes_the_database(tmp_path, monkeypatch):
    db = dashboard_db(tmp_path / "market.db")
    before = db.read_bytes()
    for page in PAGES:
        open_page(monkeypatch, db, page)
    assert db.read_bytes() == before
```

(`DASHBOARD_DAYS[-1]` is 2026-09-25, a Friday: 40 business days from 2026-08-03.)

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_app.py -q`
Expected: FAIL — `AppTest.from_file` cannot find `app.py` (`FileNotFoundError`).

- [ ] **Step 4: Implement `src/market_agent/dashboard/app.py`**

```python
"""Read-only dashboard (spec section 9). Started by `agent dashboard`, which sets
MARKET_AGENT_DB and MARKET_AGENT_CONFIG; run on its own it uses data/market.db."""

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from market_agent.dashboard.backtests import backtest_curve, backtest_runs, backtest_settings
from market_agent.dashboard.paper import (
    closed_trades,
    equity_curves,
    open_positions,
    performance,
    today,
)
from market_agent.dashboard.reviews import review_table, what_if
from market_agent.settings import Settings, load_settings
from market_agent.store import Store

PAGES = ("Overview", "Today", "Positions and trades", "AI review", "Backtest")
NO_PAPER = "No paper trading yet. Run `agent run-daily` to start both portfolios."


def as_percent(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Fractions as percentages with one decimal, the column renamed '<name> (%)'."""
    out = frame.copy()
    present = [c for c in columns if c in out.columns]
    for column in present:
        out[column] = (pd.to_numeric(out[column], errors="coerce") * 100).round(1)
    return out.rename(columns={c: f"{c} (%)" for c in present})


def overview(store: Store, settings: Settings) -> None:
    st.header("Overview")
    curves = equity_curves(store, settings)
    if curves.empty:
        st.info(NO_PAPER)
        return
    st.caption(
        f"Value of each paper portfolio since {curves.index[0]:%Y-%m-%d}, against the same "
        f"US${settings.risk.starting_cash:,.0f} in {settings.data.benchmark}."
    )
    st.line_chart(curves)
    st.subheader("Performance")
    columns = ["total_return", "max_drawdown", "win_rate", "avg_win", "avg_loss"]
    st.dataframe(as_percent(performance(store, settings), columns))


def today_page(store: Store, settings: Settings) -> None:
    st.header("Today")
    days = store.paper_days()
    if not days:
        st.info(NO_PAPER)
        return
    day = st.selectbox("Day", list(reversed(days)), format_func=lambda d: f"{d:%Y-%m-%d}")
    view = today(store, day)
    if view is None:
        st.info("No report saved for that day.")
        return
    sent = "sent to Telegram" if view.sent else "not sent to Telegram"
    st.caption(f"Status: {view.status}; report {sent}.")
    for alert in view.alerts:
        st.warning(alert)
    st.subheader("Candidates and AI review")
    if view.reviews.empty:
        st.write("No candidates were reviewed that day.")
    else:
        st.dataframe(view.reviews, hide_index=True)
    st.subheader("Orders for the next open")
    if view.orders.empty:
        st.write("No orders.")
    else:
        st.dataframe(view.orders, hide_index=True)
    st.subheader("Report")
    st.code(view.report, language=None)


def positions_page(store: Store, settings: Settings) -> None:
    st.header("Positions and trades")
    st.subheader("Open positions")
    positions = open_positions(store)
    if positions.empty:
        st.write("No open positions.")
    else:
        st.dataframe(positions, hide_index=True)
    st.subheader("Closed trades")
    trades = closed_trades(store)
    if trades.empty:
        st.write("No closed trades yet.")
    else:
        st.dataframe(as_percent(trades, ["return"]), hide_index=True)


def reviews_page(store: Store, settings: Settings) -> None:
    st.header("AI review")
    table = review_table(store)
    if table.empty:
        st.info("No Claude reviews yet.")
        return
    st.caption(f"{len(table)} reviews, US${table['cost'].sum():.2f} spent in total.")
    st.dataframe(table, hide_index=True)
    st.subheader("What if the skipped candidates had been bought")
    st.caption(
        "Each candidate Claude skipped, traded by the rules alone: bought at the next open, "
        "same stop, target and time exit."
    )
    skipped = what_if(store, settings)
    if skipped.empty:
        st.write("Claude has not skipped any candidate yet.")
    else:
        st.dataframe(as_percent(skipped, ["return"]), hide_index=True)


def backtest_page(store: Store, settings: Settings) -> None:
    st.header("Backtest")
    runs = backtest_runs(store)
    if runs.empty:
        st.info("No backtests yet. Run `agent backtest --period tuning`.")
        return
    columns = [
        "total_return",
        "cagr",
        "max_drawdown",
        "win_rate",
        "spy_total_return",
        "spy_cagr",
        "spy_max_drawdown",
    ]
    st.dataframe(as_percent(runs, columns), hide_index=True)
    labels = {
        r["id"]: f"#{r['id']} {r['period']}, {r['start']:%Y-%m-%d} to {r['end']:%Y-%m-%d}"
        for r in runs.to_dict("records")
    }
    run_id = st.selectbox("Run", list(labels), format_func=labels.get)
    st.line_chart(backtest_curve(store, settings, run_id))
    st.subheader("Settings used")
    st.json(backtest_settings(store, run_id))


def main() -> None:
    st.set_page_config(page_title="Market agent", layout="wide")
    st.sidebar.title("Market agent")
    st.sidebar.caption("Paper trading with simulated money. This dashboard only reads.")
    page = st.sidebar.radio("Page", PAGES)
    db = Path(os.environ.get("MARKET_AGENT_DB", "data/market.db"))
    if not db.exists():
        st.error(
            f"No database at {db}. Start the dashboard from the market-agent folder "
            "with `agent dashboard`."
        )
        return
    settings = load_settings(Path(os.environ.get("MARKET_AGENT_CONFIG", "config.yaml")))
    store = Store(db, read_only=True)
    try:
        pages = {
            "Overview": overview,
            "Today": today_page,
            "Positions and trades": positions_page,
            "AI review": reviews_page,
            "Backtest": backtest_page,
        }
        pages[page](store, settings)
    finally:
        store.close()


main()
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboard_app.py -q`, then the full suite both ways and ruff.
Expected: all pass. If an `at.<element>` accessor named here doesn't exist in the installed Streamlit, check `dir(at)` and use the matching one; don't weaken what the test asserts.

- [ ] **Step 6: Look at it once**

Run: `$env:MARKET_AGENT_DB = (Resolve-Path data\market.db); .venv\Scripts\python.exe -m streamlit run src\market_agent\dashboard\app.py --server.address localhost --server.headless true` (PowerShell), open http://localhost:8501, click through the five pages against the real database, then stop it with Ctrl+C. Note anything that looks wrong in the report.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/market_agent/dashboard/app.py tests/test_dashboard_app.py
git commit -m "Add the read-only Streamlit dashboard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `agent dashboard` and the docs

**Files:**
- Modify: `src/market_agent/cli.py`, `README.md`, `CLAUDE.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `dashboard/app.py` (Task 5).
- Produces: `agent dashboard [--port N]` (default 8501); `cli.run_process(command: list[str], env: dict[str, str]) -> int` (replaced in tests); `cli.dashboard_command(settings, config: Path, port: int) -> int`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (add `import sys` and `from pathlib import Path` at the top if missing):

```python
def test_dashboard_starts_streamlit_on_localhost(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "run_process", lambda command, env: calls.append((command, env)) or 0)
    assert cli.main(["dashboard", "--port", "8600"]) == 0
    [(command, env)] = calls
    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    assert command[4].endswith("app.py") and Path(command[4]).exists()
    assert command[command.index("--server.address") + 1] == "localhost"
    assert command[command.index("--server.port") + 1] == "8600"
    assert env["MARKET_AGENT_DB"] == str((tmp_path / "data" / "market.db").resolve())
    assert env["MARKET_AGENT_CONFIG"] == str((tmp_path / "config.yaml").resolve())
    assert "http://localhost:8600" in capsys.readouterr().out
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli.py::test_dashboard_starts_streamlit_on_localhost -q`
Expected: FAIL (`argument command: invalid choice: 'dashboard'`).

- [ ] **Step 3: Implement in `cli.py`**

Add `import subprocess` and, next to the other factories:

```python
def run_process(command: list[str], env: dict[str, str]) -> int:
    """Replaced in tests."""
    return subprocess.run(command, env=env).returncode


def dashboard_command(settings: Settings, config: Path, port: int) -> int:
    app = Path(__file__).parent / "dashboard" / "app.py"
    env = {
        **os.environ,
        "MARKET_AGENT_DB": str(Path(settings.data.db_path).resolve()),
        "MARKET_AGENT_CONFIG": str(config.resolve()),
    }
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        "localhost",
        "--server.port",
        str(port),
        "--browser.gatherUsageStats",
        "false",
    ]
    print(f"Dashboard at http://localhost:{port} (Ctrl+C to stop)")
    return run_process(command, env)
```

In `main`, add the parser:

```python
    dashboard = commands.add_parser("dashboard", help="open the read-only dashboard in a browser")
    dashboard.add_argument("--port", type=int, default=8501)
```

and dispatch it right after the settings are loaded and BEFORE `store = Store(...)` is opened (the dashboard opens its own read-only connection):

```python
    if args.command == "dashboard":
        return dashboard_command(settings, args.config or DEFAULT_CONFIG, args.port)
```

Update the module docstring's command list to include `dashboard`.

- [ ] **Step 4: Docs**

`README.md`, after the "Paper trading" section:

````markdown
## Dashboard

```powershell
agent dashboard        # opens http://localhost:8501; Ctrl+C to stop
```

Five pages, read-only: Overview (both portfolios against SPY), Today (any saved day's report,
candidates, Claude's verdicts with the news used, and the orders for the next open), Positions
and trades, AI review (every review and how the skipped candidates would have done) and
Backtest (every run against SPY with the settings it used). It only reads `data\market.db`, so
it can stay open while the 06:30 run works, and it listens on this computer only.
````

`CLAUDE.md`: under Architecture add

```markdown
- **Dashboard** (`dashboard/`): `reviews.py`, `paper.py` and `backtests.py` turn the Store into
  DataFrames and are tested without Streamlit; `app.py` only renders them. It opens the database
  with `Store(path, read_only=True)` (SQLite `mode=ro`: no schema, no migrations), and
  `agent dashboard` starts Streamlit on localhost with `MARKET_AGENT_DB` / `MARKET_AGENT_CONFIG`
  set. App tests use `streamlit.testing.v1.AppTest` on `helpers.dashboard_db`.
```

and add `agent dashboard` to the Commands block.

- [ ] **Step 5: Run the tests**

Run: the full suite both ways (`.venv/Scripts/python.exe -m pytest -q`, `.venv/Scripts/pytest.exe -q`) and `ruff check .`, `ruff format --check .`.
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/market_agent/cli.py tests/test_cli.py README.md CLAUDE.md
git commit -m "Add agent dashboard and document it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
