# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal swing-trading analyst and **paper trader** (simulated money only) for US shares.
Built so far: free Yahoo data download and caching, the breakout strategy, risk rules, the trade
simulator and an honest backtest against SPY. This is plan 1 of 3. Plan 2 adds daily paper
trading, a Claude review step ("rules + AI" portfolio) and Telegram. Plan 3 adds a Streamlit
dashboard, scheduling and Docker.

- Design spec (source of truth, cited by section in docstrings): `docs/superpowers/specs/2026-10-02-market-agent-design.md`
- Plan 1 with its global constraints: `docs/superpowers/plans/2026-10-02-data-and-backtest.md`
- Free data sources and their limits: `docs/data-sources.md`

## Commands (PowerShell, from the repo root)

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"                 # Python 3.12+
pytest                                  # no network; `live` tests are skipped by default
pytest tests/test_strategy.py::test_name   # a single test
pytest -m live                          # tests that reach Yahoo Finance
ruff check --fix .
ruff format .
```

CI (`.github/workflows/ci.yml`) runs `ruff check .`, `ruff format --check .` and `pytest`.

The CLI entry point is `agent` (`market_agent.cli:main`), with an optional `--config path`
(default `config.yaml`, which is gitignored; `config.example.yaml` is the template):

```powershell
agent fetch                      # ~10 years of prices + earnings dates into data/market.db
agent build-sectors              # writes data/sectors.csv
agent backtest --period tuning   # 2015–2021, re-run freely
agent backtest --period test     # 2022–today, guarded (see below)
agent run-daily                  # download, then paper-trade every closed day not processed yet
agent catch-up                   # the same, without waiting for late data
agent report [--day YYYY-MM-DD]  # show a saved daily report
agent reset-breaker rules+ai     # owner's reset after the circuit breaker halts a portfolio
agent dashboard [--port 8501]    # read-only Streamlit dashboard on localhost
```

## Architecture

Pipeline: `data` sources → SQLite `Store` cache → `Panel` → per day: `Simulator` (broker) →
`strategy.screen` → `risk.plan_entries`. `backtest.run_backtest` drives that day loop. The same
strategy, risk and broker code must also serve paper trading in plan 2, so keep it free of
backtest-only assumptions.

- **Settings** (`settings.py`): frozen dataclasses (`strategy`, `risk`, `data`, `backtest`) hold
  every tunable number with the spec's defaults. `config.yaml` overrides them, and unknown keys
  or wrong types raise `SettingsError`. `Settings.fingerprint()` hashes only strategy + risk, and
  values are coerced to their field type so `2` and `2.0` give the same fingerprint.
- **Data** (`data/`): `PriceSource` / `EarningsSource` are Protocols in `sources.py`; `yahoo.py`
  implements them. `cache.py` downloads each ticker at most once a day and **replaces the whole
  series** (adjusted prices change retroactively). An empty download never wipes cached prices,
  because Yahoo also returns nothing on errors and rate limits. `agent fetch` skips former index
  members that never had data until `data.missing_recheck_days` (30) have passed since their
  last try; current members are always downloaded.
- **Store** (`store.py`): one SQLite file. It holds prices, earnings, fetch metadata (which also
  records tickers that were tried and had no data) and every backtest run with its settings,
  fingerprint, metrics, trades and equity.
- **Universe** (`universe.py`): historical S&P 500 membership from `data/sp500_membership.csv`,
  looked up by date to avoid survivorship bias. Never use today's members for past days.
- **Panel** (`panel.py`): turns per-ticker frames into day × ticker wide tables of bars plus
  indicators (`indicators.py`), so one day's screen is a single row lookup. Suspicious price
  jumps (`max_daily_jump`) are dropped and recorded in `panel.excluded`.
- **Simulator** (`broker.py`): the order of each day matters. `open()` fills orders placed the
  day before, at the open. `intraday()` checks stops and targets: a gap past either fills at the
  open, and if both are inside the day's range the stop wins. `close()` marks to market, exits
  delisted positions at the last close, queues time exits and trips the circuit breaker. Every
  fill pays slippage and commission. The breaker halts new entries until the owner resets it,
  except in the backtest (`halt_on_breaker=False`), which records each trip and keeps trading.
- **Paper trading** (`daily.py`): `run_days` processes each closed NYSE session not yet in
  `paper_states`, in order (catch-up), and only the latest day may wait for late data.
  `DailyRun.process` checks the data (`checks.py`), screens, has Claude review the top
  candidates (`reviewer.py`, cost-capped), then per portfolio runs `paper.restate` (Yahoo
  re-adjusts history after splits/dividends) and `trading.trade_day`, the same step the backtest
  uses. Each day saves a JSON snapshot per portfolio plus the report in one transaction. Paper
  portfolios halt on the circuit breaker until `agent reset-breaker`. News for day D is cut off
  at 18:00 New York time so catch-up runs see what an on-time run would have.
- **Dashboard** (`dashboard/`): `reviews.py`, `paper.py` and `backtests.py` turn the Store into
  DataFrames and are tested without Streamlit; `explain.py` turns them into plain-English
  sentences (the portfolios against SPY, and why each share was bought or sold, rebuilt from the
  signal day's cached prices plus Claude's review); `display.py` formats them (percentages, plain
  dates, value charts whose y axis doesn't start at zero); `app.py` only renders. It opens the
  database with `Store(path, read_only=True)` (SQLite `mode=ro`: no schema, no migrations), and
  `agent dashboard` starts Streamlit on localhost with `MARKET_AGENT_DB` / `MARKET_AGENT_CONFIG`
  set. App tests use `streamlit.testing.v1.AppTest` on `helpers.dashboard_db`.
- **Test-period guard** (`guard.py` + `cli.backtest_command`): `test` and `full` both cover the
  test period. Re-running them with an already-used fingerprint is refused. Running them with new
  settings after an earlier run is allowed but warns that this is tuning on the test period.
  They also refuse to run until `agent fetch` has tried every universe member of the period.
  Don't weaken these checks; they are the point of the project.

## Rules that must hold

- No look-ahead: decisions on day D use data up to and including D only. Orders are placed after
  D's close and filled at D+1's open.
- `backtest.tuning_end` must be before `test_start`; tuning must never see the test period.
- Normal tests use no network. Network sources are injected through the factory functions in
  `cli.py` (`price_source()` etc., plus `pause`), which tests replace with fakes. `tests/helpers.py`
  has `make_bars()` for synthetic bars and `settings_with()` for changed settings.
- Ruff: line length 100, rules `E,F,I,UP,B`. `helpers` counts as first-party for isort.
- Two kinds of price per day (`data/sources.py`): `open`..`volume` are adjusted for later splits
  and dividends and drive signals, stops and fills; `raw_close` / `raw_volume` are as traded and
  drive only the minimum-price and traded-value filters. Yahoo's `Close` is already
  split-adjusted, so `yahoo.py` undoes splits from `Ticker.splits` (the whole history, not just
  the requested days). The backtest refuses to run while any cached ticker lacks as-traded prices.
- Free Yahoo data is for personal use only; this is not investment advice.
