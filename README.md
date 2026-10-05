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

## Paper trading

Both portfolios start with US$10,000 of simulated money on the first run. `rules-only` buys
every candidate the rules allow; `rules+ai` leaves out candidates Claude marks "skip".

1. Copy `.env.example` to `.env` and fill in the keys (Claude, Finnhub, Telegram). Without a
   Claude key candidates are "not reviewed"; without Finnhub the reviews see no news; without
   Telegram the report is only saved.
2. Run `agent build-sectors` once so reviews see company names.

```powershell
agent run-daily              # download, then trade every closed day not processed yet
agent catch-up               # the same, without waiting for late data
agent report                 # the latest report (--day YYYY-MM-DD for another day)
agent reset-breaker rules+ai # after a 15% fall the portfolio stops buying until reset
```

`run-daily` waits up to 2 hours for the latest day's data, then skips trading that day. Claude
reviews cost about US$4 a month and stop at the US$5 monthly cap (`ai.monthly_cap`).

To run it every trading day by itself (06:30 Tuesday–Saturday Malaysia time, or as soon as the
computer is back on), run once:
`powershell -ExecutionPolicy Bypass -File scripts\schedule-daily.ps1`. Output goes to
`data\daily.log`. Remove it with `Unregister-ScheduledTask "Market agent daily run"`.

## Dashboard

```powershell
agent dashboard        # opens http://localhost:8501; Ctrl+C to stop
```

Five pages, read-only: Overview (both portfolios against SPY), Today (any saved day's report,
candidates, Claude's verdicts with the news used, and the orders for the next open), Positions
and trades, AI review (every review and how the skipped candidates would have done) and
Backtest (every run against SPY with the settings it used). It only reads `data\market.db`, so
it can stay open while the 06:30 run works, and it listens on this computer only.

## Prices

Signals and simulated fills use prices adjusted for later splits and dividends, so returns are
continuous. The minimum-price and traded-value filters use the prices as they really traded that
day (NVDA closed at $20.13 on 2015-01-02, though it is under $1 adjusted), so stocks that later
split are not wrongly left out. A cache from before this change is downloaded again by the next
`agent fetch`; until then the backtest refuses to run.

## Develop

`pytest` (no network), `pytest -m live` (reaches Yahoo), `ruff check --fix .`, `ruff format .`.
