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

`--period full` also covers the test period, so it is guarded the same way: a second run with
the same settings is refused, and changing settings and re-running is flagged as tuning on the
test period.

`agent fetch` retries a ticker that fails (timeouts, rate limits) a few times, then moves on. At
the end it lists the tickers that still failed and exits with an error; run it again to retry
them. Cached prices are never replaced by an empty download. `--period test` and `--period full`
refuse to run until every universe member of the period has been tried at least once.

The backtest prints how many universe members had no data (companies that left the index and
vanished from Yahoo). They are a mix of failed and acquired companies, so the direction of the
bias is uncertain, though probably upward.

## Known limitations

- Prices are adjusted for later splits and dividends, and the minimum-price and traded-value
  filters run on those adjusted prices. A stock that later split looks cheaper in the past than
  it really traded (NVDA in 2015 is under $1 adjusted), so it is wrongly left out on days before
  the split. This works against later winners. Fixing it means fetching split history to
  un-adjust prices; decide whether to do that before the first test-period run.

## Develop

`pytest` (no network), `pytest -m live` (reaches Yahoo), `ruff check --fix .`, `ruff format .`.
