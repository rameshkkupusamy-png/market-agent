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

The backtest prints how many universe members had no data (companies that left the index and
vanished from Yahoo). Those are mostly failures, so results are somewhat better than reality.

## Develop

`pytest` (no network), `pytest -m live` (reaches Yahoo), `ruff check --fix .`, `ruff format .`.
