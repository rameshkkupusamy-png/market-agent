# Market agent: data and backtest implementation plan (plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download and cache free US market data, implement the breakout strategy, risk rules and
trade simulator, and run an honest backtest (`agent backtest`) for the tuning period (2015–2021) and
the untouched test period (2022 to now), compared with SPY.

**Architecture:** A Python package `market_agent` in `src/`. Data sources sit behind small
interfaces (`PriceSource`, `EarningsSource`) with Yahoo Finance implementations and a SQLite cache.
A `Panel` turns per-ticker price frames into day × ticker tables of indicators, so screening a day is
one vectorised filter. The strategy, risk and simulator code is written once and will be reused
unchanged by paper trading in plan 2. Plans 2 (daily paper trading, AI review, Telegram) and 3
(dashboard, scheduling, Docker) follow.

**Tech Stack:** Python 3.12, pandas, numpy, yfinance, PyYAML, SQLite (standard library), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-02-market-agent-design.md`

## Global Constraints

- Python `>=3.12`; ruff line length 100, rules `E,F,I,UP,B`; run `ruff check --fix .` and `ruff format .` before every commit.
- Normal tests use no network; tests that reach Yahoo are marked `live` and skipped by default.
- Strategy numbers (spec section 4) and risk numbers (section 5) are defaults in `settings.py`, overridable in `config.yaml`: min price US$10, average traded value > US$20 million, close > SMA200, SMA50 > SMA200, no earnings within 5 trading days, close = highest close of 20 days, volume ≥ 1.5 × 20-day average, rank by 63-day return, ≤ 3 new positions a day, stop = entry − 2 × ATR(14), target = entry + 4 × ATR(14), time exit after 20 trading days, US$10,000 start, 1% risk per trade, ≤ 10% of equity per position, ≤ 10 positions, ≤ 3 per sector, circuit breaker at −15% from peak, US$1 commission per order, 0.1% slippage on every fill.
- Orders are placed after the close of day D and filled at the open of D+1. If the open is past the stop or target, fill at the open. If the stop and target are both inside one day's range, assume the stop.
- No look-ahead: anything decided on day D uses data up to and including D only.
- Backtest periods: tuning 2015-01-01 to 2021-12-31; test 2022-01-01 to the latest close. Results always shown next to SPY buy-and-hold.
- Prices are split- and dividend-adjusted (Yahoo `auto_adjust=True`), so splits need no special handling in the backtest.
- Free Yahoo data is for personal use only.
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Companies that left the S&P 500 because they failed or were bought often have no Yahoo data.** Expected: the backtest reports how many universe members had no data, so its survivorship bias is visible, not hidden. Pinned by: Task 10 `test_reports_tickers_without_data`.
2. **A held share stops trading partway through the backtest** (delisted or acquired). Expected: the position closes at its last close with reason `delisted`, and equity never counts a stale price forever. Pinned by: Task 9 `test_delisted_position_closes_at_last_close`.
3. **Several buy orders fill at opens above the estimated price, with little cash left.** Expected: shares are reduced so cash never goes negative; an order that can't buy one share is dropped. Pinned by: Task 9 `test_buy_reduced_to_available_cash`.
4. **Yahoo has no earnings dates for the early years.** Expected: the earnings rule can't be applied there, and the backtest counts how many signals were taken without earnings data. Pinned by: Task 7 `test_candidate_records_missing_earnings_coverage`, Task 10 `test_counts_signals_without_earnings_data`.
5. **The owner changes settings after seeing a test-period result and runs the test period again.** Expected: a clear warning that this is tuning on the test period; re-running with identical settings is refused. Pinned by: Task 11 `test_test_period_guard`.

---

## File structure

```
.gitignore
.github/workflows/ci.yml
pyproject.toml
config.example.yaml
README.md
data/
  sp500_membership.csv          historical S&P 500 members (Task 1)
  sectors.csv                   ticker,sector (built by `agent build-sectors`, Task 11)
  market.db                     SQLite cache and results (not committed)
docs/data-sources.md            findings from Task 1
src/market_agent/
  __init__.py
  settings.py                   Settings dataclasses, load_settings, fingerprint
  store.py                      SQLite: prices, earnings, backtest results
  data/
    __init__.py
    sources.py                  PriceSource, EarningsSource, EarningsHistory, NoData, normalize_bars
    yahoo.py                    YahooPrices, YahooEarnings, YahooSectors, yahoo_symbol
    cache.py                    PriceCache, EarningsCache
  universe.py                   Universe (membership by date), load_sectors
  indicators.py                 add_indicators, suspicious_days
  panel.py                      Panel (day × ticker tables), Bar
  earnings.py                   EarningsCalendar
  strategy.py                   Candidate, screen
  portfolio.py                  Position, Order, Trade, Portfolio
  broker.py                     Simulator (fills, exits, end of day, circuit breaker)
  risk.py                       position_size, check_limits, plan_entries
  backtest.py                   run_backtest, compute_metrics, BacktestResult
  guard.py                      test-period guard
  cli.py                        agent fetch | build-sectors | backtest
tests/
  helpers.py                    make_bars, settings_with
  test_*.py
  live/test_yahoo_live.py
```

Commands run from the repository root. On Windows: `py -3.12 -m venv .venv`, then
`.venv\Scripts\Activate.ps1`, then `pip install -e ".[dev]"`.

---

### Task 1: Confirm the free data sources (investigation, no product code)

The spec leaves three facts to confirm before building on them. This task answers them with a
throwaway script and records the answers in `docs/data-sources.md`. Later tasks are written for the
expected answers; the "If not" lines say what changes if an answer differs.

**Files:**
- Create: `docs/data-sources.md`, `data/sp500_membership.csv`
- Scratch only (not committed): `check_sources.py` in a temporary folder

**Interfaces:**
- Produces: `data/sp500_membership.csv` with columns `date,tickers` (one row per change date;
  `tickers` is a comma-separated list of every member on that date).

- [ ] **Step 1: Get the historical membership file**

Download the latest "S&P 500 Historical Components & Changes" CSV from
https://github.com/fja05680/sp500 (the file name contains its date). Save it as
`data/sp500_membership.csv`. Check the header is `date,tickers` and the first date is before
2014-01-01.

If not (different columns): rename the columns to `date,tickers` in the saved copy and note it in
`docs/data-sources.md`.

- [ ] **Step 2: Write and run the check script**

In a scratch folder, with `pip install yfinance pandas`:

```python
# check_sources.py: throwaway
import pandas as pd
import yfinance as yf

members = pd.read_csv("data/sp500_membership.csv")
first, last = members.iloc[0], members.iloc[-1]
print("membership rows:", len(members), "from", first["date"], "to", last["date"])
all_tickers = sorted({t for row in members["tickers"] for t in row.split(",")})
print("distinct tickers ever:", len(all_tickers), "sample:", all_tickers[:15])

# 1. Prices: current, renamed and removed companies
for ticker in ["AAPL", "BRK-B", "SPY", "TWTR", "XLNX", "FRC", "ATVI"]:
    bars = yf.Ticker(ticker).history(start="2014-01-01", auto_adjust=True, actions=False)
    span = (bars.index.min(), bars.index.max()) if not bars.empty else None
    print("prices", ticker, len(bars), span)

# 2. Earnings dates: how far back?
for ticker in ["AAPL", "MSFT", "JPM", "KO"]:
    dates = yf.Ticker(ticker).get_earnings_dates(limit=100)
    print("earnings", ticker, len(dates), dates.index.min(), dates.index.max())

# 3. Sectors
for ticker in ["AAPL", "JPM", "XOM"]:
    print("sector", ticker, yf.Ticker(ticker).info.get("sector"))

# 4. How many removed members have no Yahoo data? (sample of 40)
removed = [t for t in all_tickers if t not in last["tickers"].split(",")][:40]
missing = [
    t
    for t in removed
    if yf.Ticker(t.replace(".", "-")).history(period="max", auto_adjust=True).empty
]
print("removed sample without data:", len(missing), "of", len(removed), missing)
```

Run: `python check_sources.py`

Expected (the plan assumes these):
- Current tickers return data from 2014; `BRK-B` works with a dash (dataset uses `BRK.B`).
- Several removed tickers (e.g. `TWTR`, `FRC`) return no data: survivorship bias we can only
  measure, not fix, with free data.
- `get_earnings_dates(limit=100)` returns roughly 20+ years of quarterly dates for large companies.
- `info["sector"]` returns Yahoo sector names (close to GICS, e.g. "Technology").

If not:
- Earnings history shorter than 2015: no code change. The backtest already counts signals taken
  without earnings coverage (Task 10); record the earliest date found.
- `get_earnings_dates` fails entirely: in Task 4 make `YahooEarnings.fetch` return
  `EarningsHistory([], None)` on any exception and record that the earnings rule is inactive.
- Ticker symbols in the dataset carry suffixes for removed companies (e.g. `ABC-201912`): record the
  pattern; Task 5's `clean_ticker` strips a trailing `-YYYYMM` suffix.

- [ ] **Step 3: Record the findings**

Create `docs/data-sources.md` with the actual numbers printed in Step 2:

```markdown
# Data sources (checked 2026-10-02)

| Need | Source | Finding |
|---|---|---|
| Daily prices | yfinance `history(auto_adjust=True)` | <rows for AAPL since 2014>; dash for class shares (BRK-B) |
| Earnings dates | yfinance `get_earnings_dates(limit=100)` | earliest date for AAPL/MSFT/JPM/KO: <dates> |
| Sectors | yfinance `info["sector"]` | Yahoo sector names, close to GICS |
| Index membership | github.com/fja05680/sp500, file <name> | <rows>, <first date> to <last date>, <distinct tickers> tickers |
| Removed members | yfinance | <n> of 40 sampled removed members have no data |

Consequences: <one line per "If not" case that applied, or "none">.
News source for plan 2: not checked here.
```

Replace every `<…>` with the printed value before committing.

- [ ] **Step 4: Commit**

```bash
git add data/sp500_membership.csv docs/data-sources.md
git commit -m "Record free data source findings and add S&P 500 membership history

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Project skeleton and settings

**Files:**
- Create: `.gitignore`, `.github/workflows/ci.yml`, `pyproject.toml`, `config.example.yaml`, `src/market_agent/__init__.py`, `src/market_agent/settings.py`, `tests/helpers.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `StrategySettings`, `RiskSettings`, `DataSettings`, `BacktestSettings`, `Settings` (frozen dataclasses, fields below), `Settings.fingerprint() -> str` (12 hex characters, from strategy and risk only), `load_settings(path: Path | None) -> Settings`, `SettingsError`; test helpers `make_bars(closes, volumes=None, start="2023-01-02") -> pd.DataFrame`, `settings_with(**sections) -> Settings`.

- [ ] **Step 1: Create the skeleton**

`.gitignore`:
```
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/
.env
config.yaml
data/*.db
```

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "market-agent"
version = "0.1.0"
description = "Swing-trading analyst and paper trader"
requires-python = ">=3.12"
dependencies = ["pandas>=2.2", "numpy>=1.26", "yfinance>=0.2.40", "PyYAML>=6.0"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.6"]

[project.scripts]
agent = "market_agent.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: reaches Yahoo Finance (skipped by default)"]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.ruff.lint.isort]
known-first-party = ["market_agent", "helpers"]
```

`.github/workflows/ci.yml`:
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check .
      - run: ruff format --check .
      - run: pytest
```

`src/market_agent/__init__.py`:
```python
"""Swing-trading analyst and paper trader."""

__version__ = "0.1.0"
```

`config.example.yaml`:
```yaml
# Copy to config.yaml to change settings. Anything left out uses the default.
strategy:
  volume_ratio: 1.5
  stop_atr: 2.0
  target_atr: 4.0
risk:
  starting_cash: 10000
data:
  db_path: data/market.db
backtest:
  start: 2015-01-01
  tuning_end: 2021-12-31
  test_start: 2022-01-01
```

`tests/helpers.py`:
```python
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
```

Run: `pip install -e ".[dev]"`

- [ ] **Step 2: Write the failing tests**

`tests/test_settings.py`:
```python
from datetime import date

import pytest

from helpers import settings_with
from market_agent.settings import Settings, SettingsError, load_settings


def test_defaults_match_the_spec():
    s = Settings()
    assert s.strategy.min_price == 10.0
    assert s.strategy.min_traded_value == 20_000_000
    assert s.strategy.volume_ratio == 1.5
    assert (s.strategy.stop_atr, s.strategy.target_atr) == (2.0, 4.0)
    assert s.strategy.max_hold_days == 20
    assert s.strategy.max_new_per_day == 3
    assert s.risk.starting_cash == 10_000
    assert s.risk.risk_per_trade == 0.01
    assert s.risk.max_position_pct == 0.10
    assert (s.risk.max_positions, s.risk.max_per_sector) == (10, 3)
    assert s.risk.breaker_drawdown == 0.15
    assert (s.risk.commission, s.risk.slippage) == (1.0, 0.001)
    assert s.backtest.tuning_end == date(2021, 12, 31)


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "none.yaml") == Settings()


def test_file_overrides_some_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("strategy:\n  volume_ratio: 2.0\nrisk:\n  max_positions: 5\n", "utf-8")
    s = load_settings(path)
    assert s.strategy.volume_ratio == 2.0
    assert s.risk.max_positions == 5
    assert s.strategy.stop_atr == 2.0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("strategy:\n  volume_ratoi: 2\n", "Unknown setting strategy.volume_ratoi"),
        ("riks:\n  max_positions: 2\n", "Unknown section riks"),
    ],
)
def test_unknown_keys_are_rejected(tmp_path, text, message):
    path = tmp_path / "config.yaml"
    path.write_text(text, "utf-8")
    with pytest.raises(SettingsError, match=message):
        load_settings(path)


def test_fingerprint_changes_with_strategy_or_risk_only():
    base = Settings().fingerprint()
    assert len(base) == 12
    assert settings_with(strategy={"volume_ratio": 2.0}).fingerprint() != base
    assert settings_with(risk={"max_positions": 5}).fingerprint() != base
    assert settings_with(data={"db_path": "other.db"}).fingerprint() == base
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.settings'`

- [ ] **Step 4: Implement `settings.py`**

`src/market_agent/settings.py`:
```python
"""All tunable numbers, with the spec's defaults. config.yaml overrides any of them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path

import yaml


class SettingsError(Exception):
    """config.yaml has an unknown section or setting."""


@dataclass(frozen=True)
class StrategySettings:
    min_price: float = 10.0
    min_traded_value: float = 20_000_000
    sma_fast: int = 50
    sma_slow: int = 200
    breakout_days: int = 20
    volume_days: int = 20
    volume_ratio: float = 1.5
    atr_days: int = 14
    rank_days: int = 63
    earnings_buffer_days: int = 5
    stop_atr: float = 2.0
    target_atr: float = 4.0
    max_hold_days: int = 20
    max_new_per_day: int = 3


@dataclass(frozen=True)
class RiskSettings:
    starting_cash: float = 10_000
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.10
    max_positions: int = 10
    max_per_sector: int = 3
    breaker_drawdown: float = 0.15
    commission: float = 1.0
    slippage: float = 0.001


@dataclass(frozen=True)
class DataSettings:
    db_path: str = "data/market.db"
    membership_csv: str = "data/sp500_membership.csv"
    sectors_csv: str = "data/sectors.csv"
    history_start: date = date(2014, 1, 1)  # a year of warm-up for the 200-day average
    benchmark: str = "SPY"
    max_daily_jump: float = 0.40


@dataclass(frozen=True)
class BacktestSettings:
    start: date = date(2015, 1, 1)
    tuning_end: date = date(2021, 12, 31)
    test_start: date = date(2022, 1, 1)


@dataclass(frozen=True)
class Settings:
    strategy: StrategySettings = field(default_factory=StrategySettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    data: DataSettings = field(default_factory=DataSettings)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)

    def fingerprint(self) -> str:
        """Identifies the trading rules: changes when any strategy or risk number changes."""
        text = json.dumps(
            {"strategy": asdict(self.strategy), "risk": asdict(self.risk)},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(text.encode()).hexdigest()[:12]


SECTIONS = {
    "strategy": StrategySettings,
    "risk": RiskSettings,
    "data": DataSettings,
    "backtest": BacktestSettings,
}


def load_settings(path: Path | None) -> Settings:
    raw = {}
    if path is not None and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for name in raw:
        if name not in SECTIONS:
            raise SettingsError(f"Unknown section {name} in {path}")
    parts = {}
    for name, cls in SECTIONS.items():
        values = raw.get(name) or {}
        allowed = {f.name for f in fields(cls)}
        for key in values:
            if key not in allowed:
                raise SettingsError(f"Unknown setting {name}.{key} in {path}")
        parts[name] = cls(**values)
    return Settings(**parts)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_settings.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add .gitignore .github pyproject.toml config.example.yaml src tests
git commit -m "Add project skeleton and settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Data interfaces, SQLite store and caches

**Files:**
- Create: `src/market_agent/data/__init__.py`, `src/market_agent/data/sources.py`, `src/market_agent/store.py`, `src/market_agent/data/cache.py`
- Test: `tests/test_store_cache.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except test helpers.
- Produces:
  - `NoData(Exception)`; `EarningsHistory(dates: list[pd.Timestamp], coverage_start: pd.Timestamp | None)` (frozen dataclass)
  - `PriceSource` protocol: `fetch(ticker, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame`; `EarningsSource` protocol: `fetch(ticker) -> EarningsHistory`
  - `normalize_bars(raw: pd.DataFrame) -> pd.DataFrame` (index `day`: tz-naive normalized `DatetimeIndex`; float columns `open, high, low, close, volume`; sorted, no duplicates, no NaN close)
  - `Store(path)`: `replace_prices(ticker, bars, fetched_on: str)`, `load_prices(ticker) -> pd.DataFrame | None`, `fetched_on(ticker) -> str | None`, `mark_missing(ticker, fetched_on: str)`, `missing_tickers() -> list[str]`, `tickers_with_prices() -> list[str]`, `replace_earnings(ticker, history, fetched_on)`, `load_earnings() -> dict[str, EarningsHistory]`, `earnings_fetched_on(ticker) -> str | None`, `save_backtest(...)`, `backtest_runs(period) -> list[dict]`, `close()`
  - `PriceCache(store, source, today: Callable[[], date])` with `.update(ticker, start: date) -> bool`, `.load(ticker) -> pd.DataFrame | None`; `EarningsCache(store, source, today)` with `.update(ticker) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_store_cache.py`:
```python
from datetime import date

import pandas as pd
import pytest

from helpers import make_bars
from market_agent.data.cache import EarningsCache, PriceCache
from market_agent.data.sources import EarningsHistory, NoData, normalize_bars
from market_agent.store import Store


class FakePrices:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def fetch(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        if ticker not in self.frames:
            raise NoData(ticker)
        return self.frames[ticker]


class FakeEarnings:
    def __init__(self, histories):
        self.histories = histories
        self.calls = 0

    def fetch(self, ticker):
        self.calls += 1
        return self.histories[ticker]


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "market.db")
    yield s
    s.close()


def test_normalize_bars_handles_yahoo_shape():
    index = pd.DatetimeIndex(
        ["2024-01-03 00:00", "2024-01-02 00:00", "2024-01-03 00:00", "2024-01-04 00:00"],
        tz="America/New_York",
    )
    raw = pd.DataFrame(
        {
            "Open": [2, 1, 2, 3],
            "High": [2, 1, 2, 3],
            "Low": [2, 1, 2, 3],
            "Close": [2, 1, 2, None],
            "Volume": [20, 10, 20, 30],
        },
        index=index,
    )
    bars = normalize_bars(raw)
    assert list(bars.columns) == ["open", "high", "low", "close", "volume"]
    assert bars.index.tz is None
    assert bars.index.name == "day"
    assert list(bars.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert bars["close"].dtype == float


def test_store_round_trips_prices(store):
    bars = make_bars([10, 11, 12])
    store.replace_prices("AAPL", bars, "2026-10-02")
    loaded = store.load_prices("AAPL")
    pd.testing.assert_frame_equal(loaded, bars, check_freq=False)
    assert store.fetched_on("AAPL") == "2026-10-02"
    assert store.load_prices("MSFT") is None
    assert store.tickers_with_prices() == ["AAPL"]


def test_replace_prices_replaces_the_whole_series(store):
    store.replace_prices("AAPL", make_bars([10, 11, 12]), "2026-10-01")
    store.replace_prices("AAPL", make_bars([5, 6]), "2026-10-02")
    assert list(store.load_prices("AAPL")["close"]) == [5.0, 6.0]


def test_price_cache_fetches_once_per_day(store):
    source = FakePrices({"AAPL": make_bars([10, 11])})
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2))
    assert cache.update("AAPL", date(2014, 1, 1)) is True
    assert cache.update("AAPL", date(2014, 1, 1)) is True
    assert len(source.calls) == 1
    ticker, start, end = source.calls[0]
    assert (start, end) == (pd.Timestamp("2014-01-01"), pd.Timestamp("2026-10-02"))
    assert list(cache.load("AAPL")["close"]) == [10.0, 11.0]


def test_price_cache_records_missing_tickers(store):
    cache = PriceCache(store, FakePrices({}), today=lambda: date(2026, 10, 2))
    assert cache.update("TWTR", date(2014, 1, 1)) is False
    assert store.missing_tickers() == ["TWTR"]
    assert cache.load("TWTR") is None


def test_earnings_cache_round_trip(store):
    history = EarningsHistory(
        [pd.Timestamp("2015-01-27"), pd.Timestamp("2015-04-27")], pd.Timestamp("2015-01-27")
    )
    source = FakeEarnings({"AAPL": history, "NEW": EarningsHistory([], None)})
    cache = EarningsCache(store, source, today=lambda: date(2026, 10, 2))
    cache.update("AAPL")
    cache.update("AAPL")
    cache.update("NEW")
    assert source.calls == 2
    loaded = store.load_earnings()
    assert loaded["AAPL"] == history
    assert loaded["NEW"] == EarningsHistory([], None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_store_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.data'`

- [ ] **Step 3: Implement the interfaces**

`src/market_agent/data/__init__.py`:
```python
"""Market data sources and caching."""
```

`src/market_agent/data/sources.py`:
```python
"""Interfaces every market's data source implements, and the common bar format."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


class NoData(Exception):
    """The source has no data for this ticker (unknown, delisted or renamed)."""


@dataclass(frozen=True)
class EarningsHistory:
    dates: list[pd.Timestamp]
    coverage_start: pd.Timestamp | None  # earliest date the source knows about


class PriceSource(Protocol):
    def fetch(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame: ...


class EarningsSource(Protocol):
    def fetch(self, ticker: str) -> EarningsHistory: ...


def to_day(index: pd.Index) -> pd.DatetimeIndex:
    """Exchange timestamps → tz-naive dates in New York time."""
    days = pd.DatetimeIndex(index)
    if days.tz is not None:
        days = days.tz_convert("America/New_York").tz_localize(None)
    return days.normalize()


def normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    bars = raw.rename(columns=str.lower)[BAR_COLUMNS].astype(float)
    bars.index = to_day(raw.index)
    bars.index.name = "day"
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    return bars.dropna(subset=["close"])
```

`src/market_agent/store.py`:
```python
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
                    json.dumps(settings, default=str),
                    json.dumps(metrics),
                    json.dumps(benchmark),
                    json.dumps(notes, default=str),
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
```

`src/market_agent/data/cache.py`:
```python
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


class PriceCache:
    def __init__(self, store: Store, source: PriceSource, today: Callable[[], date] = date.today):
        self._store = store
        self._source = source
        self._today = today

    def update(self, ticker: str, start: date) -> bool:
        """Make sure the cache holds `ticker` up to today. Returns False if there is no data."""
        today = self._today()
        stamp = today.isoformat()
        if self._store.fetched_on(ticker) == stamp:
            return self._store.load_prices(ticker) is not None
        try:
            bars = self._source.fetch(ticker, pd.Timestamp(start), pd.Timestamp(today))
        except NoData:
            log.info("%s: no price data", ticker)
            self._store.mark_missing(ticker, stamp)
            return False
        self._store.replace_prices(ticker, bars, stamp)
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
        stamp = self._today().isoformat()
        if self._store.earnings_fetched_on(ticker) == stamp:
            return
        self._store.replace_earnings(ticker, self._source.fetch(ticker), stamp)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_store_cache.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add data interfaces, SQLite store and daily caches

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Yahoo Finance sources

**Files:**
- Create: `src/market_agent/data/yahoo.py`, `tests/live/test_yahoo_live.py`
- Test: `tests/test_yahoo.py`

**Interfaces:**
- Consumes: `NoData`, `EarningsHistory`, `normalize_bars`, `to_day` (Task 3).
- Produces: `yahoo_symbol(ticker) -> str`; `YahooPrices(client=None)` implementing `PriceSource`; `YahooEarnings(client=None)` implementing `EarningsSource`; `YahooSectors(client=None)` with `.sector(ticker) -> str` ("Unknown" when missing). `client` is the `yfinance` module or a fake with a `Ticker(symbol)` factory.

- [ ] **Step 1: Write the failing tests**

`tests/test_yahoo.py`:
```python
import pandas as pd
import pytest

from market_agent.data.sources import NoData
from market_agent.data.yahoo import YahooEarnings, YahooPrices, YahooSectors, yahoo_symbol


class FakeTicker:
    def __init__(self, history=None, earnings=None, info=None, error=None):
        self._history = history
        self._earnings = earnings
        self.info = info or {}
        self._error = error
        self.history_args = None

    def history(self, **kwargs):
        self.history_args = kwargs
        return self._history if self._history is not None else pd.DataFrame()

    def get_earnings_dates(self, limit):
        if self._error:
            raise self._error
        return self._earnings


class FakeYf:
    def __init__(self, tickers):
        self.tickers = tickers
        self.requested = []

    def Ticker(self, symbol):  # noqa: N802  same name as yfinance
        self.requested.append(symbol)
        return self.tickers[symbol]


def test_yahoo_symbol_uses_dashes():
    assert yahoo_symbol("BRK.B") == "BRK-B"
    assert yahoo_symbol("AAPL") == "AAPL"


def test_prices_are_requested_adjusted_and_normalized():
    raw = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [5.0]},
        index=pd.DatetimeIndex(["2024-01-02"], tz="America/New_York"),
    )
    ticker = FakeTicker(history=raw)
    yf = FakeYf({"BRK-B": ticker})
    bars = YahooPrices(yf).fetch("BRK.B", pd.Timestamp("2014-01-01"), pd.Timestamp("2024-01-02"))
    assert yf.requested == ["BRK-B"]
    assert ticker.history_args["auto_adjust"] is True
    assert str(ticker.history_args["start"]) == "2014-01-01"
    assert str(ticker.history_args["end"]) == "2024-01-03"  # yfinance's end is exclusive
    assert list(bars.index) == [pd.Timestamp("2024-01-02")]


def test_no_prices_raises_no_data():
    with pytest.raises(NoData):
        YahooPrices(FakeYf({"TWTR": FakeTicker()})).fetch(
            "TWTR", pd.Timestamp("2014-01-01"), pd.Timestamp("2024-01-02")
        )


def test_earnings_dates_become_days_with_coverage():
    frame = pd.DataFrame(
        {"EPS Estimate": [1.0, 1.0]},
        index=pd.DatetimeIndex(
            ["2024-04-30 16:30", "2015-01-27 16:30"], tz="America/New_York", name="Earnings Date"
        ),
    )
    history = YahooEarnings(FakeYf({"AAPL": FakeTicker(earnings=frame)})).fetch("AAPL")
    assert history.dates == [pd.Timestamp("2015-01-27"), pd.Timestamp("2024-04-30")]
    assert history.coverage_start == pd.Timestamp("2015-01-27")


def test_earnings_failure_gives_empty_history():
    yf = FakeYf({"AAPL": FakeTicker(error=KeyError("Earnings Date"))})
    history = YahooEarnings(yf).fetch("AAPL")
    assert history.dates == [] and history.coverage_start is None


def test_sector():
    yf = FakeYf({"AAPL": FakeTicker(info={"sector": "Technology"}), "X": FakeTicker(info={})})
    assert YahooSectors(yf).sector("AAPL") == "Technology"
    assert YahooSectors(yf).sector("X") == "Unknown"
```

`tests/live/test_yahoo_live.py`:
```python
"""Reaches Yahoo Finance. Run with: pytest -m live"""

import pandas as pd
import pytest

from market_agent.data.yahoo import YahooEarnings, YahooPrices

pytestmark = pytest.mark.live


def test_real_prices_and_earnings():
    bars = YahooPrices().fetch("AAPL", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-03-01"))
    assert len(bars) > 30
    assert (bars["high"] >= bars["low"]).all()
    assert YahooEarnings().fetch("AAPL").dates
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_yahoo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.data.yahoo'`

- [ ] **Step 3: Implement `yahoo.py`**

`src/market_agent/data/yahoo.py`:
```python
"""Yahoo Finance through the unofficial yfinance library. Free, personal use only."""

from __future__ import annotations

import importlib
import logging
from typing import Any

import pandas as pd

from market_agent.data.sources import EarningsHistory, NoData, normalize_bars, to_day

log = logging.getLogger(__name__)


def yahoo_symbol(ticker: str) -> str:
    """Class shares: BRK.B in index lists is BRK-B on Yahoo."""
    return ticker.replace(".", "-")


def _client(client: Any) -> Any:
    return client if client is not None else importlib.import_module("yfinance")


class YahooPrices:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def fetch(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        raw = self._yf.Ticker(yahoo_symbol(ticker)).history(
            start=start.date(),
            end=(end + pd.Timedelta(days=1)).date(),
            auto_adjust=True,
            actions=False,
        )
        if raw is None or raw.empty:
            raise NoData(ticker)
        bars = normalize_bars(raw)
        if bars.empty:
            raise NoData(ticker)
        return bars


class YahooEarnings:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def fetch(self, ticker: str) -> EarningsHistory:
        try:
            frame = self._yf.Ticker(yahoo_symbol(ticker)).get_earnings_dates(limit=100)
        except Exception as exc:  # yfinance raises many kinds of errors for missing data
            log.info("%s: no earnings dates (%s)", ticker, exc)
            return EarningsHistory([], None)
        if frame is None or frame.empty:
            return EarningsHistory([], None)
        days = sorted(set(to_day(frame.index)))
        return EarningsHistory(days, days[0])


class YahooSectors:
    def __init__(self, client: Any = None):
        self._yf = _client(client)

    def sector(self, ticker: str) -> str:
        try:
            info = self._yf.Ticker(yahoo_symbol(ticker)).info or {}
        except Exception as exc:
            log.info("%s: no sector (%s)", ticker, exc)
            return "Unknown"
        return info.get("sector") or "Unknown"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_yahoo.py -v`
Expected: PASS (6 tests). Then once, with network: `pytest -m live -v` → PASS.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add Yahoo Finance price, earnings and sector sources

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Universe (historical membership) and sectors

**Files:**
- Create: `src/market_agent/universe.py`
- Test: `tests/test_universe.py`

**Interfaces:**
- Produces: `clean_ticker(raw) -> str`; `Universe(snapshots: list[tuple[pd.Timestamp, frozenset[str]]])` with `Universe.from_csv(path)`, `.members(day) -> frozenset[str]`, `.tickers_between(start, end) -> set[str]`; `load_sectors(path) -> dict[str, str]`; `write_sectors(path, sectors: dict[str, str])`.

- [ ] **Step 1: Write the failing tests**

`tests/test_universe.py`:
```python
import pandas as pd

from market_agent.universe import Universe, clean_ticker, load_sectors, write_sectors

CSV = """date,tickers
2014-01-02,"AAPL,MSFT,TWTR"
2015-06-01,"AAPL,MSFT,BRK.B"
2020-03-02,"AAPL,BRK.B,ZZZ-201912"
"""


def universe(tmp_path):
    path = tmp_path / "members.csv"
    path.write_text(CSV, encoding="utf-8")
    return Universe.from_csv(path)


def test_clean_ticker():
    assert clean_ticker(" aapl ") == "AAPL"
    assert clean_ticker("ZZZ-201912") == "ZZZ"
    assert clean_ticker("BRK.B") == "BRK.B"


def test_members_on_a_day_use_the_latest_snapshot(tmp_path):
    u = universe(tmp_path)
    assert u.members(pd.Timestamp("2013-12-31")) == frozenset()
    assert u.members(pd.Timestamp("2014-01-02")) == {"AAPL", "MSFT", "TWTR"}
    assert u.members(pd.Timestamp("2015-05-29")) == {"AAPL", "MSFT", "TWTR"}
    assert u.members(pd.Timestamp("2015-06-01")) == {"AAPL", "MSFT", "BRK.B"}
    assert u.members(pd.Timestamp("2024-01-01")) == {"AAPL", "BRK.B", "ZZZ"}


def test_tickers_between_includes_every_snapshot_in_force(tmp_path):
    u = universe(tmp_path)
    assert u.tickers_between(pd.Timestamp("2015-01-01"), pd.Timestamp("2016-01-01")) == {
        "AAPL",
        "MSFT",
        "TWTR",
        "BRK.B",
    }


def test_sectors_round_trip(tmp_path):
    path = tmp_path / "sectors.csv"
    write_sectors(path, {"MSFT": "Technology", "JPM": "Financial Services"})
    assert load_sectors(path) == {"JPM": "Financial Services", "MSFT": "Technology"}
    assert load_sectors(tmp_path / "none.csv") == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_universe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.universe'`

- [ ] **Step 3: Implement `universe.py`**

`src/market_agent/universe.py`:
```python
"""Which tickers could be traded on a given day: historical S&P 500 membership.

Using today's members for past years would hide every company that later failed
(survivorship bias), so membership is looked up by date.
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_right
from pathlib import Path

import pandas as pd

REMOVED_SUFFIX = re.compile(r"-\d{6}$")  # some datasets mark removed tickers as ABC-201912


def clean_ticker(raw: str) -> str:
    return REMOVED_SUFFIX.sub("", raw.strip().upper())


class Universe:
    def __init__(self, snapshots: list[tuple[pd.Timestamp, frozenset[str]]]):
        self._snapshots = sorted(snapshots, key=lambda item: item[0])
        self._days = [day for day, _ in self._snapshots]

    @classmethod
    def from_csv(cls, path: Path) -> Universe:
        frame = pd.read_csv(path)
        snapshots = [
            (
                pd.Timestamp(row["date"]),
                frozenset(clean_ticker(t) for t in str(row["tickers"]).split(",") if t.strip()),
            )
            for _, row in frame.iterrows()
        ]
        return cls(snapshots)

    def members(self, day: pd.Timestamp) -> frozenset[str]:
        index = bisect_right(self._days, day) - 1
        return self._snapshots[index][1] if index >= 0 else frozenset()

    def tickers_between(self, start: pd.Timestamp, end: pd.Timestamp) -> set[str]:
        tickers = set(self.members(start))
        for day, members in self._snapshots:
            if start < day <= end:
                tickers |= members
        return tickers


def load_sectors(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["ticker"]: row["sector"] for row in csv.DictReader(handle)}


def write_sectors(path: Path, sectors: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "sector"])
        for ticker in sorted(sectors):
            writer.writerow([ticker, sectors[ticker]])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_universe.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add historical S&P 500 universe and sector lists

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Indicators, bad-data checks and the day × ticker panel

**Files:**
- Create: `src/market_agent/indicators.py`, `src/market_agent/panel.py`
- Test: `tests/test_indicators_panel.py`

**Interfaces:**
- Consumes: `StrategySettings` (Task 2); `make_bars` helper.
- Produces:
  - `INDICATOR_COLUMNS` (tuple); `add_indicators(bars, s: StrategySettings) -> pd.DataFrame` adding `sma_fast, sma_slow, atr, high_close, avg_volume_prev, avg_value, ret_rank`
  - `suspicious_days(bars, max_jump: float) -> pd.DatetimeIndex`
  - `Bar(open, high, low, close)` (frozen dataclass)
  - `Panel(frames: Mapping[str, pd.DataFrame], days: pd.DatetimeIndex, strategy: StrategySettings, max_jump: float)` with `.days`, `.tickers: set[str]`, `.snapshot(day) -> pd.DataFrame` (index ticker; columns bar + indicator columns), `.bar(ticker, day) -> Bar | None`, `.last_day(ticker) -> pd.Timestamp | None`, `.excluded: dict[str, list[pd.Timestamp]]`

- [ ] **Step 1: Write the failing tests**

`tests/test_indicators_panel.py`:
```python
import math

import pandas as pd

from helpers import make_bars
from market_agent.indicators import add_indicators, suspicious_days
from market_agent.panel import Bar, Panel
from market_agent.settings import StrategySettings

S = StrategySettings()


def rising(n=260, start=50.0, step=0.1):
    return [start + step * i for i in range(n)]


def test_indicator_values():
    bars = make_bars([10, 11, 12, 13, 14], volumes=[100, 200, 300, 400, 500])
    s = StrategySettings(
        sma_fast=2, sma_slow=3, atr_days=2, breakout_days=3, volume_days=2, rank_days=2
    )
    df = add_indicators(bars, s)
    last = df.iloc[-1]
    assert last["sma_fast"] == 13.5
    assert last["sma_slow"] == 13.0
    assert last["high_close"] == 14.0
    assert last["avg_volume_prev"] == 350.0  # days 3 and 4, not today
    assert last["avg_value"] == (13 * 400 + 14 * 500) / 2
    assert math.isclose(last["ret_rank"], 14 / 12 - 1)
    # true range on the last day: max(high-low, |high-prev close|, |low-prev close|)
    tr_last = max(14.14 - 13.86, abs(14.14 - 13), abs(13.86 - 13))
    tr_prev = max(13.13 - 12.87, abs(13.13 - 12), abs(12.87 - 12))
    assert math.isclose(last["atr"], (tr_last + tr_prev) / 2)


def test_indicators_never_look_ahead():
    bars = make_bars(rising(300), volumes=[1e6 + 1000 * (i % 7) for i in range(300)])
    full = add_indicators(bars, S)
    for cut in (210, 250, 299):
        partial = add_indicators(bars.iloc[: cut + 1], S)
        pd.testing.assert_series_equal(partial.iloc[-1], full.iloc[cut], check_names=False)


def test_suspicious_days_flags_big_jumps():
    bars = make_bars([10, 10.5, 16, 15.5, 15])
    assert list(suspicious_days(bars, 0.40)) == [bars.index[2]]


def test_panel_snapshot_and_bar():
    a = make_bars(rising(260))
    b = make_bars(rising(260, start=20))
    panel = Panel({"AAA": a, "BBB": b}, a.index, S, 0.40)
    day = a.index[-1]
    snap = panel.snapshot(day)
    assert set(snap.index) == {"AAA", "BBB"}
    assert snap.loc["AAA", "close"] == a["close"].iloc[-1]
    assert not math.isnan(snap.loc["AAA", "sma_slow"])
    assert panel.bar("AAA", day) == Bar(
        a["open"].iloc[-1], a["high"].iloc[-1], a["low"].iloc[-1], a["close"].iloc[-1]
    )
    assert panel.bar("ZZZ", day) is None
    assert panel.tickers == {"AAA", "BBB"}


def test_panel_excludes_suspicious_days_and_knows_last_day():
    closes = rising(30)
    closes[10] = closes[10] * 1.5  # one bad spike (+50%); the fall back next day is −33%
    a = make_bars(closes)
    short = make_bars(rising(20))
    panel = Panel({"AAA": a, "SHORT": short}, a.index, S, 0.40)
    assert panel.bar("AAA", a.index[10]) is None
    assert panel.excluded == {"AAA": [a.index[10]]}
    assert panel.last_day("SHORT") == short.index[-1]
    assert panel.bar("SHORT", a.index[25]) is None
    assert panel.last_day("NOPE") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_indicators_panel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.indicators'`

- [ ] **Step 3: Implement indicators**

`src/market_agent/indicators.py`:
```python
"""Indicators from daily bars. Every rolling window looks backwards only (no look-ahead)."""

from __future__ import annotations

import pandas as pd

from market_agent.settings import StrategySettings

INDICATOR_COLUMNS = (
    "sma_fast",
    "sma_slow",
    "atr",
    "high_close",
    "avg_volume_prev",
    "avg_value",
    "ret_rank",
)


def add_indicators(bars: pd.DataFrame, s: StrategySettings) -> pd.DataFrame:
    df = bars.copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    df["sma_fast"] = close.rolling(s.sma_fast).mean()
    df["sma_slow"] = close.rolling(s.sma_slow).mean()
    previous = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous).abs(), (low - previous).abs()], axis=1
    ).max(axis=1)
    df["atr"] = true_range.rolling(s.atr_days).mean()
    df["high_close"] = close.rolling(s.breakout_days).max()
    df["avg_volume_prev"] = volume.shift(1).rolling(s.volume_days).mean()
    df["avg_value"] = (close * volume).rolling(s.volume_days).mean()
    df["ret_rank"] = close / close.shift(s.rank_days) - 1
    return df


def suspicious_days(bars: pd.DataFrame, max_jump: float) -> pd.DatetimeIndex:
    """Days whose close moved more than max_jump from the previous close.

    Prices are adjusted for splits, so such a jump is usually bad data. A real jump that large
    (e.g. a takeover) is also skipped for that day, which is the cautious choice.
    """
    change = bars["close"].pct_change().abs()
    return bars.index[change > max_jump]
```

- [ ] **Step 4: Implement the panel**

`src/market_agent/panel.py`:
```python
"""Day × ticker tables of prices and indicators, so one day's screen is a single row lookup."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from market_agent.data.sources import BAR_COLUMNS
from market_agent.indicators import INDICATOR_COLUMNS, add_indicators, suspicious_days
from market_agent.settings import StrategySettings

COLUMNS = (*BAR_COLUMNS, *INDICATOR_COLUMNS)


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


class Panel:
    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        days: pd.DatetimeIndex,
        strategy: StrategySettings,
        max_jump: float,
    ):
        self.days = pd.DatetimeIndex(days)
        self.excluded: dict[str, list[pd.Timestamp]] = {}
        self._last_day: dict[str, pd.Timestamp] = {}
        enriched = {}
        for ticker, bars in frames.items():
            bad = suspicious_days(bars, max_jump)
            if len(bad):
                self.excluded[ticker] = list(bad)
            clean = bars.drop(index=bad)
            if clean.empty:
                continue
            self._last_day[ticker] = clean.index[-1]
            enriched[ticker] = add_indicators(clean, strategy).reindex(self.days)
        self.tickers = set(enriched)
        self._wide = {
            column: pd.DataFrame(
                {ticker: df[column] for ticker, df in enriched.items()}, index=self.days
            )
            for column in COLUMNS
        }

    def snapshot(self, day: pd.Timestamp) -> pd.DataFrame:
        return pd.DataFrame({column: wide.loc[day] for column, wide in self._wide.items()})

    def bar(self, ticker: str, day: pd.Timestamp) -> Bar | None:
        if ticker not in self.tickers or day not in self.days:
            return None
        values = [self._wide[c].at[day, ticker] for c in ("open", "high", "low", "close")]
        if any(math.isnan(v) for v in values):
            return None
        return Bar(*(float(v) for v in values))

    def last_day(self, ticker: str) -> pd.Timestamp | None:
        return self._last_day.get(ticker)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_indicators_panel.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add indicators, bad-data checks and the day-by-ticker panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Earnings calendar and the screen

**Files:**
- Create: `src/market_agent/earnings.py`, `src/market_agent/strategy.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Consumes: `EarningsHistory` (Task 3); `Panel` (Task 6); `StrategySettings` (Task 2).
- Produces:
  - `EarningsCalendar(histories: Mapping[str, EarningsHistory], trading_days: Sequence[pd.Timestamp])` with `.reports_within(ticker, day, n) -> bool` (report on day D or in the next n trading days) and `.known(ticker, day) -> bool`
  - `Candidate(ticker, day, close, atr, score, volume_ratio, earnings_known)` (frozen dataclass)
  - `screen(day, snapshot: pd.DataFrame, members: Iterable[str], earnings: EarningsCalendar, s: StrategySettings) -> list[Candidate]` (best score first, ties by ticker)

- [ ] **Step 1: Write the failing tests**

`tests/test_strategy.py`:
```python
import pandas as pd

from helpers import make_bars
from market_agent.data.sources import EarningsHistory
from market_agent.earnings import EarningsCalendar
from market_agent.panel import Panel
from market_agent.settings import StrategySettings
from market_agent.strategy import screen

S = StrategySettings()
N = 260


def breakout(start=50.0, step=0.1, last_volume=2e6):
    """Steady rise (always a 20-day high), normal volume, then a volume spike on the last day."""
    closes = [start + step * i for i in range(N)]
    volumes = [1e6] * (N - 1) + [last_volume]
    return make_bars(closes, volumes)


def run(frames, earnings=None, members=None):
    days = next(iter(frames.values())).index
    panel = Panel(frames, days, S, 0.40)
    histories = earnings or {t: EarningsHistory([], days[0]) for t in frames}
    calendar = EarningsCalendar(histories, list(days))
    day = days[-1]
    return screen(day, panel.snapshot(day), members or set(frames), calendar, S)


def test_breakout_on_volume_is_a_candidate():
    [candidate] = run({"AAA": breakout()})
    assert candidate.ticker == "AAA"
    assert candidate.volume_ratio == 2.0
    assert candidate.close == 50 + 0.1 * (N - 1)
    assert candidate.atr > 0
    assert candidate.earnings_known is True


def test_needs_enough_volume():
    assert run({"AAA": breakout(last_volume=1.4e6)}) == []


def test_needs_an_uptrend():
    falling = make_bars([100 - 0.1 * i for i in range(N - 1)] + [100.0], [1e6] * (N - 1) + [2e6])
    assert run({"AAA": falling}) == []


def test_needs_price_and_liquidity():
    cheap = breakout(start=5.0, step=0.01)
    assert run({"AAA": cheap}) == []
    thin = make_bars([50 + 0.1 * i for i in range(N)], [1e5] * (N - 1) + [2e5])
    assert run({"AAA": thin}) == []


def test_only_universe_members():
    assert run({"AAA": breakout()}, members={"BBB"}) == []


def test_earnings_within_five_trading_days_blocks_entry():
    bars = breakout()
    days = bars.index
    soon = EarningsHistory([days[-1] + pd.offsets.BDay(3)], days[0])
    later = EarningsHistory([days[-1] + pd.offsets.BDay(8)], days[0])
    assert run({"AAA": bars}, earnings={"AAA": soon}) == []
    assert len(run({"AAA": bars}, earnings={"AAA": later})) == 1


def test_candidate_records_missing_earnings_coverage():
    [candidate] = run({"AAA": breakout()}, earnings={"AAA": EarningsHistory([], None)})
    assert candidate.earnings_known is False


def test_ranked_by_three_month_return():
    slow = breakout(step=0.05)
    fast = breakout(step=0.2)
    assert [c.ticker for c in run({"SLOW": slow, "FAST": fast})] == ["FAST", "SLOW"]


def test_reports_within_uses_trading_days():
    days = list(pd.bdate_range("2024-01-01", periods=30))
    cal = EarningsCalendar({"A": EarningsHistory([days[10]], days[0])}, days)
    assert cal.reports_within("A", days[5], 5) is True
    assert cal.reports_within("A", days[4], 5) is False
    assert cal.reports_within("A", days[10], 5) is True
    assert cal.reports_within("A", days[11], 5) is False
    assert cal.known("A", days[0]) is True
    assert cal.known("B", days[0]) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_strategy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.earnings'`

- [ ] **Step 3: Implement the earnings calendar**

`src/market_agent/earnings.py`:
```python
"""Answers 'is there an earnings report on this day or in the next n trading days?'"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence

import pandas as pd

from market_agent.data.sources import EarningsHistory


class EarningsCalendar:
    def __init__(
        self, histories: Mapping[str, EarningsHistory], trading_days: Sequence[pd.Timestamp]
    ):
        self._dates = {ticker: sorted(h.dates) for ticker, h in histories.items()}
        self._coverage = {ticker: h.coverage_start for ticker, h in histories.items()}
        self._days = list(trading_days)

    def window_end(self, day: pd.Timestamp, n: int) -> pd.Timestamp:
        index = bisect_right(self._days, day) - 1 + n
        if 0 <= index < len(self._days):
            return self._days[index]
        # past the known calendar (the latest days of a run): about n trading days later
        return day + pd.Timedelta(days=math.ceil(n * 7 / 5) + 2)

    def reports_within(self, ticker: str, day: pd.Timestamp, n: int) -> bool:
        dates = self._dates.get(ticker, [])
        index = bisect_left(dates, day)
        return index < len(dates) and dates[index] <= self.window_end(day, n)

    def known(self, ticker: str, day: pd.Timestamp) -> bool:
        start = self._coverage.get(ticker)
        return start is not None and start <= day
```

- [ ] **Step 4: Implement the screen**

`src/market_agent/strategy.py`:
```python
"""The breakout strategy: which shares to buy on day D (spec section 4)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from market_agent.earnings import EarningsCalendar
from market_agent.settings import StrategySettings

REQUIRED = [
    "close",
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
        (snap["close"] > s.min_price)
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_strategy.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add earnings calendar and breakout screen

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Portfolio records and risk rules

**Files:**
- Create: `src/market_agent/portfolio.py`, `src/market_agent/risk.py`
- Test: `tests/test_risk.py`

**Interfaces:**
- Consumes: `Candidate` (Task 7); `RiskSettings`, `StrategySettings` (Task 2).
- Produces:
  - `Position(ticker, sector, shares, entry_day, entry_price, stop, target, last_close, days_held=0)` (mutable dataclass)
  - `Order(ticker, side: "buy" | "sell", shares, reason, created, atr=0.0, sector="Unknown")`
  - `Trade(ticker, sector, entry_day, entry_price, exit_day, exit_price, shares, exit_reason, pnl)` (frozen)
  - `Portfolio(name, cash)` with fields `positions: dict[str, Position]`, `orders: list[Order]`, `trades: list[Trade]`, `equity_history: list[tuple[pd.Timestamp, float]]`, `peak: float`, `halted: bool`, `events: list[str]`; method `open_sectors() -> dict[str, str]` (held and pending-buy tickers → sector)
  - `position_size(equity, cash, price, atr, risk: RiskSettings, strategy: StrategySettings) -> int`
  - `check_limits(ticker, sector, open_sectors: Mapping[str, str], risk) -> str | None` (reason refused, or None)
  - `plan_entries(portfolio, candidates, equity, sectors: Mapping[str, str], risk, strategy, day) -> list[str]` (adds buy orders; returns refusal notes)

- [ ] **Step 1: Write the failing tests**

`tests/test_risk.py`:
```python
import pandas as pd

from market_agent.portfolio import Order, Portfolio, Position
from market_agent.risk import check_limits, plan_entries, position_size
from market_agent.settings import RiskSettings, StrategySettings
from market_agent.strategy import Candidate

R = RiskSettings()
S = StrategySettings()
DAY = pd.Timestamp("2024-03-01")


def candidate(ticker, close=100.0, atr=2.0, score=0.1):
    return Candidate(ticker, DAY, close, atr, score, 2.0, True)


def test_size_risks_one_percent():
    # risk per share = 2 × ATR = 4; 1% of 10,000 = 100 → 25 shares (2,500 = 25% → capped)
    assert position_size(10_000, 10_000, 100.0, 2.0, R, S) == 10  # 10% cap: 1,000 / 100
    # wide stop: ATR 10 → risk/share 20 → 5 shares (500, under the cap)
    assert position_size(10_000, 10_000, 100.0, 10.0, R, S) == 5


def test_size_limited_by_cash():
    assert position_size(10_000, 301.0, 100.0, 10.0, R, S) == 2  # (301 − 1) / 100.1


def test_size_zero_when_nothing_fits():
    assert position_size(10_000, 50.0, 100.0, 2.0, R, S) == 0
    assert position_size(10_000, 10_000, 100.0, 0.0, R, S) == 0


def test_limits():
    held = {f"T{i}": "Tech" if i < 3 else f"S{i}" for i in range(9)}
    assert check_limits("T0", "Tech", held, R) == "already held"
    assert check_limits("NEW", "Tech", held, R) == "3 positions in Tech already"
    assert check_limits("NEW", "Energy", held, R) is None
    held["X"] = "Other"
    assert check_limits("NEW", "Energy", held, R) == "10 positions already open"


def test_plan_entries_respects_daily_limit_and_cash():
    p = Portfolio("rules-only", 10_000)
    candidates = [candidate(t) for t in ["A", "B", "C", "D"]]
    notes = plan_entries(p, candidates, 10_000, {}, R, S, DAY)
    assert [o.ticker for o in p.orders] == ["A", "B", "C"]
    assert all(o.side == "buy" and o.shares == 10 and o.atr == 2.0 for o in p.orders)
    assert notes == []


def test_plan_entries_counts_pending_orders_against_limits():
    p = Portfolio("rules-only", 10_000)
    sectors = {t: "Tech" for t in "ABCD"}
    p.positions["A"] = Position("A", "Tech", 10, DAY, 100.0, 96.0, 108.0, 100.0)
    p.orders.append(Order("B", "buy", 10, "breakout", DAY, 2.0, "Tech"))
    notes = plan_entries(p, [candidate("C"), candidate("D")], 10_000, sectors, R, S, DAY)
    assert [o.ticker for o in p.orders] == ["B", "C"]
    assert notes == ["D: 3 positions in Tech already"]


def test_halted_portfolio_opens_nothing():
    p = Portfolio("rules-only", 10_000)
    p.halted = True
    assert plan_entries(p, [candidate("A")], 10_000, {}, R, S, DAY) == ["circuit breaker on"]
    assert p.orders == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_risk.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.portfolio'`

- [ ] **Step 3: Implement the portfolio records**

`src/market_agent/portfolio.py`:
```python
"""Holdings, pending orders and closed trades of one simulated portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


@dataclass
class Position:
    ticker: str
    sector: str
    shares: int
    entry_day: pd.Timestamp
    entry_price: float  # after slippage
    stop: float
    target: float
    last_close: float
    days_held: int = 0


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    reason: str
    created: pd.Timestamp
    atr: float = 0.0
    sector: str = "Unknown"


@dataclass(frozen=True)
class Trade:
    ticker: str
    sector: str
    entry_day: pd.Timestamp
    entry_price: float
    exit_day: pd.Timestamp
    exit_price: float
    shares: int
    exit_reason: str
    pnl: float  # after both commissions


@dataclass
class Portfolio:
    name: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    equity_history: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    peak: float = 0.0
    halted: bool = False
    events: list[str] = field(default_factory=list)

    def open_sectors(self) -> dict[str, str]:
        held = {ticker: pos.sector for ticker, pos in self.positions.items()}
        pending = {o.ticker: o.sector for o in self.orders if o.side == "buy"}
        return {**held, **pending}
```

- [ ] **Step 4: Implement the risk rules**

`src/market_agent/risk.py`:
```python
"""Position sizing and portfolio limits (spec section 5)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence

import pandas as pd

from market_agent.portfolio import Order, Portfolio
from market_agent.settings import RiskSettings, StrategySettings
from market_agent.strategy import Candidate


def position_size(
    equity: float,
    cash: float,
    price: float,
    atr: float,
    risk: RiskSettings,
    strategy: StrategySettings,
) -> int:
    """Shares so that a stop-out loses risk_per_trade of equity, within the size and cash caps."""
    risk_per_share = strategy.stop_atr * atr
    if risk_per_share <= 0 or price <= 0:
        return 0
    by_risk = math.floor(risk.risk_per_trade * equity / risk_per_share)
    by_cap = math.floor(risk.max_position_pct * equity / price)
    by_cash = math.floor((cash - risk.commission) / (price * (1 + risk.slippage)))
    return max(0, min(by_risk, by_cap, by_cash))


def check_limits(
    ticker: str, sector: str, open_sectors: Mapping[str, str], risk: RiskSettings
) -> str | None:
    if ticker in open_sectors:
        return "already held"
    if len(open_sectors) >= risk.max_positions:
        return f"{risk.max_positions} positions already open"
    if Counter(open_sectors.values())[sector] >= risk.max_per_sector:
        return f"{risk.max_per_sector} positions in {sector} already"
    return None


def plan_entries(
    portfolio: Portfolio,
    candidates: Sequence[Candidate],
    equity: float,
    sectors: Mapping[str, str],
    risk: RiskSettings,
    strategy: StrategySettings,
    day: pd.Timestamp,
) -> list[str]:
    """Add buy orders for the best candidates. Returns why candidates were refused."""
    if portfolio.halted:
        return ["circuit breaker on"]
    # Buy orders are created after the close and filled at the next open, so earlier buy orders
    # have already been filled when this runs; only orders added here need cash set aside.
    # Pending buys still count in open_sectors() for the position and sector limits.
    notes = []
    committed = 0.0
    slots = strategy.max_new_per_day
    for c in candidates:
        if slots == 0:
            break
        sector = sectors.get(c.ticker, "Unknown")
        refusal = check_limits(c.ticker, sector, portfolio.open_sectors(), risk)
        if refusal:
            notes.append(f"{c.ticker}: {refusal}")
            continue
        shares = position_size(equity, portfolio.cash - committed, c.close, c.atr, risk, strategy)
        if shares == 0:
            notes.append(f"{c.ticker}: not enough cash")
            continue
        portfolio.orders.append(Order(c.ticker, "buy", shares, "breakout", day, c.atr, sector))
        committed += shares * c.close * (1 + risk.slippage) + risk.commission
        slots -= 1
    return notes
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_risk.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add portfolio records, position sizing and limits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Trade simulator

**Files:**
- Create: `src/market_agent/broker.py`
- Test: `tests/test_broker.py`

**Interfaces:**
- Consumes: `Portfolio`, `Position`, `Order`, `Trade` (Task 8); `Bar` (Task 6); settings.
- Produces: `BarLookup = Callable[[str, pd.Timestamp], Bar | None]`; `Simulator(risk, strategy)` with
  - `.open(portfolio, day, bar)`: fill pending orders at the day's open
  - `.intraday(portfolio, day, bar)`: stop/target exits
  - `.close(portfolio, day, bar, last_day: Callable[[str], pd.Timestamp | None]) -> float`: delistings, holding days, time-exit orders, equity, circuit breaker; returns equity

- [ ] **Step 1: Write the failing tests**

`tests/test_broker.py`:
```python
import pandas as pd
import pytest

from market_agent.broker import Simulator
from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio, Position
from market_agent.settings import RiskSettings, StrategySettings

R = RiskSettings()
S = StrategySettings()
D0, D1, D2, D3 = pd.bdate_range("2024-03-01", periods=4)


def lookup(table):
    return lambda ticker, day: table.get((ticker, day))


def held(stop=96.0, target=108.0, entry_day=D0, shares=10):
    p = Portfolio("p", 9_000.0)
    p.positions["AAA"] = Position("AAA", "Tech", shares, entry_day, 100.1, stop, target, 100.0)
    return p


def test_buy_fills_at_next_open_with_costs_and_sets_stop_target():
    p = Portfolio("p", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0, sector="Tech"))
    Simulator(R, S).open(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100.5)}))
    pos = p.positions["AAA"]
    assert pos.entry_price == pytest.approx(100.1)
    assert p.cash == pytest.approx(10_000 - 10 * 100.1 - 1)
    assert (pos.stop, pos.target) == (96.0, 108.0)
    assert p.orders == []


def test_buy_reduced_to_available_cash():
    p = Portfolio("p", 505.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0))
    p.orders.append(Order("BBB", "buy", 10, "breakout", D0, atr=2.0))
    bars = {("AAA", D1): Bar(100, 101, 99, 100), ("BBB", D1): Bar(100, 101, 99, 100)}
    Simulator(R, S).open(p, D1, lookup(bars))
    assert p.positions["AAA"].shares == 5  # (505 − 1) / 100.1
    assert "BBB" not in p.positions
    assert p.cash >= 0


def test_buy_without_a_bar_is_dropped():
    p = Portfolio("p", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0))
    Simulator(R, S).open(p, D1, lookup({}))
    assert p.positions == {} and p.orders == []


@pytest.mark.parametrize(
    ("bar", "price", "reason"),
    [
        (Bar(97, 99, 95, 98), 96.0, "stop"),
        (Bar(100, 109, 99, 108.5), 108.0, "target"),
        (Bar(100, 110, 95, 100), 96.0, "stop"),  # both inside the range: assume the stop
        (Bar(94, 95, 93, 94), 94.0, "stop (gap)"),
        (Bar(110, 111, 109, 110), 110.0, "target (gap)"),
    ],
)
def test_exits(bar, price, reason):
    p = held()
    Simulator(R, S).intraday(p, D1, lookup({("AAA", D1): bar}))
    [trade] = p.trades
    assert trade.exit_reason == reason
    assert trade.exit_price == pytest.approx(price * (1 - R.slippage))
    assert trade.pnl == pytest.approx((trade.exit_price - 100.1) * 10 - 2 * R.commission)
    assert p.cash == pytest.approx(9_000 + 10 * trade.exit_price - 1)
    assert p.positions == {}


def test_gap_rule_does_not_apply_on_entry_day():
    p = held(entry_day=D1)
    Simulator(R, S).intraday(p, D1, lookup({("AAA", D1): Bar(94, 95, 93, 94)}))
    assert p.trades[0].exit_reason == "stop"


def test_time_exit_after_max_hold_days():
    p = held()
    p.positions["AAA"].days_held = S.max_hold_days - 1
    sim = Simulator(R, S)
    sim.close(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100)}), lambda t: D3)
    assert p.orders == [Order("AAA", "sell", 10, "time", D1)]
    sim.open(p, D2, lookup({("AAA", D2): Bar(102, 103, 101, 102)}))
    assert p.trades[0].exit_reason == "time"
    assert p.trades[0].exit_price == pytest.approx(102 * (1 - R.slippage))


def test_delisted_position_closes_at_last_close():
    p = held()
    p.positions["AAA"].last_close = 90.0
    Simulator(R, S).close(p, D2, lookup({}), lambda t: D1)
    [trade] = p.trades
    assert trade.exit_reason == "delisted"
    assert trade.exit_price == pytest.approx(90.0 * (1 - R.slippage))


def test_missing_day_without_delisting_keeps_the_position():
    p = held()
    Simulator(R, S).close(p, D1, lookup({}), lambda t: D3)
    assert "AAA" in p.positions


def test_equity_and_circuit_breaker():
    p = held()
    sim = Simulator(R, S)
    assert sim.close(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100)}), lambda t: D3) == 10_000
    assert p.peak == 10_000 and not p.halted
    p.cash = 7_000.0
    equity = sim.close(p, D2, lookup({("AAA", D2): Bar(50, 51, 49, 50)}), lambda t: D3)
    assert equity == 7_500
    assert p.halted
    assert p.events == ["2024-03-05: circuit breaker on, equity 7,500 is 25.0% below peak 10,000"]
    assert p.equity_history == [(D1, 10_000.0), (D2, 7_500.0)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_broker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.broker'`

- [ ] **Step 3: Implement the simulator**

`src/market_agent/broker.py`:
```python
"""Simulated broker shared by the backtest and paper trading (spec sections 4 and 5).

Day order: open() fills orders placed yesterday, intraday() checks stops and targets, close()
marks to market and queues time exits. Every fill pays slippage and commission.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pandas as pd

from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio, Position, Trade
from market_agent.settings import RiskSettings, StrategySettings

BarLookup = Callable[[str, pd.Timestamp], Bar | None]


class Simulator:
    def __init__(self, risk: RiskSettings, strategy: StrategySettings):
        self.risk = risk
        self.strategy = strategy

    def open(self, p: Portfolio, day: pd.Timestamp, bar: BarLookup) -> None:
        orders, p.orders = p.orders, []
        for order in orders:
            b = bar(order.ticker, day)
            if order.side == "sell":
                if order.ticker not in p.positions:
                    continue
                if b is None:
                    p.orders.append(order)  # no price today: try again tomorrow
                    continue
                self._exit(p, order.ticker, day, b.open, order.reason)
            elif b is not None:
                self._buy(p, order, day, b)

    def intraday(self, p: Portfolio, day: pd.Timestamp, bar: BarLookup) -> None:
        for ticker, pos in list(p.positions.items()):
            b = bar(ticker, day)
            if b is None:
                continue
            opened_today = pos.entry_day == day
            if not opened_today and b.open <= pos.stop:
                self._exit(p, ticker, day, b.open, "stop (gap)")
            elif not opened_today and b.open >= pos.target:
                self._exit(p, ticker, day, b.open, "target (gap)")
            elif b.low <= pos.stop:
                self._exit(p, ticker, day, pos.stop, "stop")
            elif b.high >= pos.target:
                self._exit(p, ticker, day, pos.target, "target")

    def close(
        self,
        p: Portfolio,
        day: pd.Timestamp,
        bar: BarLookup,
        last_day: Callable[[str], pd.Timestamp | None],
    ) -> float:
        selling = {o.ticker for o in p.orders if o.side == "sell"}
        for ticker, pos in list(p.positions.items()):
            b = bar(ticker, day)
            if b is None:
                end = last_day(ticker)
                if end is not None and end < day:
                    self._exit(p, ticker, day, pos.last_close, "delisted")
                continue
            pos.last_close = b.close
            pos.days_held += 1
            if pos.days_held >= self.strategy.max_hold_days and ticker not in selling:
                p.orders.append(Order(ticker, "sell", pos.shares, "time", day))
        equity = p.cash + sum(pos.shares * pos.last_close for pos in p.positions.values())
        p.equity_history.append((day, equity))
        p.peak = max(p.peak, equity)
        if not p.halted and equity <= p.peak * (1 - self.risk.breaker_drawdown):
            p.halted = True
            fall = 100 * (1 - equity / p.peak)
            p.events.append(
                f"{day:%Y-%m-%d}: circuit breaker on, equity {equity:,.0f} is {fall:.1f}% "
                f"below peak {p.peak:,.0f}"
            )
        return equity

    def _buy(self, p: Portfolio, order: Order, day: pd.Timestamp, b: Bar) -> None:
        price = b.open * (1 + self.risk.slippage)
        affordable = math.floor((p.cash - self.risk.commission) / price)
        shares = min(order.shares, affordable)
        if shares <= 0:
            return
        p.cash -= shares * price + self.risk.commission
        p.positions[order.ticker] = Position(
            ticker=order.ticker,
            sector=order.sector,
            shares=shares,
            entry_day=day,
            entry_price=price,
            stop=b.open - self.strategy.stop_atr * order.atr,
            target=b.open + self.strategy.target_atr * order.atr,
            last_close=b.open,
        )

    def _exit(
        self, p: Portfolio, ticker: str, day: pd.Timestamp, price: float, reason: str
    ) -> None:
        pos = p.positions.pop(ticker)
        fill = price * (1 - self.risk.slippage)
        p.cash += pos.shares * fill - self.risk.commission
        p.trades.append(
            Trade(
                ticker=ticker,
                sector=pos.sector,
                entry_day=pos.entry_day,
                entry_price=pos.entry_price,
                exit_day=day,
                exit_price=fill,
                shares=pos.shares,
                exit_reason=reason,
                pnl=(fill - pos.entry_price) * pos.shares - 2 * self.risk.commission,
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_broker.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add trade simulator: fills, exits, delistings and circuit breaker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Backtest engine and metrics

**Files:**
- Create: `src/market_agent/backtest.py`
- Test: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `Panel` (Task 6), `Universe` (Task 5), `EarningsCalendar`, `screen` (Task 7), `plan_entries` (Task 8), `Simulator`, `Portfolio` (Tasks 8–9), `Settings`.
- Produces:
  - `BacktestResult(start, end, equity: pd.Series, trades: list[Trade], metrics: dict, benchmark: dict, notes: dict, open_positions: int)`
  - `run_backtest(panel, universe, earnings, sectors, benchmark_close: pd.Series, settings, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult`
  - `compute_metrics(equity: pd.Series, trades: list[Trade] | None, exposure: float | None = None) -> dict` with keys `total_return, cagr, max_drawdown` (fractions, rounded to 4 places) plus, when trades are given, `trades, win_rate, avg_win, avg_loss, exposure`
  - `notes` keys: `signals`, `signals_without_earnings_data`, `tickers_in_universe`, `tickers_without_data` (sorted list), `excluded_bad_days`, `circuit_breaker_events`

- [ ] **Step 1: Write the failing tests**

`tests/test_backtest.py`:
```python
import pandas as pd
import pytest

from helpers import make_bars
from market_agent.backtest import compute_metrics, run_backtest
from market_agent.data.sources import EarningsHistory
from market_agent.earnings import EarningsCalendar
from market_agent.panel import Panel
from market_agent.portfolio import Trade
from market_agent.settings import Settings
from market_agent.universe import Universe

S = Settings()


def scenario(earnings_covered=True, extra_members=()):
    """AAA rises steadily with a volume spike on day 250, then keeps rising to its target."""
    n = 320
    closes = [50 + 0.1 * i for i in range(250)] + [75 + 0.6 * i for i in range(n - 250)]
    volumes = [1e6] * 250 + [3e6] + [1e6] * (n - 251)
    aaa = make_bars(closes, volumes)
    spy = make_bars([400 + 0.2 * i for i in range(n)])
    days = spy.index
    panel = Panel({"AAA": aaa, "SPY": spy}, days, S.strategy, S.data.max_daily_jump)
    universe = Universe([(days[0], frozenset({"AAA", *extra_members}))])
    coverage = days[0] if earnings_covered else None
    earnings = EarningsCalendar({"AAA": EarningsHistory([], coverage)}, list(days))
    return panel, universe, earnings, spy["close"], days


def run(**kwargs):
    panel, universe, earnings, spy, days = scenario(**kwargs)
    return run_backtest(panel, universe, earnings, {}, spy, S, days[200], days[-1])


def test_a_breakout_trade_hits_its_target():
    result = run()
    [trade] = [t for t in result.trades if t.ticker == "AAA"]
    assert trade.exit_reason in ("target", "target (gap)")
    assert trade.pnl > 0
    assert result.equity.iloc[0] == S.risk.starting_cash
    assert result.metrics["trades"] >= 1
    assert result.benchmark["total_return"] > 0
    assert set(result.benchmark) == {"total_return", "cagr", "max_drawdown"}


def test_counts_signals_without_earnings_data():
    result = run(earnings_covered=False)
    assert result.notes["signals"] >= 1
    assert result.notes["signals_without_earnings_data"] == result.notes["signals"]


def test_reports_tickers_without_data():
    result = run(extra_members=("GONE",))
    assert result.notes["tickers_without_data"] == ["GONE"]
    assert result.notes["tickers_in_universe"] == 2


def test_no_days_in_period_raises():
    panel, universe, earnings, spy, days = scenario()
    with pytest.raises(ValueError, match="No trading days"):
        run_backtest(
            panel,
            universe,
            earnings,
            {},
            spy,
            S,
            pd.Timestamp("2030-01-01"),
            pd.Timestamp("2030-12-31"),
        )


def test_compute_metrics():
    days = pd.bdate_range("2024-01-01", periods=4)
    equity = pd.Series([100.0, 120.0, 90.0, 110.0], index=days)
    day = days[0]
    trades = [
        Trade("A", "X", day, 100.0, day, 110.0, 1, "target", 8.0),
        Trade("B", "X", day, 100.0, day, 95.0, 1, "stop", -7.0),
    ]
    m = compute_metrics(equity, trades, exposure=0.5)
    assert m["total_return"] == 0.1
    assert m["max_drawdown"] == -0.25
    assert m["trades"] == 2
    assert m["win_rate"] == 0.5
    assert m["avg_win"] == 0.1
    assert m["avg_loss"] == -0.05
    assert m["exposure"] == 0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_backtest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.backtest'`

- [ ] **Step 3: Implement the backtest**

`src/market_agent/backtest.py`:
```python
"""Replays history day by day through the same strategy, risk and simulator as paper trading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from statistics import mean
from typing import Any

import pandas as pd

from market_agent.broker import Simulator
from market_agent.earnings import EarningsCalendar
from market_agent.panel import Panel
from market_agent.portfolio import Portfolio, Trade
from market_agent.risk import plan_entries
from market_agent.settings import Settings
from market_agent.strategy import screen
from market_agent.universe import Universe


@dataclass
class BacktestResult:
    start: pd.Timestamp
    end: pd.Timestamp
    equity: pd.Series
    trades: list[Trade]
    metrics: dict[str, Any]
    benchmark: dict[str, Any]
    notes: dict[str, Any]
    open_positions: int


def compute_metrics(
    equity: pd.Series, trades: list[Trade] | None, exposure: float | None = None
) -> dict[str, Any]:
    total = equity.iloc[-1] / equity.iloc[0] - 1
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0
    drawdown = (equity / equity.cummax() - 1).min()
    metrics: dict[str, Any] = {
        "total_return": round(float(total), 4),
        "cagr": round(float(cagr), 4),
        "max_drawdown": round(float(drawdown), 4),
    }
    if trades is not None:
        changes = [t.exit_price / t.entry_price - 1 for t in trades]
        wins = [c for c, t in zip(changes, trades, strict=True) if t.pnl > 0]
        losses = [c for c, t in zip(changes, trades, strict=True) if t.pnl <= 0]
        metrics.update(
            trades=len(trades),
            win_rate=round(len(wins) / len(trades), 4) if trades else 0.0,
            avg_win=round(mean(wins), 4) if wins else 0.0,
            avg_loss=round(mean(losses), 4) if losses else 0.0,
            exposure=round(float(exposure or 0.0), 4),
        )
    return metrics


def run_backtest(
    panel: Panel,
    universe: Universe,
    earnings: EarningsCalendar,
    sectors: Mapping[str, str],
    benchmark_close: pd.Series,
    settings: Settings,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> BacktestResult:
    days = [day for day in panel.days if start <= day <= end]
    if not days:
        raise ValueError(f"No trading days between {start:%Y-%m-%d} and {end:%Y-%m-%d}")
    sim = Simulator(settings.risk, settings.strategy)
    portfolio = Portfolio("rules-only", settings.risk.starting_cash)
    signals = without_earnings = exposed_days = 0

    for day in days:
        sim.open(portfolio, day, panel.bar)
        sim.intraday(portfolio, day, panel.bar)
        equity = sim.close(portfolio, day, panel.bar, panel.last_day)
        if portfolio.positions:
            exposed_days += 1
        if portfolio.halted:
            continue
        candidates = screen(
            day, panel.snapshot(day), universe.members(day), earnings, settings.strategy
        )
        signals += len(candidates)
        without_earnings += sum(not c.earnings_known for c in candidates)
        plan_entries(portfolio, candidates, equity, sectors, settings.risk, settings.strategy, day)

    equity_series = pd.Series(dict(portfolio.equity_history), name="equity")
    bench = benchmark_close.loc[days[0] : days[-1]]
    bench_equity = bench / bench.iloc[0] * settings.risk.starting_cash
    members = universe.tickers_between(days[0], days[-1])
    return BacktestResult(
        start=days[0],
        end=days[-1],
        equity=equity_series,
        trades=list(portfolio.trades),
        metrics=compute_metrics(equity_series, portfolio.trades, exposed_days / len(days)),
        benchmark=compute_metrics(bench_equity, None),
        notes={
            "signals": signals,
            "signals_without_earnings_data": without_earnings,
            "tickers_in_universe": len(members),
            "tickers_without_data": sorted(t for t in members if t not in panel.tickers),
            "excluded_bad_days": sum(len(v) for v in panel.excluded.values()),
            "circuit_breaker_events": list(portfolio.events),
        },
        open_positions=len(portfolio.positions),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_backtest.py -v`
Expected: PASS (5 tests). If `test_a_breakout_trade_hits_its_target` finds no trade, print
`result.notes` and the AAA snapshot on day 250 and check the scenario still meets every filter
(price, traded value, trend, 20-day high, volume ratio) before changing any code.

- [ ] **Step 5: Lint and commit**

```bash
ruff check --fix . && ruff format .
git add src tests
git commit -m "Add backtest engine and performance metrics

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Test-period guard, command line and README

**Files:**
- Create: `src/market_agent/guard.py`, `src/market_agent/cli.py`, `README.md`
- Test: `tests/test_guard.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `GuardError(Exception)`; `check_test_period(store, fingerprint) -> list[str]` (warnings; raises when the same settings already ran the test period)
  - `PERIODS = ("tuning", "test", "full")`; `period_dates(period, settings, latest: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]`
  - `main(argv=None) -> int` with commands `fetch`, `build-sectors`, `backtest --period {tuning,test,full}`; options `--config PATH` (default `config.yaml`)
  - Module-level factories the tests replace: `price_source()`, `earnings_source()`, `sector_source()`

- [ ] **Step 1: Write the failing tests**

`tests/test_guard.py`:
```python
import pandas as pd
import pytest

from market_agent.guard import GuardError, check_test_period
from market_agent.store import Store


def save(store, fingerprint):
    day = pd.Timestamp("2022-01-03")
    store.save_backtest(
        "test",
        day,
        day,
        fingerprint,
        {},
        {"total_return": 0.1},
        {},
        {},
        [],
        pd.Series([1.0], index=[day]),
    )


def test_test_period_guard(tmp_path):
    store = Store(tmp_path / "m.db")
    assert check_test_period(store, "aaa") == []
    save(store, "aaa")
    with pytest.raises(GuardError, match="already run with these exact settings"):
        check_test_period(store, "aaa")
    [warning] = check_test_period(store, "bbb")
    assert "run 1 time before with other settings" in warning
    store.close()
```

`tests/test_cli.py`:
```python
import pandas as pd

from helpers import make_bars
from market_agent import cli
from market_agent.data.sources import EarningsHistory, NoData

N = 320


def aaa_bars():
    closes = [50 + 0.1 * i for i in range(250)] + [75 + 0.6 * i for i in range(N - 250)]
    volumes = [1e6] * 250 + [3e6] + [1e6] * (N - 251)
    return make_bars(closes, volumes, start="2020-06-01")


class Prices:
    frames = {
        "AAA": aaa_bars(),
        "SPY": make_bars([400 + 0.2 * i for i in range(N)], start="2020-06-01"),
    }

    def fetch(self, ticker, start, end):
        if ticker not in self.frames:
            raise NoData(ticker)
        return self.frames[ticker]


class Earnings:
    def fetch(self, ticker):
        return EarningsHistory([], pd.Timestamp("2020-06-01"))


class Sectors:
    def sector(self, ticker):
        return "Technology"


def setup(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sp500_membership.csv").write_text(
        'date,tickers\n2020-06-01,"AAA,GONE"\n', encoding="utf-8"
    )
    (tmp_path / "config.yaml").write_text(
        "backtest:\n  start: 2021-03-01\n  tuning_end: 2021-06-30\n  test_start: 2021-07-01\n"
        "data:\n  history_start: 2020-06-01\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "price_source", Prices)
    monkeypatch.setattr(cli, "earnings_source", Earnings)
    monkeypatch.setattr(cli, "sector_source", Sectors)


def test_fetch_build_sectors_and_backtest(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    assert cli.main(["fetch"]) == 0
    out = capsys.readouterr().out
    assert "Prices: 2 tickers with data, 1 without (GONE)" in out
    assert cli.main(["build-sectors"]) == 0
    assert "AAA,Technology" in (tmp_path / "data" / "sectors.csv").read_text("utf-8")
    assert cli.main(["backtest", "--period", "tuning"]) == 0
    out = capsys.readouterr().out
    assert "Backtest (tuning) 2021-03-01 to 2021-06-30" in out
    assert "Strategy" in out and "SPY" in out
    assert "Universe members without data: 1 of 2" in out


def test_test_period_runs_once_per_settings(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    cli.main(["fetch"])
    assert cli.main(["backtest", "--period", "test"]) == 0
    assert cli.main(["backtest", "--period", "test"]) == 1
    assert "already run with these exact settings" in capsys.readouterr().err


def test_backtest_without_data_explains(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch)
    assert cli.main(["backtest", "--period", "tuning"]) == 1
    assert "Run `agent fetch` first" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_guard.py tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.guard'`

- [ ] **Step 3: Implement the guard**

`src/market_agent/guard.py`:
```python
"""Protects the test period from being used for tuning (spec section 6)."""

from __future__ import annotations

from market_agent.store import Store


class GuardError(Exception):
    """The test period may not be run again with the same settings."""


def check_test_period(store: Store, fingerprint: str) -> list[str]:
    runs = store.backtest_runs("test")
    for run in runs:
        if run["fingerprint"] == fingerprint:
            raise GuardError(
                f"The test period was already run with these exact settings (run {run['id']} on "
                f"{run['created_at']}): total return {run['metrics'].get('total_return')}. "
                "Running it again would give the same answer."
            )
    if not runs:
        return []
    times = "time" if len(runs) == 1 else "times"
    return [
        f"The test period has been run {len(runs)} {times} before with other settings. "
        "Changing settings after seeing a test result is tuning on the test period, "
        "so treat this result with suspicion."
    ]
```

- [ ] **Step 4: Implement the command line**

`src/market_agent/cli.py`:
```python
"""agent fetch | build-sectors | backtest --period tuning|test|full"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from market_agent.backtest import BacktestResult, run_backtest
from market_agent.data.cache import EarningsCache, PriceCache
from market_agent.data.yahoo import YahooEarnings, YahooPrices, YahooSectors
from market_agent.earnings import EarningsCalendar
from market_agent.guard import GuardError, check_test_period
from market_agent.panel import Panel
from market_agent.settings import Settings, SettingsError, load_settings
from market_agent.store import Store
from market_agent.universe import Universe, load_sectors, write_sectors

PERIODS = ("tuning", "test", "full")


# Factories replaced in tests.
def price_source():
    return YahooPrices()


def earnings_source():
    return YahooEarnings()


def sector_source():
    return YahooSectors()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="Swing-trading analyst")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="download prices and earnings dates for the universe")
    commands.add_parser("build-sectors", help="write data/sectors.csv from Yahoo")
    backtest = commands.add_parser("backtest", help="run the backtest from cached data")
    backtest.add_argument("--period", choices=PERIODS, default="tuning")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    try:
        settings = load_settings(args.config)
    except SettingsError as exc:
        print(f"Settings error: {exc}", file=sys.stderr)
        return 1
    store = Store(settings.data.db_path)
    try:
        if args.command == "fetch":
            return fetch(settings, store)
        if args.command == "build-sectors":
            return build_sectors(settings, store)
        return backtest_command(settings, store, args.period)
    finally:
        store.close()


def universe_tickers(settings: Settings) -> list[str]:
    universe = Universe.from_csv(Path(settings.data.membership_csv))
    start = pd.Timestamp(settings.data.history_start)
    return sorted(universe.tickers_between(start, pd.Timestamp(date.today())))


def fetch(settings: Settings, store: Store) -> int:
    prices = PriceCache(store, price_source())
    earnings = EarningsCache(store, earnings_source())
    tickers = [*universe_tickers(settings), settings.data.benchmark]
    for index, ticker in enumerate(tickers, 1):
        if prices.update(ticker, settings.data.history_start) and ticker != settings.data.benchmark:
            earnings.update(ticker)
        if index % 50 == 0:
            print(f"  {index} of {len(tickers)} tickers")
    missing = store.missing_tickers()
    shown = ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")
    print(
        f"Prices: {len(store.tickers_with_prices())} tickers with data, "
        f"{len(missing)} without ({shown})"
    )
    return 0


def build_sectors(settings: Settings, store: Store) -> int:
    source = sector_source()
    sectors = {ticker: source.sector(ticker) for ticker in store.tickers_with_prices()}
    sectors.pop(settings.data.benchmark, None)
    write_sectors(Path(settings.data.sectors_csv), sectors)
    unknown = sum(1 for s in sectors.values() if s == "Unknown")
    print(f"Sectors written for {len(sectors)} tickers ({unknown} unknown)")
    return 0


def period_dates(
    period: str, settings: Settings, latest: pd.Timestamp
) -> tuple[pd.Timestamp, pd.Timestamp]:
    b = settings.backtest
    if period == "tuning":
        return pd.Timestamp(b.start), pd.Timestamp(b.tuning_end)
    if period == "test":
        return pd.Timestamp(b.test_start), latest
    return pd.Timestamp(b.start), latest


def backtest_command(settings: Settings, store: Store, period: str) -> int:
    benchmark = store.load_prices(settings.data.benchmark)
    if benchmark is None:
        print("No cached data. Run `agent fetch` first.", file=sys.stderr)
        return 1
    warnings: list[str] = []
    if period == "test":
        try:
            warnings = check_test_period(store, settings.fingerprint())
        except GuardError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    frames = {t: f for t in store.tickers_with_prices() if (f := store.load_prices(t)) is not None}
    days = benchmark.index
    panel = Panel(frames, days, settings.strategy, settings.data.max_daily_jump)
    universe = Universe.from_csv(Path(settings.data.membership_csv))
    earnings = EarningsCalendar(store.load_earnings(), list(days))
    sectors = load_sectors(Path(settings.data.sectors_csv))
    start, end = period_dates(period, settings, days[-1])
    result = run_backtest(
        panel, universe, earnings, sectors, benchmark["close"], settings, start, end
    )
    store.save_backtest(
        period,
        result.start,
        result.end,
        settings.fingerprint(),
        {"strategy": asdict(settings.strategy), "risk": asdict(settings.risk)},
        result.metrics,
        result.benchmark,
        result.notes,
        result.trades,
        result.equity,
    )
    print_result(period, result)
    for warning in warnings:
        print(f"Warning: {warning}")
    return 0


def _pct(value: float) -> str:
    return f"{100 * value:+.1f}%"


def print_result(period: str, r: BacktestResult) -> None:
    m, b, n = r.metrics, r.benchmark, r.notes
    print(f"Backtest ({period}) {r.start:%Y-%m-%d} to {r.end:%Y-%m-%d}")
    print(f"{'':16}{'Strategy':>12}{'SPY':>12}")
    for key, label in (
        ("total_return", "Total return"),
        ("cagr", "Per year"),
        ("max_drawdown", "Worst fall"),
    ):
        print(f"{label:16}{_pct(m[key]):>12}{_pct(b[key]):>12}")
    print(
        f"Trades {m['trades']}, win rate {100 * m['win_rate']:.0f}%, "
        f"average win {_pct(m['avg_win'])}, average loss {_pct(m['avg_loss'])}, "
        f"invested {100 * m['exposure']:.0f}% of days, {r.open_positions} still open"
    )
    print(
        f"Universe members without data: {len(n['tickers_without_data'])} of "
        f"{n['tickers_in_universe']} (results are biased upwards by roughly that share)"
    )
    print(
        f"Signals taken without earnings dates: {n['signals_without_earnings_data']} of "
        f"{n['signals']}; bad price days skipped: {n['excluded_bad_days']}"
    )
    for event in n["circuit_breaker_events"]:
        print(f"Circuit breaker: {event}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest -v`
Expected: PASS (all tests)

- [ ] **Step 6: Write the README**

`README.md`:
````markdown
# Market agent

A personal swing-trading analyst and **paper trader** (simulated money only). This first part
downloads free US market data and backtests the breakout strategy honestly:
tuning on 2015–2021, testing once on 2022 to today, always against SPY.
Design: `docs/superpowers/specs/2026-10-02-market-agent-design.md`.
Data sources and their limits: `docs/data-sources.md`.

Not investment advice. Free Yahoo data is for personal use only.

## Set up (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy config.example.yaml config.yaml   # optional: change settings here
```

## Use

```powershell
agent fetch                      # download ~10 years of prices and earnings dates (slow the first time)
agent build-sectors              # write data/sectors.csv
agent backtest --period tuning   # 2015–2021: change settings and re-run as often as you like
agent backtest --period test     # 2022–today: run once per settings version, never tune on it
```

The backtest prints how many universe members had no data (companies that left the index and
vanished from Yahoo). Those are mostly failures, so results are somewhat better than reality.

## Develop

`pytest` (no network), `pytest -m live` (reaches Yahoo), `ruff check --fix .`, `ruff format .`.
````

- [ ] **Step 7: Lint and commit**

```bash
ruff check --fix . && ruff format . && pytest
git add src tests README.md
git commit -m "Add backtest command line with test-period guard, and README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: First real backtest

Needs network access and about 30–60 minutes for the first download.

**Files:**
- Modify: `docs/data-sources.md` (append a "First backtest" section)

- [ ] **Step 1: Download the data**

Run: `agent fetch`, then `agent build-sectors`.
Expected: "Prices: about 700–900 tickers with data, N without (…)". Note N.

- [ ] **Step 2: Run the tuning backtest**

Run: `agent backtest --period tuning`
Expected: a table of strategy vs SPY for 2015-01-02 to 2021-12-31, at least 100 trades.
If there are fewer than 20 trades, check one known breakout by hand before changing settings:
pick a ticker and day from the `prices` table where the close was a 20-day high on 1.5× volume,
and run `panel.snapshot(day).loc[ticker]` in a Python shell to see which filter failed.

- [ ] **Step 3: Do not run the test period yet**

Record the tuning result in `docs/data-sources.md`:

```markdown
## First backtest (tuning period, settings <fingerprint>)

| | Strategy | SPY |
|---|---|---|
| Total return | … | … |
| Per year | … | … |
| Worst fall | … | … |

Trades …, win rate …, members without data … of …, signals without earnings dates … of ….
```

The test period is run only after the owner has reviewed the tuning result and agreed the settings.

- [ ] **Step 4: Commit**

```bash
git add docs/data-sources.md
git commit -m "Record the first tuning-period backtest

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
