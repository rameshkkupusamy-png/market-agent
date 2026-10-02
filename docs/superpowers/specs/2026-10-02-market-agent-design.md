# Market agent: design

Date: 2026-10-02
Status: draft for review

## 1. Purpose

A personal agent that analyses US shares for swing trades and **paper trades** them (simulated
money only), so the owner can find out, honestly and cheaply, whether the strategy makes money
before risking real money.

- **First user:** the owner only. No sign-in, no subscriptions.
- **Later, only if the results justify it:** invest real money (a separate live-trading project,
  e.g. through Alpaca), and possibly offer the tool to others. Selling signals or advice is
  regulated (in Malaysia usually a Securities Commission licence); that needs legal advice first and
  is out of scope here.
- **Success:** after 3–6 months of paper trading, a clear record of how **rules only** and
  **rules + AI** performed after costs, against simply holding the S&P 500 (SPY), plus a backtest
  that was not tuned on its own test period.

### In the first version

- US market (S&P 500 and Nasdaq-100 members, by historical membership). Bursa Malaysia later, as
  another market plug-in.
- Swing trading on end-of-day data: one run per US trading day, positions held days to weeks.
- One strategy (trend breakout on volume), rules find candidates, Claude reviews them.
- Two simulated portfolios side by side, a backtest, a daily Telegram report, a local dashboard.
- Running cost about US$0–5 a month (free data; Claude capped).

### Not in the first version

Real-money trading or any broker connection, short selling, options, margin, intraday data,
multiple users, a hosted product, more than one strategy, Bursa Malaysia data.

## 2. Architecture

```
            ┌──────────────────────────── Engine (Python package) ─────────────────────────┐
 Market     │ 1 Update data ─► 2 Screen (rules) ─► 3 AI review ─► 4 Paper trade ─► 5 Report  │
 plug-ins   │  prices, earnings  candidates +       approve/skip/    rules-only and   Telegram│
 (US first) │  dates, news       entry/stop/size    flag + reasons   rules+ai                 │
            └──────────────────────────────────┬────────────────────────────────────────────┘
                                               │  SQLite (one file)
                                     Dashboard (Streamlit, read-only)
```

Units (one job each, plain interfaces so tests can use fakes):

| Unit | Job |
|---|---|
| `data` | `PriceSource`, `EarningsSource`, `NewsSource` interfaces; US implementations; caching in SQLite; daily data checks |
| `universe` | which tickers are eligible on a given date (historical index membership) |
| `strategy` | filters, entry signal, ranking, exits; returns candidates with entry, stop, target, reason |
| `risk` | position size, position/sector/count limits, circuit breaker |
| `reviewer` | builds the prompt, calls Claude, validates the JSON answer, enforces the spending cap |
| `broker` | simulator: orders, fills, stops/targets/time exits, splits, delistings, costs |
| `portfolio` | holdings, cash, equity history per portfolio |
| `backtest` | replays history day by day through strategy, risk and broker (no AI) |
| `daily` | the daily run and catch-up |
| `report` | Telegram message and alerts |
| `dashboard` | Streamlit pages, read-only |
| `store` | SQLite schema and access |

The **same** strategy, risk and broker code serves the backtest and paper trading, so they cannot
disagree. Settings live in `config.yaml`; secrets (Claude key, Telegram token, news API key) in
environment variables or a `.env` file that is never committed.

## 3. Data (start free)

| Need | Source (first version) | Notes |
|---|---|---|
| Daily prices (OHLCV, split/dividend adjusted) | Yahoo Finance via `yfinance` | free, unofficial, personal use only; may break for a few days when Yahoo changes |
| Earnings dates (past and upcoming) | Yahoo Finance via `yfinance` | confirm coverage of past dates in the first plan task |
| News headlines and summaries | a free tier (Finnhub, Alpaca or Tiingo) | the first plan task confirms current limits and picks one |
| Historical S&P 500 / Nasdaq-100 membership | a public dataset of index changes | the first plan task names the dataset; avoids survivorship bias |
| Benchmark | SPY prices from the same price source | |

- Every source sits behind its interface, so a paid source (Tiingo, FMP) is a configuration change.
- Downloaded data is cached; backtests and reruns never download the same day twice.
- **Daily data checks** before any trading: every eligible ticker has a bar for the day; no
  unexplained jump over 40% day-on-day without a split or dividend record; SPY present. Failing
  tickers are excluded for the day and listed in the report; if SPY or more than 5% of tickers fail,
  nothing trades that day.
- Free data is for personal use. A product for others needs licensed data.

## 4. Strategy (all numbers are settings in `config.yaml`)

**Eligible shares on day D** (using data up to and including D only):
- member of the universe on D; close > US$10; 20-day average traded value > US$20 million
- close > 200-day simple moving average, and 50-day SMA > 200-day SMA
- no earnings report in the next 5 trading days

**Entry signal:** close on D is the highest close of the last 20 trading days **and** volume on D is
at least 1.5 × its 20-day average.

**Ranking:** by 63-day (3-month) return, highest first. At most 3 new positions per day per portfolio.

**Orders:** placed after the close of D, filled at the **open of D+1**.

**Exits** (first one hit):
- stop-loss = entry − 2 × ATR(14) at entry
- target = entry + 4 × ATR(14) at entry
- time exit: sell at the open after 20 trading days held
- if the open gaps past the stop or target, fill at the open price
- if both stop and target fall within the same day's range, assume the stop (the cautious choice)

## 5. Risk limits (same for both portfolios)

- Starting balance US$10,000 per portfolio, cash only (no margin, no shorts).
- Size so that a stop-out loses 1% of current equity: `shares = floor(0.01 × equity / (entry − stop))`,
  capped so the position is at most 10% of equity and never more than available cash.
- At most 10 open positions; at most 3 in the same GICS sector.
- **Circuit breaker:** if equity falls 15% below its highest value, the portfolio opens no new
  positions (exits still run) and an alert is sent, until the owner resets it with a command.
- Costs on every fill: US$1 commission per order plus 0.1% slippage against the trader.

## 6. Backtest

- Period: 2015-01-01 to the latest close. **Tuning period** 2015–2021; **test period** 2022 to now,
  run once per settings version and never used for tuning. Both reported against SPY.
- Rules only (the AI cannot be backtested honestly: the model already knows how those years ended).
- Day-by-day replay with the same strategy, risk and broker code as paper trading.
- Output stored in SQLite with the settings used: equity curve, trades, total return, CAGR,
  maximum drawdown, win rate, average win/loss, number of trades, exposure.

## 7. AI review (rules + AI portfolio only)

- Runs forward only, in paper trading, after screening. Both portfolios receive the same candidate
  list; `rules-only` ignores the review, `rules+ai` drops candidates marked **skip**.
- **Input per candidate:** ticker, company, sector; price summary (trend, breakout, volume ratio,
  ATR, 63-day return); next earnings date; up to 15 headlines with summaries from the last 7 days.
- **Output (JSON, validated):** `verdict` (approve | skip | flag), `confidence` (low | medium | high),
  `reasons` (≤ 3 short strings), `risks` (≤ 3), `news_used` (indexes of headlines relied on).
- **Instructions:** judge only from the given information, never invent facts, say "no relevant
  news" when there is none. The AI cannot change size, stop or target.
- Invalid answer: retried once, then `flag` with "review failed". API unavailable or monthly cap
  (default US$5) reached: `not reviewed`, treated as approve, counted separately.
- Model: chosen in the plan from current Claude models and prices; every prompt, answer, model id
  and cost is stored.

## 8. Daily run and catch-up

Scheduled at 06:30 Malaysia time, Tuesday–Saturday: after each US trading day, and late enough for
both US summer time (close 04:00 MYT) and winter time (close 05:00 MYT) with time for the free data
to settle. US holidays from the exchange calendar are skipped.

1. Download and check data for day D (retry for up to 2 hours if incomplete).
2. Fill yesterday's orders at D's open; apply stops, targets, time exits, splits, delistings for D.
3. Screen for candidates on D; review with the AI.
4. Size and place orders for D+1 in both portfolios.
5. Save everything; send the Telegram report.

**Catch-up:** if runs were missed, each missing trading day is processed in order with the same
steps. Fills always use the real prices of the day, so results equal an on-time run; AI reviews done
late are marked `late`.

**Commands:** `agent backtest`, `agent run-daily`, `agent catch-up`, `agent reset-breaker <portfolio>`,
`agent dashboard`.

## 9. Report and dashboard

**Telegram (after each run):** both portfolios' value and return, SPY return, new candidates with
AI verdict and one-line reason, fills, open position counts, warnings. Errors (no data, circuit
breaker, AI cap) are sent as separate alerts. If Telegram fails, the report is still saved and shown
on the dashboard.

**Dashboard (Streamlit, localhost, read-only):**
1. Overview: equity curves of both portfolios vs SPY; return, max drawdown, win rate, average
   win/loss, trades.
2. Today: candidates, AI verdicts with reasons and news used, orders for tomorrow.
3. Positions and trades: open (entry, stop, target, days held) and closed (result, exit reason).
4. AI review: every review, and how the skipped candidates would have done ("what if").
5. Backtest: tuning and test periods vs SPY, with the settings used.

## 10. Failure handling

| Situation | Result |
|---|---|
| Data incomplete after retries | no trading for that day; alert says why |
| Ticker fails the data check | excluded for the day; listed in the report |
| Split or dividend | prices adjusted; open positions adjusted |
| Delisted or acquired while held | closed at the last available price; noted |
| Computer off | catch-up on next start, same results |
| Claude unavailable or cap reached | `not reviewed`, treated as approve, counted separately |
| Telegram fails | report saved, visible on dashboard |
| Circuit breaker | no new positions until reset; alert |

## 11. Testing

- Strategy and risk with small hand-made price series: breakout signal; no signal with earnings
  within 5 days; ranking; size for 1% risk; 10% cap; cash limit; 11th position and 4th per sector
  refused; circuit breaker at −15%.
- Broker: next-open fills with costs; stop and target within the day; gap past the stop; stop and
  target on the same day; time exit; split; delisting.
- No look-ahead: the strategy for day D is given data only up to D (enforced by the data access
  layer and tested).
- Catch-up gives the same portfolios as on-time runs.
- Data checks: missing day, bad jump, holiday.
- Reviewer with a fake model: valid answer, invalid then retried, unavailable, cap reached; the
  prompt contains only the given data.
- Normal tests use no network. `pytest -m live` checks yfinance, the news source, Claude and Telegram.
- ruff and pytest in CI (GitHub Actions).

## 12. Running and deployment

- Python 3.12, one repository (`market-agent`), `pyproject.toml`, SQLite file in `data/`.
- First on the owner's Windows computer (Task Scheduler for the daily run; dashboard started on
  demand), packaged with Docker so it can move to a small Linux cloud server later.
- Running cost: about US$0–5 a month (Claude only). Paid data is considered after 2–3 months of
  paper results, or if free data proves unreliable.

## 13. Later

Paid licensed data; Bursa Malaysia market plug-in; more strategies; live trading through a broker
API with its own risk review; a hosted multi-user product (needs licensed data and legal advice).
