# Market agent: daily paper trading implementation plan (plan 2 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run both paper portfolios (`rules-only` and `rules+ai`) once per US trading day on fresh
data, with Claude reviewing candidates for `rules+ai`, catch-up for missed days, a Telegram report,
and a Windows scheduled task, so the owner gets a daily paper-trading record.

**Architecture:** A `daily` module processes one trading day at a time: check the day's data,
screen, review with Claude, then for each portfolio rescale positions if Yahoo restated prices
and run the same `trade_day` step the backtest uses (shared code, so they cannot disagree).
Each processed day saves a JSON snapshot of both portfolios plus the report in SQLite, so
catch-up is "process every unprocessed session in order". Sources (news, Claude, Telegram,
calendar, clock) sit behind small interfaces with fakes in tests. Plan 3 adds the Streamlit
dashboard and Docker. (Plan 1 put scheduling in plan 3; it moves here, because the daily run is
only useful once it runs by itself.)

**Tech Stack:** Python 3.12, pandas, yfinance, SQLite, `anthropic` (Claude API, structured JSON
output), `exchange_calendars` (NYSE sessions and holidays), `python-dotenv` (secrets in `.env`),
Finnhub free company news (plain HTTPS), Telegram Bot API (plain HTTPS), Windows Task Scheduler,
pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-10-02-market-agent-design.md` (sections 2, 3, 5, 7–11).
Plan 1: `docs/superpowers/plans/2026-10-02-data-and-backtest.md`.

## Global Constraints

- Python `>=3.12`; ruff line length 100, rules `E,F,I,UP,B`; run `ruff check --fix .` and `ruff format .` before every commit.
- Normal tests use no network; tests that reach Finnhub, Claude, Telegram or Yahoo are marked `live` and skipped by default.
- The same strategy, risk and broker code serves the backtest and paper trading. Paper trading halts on the circuit breaker (`Simulator(..., halt_on_breaker=True)`, the default); the backtest does not.
- Orders are placed after the close of day D and filled at the open of D+1. No look-ahead: anything decided for day D uses prices up to D and news published up to 18:00 New York time on D.
- Daily run: 06:30 Malaysia time, Tuesday–Saturday; US holidays from the NYSE calendar are skipped.
- Data check: if SPY has no price for D, or more than 5% of eligible tickers fail, nothing trades that day; failing tickers are otherwise excluded for the day and listed in the report. The latest day is retried for up to 2 hours.
- AI review (rules+ai only): input is ticker, company, sector, price summary, next earnings date and up to 15 headlines with summaries from the last 7 days. Output JSON: `verdict` (approve | skip | flag), `confidence` (low | medium | high), `reasons` (≤ 3), `risks` (≤ 3), `news_used` (indexes). `rules+ai` drops only `skip`. Invalid answer: retried once, then `flag` with "review failed". API unavailable or monthly cap (default US$5) reached: `not reviewed`, treated as approve, counted separately. Every prompt, answer, model id and cost is stored.
- Secrets (`ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) live in environment variables or `.env`, which is never committed.
- If Telegram fails, the report is still saved and the run still succeeds.
- Free Yahoo and Finnhub data are for personal use only.
- Commit messages end with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Yahoo re-adjusts the whole price history after a split or dividend while a share is held.** Expected: the stored entry, stop, target and shares are rescaled, so a 4:1 split does not trigger a false stop and the position's value is unchanged. Pinned by: Task 6 `test_split_while_held_rescales_the_position`.
2. **Yahoo's data for the latest day is incomplete at 06:30.** Expected: the run re-downloads the missing tickers every 15 minutes for up to 2 hours; if still incomplete, nothing trades that day, an alert says why, and pending orders wait for the next day. Pinned by: Task 11 `test_incomplete_latest_day_waits_then_does_not_trade`, `test_late_data_is_used_when_it_arrives`.
3. **The computer was off for several days.** Expected: the next run processes every missed session in order (holidays skipped) and ends with exactly the portfolios on-time runs would have produced; reviews done late are marked `late`. Pinned by: Task 11 `test_catch_up_matches_on_time_runs`, `test_holidays_are_not_processed`.
4. **Claude is down, over the monthly cap, or answers with malformed JSON.** Expected: the candidate is `not reviewed` (treated as approve) or `flag`ged after one retry; `rules+ai` still trades and the report counts it. Pinned by: Task 9 `test_unavailable_model_is_not_reviewed`, `test_cap_reached_skips_the_call`, `test_invalid_twice_is_flagged`.
5. **The owner runs `agent catch-up` just after the US close, before Yahoo's data is final.** Expected: the incomplete latest day is left for the next run, not recorded as a no-trading day. Pinned by: Task 11 `test_catch_up_leaves_an_incomplete_latest_day_for_later`.

---

## File structure

```
src/market_agent/
  sessions.py        NEW  TradingCalendar: NYSE sessions, latest closed session
  trading.py         NEW  trade_day(): one day for one portfolio (backtest and paper)
  paper.py           NEW  PORTFOLIOS, restate(), gone_lookup(), reset_breaker()
  checks.py          NEW  check_day(): the daily data check
  reviewer.py        NEW  prompt, Claude call, answer validation, cap, Review records
  report.py          NEW  build_report(), fills_on()
  notify.py          NEW  Telegram sender, message splitting
  daily.py           NEW  DailyRun.process(), run_days() (catch-up + waiting), send_results()
  data/finnhub.py    NEW  FinnhubNews, NoNews
  data/sources.py    MOD  Headline, NewsSource, Profile
  data/yahoo.py      MOD  YahooSectors.profile() (sector + company name)
  data/cache.py      MOD  PriceCache.update(force=...)
  portfolio.py       MOD  JSON round trip; Position.marked_on; Order.ref_close; float shares
  broker.py          MOD  records marked_on
  risk.py            MOD  orders remember the close they were sized on
  backtest.py        MOD  uses trade_day()
  panel.py           MOD  last_bar_until()
  earnings.py        MOD  next_report()
  universe.py        MOD  load_profiles(), write_profiles() (sectors.csv gains a name column)
  settings.py        MOD  PaperSettings, AiSettings
  store.py           MOD  paper_states, daily_runs, reviews tables
  cli.py             MOD  run-daily, catch-up, reset-breaker, report; loads .env
scripts/schedule-daily.ps1  NEW  registers the Windows scheduled task
.env.example         NEW  names of the secrets
```

---

### Task 1: Confirm news, calendar, Telegram and Claude (investigation, no product code)

The owner creates three accounts; a throwaway script confirms each service works as the later
tasks assume, and the findings go into `docs/data-sources.md`. The "If not" lines say what
changes if an answer differs.

**Files:**
- Create: `.env.example`
- Modify: `docs/data-sources.md` (new section)
- Owner only, never committed: `.env`
- Scratch only (not committed): `check_plan2.py` in the session scratch folder

**Interfaces:**
- Produces: the four environment variable names used by Task 12:
  `ANTHROPIC_API_KEY`, `FINNHUB_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

- [ ] **Step 1: The owner creates the keys**

Ask the owner to do these (they need their own logins) and paste the values into `.env` in the
repo root (already in `.gitignore`):

1. Claude: https://console.anthropic.com → API keys → Create key. Also set a monthly spend
   limit there (e.g. US$10) as a second guard behind the agent's own US$5 cap.
2. Finnhub: https://finnhub.io/register → the dashboard shows a free API key.
3. Telegram: in Telegram, message `@BotFather`, send `/newbot`, follow the prompts, copy the
   token. Send any message to the new bot, then open
   `https://api.telegram.org/bot<token>/getUpdates` in a browser and copy `"chat":{"id":…}`.

Create `.env.example` (committed) with the names only:

```
# Copy to .env and fill in. Never commit .env.
ANTHROPIC_API_KEY=
FINNHUB_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

- [ ] **Step 2: Install the new packages into the venv**

Run: `.venv\Scripts\python.exe -m pip install "anthropic>=1.11" "exchange_calendars>=4.13" "python-dotenv>=1.0"`
Expected: installs without errors.

- [ ] **Step 3: Write and run the check script**

```python
# check_plan2.py: throwaway. Run from the repo root: .venv\Scripts\python.exe <path>\check_plan2.py
import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta

import anthropic
import exchange_calendars as xcals
import pandas as pd
from dotenv import load_dotenv

load_dotenv(".env")

# 1. Finnhub company news, last 7 days
key = os.environ["FINNHUB_API_KEY"]
end = date.today()
start = end - timedelta(days=7)
for ticker in ["AAPL", "NVDA", "BRK.B"]:
    query = urllib.parse.urlencode({"symbol": ticker, "from": start, "to": end, "token": key})
    with urllib.request.urlopen(f"https://finnhub.io/api/v1/company-news?{query}", timeout=30) as r:
        items = json.load(r)
    print("news", ticker, len(items), sorted(items[0]) if items else None)
    if items:
        newest = items[0]
        when = pd.Timestamp(newest["datetime"], unit="s", tz="UTC")
        print("  newest:", when, newest["source"], newest["headline"][:80])

# 2. NYSE calendar (offline)
cal = xcals.get_calendar("XNYS", start="2014-01-01", end="2027-12-31")
sessions = cal.sessions_in_range("2026-11-20", "2026-12-01")
print("sessions", [f"{d:%m-%d}" for d in sessions], "tz:", sessions.tz)
print("close 2026-11-27:", cal.session_close(pd.Timestamp("2026-11-27")))

# 3. Telegram
token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
data = urllib.parse.urlencode({"chat_id": chat, "text": "Market agent: test message"}).encode()
with urllib.request.urlopen(
    f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30
) as r:
    print("telegram ok:", json.load(r)["ok"])

# 4. Claude: one review-sized structured request
schema = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["approve", "skip", "flag"]}},
    "required": ["verdict"],
    "additionalProperties": False,
}
response = anthropic.Anthropic().beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=4000,
    betas=["server-side-fallback-2026-07-01"],
    fallbacks="default",
    output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
    system="Answer in JSON.",
    messages=[{"role": "user", "content": "A stock broke out on 1.8x volume, no news. Verdict?"}],
)
print(
    "claude",
    response.model,
    response.stop_reason,
    response.usage.input_tokens,
    response.usage.output_tokens,
    [(b.type, getattr(b, "text", "")[:60]) for b in response.content],
)
```

Expected (later tasks assume these):
- Finnhub returns a list of objects with `datetime` (Unix seconds), `source`, `headline`,
  `summary`; AAPL and NVDA have several items; `BRK.B` (dot) works or returns `[]`.
- Sessions skip `11-26` (Thanksgiving), `sessions.tz` is `None` (tz-naive), and the
  2026-11-27 close is `2026-11-27 18:00:00+00:00` (early close, UTC).
- Telegram prints `telegram ok: True` and the message arrives on the owner's phone.
- Claude prints `claude-opus-5-5 end_turn`, a few hundred tokens each way, and a `text` block
  holding `{"verdict": …}` (thinking blocks may come first).

If not:
- Finnhub fails or has no free company news any more: Task 8 still builds `FinnhubNews`, but
  Task 12's `news_source()` returns `NoNews()` and the report notes "news off"; record it.
- `sessions.tz` is not `None`: in Task 3's `TradingCalendar.nyse`, add `.tz_localize(None)` to
  the sessions.
- Claude rejects `fallbacks` or `betas`: in Task 9 remove the model from `FALLBACK_MODELS`
  (the plain `messages.create` path is then used). Rejects `effort`: set `ai.effort: ""`.
- Telegram fails: re-check the chat id (it can be negative for groups).

- [ ] **Step 4: Record the findings**

Append to `docs/data-sources.md`, with the actual values printed in Step 3:

```markdown
## Plan 2 sources (checked <date>)

| Need | Source | Finding |
|---|---|---|
| Company news | Finnhub `company-news` (free key) | AAPL <n> / NVDA <n> / BRK.B <n> items in 7 days; fields <list> |
| Trading days | `exchange_calendars` XNYS (offline) | 2026-11-26 closed; 2026-11-27 closes 18:00 UTC; sessions tz-naive |
| Messages | Telegram Bot API `sendMessage` | test message delivered |
| AI review | Claude `claude-opus-5-5`, effort low, JSON schema output | <in>/<out> tokens for a short request |

Consequences: <one line per "If not" case that applied, or "none">.
```

- [ ] **Step 5: Commit**

```bash
git add .env.example docs/data-sources.md
git commit -m "Record plan 2 source checks and list the secrets in .env.example

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Dependencies, settings and `.env` loading

**Files:**
- Modify: `pyproject.toml`, `src/market_agent/settings.py`, `src/market_agent/cli.py:56-62`,
  `config.example.yaml`
- Test: `tests/test_settings.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `PaperSettings(retry_hours=2.0, retry_every_minutes=15.0, max_missing_share=0.05,
  delisted_after_days=5)`; `AiSettings(model="claude-opus-5-5", effort="low", max_tokens=4000,
  monthly_cap=5.0, input_price=4.0, output_price=20.0, max_headlines=15, news_days=7,
  max_reviews_per_day=6)`; `Settings.paper`, `Settings.ai`. Prices are US$ per million tokens.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_settings.py` (it already imports `Settings`, `load_settings`; add
`from helpers import settings_with` if missing):

```python
def test_paper_and_ai_defaults_and_overrides(tmp_path):
    s = Settings()
    assert (s.paper.retry_hours, s.paper.max_missing_share, s.paper.delisted_after_days) == (
        2.0,
        0.05,
        5,
    )
    assert (s.ai.model, s.ai.effort, s.ai.monthly_cap) == ("claude-opus-5-5", "low", 5.0)
    path = tmp_path / "config.yaml"
    path.write_text("ai:\n  model: claude-sonnet-5-5\n  input_price: 2\n", encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.ai.model == "claude-sonnet-5-5"
    assert loaded.ai.input_price == 2.0


def test_ai_and_paper_settings_do_not_change_the_fingerprint():
    changed = settings_with(ai={"model": "claude-haiku-4-5"}, paper={"retry_hours": 1.0})
    assert changed.fingerprint() == Settings().fingerprint()
```

Add to `tests/test_cli.py` (add `import os` at the top):

```python
def test_env_file_in_the_working_folder_is_loaded(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(os, "environ", dict(os.environ))  # restored after the test
    (tmp_path / ".env").write_text("MARKET_AGENT_TEST=yes\n", encoding="utf-8")
    cli.main(["backtest"])  # fails (no data), but loads .env first
    assert os.environ["MARKET_AGENT_TEST"] == "yes"
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_settings.py tests/test_cli.py::test_env_file_in_the_working_folder_is_loaded -v`
Expected: FAIL (`Settings` has no attribute `paper`; `KeyError: 'MARKET_AGENT_TEST'`).

- [ ] **Step 3: Implement**

`pyproject.toml` dependencies:

```toml
dependencies = [
    "pandas>=2.2",
    "numpy>=1.26",
    "yfinance>=0.2.40",
    "PyYAML>=6.0",
    "anthropic>=1.11",
    "exchange_calendars>=4.13",
    "python-dotenv>=1.0",
]
```

`settings.py`, after `BacktestSettings`:

```python
@dataclass(frozen=True)
class PaperSettings:
    retry_hours: float = 2.0  # how long run-daily waits for the latest day's data
    retry_every_minutes: float = 15.0
    max_missing_share: float = 0.05  # more eligible tickers failing than this: no trading
    delisted_after_days: int = 5  # sessions without a price before a held share is closed


@dataclass(frozen=True)
class AiSettings:
    model: str = "claude-opus-5-5"
    effort: str = "low"  # "" leaves it out (Claude Haiku 4.5 rejects it)
    max_tokens: int = 4000
    monthly_cap: float = 5.0  # US$
    input_price: float = 4.0  # US$ per million input tokens (claude-opus-5-5)
    output_price: float = 20.0  # US$ per million output tokens
    max_headlines: int = 15
    news_days: int = 7
    max_reviews_per_day: int = 6  # top-ranked candidates reviewed; the rest count as approved
```

Add the fields to `Settings` and the sections to `SECTIONS`:

```python
    paper: PaperSettings = field(default_factory=PaperSettings)
    ai: AiSettings = field(default_factory=AiSettings)
```

```python
SECTIONS = {
    "strategy": StrategySettings,
    "risk": RiskSettings,
    "data": DataSettings,
    "backtest": BacktestSettings,
    "paper": PaperSettings,
    "ai": AiSettings,
}
```

`cli.py`: add `from dotenv import load_dotenv` and, as the first line of `main` after the
arguments are parsed and logging is set up:

```python
    load_dotenv(Path(".env"))  # the working folder only, so tests never pick up real keys
```

`config.example.yaml`, append:

```yaml
ai:
  model: claude-opus-5-5   # claude-sonnet-5-5 costs half: also set input_price 2, output_price 10
  monthly_cap: 5
```

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml config.example.yaml src/market_agent/settings.py src/market_agent/cli.py tests/test_settings.py tests/test_cli.py
git commit -m "Add paper-trading and AI settings, new dependencies and .env loading

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Trading calendar

**Files:**
- Create: `src/market_agent/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces: `TradingCalendar(sessions: Sequence[pd.Timestamp], closes: Sequence[pd.Timestamp])`
  (sessions tz-naive, closes tz-aware UTC); `.sessions: list[pd.Timestamp]`;
  `TradingCalendar.nyse(start, end)`; `.latest_closed(now: pd.Timestamp) -> pd.Timestamp | None`
  (`now` tz-aware); `.between(after: pd.Timestamp | None, until) -> list[pd.Timestamp]`
  (after exclusive, until inclusive); `.up_to(day) -> list[pd.Timestamp]`.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from market_agent.sessions import TradingCalendar

DAYS = [pd.Timestamp(d) for d in ["2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"]]
CLOSES = [pd.Timestamp(f"{d:%Y-%m-%d} 21:00", tz="UTC") for d in DAYS]
CLOSES[2] = pd.Timestamp("2026-11-27 18:00", tz="UTC")  # early close after Thanksgiving
CAL = TradingCalendar(DAYS, CLOSES)


def test_latest_closed_session():
    assert CAL.latest_closed(pd.Timestamp("2026-11-24 20:59", tz="UTC")) is None
    assert CAL.latest_closed(pd.Timestamp("2026-11-24 21:00", tz="UTC")) == DAYS[0]
    assert CAL.latest_closed(pd.Timestamp("2026-11-26 22:30", tz="UTC")) == DAYS[1]  # holiday
    assert CAL.latest_closed(pd.Timestamp("2026-11-27 18:30", tz="UTC")) == DAYS[2]


def test_between_and_up_to():
    assert CAL.between(DAYS[0], DAYS[2]) == DAYS[1:3]
    assert CAL.between(None, DAYS[1]) == DAYS[:2]
    assert CAL.up_to(DAYS[1]) == DAYS[:2]


def test_nyse_calendar_knows_holidays_and_early_closes():
    cal = TradingCalendar.nyse(pd.Timestamp("2026-11-01"), pd.Timestamp("2026-12-31"))
    assert pd.Timestamp("2026-11-26") not in cal.sessions
    assert cal.latest_closed(pd.Timestamp("2026-11-27 18:30", tz="UTC")) == pd.Timestamp(
        "2026-11-27"
    )
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_sessions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.sessions'`.

- [ ] **Step 3: Implement `src/market_agent/sessions.py`**

```python
"""US trading sessions (NYSE calendar): which days the daily run processes."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence

import pandas as pd


class TradingCalendar:
    def __init__(self, sessions: Sequence[pd.Timestamp], closes: Sequence[pd.Timestamp]):
        """sessions are tz-naive dates; closes are each session's closing time in UTC."""
        self.sessions = [pd.Timestamp(s) for s in sessions]
        self._closes = [pd.Timestamp(c) for c in closes]

    @classmethod
    def nyse(cls, start: pd.Timestamp, end: pd.Timestamp) -> TradingCalendar:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XNYS", start=start, end=end)
        sessions = calendar.sessions_in_range(start, end)
        return cls(list(sessions), [calendar.session_close(s) for s in sessions])

    def latest_closed(self, now: pd.Timestamp) -> pd.Timestamp | None:
        """The last session whose close is at or before `now`."""
        index = bisect_right(self._closes, now) - 1
        return self.sessions[index] if index >= 0 else None

    def between(self, after: pd.Timestamp | None, until: pd.Timestamp) -> list[pd.Timestamp]:
        return [s for s in self.sessions if (after is None or s > after) and s <= until]

    def up_to(self, day: pd.Timestamp) -> list[pd.Timestamp]:
        return [s for s in self.sessions if s <= day]
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_sessions.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/sessions.py tests/test_sessions.py
git commit -m "Add the NYSE trading calendar

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Portfolio snapshots and paper-trading tables

Paper portfolios live between runs as a JSON snapshot per portfolio per processed day. Two
fields prepare for Task 6: `Position.marked_on` (the day `last_close` came from) and
`Order.ref_close` (the close a buy order was sized on). Shares become `float`, because a
dividend restatement can leave a fractional share count.

**Files:**
- Modify: `src/market_agent/portfolio.py`, `src/market_agent/broker.py`,
  `src/market_agent/risk.py:77`, `src/market_agent/store.py`
- Test: `tests/test_portfolio.py` (new), `tests/test_broker.py`, `tests/test_risk.py`,
  `tests/test_store_cache.py`

**Interfaces:**
- Produces: `portfolio_to_json(p: Portfolio) -> str`, `portfolio_from_json(text: str) -> Portfolio`;
  `Position.marked_on: pd.Timestamp | None = None` (last field); `Order.ref_close: float = 0.0`
  (last field); `Store.save_day(day, states: Mapping[str, str], status: str, report: str,
  alerts: list[str])`, `Store.latest_paper_day() -> pd.Timestamp | None`,
  `Store.first_paper_day() -> pd.Timestamp | None`, `Store.paper_states(day) -> dict[str, str]`,
  `Store.replace_paper_state(portfolio, day, state)`, `Store.daily_run(day) -> dict | None`,
  `Store.latest_daily_run() -> dict | None` (keys `day`, `created_at`, `status`, `report`,
  `alerts`, `sent`), `Store.mark_sent(day)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_portfolio.py`:

```python
import pandas as pd

from market_agent.portfolio import (
    Order,
    Portfolio,
    Position,
    Trade,
    portfolio_from_json,
    portfolio_to_json,
)


def test_portfolio_round_trips_through_json():
    day = pd.Timestamp("2024-03-01")
    p = Portfolio(
        "rules+ai",
        8_500.5,
        peak=10_200.0,
        halted=True,
        breaker_tripped=True,
        events=["2024-03-01: circuit breaker on"],
    )
    p.positions["AAA"] = Position("AAA", "Tech", 12.5, day, 100.1, 96.0, 108.0, 101.0, 3, day)
    p.orders.append(Order("BBB", "buy", 7, "breakout", day, 1.5, "Energy", ref_close=40.0))
    p.trades.append(
        Trade("CCC", "Tech", day, 10.0, day + pd.Timedelta(days=3), 11.0, 5, "target", 3.0)
    )
    p.equity_history.append((day, 9_900.0))
    copy = portfolio_from_json(portfolio_to_json(p))
    assert copy == p
    assert isinstance(copy.positions["AAA"].entry_day, pd.Timestamp)
    assert isinstance(copy.equity_history[0], tuple)
```

Add to `tests/test_broker.py`:

```python
def test_close_records_the_day_prices_were_marked():
    p = held()
    Simulator(R, S).close(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100.5)}), lambda t: D3)
    assert p.positions["AAA"].marked_on == D1


def test_buy_is_marked_on_its_fill_day():
    p = Portfolio("p", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0))
    Simulator(R, S).open(p, D1, lookup({("AAA", D1): Bar(100, 101, 99, 100.5)}))
    assert p.positions["AAA"].marked_on == D1
```

Add to `tests/test_risk.py`:

```python
def test_orders_remember_the_close_they_were_sized_on():
    p = Portfolio("rules-only", 10_000)
    plan_entries(p, [candidate("A", close=50.0)], 10_000, {}, R, S, DAY)
    assert p.orders[0].ref_close == 50.0
```

Add to `tests/test_store_cache.py`:

```python
def test_paper_days_and_daily_runs(store):
    d1, d2 = pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-02")
    assert store.latest_paper_day() is None and store.first_paper_day() is None
    store.save_day(d1, {"rules-only": "{1}", "rules+ai": "{2}"}, "traded", "report 1", [])
    store.save_day(d2, {"rules-only": "{3}", "rules+ai": "{4}"}, "no trading", "report 2", ["!"])
    assert store.latest_paper_day() == d2 and store.first_paper_day() == d1
    assert store.paper_states(d1) == {"rules-only": "{1}", "rules+ai": "{2}"}
    run = store.daily_run(d2)
    assert (run["status"], run["report"], run["alerts"], run["sent"]) == (
        "no trading",
        "report 2",
        ["!"],
        False,
    )
    store.mark_sent(d2)
    assert store.daily_run(d2)["sent"] is True
    assert store.latest_daily_run()["day"] == d2
    store.replace_paper_state("rules-only", d2, "{5}")
    assert store.paper_states(d2)["rules-only"] == "{5}"
    assert store.daily_run(pd.Timestamp("2026-09-30")) is None
    assert store.paper_states(pd.Timestamp("2026-09-30")) == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_portfolio.py tests/test_broker.py tests/test_risk.py tests/test_store_cache.py -q`
Expected: FAIL (`ImportError: cannot import name 'portfolio_from_json'`, unexpected keyword
`ref_close`, no attribute `marked_on`, no attribute `save_day`).

- [ ] **Step 3: Implement**

`portfolio.py`: add `import json` and `from dataclasses import asdict, dataclass, field`; change
`Position`, `Order`, `Trade` and add the JSON functions:

```python
@dataclass
class Position:
    ticker: str
    sector: str
    shares: float  # whole at entry; a dividend restatement can make it fractional
    entry_day: pd.Timestamp
    entry_price: float  # after slippage
    stop: float
    target: float
    last_close: float
    days_held: int = 0
    marked_on: pd.Timestamp | None = None  # the day last_close comes from


@dataclass(frozen=True)
class Order:
    ticker: str
    side: Literal["buy", "sell"]
    shares: int
    reason: str
    created: pd.Timestamp
    atr: float = 0.0
    sector: str = "Unknown"
    ref_close: float = 0.0  # the close the order was sized on (to detect restated prices)


@dataclass(frozen=True)
class Trade:
    ticker: str
    sector: str
    entry_day: pd.Timestamp
    entry_price: float
    exit_day: pd.Timestamp
    exit_price: float
    shares: float
    exit_reason: str
    pnl: float  # after both commissions
```

```python
_DAY_FIELDS = {"entry_day", "exit_day", "created", "marked_on"}


def _plain(value: object) -> object:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    raise TypeError(f"Can't store {value!r}")


def _with_days(record: dict) -> dict:
    return {
        key: pd.Timestamp(value) if key in _DAY_FIELDS and value is not None else value
        for key, value in record.items()
    }


def portfolio_to_json(p: Portfolio) -> str:
    return json.dumps(
        {
            "name": p.name,
            "cash": p.cash,
            "peak": p.peak,
            "halted": p.halted,
            "breaker_tripped": p.breaker_tripped,
            "events": p.events,
            "positions": [asdict(pos) for pos in p.positions.values()],
            "orders": [asdict(order) for order in p.orders],
            "trades": [asdict(trade) for trade in p.trades],
            "equity_history": [[day, equity] for day, equity in p.equity_history],
        },
        default=_plain,
        sort_keys=True,
    )


def portfolio_from_json(text: str) -> Portfolio:
    data = json.loads(text)
    positions = [Position(**_with_days(record)) for record in data["positions"]]
    return Portfolio(
        name=data["name"],
        cash=data["cash"],
        positions={pos.ticker: pos for pos in positions},
        orders=[Order(**_with_days(record)) for record in data["orders"]],
        trades=[Trade(**_with_days(record)) for record in data["trades"]],
        equity_history=[(pd.Timestamp(day), equity) for day, equity in data["equity_history"]],
        peak=data["peak"],
        halted=data["halted"],
        breaker_tripped=data["breaker_tripped"],
        events=list(data["events"]),
    )
```

`broker.py`: in `close()`, where `pos.last_close = b.close` is set, add `pos.marked_on = day`;
in `_buy()`, pass `marked_on=day` to `Position(...)`.

`risk.py`: the order line becomes

```python
        portfolio.orders.append(
            Order(c.ticker, "buy", shares, "breakout", day, c.atr, sector, ref_close=c.close)
        )
```

`store.py`: append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS paper_states (
    portfolio TEXT, day TEXT, state TEXT, PRIMARY KEY (portfolio, day));
CREATE TABLE IF NOT EXISTS daily_runs (
    day TEXT PRIMARY KEY, created_at TEXT, status TEXT, report TEXT, alerts TEXT, sent INTEGER);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, day TEXT, ticker TEXT, status TEXT,
    verdict TEXT, confidence TEXT, reasons TEXT, risks TEXT, news_used TEXT, note TEXT,
    late INTEGER, model TEXT, prompt TEXT, answer TEXT, cost REAL);
```

and add (`Mapping` from `collections.abc`):

```python
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
            "SELECT day, created_at, status, report, alerts, sent FROM daily_runs WHERE day = ?",
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
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass (existing broker and backtest tests are unchanged by the new defaults).

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/portfolio.py src/market_agent/broker.py src/market_agent/risk.py src/market_agent/store.py tests/test_portfolio.py tests/test_broker.py tests/test_risk.py tests/test_store_cache.py
git commit -m "Store paper portfolios as daily JSON snapshots with run records

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: One trading day, shared by the backtest and paper trading

**Files:**
- Create: `src/market_agent/trading.py`
- Modify: `src/market_agent/backtest.py` (the day loop)
- Test: `tests/test_trading.py`

**Interfaces:**
- Consumes: `Simulator`, `plan_entries`, `Candidate`.
- Produces: `trade_day(sim: Simulator, portfolio: Portfolio, day: pd.Timestamp, panel: Panel,
  last_day: Callable[[str], pd.Timestamp | None], candidates: Sequence[Candidate],
  sectors: Mapping[str, str], settings: Settings) -> float` (returns the day's closing equity;
  `panel` needs only `.bar`).

- [ ] **Step 1: Write the failing test**

```python
from types import SimpleNamespace

import pandas as pd

from market_agent.broker import Simulator
from market_agent.panel import Bar
from market_agent.portfolio import Order, Portfolio
from market_agent.settings import Settings
from market_agent.strategy import Candidate
from market_agent.trading import trade_day

S = Settings()
D0, D1 = pd.bdate_range("2024-03-01", periods=2)


def test_trade_day_fills_marks_and_plans_in_order():
    p = Portfolio("rules-only", 10_000.0)
    p.orders.append(Order("AAA", "buy", 10, "breakout", D0, atr=2.0, ref_close=100.0))
    bars = {("AAA", D1): Bar(100, 101, 99, 100.5)}
    panel = SimpleNamespace(bar=lambda ticker, day: bars.get((ticker, day)))
    bbb = Candidate("BBB", D1, 50.0, 1.0, 0.2, 2.0, True)
    sim = Simulator(S.risk, S.strategy)
    equity = trade_day(sim, p, D1, panel, lambda t: None, [bbb], {}, S)
    assert "AAA" in p.positions
    assert [o.ticker for o in p.orders] == ["BBB"]
    assert p.equity_history == [(D1, equity)]
```

- [ ] **Step 2: Run it to see it fail**

Run: `pytest tests/test_trading.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'market_agent.trading'`.

- [ ] **Step 3: Implement**

`src/market_agent/trading.py`:

```python
"""One trading day for one portfolio: the same steps in the backtest and paper trading."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import pandas as pd

from market_agent.broker import Simulator
from market_agent.panel import Panel
from market_agent.portfolio import Portfolio
from market_agent.risk import plan_entries
from market_agent.settings import Settings
from market_agent.strategy import Candidate


def trade_day(
    sim: Simulator,
    portfolio: Portfolio,
    day: pd.Timestamp,
    panel: Panel,
    last_day: Callable[[str], pd.Timestamp | None],
    candidates: Sequence[Candidate],
    sectors: Mapping[str, str],
    settings: Settings,
) -> float:
    """Fill yesterday's orders, run exits, mark to market, then place orders for tomorrow."""
    sim.open(portfolio, day, panel.bar)
    sim.intraday(portfolio, day, panel.bar)
    equity = sim.close(portfolio, day, panel.bar, last_day)
    plan_entries(portfolio, candidates, equity, sectors, settings.risk, settings.strategy, day)
    return equity
```

`backtest.py`: replace the loop body with (import `trade_day`; drop the now-unused
`plan_entries` import):

```python
    for day in days:
        candidates = screen(
            day, panel.snapshot(day), universe.members(day), earnings, settings.strategy
        )
        signals += len(candidates)
        without_earnings += sum(not c.earnings_known for c in candidates)
        trade_day(sim, portfolio, day, panel, panel.last_day, candidates, sectors, settings)
        if portfolio.positions:
            exposed_days += 1
```

(The backtest never halts, so screening every day matches the old loop; exposure is still
counted after the close, because placing orders does not change positions.)

- [ ] **Step 4: Run the tests and the real tuning backtest**

Run: `pytest -q`
Expected: all pass.

Run: `.venv\Scripts\agent.exe backtest --period tuning`
Expected: the same numbers as before this task (on the 2026-10-03 cache: total return +20.5%,
1171 trades, win rate 46%). Any difference means the refactor changed behaviour: stop and fix.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/trading.py src/market_agent/backtest.py tests/test_trading.py
git commit -m "Share one trading-day step between the backtest and paper trading

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Restated prices and delistings in paper trading

Each daily download replaces a ticker's whole adjusted history. After a split or dividend,
Yahoo scales all earlier prices, so a held position's stored entry, stop, target and last close
(in the old scale) no longer match. `restate` detects the change by comparing the stored
`last_close` with the new history's close on `marked_on`, and rescales. Delisting cannot use
"the last day of the whole history" in paper trading (tomorrow is unknown), so a held share
counts as delisted after 5 sessions in a row without a price.

**Files:**
- Create: `src/market_agent/paper.py`
- Modify: `src/market_agent/panel.py`
- Test: `tests/test_paper.py`, `tests/test_indicators_panel.py`

**Interfaces:**
- Consumes: `Position.marked_on`, `Order.ref_close` (Task 4), `BarLookup` from `broker`.
- Produces: `PORTFOLIOS = ("rules-only", "rules+ai")`;
  `restate(portfolio: Portfolio, bar: BarLookup) -> list[str]` (notes for the report);
  `gone_lookup(panel: Panel, sessions: list[pd.Timestamp], n: int) -> Callable[[str], pd.Timestamp | None]`
  (`sessions` ends with the day being processed);
  `Panel.last_bar_until(ticker: str, day: pd.Timestamp) -> pd.Timestamp | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_paper.py`:

```python
import pandas as pd

from helpers import make_bars
from market_agent.panel import Bar, Panel
from market_agent.paper import gone_lookup, restate
from market_agent.portfolio import Order, Portfolio, Position
from market_agent.settings import StrategySettings

D0 = pd.Timestamp("2024-03-01")


def lookup(table):
    return lambda ticker, day: table.get((ticker, day))


def held():
    p = Portfolio("rules-only", 9_000.0)
    p.positions["AAA"] = Position("AAA", "Tech", 10, D0, 100.0, 96.0, 108.0, 100.0, 2, D0)
    return p


def test_split_while_held_rescales_the_position():
    p = held()
    notes = restate(p, lookup({("AAA", D0): Bar(25, 25.5, 24.5, 25.0)}))  # 4:1 split
    pos = p.positions["AAA"]
    assert pos.shares == 40
    assert (pos.entry_price, pos.stop, pos.target, pos.last_close) == (25.0, 24.0, 27.0, 25.0)
    assert pos.shares * pos.last_close == 1_000  # value unchanged
    assert notes == ["AAA: prices restated ×0.2500 (split or dividend); position adjusted"]


def test_tiny_differences_are_not_restatements():
    p = held()
    assert restate(p, lookup({("AAA", D0): Bar(100, 100, 100, 100.000001)})) == []
    assert p.positions["AAA"].shares == 10


def test_pending_buy_is_rescaled():
    p = Portfolio("rules-only", 10_000.0)
    p.orders.append(Order("BBB", "buy", 10, "breakout", D0, atr=2.0, ref_close=100.0))
    notes = restate(p, lookup({("BBB", D0): Bar(25, 25, 25, 25.0)}))
    [order] = p.orders
    assert (order.shares, order.atr, order.ref_close) == (40, 0.5, 25.0)
    assert notes == ["BBB: prices restated ×0.2500 (split or dividend); order adjusted"]


def test_held_share_counts_as_delisted_after_five_missing_sessions():
    sessions = pd.bdate_range("2024-01-01", periods=20)
    aaa = make_bars([50 + 0.1 * i for i in range(11)], start="2024-01-01")  # sessions 0-10
    panel = Panel({"AAA": aaa}, sessions, StrategySettings(), 0.40)
    assert gone_lookup(panel, list(sessions[:15]), 5)("AAA") is None  # 4 missing
    assert gone_lookup(panel, list(sessions[:16]), 5)("AAA") == sessions[10]  # 5 missing
    assert gone_lookup(panel, list(sessions[:16]), 5)("ZZZ") is None
```

Add to `tests/test_indicators_panel.py`:

```python
def test_last_bar_until():
    sessions = pd.bdate_range("2024-01-01", periods=20)
    aaa = make_bars(rising(11), start="2024-01-01")
    panel = Panel({"AAA": aaa}, sessions, S, 0.40)
    assert panel.last_bar_until("AAA", sessions[15]) == sessions[10]
    assert panel.last_bar_until("AAA", sessions[5]) == sessions[5]
    assert panel.last_bar_until("ZZZ", sessions[5]) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_paper.py tests/test_indicators_panel.py -q`
Expected: FAIL (`No module named 'market_agent.paper'`; no attribute `last_bar_until`).

- [ ] **Step 3: Implement**

`panel.py`, add to `Panel`:

```python
    def last_bar_until(self, ticker: str, day: pd.Timestamp) -> pd.Timestamp | None:
        """The latest day up to `day` with a usable price (no look-ahead)."""
        if ticker not in self.tickers:
            return None
        return self._wide["close"][ticker].loc[:day].last_valid_index()
```

`src/market_agent/paper.py`:

```python
"""Paper-trading rules that the backtest does not need (spec sections 5, 8 and 10)."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import pandas as pd

from market_agent.broker import BarLookup
from market_agent.panel import Panel
from market_agent.portfolio import Portfolio

PORTFOLIOS = ("rules-only", "rules+ai")
RESTATED = 1e-4  # relative change in a stored close that counts as a split or dividend


def restate(portfolio: Portfolio, bar: BarLookup) -> list[str]:
    """Rescale what was stored in the old price scale after Yahoo restated a history.

    Shares are divided by the factor so each position keeps its value and profit.
    """
    notes = []
    for ticker, pos in portfolio.positions.items():
        b = bar(ticker, pos.marked_on) if pos.marked_on is not None else None
        if b is None or pos.last_close <= 0:
            continue
        factor = b.close / pos.last_close
        if abs(factor - 1) <= RESTATED:
            continue
        pos.entry_price *= factor
        pos.stop *= factor
        pos.target *= factor
        pos.last_close *= factor
        pos.shares /= factor
        notes.append(
            f"{ticker}: prices restated ×{factor:.4f} (split or dividend); position adjusted"
        )
    orders = []
    for order in portfolio.orders:
        b = bar(order.ticker, order.created) if order.side == "buy" else None
        if b is not None and order.ref_close > 0:
            factor = b.close / order.ref_close
            if abs(factor - 1) > RESTATED:
                order = replace(
                    order,
                    shares=math.floor(order.shares / factor),
                    atr=order.atr * factor,
                    ref_close=b.close,
                )
                notes.append(
                    f"{order.ticker}: prices restated ×{factor:.4f} (split or dividend); "
                    "order adjusted"
                )
        orders.append(order)
    portfolio.orders = orders
    return notes


def gone_lookup(
    panel: Panel, sessions: list[pd.Timestamp], n: int
) -> Callable[[str], pd.Timestamp | None]:
    """For Simulator.close: a held share counts as delisted after n sessions without a price.

    Returns its last price day then, else None. Uses data up to the processed day only.
    """
    if len(sessions) < n:
        return lambda ticker: None
    day, window_start = sessions[-1], sessions[-n]

    def last_day(ticker: str) -> pd.Timestamp | None:
        end = panel.last_bar_until(ticker, day)
        return end if end is not None and end < window_start else None

    return last_day
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/paper.py src/market_agent/panel.py tests/test_paper.py tests/test_indicators_panel.py
git commit -m "Rescale held positions after restated prices; delist after five missing days

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Daily data check

**Files:**
- Create: `src/market_agent/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: `Panel.bar`, `Panel.last_bar_until` (Task 6).
- Produces: `DayCheck(ok: bool, eligible: int, excluded: list[str], reason: str | None)`;
  `check_day(panel, members: Iterable[str], day, sessions: list[pd.Timestamp], benchmark: str,
  max_missing_share: float, alive_sessions: int) -> DayCheck`. Eligible = members (not the
  benchmark) with a price in the `alive_sessions` sessions before `day`.

- [ ] **Step 1: Write the failing tests**

```python
import pandas as pd

from helpers import make_bars
from market_agent.checks import check_day
from market_agent.panel import Panel
from market_agent.settings import StrategySettings

SESSIONS = pd.bdate_range("2024-01-01", periods=30)  # last session 2024-02-09


def bars():
    return make_bars([50 + 0.1 * i for i in range(30)], start="2024-01-01")


def check(missing=(), gone=(), spy=True, jump=()):
    frames = {f"T{i:02d}": bars() for i in range(25)}
    for t in missing:
        frames[t] = frames[t].drop(index=SESSIONS[-1])
    for t in gone:
        frames[t] = frames[t].iloc[:20]
    for t in jump:
        frames[t].loc[SESSIONS[-1], "close"] *= 2
    frames["SPY"] = bars() if spy else bars().iloc[:-1]
    panel = Panel(frames, SESSIONS, StrategySettings(), 0.40)
    members = {t for t in frames if t != "SPY"}
    return check_day(panel, members, SESSIONS[-1], list(SESSIONS), "SPY", 0.05, 5)


def test_complete_day_is_ok():
    result = check()
    assert (result.ok, result.eligible, result.excluded, result.reason) == (True, 25, [], None)


def test_a_few_missing_shares_are_left_out():
    result = check(missing=["T03"])  # 1 of 25 = 4%
    assert result.ok and result.excluded == ["T03"]


def test_too_many_missing_shares_stop_trading():
    result = check(missing=["T03", "T07"])  # 8%
    assert not result.ok
    assert result.reason == "2 of 25 shares have no usable price for 2024-02-09"


def test_missing_benchmark_stops_trading():
    result = check(spy=False)
    assert not result.ok and result.reason == "no SPY price for 2024-02-09"


def test_long_gone_shares_are_not_counted():
    result = check(gone=["T01", "T02"])
    assert result.ok and result.eligible == 23


def test_bad_jump_counts_as_missing():
    assert check(jump=["T05"]).excluded == ["T05"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_checks.py -v`
Expected: FAIL with `No module named 'market_agent.checks'`.

- [ ] **Step 3: Implement `src/market_agent/checks.py`**

```python
"""The data check before a day is traded (spec section 3, "Daily data checks")."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from market_agent.panel import Panel


@dataclass(frozen=True)
class DayCheck:
    ok: bool
    eligible: int
    excluded: list[str]  # eligible tickers without a usable price that day (left out)
    reason: str | None  # why nothing trades, when not ok


def check_day(
    panel: Panel,
    members: Iterable[str],
    day: pd.Timestamp,
    sessions: list[pd.Timestamp],
    benchmark: str,
    max_missing_share: float,
    alive_sessions: int,
) -> DayCheck:
    """A missing price includes a bad jump: the panel drops those days."""
    if panel.bar(benchmark, day) is None:
        return DayCheck(False, 0, [], f"no {benchmark} price for {day:%Y-%m-%d}")
    before = [d for d in sessions if d < day][-alive_sessions:]
    if not before:
        return DayCheck(True, 0, [], None)
    start, previous = before[0], before[-1]
    eligible = sorted(
        t
        for t in members
        if t != benchmark
        and (end := panel.last_bar_until(t, previous)) is not None
        and end >= start
    )
    excluded = [t for t in eligible if panel.bar(t, day) is None]
    if len(excluded) > max_missing_share * len(eligible):
        return DayCheck(
            False,
            len(eligible),
            excluded,
            f"{len(excluded)} of {len(eligible)} shares have no usable price for {day:%Y-%m-%d}",
        )
    return DayCheck(True, len(eligible), excluded, None)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_checks.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/checks.py tests/test_checks.py
git commit -m "Add the daily data check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: News source and company names

**Files:**
- Create: `src/market_agent/data/finnhub.py`
- Modify: `src/market_agent/data/sources.py`, `src/market_agent/data/yahoo.py`,
  `src/market_agent/universe.py`, `src/market_agent/earnings.py`, `src/market_agent/cli.py`
  (`build_sectors`)
- Test: `tests/test_finnhub.py`, `tests/test_yahoo.py`, `tests/test_universe.py`,
  `tests/test_strategy.py`, `tests/test_cli.py`, `tests/live/test_finnhub_live.py`

**Interfaces:**
- Produces: `Headline(published: pd.Timestamp (tz-aware), source: str, headline: str,
  summary: str)`; `NewsSource` protocol `fetch(ticker, start: date, end: date) -> list[Headline]`
  (newest first); `FinnhubNews(api_key, get_json=...)`; `NoNews()`;
  `Profile(sector: str, name: str = "")`; `YahooSectors.profile(ticker) -> Profile` (replaces
  `.sector`); `load_profiles(path) -> dict[str, Profile]`; `write_profiles(path, profiles)`
  (replaces `write_sectors`; `sectors.csv` columns `ticker,sector,name`); `load_sectors` keeps
  its signature; `EarningsCalendar.next_report(ticker, day) -> pd.Timestamp | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_finnhub.py`:

```python
from datetime import date

import pandas as pd

from market_agent.data.finnhub import FinnhubNews, NoNews


def test_finnhub_news_becomes_headlines_newest_first():
    urls = []

    def get_json(url):
        urls.append(url)
        return [
            {
                "datetime": 1759329000,
                "source": "Reuters",
                "headline": " Nvidia wins order ",
                "summary": "A large cloud order.",
                "url": "https://example.com/a",
            },
            {"datetime": 1759415400, "source": "CNBC", "headline": "Later story", "summary": ""},
            {"datetime": 1759415500, "source": "X", "headline": "", "summary": "no headline"},
        ]

    news = FinnhubNews("KEY", get_json).fetch("NVDA", date(2026, 9, 25), date(2026, 10, 2))
    assert [h.headline for h in news] == ["Later story", "Nvidia wins order"]
    assert news[1].published == pd.Timestamp(1759329000, unit="s", tz="UTC")
    assert (news[1].source, news[1].summary) == ("Reuters", "A large cloud order.")
    assert urls == [
        "https://finnhub.io/api/v1/company-news?symbol=NVDA&from=2026-09-25&to=2026-10-02&token=KEY"
    ]


def test_no_news():
    assert NoNews().fetch("NVDA", date(2026, 9, 25), date(2026, 10, 2)) == []
```

`tests/test_yahoo.py`: replace `test_sector` and `test_sector_transport_errors_propagate`
(import `Profile` from `market_agent.data.sources`):

```python
def test_profile():
    yf = FakeYf(
        {
            "AAPL": FakeTicker(info={"sector": "Technology", "longName": "Apple Inc."}),
            "X": FakeTicker(info={}),
        }
    )
    assert YahooSectors(yf).profile("AAPL") == Profile("Technology", "Apple Inc.")
    assert YahooSectors(yf).profile("X") == Profile("Unknown", "")


def test_profile_transport_errors_propagate():
    yf = FakeYf({"AAPL": FakeTicker(error=YFRateLimitError())})
    with pytest.raises(YFRateLimitError):
        YahooSectors(yf).profile("AAPL")
```

`tests/test_universe.py`: replace `test_sectors_round_trip` (import `Profile`,
`load_profiles`, `write_profiles` instead of `write_sectors`):

```python
def test_profiles_round_trip(tmp_path):
    path = tmp_path / "sectors.csv"
    write_profiles(
        path,
        {
            "MSFT": Profile("Technology", "Microsoft Corporation"),
            "JPM": Profile("Financial Services", ""),
        },
    )
    assert load_sectors(path) == {"JPM": "Financial Services", "MSFT": "Technology"}
    assert load_profiles(path)["MSFT"] == Profile("Technology", "Microsoft Corporation")
    assert load_profiles(tmp_path / "none.csv") == {}


def test_sector_file_without_names(tmp_path):
    path = tmp_path / "sectors.csv"
    path.write_text("ticker,sector\nAAPL,Technology\n", encoding="utf-8")
    assert load_profiles(path) == {"AAPL": Profile("Technology", "")}
```

`tests/test_strategy.py` (import `EarningsHistory` and `EarningsCalendar` are already there):

```python
def test_next_report():
    days = list(pd.bdate_range("2024-01-01", periods=10))
    dates = [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-10")]
    calendar = EarningsCalendar({"AAA": EarningsHistory(dates, days[0])}, days)
    assert calendar.next_report("AAA", pd.Timestamp("2024-01-03")) == pd.Timestamp("2024-01-10")
    assert calendar.next_report("AAA", pd.Timestamp("2024-01-02")) == pd.Timestamp("2024-01-03")
    assert calendar.next_report("AAA", pd.Timestamp("2024-01-10")) is None
    assert calendar.next_report("ZZZ", pd.Timestamp("2024-01-02")) is None
```

`tests/test_cli.py`: change the three sector fakes to the new method (import `Profile`):

```python
class Sectors:
    def profile(self, ticker):
        return Profile("Technology", f"{ticker} Corp")


class FlakySectors:
    def profile(self, ticker):
        raise ConnectionError("timed out")


class UnknownSectors:
    def profile(self, ticker):
        return Profile("Unknown", "")
```

and in `test_build_sectors_keeps_known_sectors` add after the first `build-sectors`:

```python
    assert "AAA,Technology,AAA Corp" in csv.read_text("utf-8")
```

`tests/live/test_finnhub_live.py`:

```python
"""Reaches Finnhub; needs FINNHUB_API_KEY in the environment or .env. Run: pytest -m live"""

import os
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv

from market_agent.data.finnhub import FinnhubNews

pytestmark = pytest.mark.live


def test_real_company_news():
    load_dotenv(".env")
    end = date.today()
    news = FinnhubNews(os.environ["FINNHUB_API_KEY"]).fetch("AAPL", end - timedelta(days=7), end)
    assert news and news[0].headline
    assert news[0].published >= news[-1].published
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest -q`
Expected: FAIL (`No module named 'market_agent.data.finnhub'`, cannot import `Profile`,
no attribute `next_report`, `profile`).

- [ ] **Step 3: Implement**

`data/sources.py`, add:

```python
@dataclass(frozen=True)
class Headline:
    published: pd.Timestamp  # tz-aware
    source: str
    headline: str
    summary: str


class NewsSource(Protocol):
    def fetch(self, ticker: str, start: date, end: date) -> list[Headline]: ...


@dataclass(frozen=True)
class Profile:
    sector: str
    name: str = ""  # company name; empty when unknown
```

(with `from datetime import date` at the top).

`src/market_agent/data/finnhub.py`:

```python
"""Company news from Finnhub's free tier: headlines and summaries. Personal use only."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from market_agent.data.sources import Headline

API = "https://finnhub.io/api/v1/company-news"


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


class FinnhubNews:
    def __init__(self, api_key: str, get_json: Callable[[str], Any] = _get_json):
        self._key = api_key
        self._get_json = get_json

    def fetch(self, ticker: str, start: date, end: date) -> list[Headline]:
        query = urllib.parse.urlencode(
            {"symbol": ticker, "from": start.isoformat(), "to": end.isoformat(), "token": self._key}
        )
        items = self._get_json(f"{API}?{query}") or []
        headlines = [
            Headline(
                published=pd.Timestamp(item["datetime"], unit="s", tz="UTC"),
                source=item.get("source", ""),
                headline=item["headline"].strip(),
                summary=(item.get("summary") or "").strip(),
            )
            for item in items
            if (item.get("headline") or "").strip()
        ]
        return sorted(headlines, key=lambda h: h.published, reverse=True)


class NoNews:
    """Used when no news key is set: reviews then say there is no relevant news."""

    def fetch(self, ticker: str, start: date, end: date) -> list[Headline]:
        return []
```

`data/yahoo.py`: replace `YahooSectors.sector` with

```python
    def profile(self, ticker: str) -> Profile:
        try:
            info = self._yf.Ticker(yahoo_symbol(ticker)).info or {}
        except Exception as exc:
            if not _no_data_error(exc):
                raise
            log.info("%s: no profile (%s)", ticker, exc)
            return Profile("Unknown", "")
        name = info.get("longName") or info.get("shortName") or ""
        return Profile(info.get("sector") or "Unknown", name)
```

`universe.py`: replace `load_sectors` / `write_sectors` with

```python
def load_profiles(path: Path) -> dict[str, Profile]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {
            row["ticker"]: Profile(row["sector"], row.get("name") or "")
            for row in csv.DictReader(handle)
        }


def load_sectors(path: Path) -> dict[str, str]:
    return {ticker: p.sector for ticker, p in load_profiles(path).items()}


def write_profiles(path: Path, profiles: Mapping[str, Profile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "sector", "name"])
        for ticker in sorted(profiles):
            writer.writerow([ticker, profiles[ticker].sector, profiles[ticker].name])
```

(import `Mapping` and `Profile`).

`earnings.py`, add to `EarningsCalendar`:

```python
    def next_report(self, ticker: str, day: pd.Timestamp) -> pd.Timestamp | None:
        """The first known report after `day`."""
        dates = self._dates.get(ticker, [])
        index = bisect_right(dates, day)
        return dates[index] if index < len(dates) else None
```

`cli.py`: `build_sectors` becomes (imports `load_profiles`, `write_profiles`, `Profile`):

```python
def build_sectors(settings: Settings, store: Store) -> int:
    """Keeps a ticker's earlier sector and name when the new lookup fails or says "Unknown"."""
    source = sector_source()
    path = Path(settings.data.sectors_csv)
    previous = load_profiles(path)
    profiles: dict[str, Profile] = {}
    failed = []
    for ticker in store.tickers_with_prices():
        if ticker == settings.data.benchmark:
            continue
        try:
            profile = with_retries(ticker, lambda t=ticker: source.profile(t))
        except Exception as exc:
            log.warning("%s: profile lookup failed (%s)", ticker, exc)
            failed.append(ticker)
            profile = None
        old = previous.get(ticker)
        if old is not None and (profile is None or profile.sector == "Unknown"):
            profile = Profile(old.sector, (profile.name if profile else "") or old.name)
        if profile is not None:
            profiles[ticker] = profile
    write_profiles(path, profiles)
    unknown = sum(1 for p in profiles.values() if p.sector == "Unknown")
    print(f"Sectors written for {len(profiles)} tickers ({unknown} unknown)")
    return report_failures(failed)
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass. Then `pytest -m live tests/live/test_finnhub_live.py -v` (needs `.env`):
1 passed.

- [ ] **Step 5: Rebuild the sector list with names and commit**

Run: `.venv\Scripts\agent.exe build-sectors` (about 10 minutes; adds company names).

```bash
git add src/market_agent tests data/sectors.csv
git commit -m "Add Finnhub company news and company names in the sector list

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The Claude reviewer

Model choice (spec section 7 leaves it to this plan): `claude-opus-5-5` at effort `low`,
$4 / $20 per million tokens. A review is roughly 2–3k input and up to ~1.5k output tokens
(thinking included), about US$0.04; at ~5 reviews per trading day that is about US$4 a month,
under the US$5 cap, and the cap stops reviews if it runs over. `claude-sonnet-5-5` ($2 / $10)
halves the cost; the owner can switch in `config.yaml` (model and both prices). Requests on
Opus 5.5 / Sonnet 5.5 include server-side refusal fallbacks (`fallbacks: "default"`), so a
safety-classifier false positive is retried on another model instead of failing the review.

**Files:**
- Create: `src/market_agent/reviewer.py`
- Modify: `src/market_agent/store.py`
- Test: `tests/test_reviewer.py`, `tests/test_store_cache.py`, `tests/live/test_claude_live.py`

**Interfaces:**
- Consumes: `AiSettings` (Task 2), `Headline` (Task 8).
- Produces: `ReviewInput(ticker, company, sector, day, close, sma_fast, sma_slow, volume_ratio,
  atr, return_63, next_earnings, headlines)`; `Review(ticker, status, verdict, confidence,
  reasons, risks, news_used, note, cost, model, prompt, answer)` with `status` in
  `"reviewed" | "failed" | "not reviewed"`; `Answer(text, input_tokens, output_tokens, model)`;
  `ModelUnavailable`; `ClaudeModel(settings, client=None).ask(system, prompt) -> Answer`;
  `Reviewer(model | None, settings, spent: Callable[[], float]).review(item) -> Review`;
  `build_prompt(item) -> str`; `parse_answer(text, headline_count) -> dict | None`;
  `Store.save_review(day, review, late: bool)`, `Store.ai_spent_since(since: datetime) -> float`,
  `Store.reviews_on(day) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_reviewer.py`:

```python
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pandas as pd
import pytest

from market_agent.data.sources import Headline
from market_agent.reviewer import (
    SCHEMA,
    Answer,
    ClaudeModel,
    ModelUnavailable,
    Reviewer,
    ReviewInput,
    build_prompt,
)
from market_agent.settings import AiSettings

AI = AiSettings()
HEADLINE = Headline(
    pd.Timestamp("2026-10-01 14:30", tz="UTC"),
    "Reuters",
    "Nvidia wins order",
    "A large cloud order.",
)
ITEM = ReviewInput(
    ticker="NVDA",
    company="NVIDIA Corporation",
    sector="Technology",
    day=pd.Timestamp("2026-10-02"),
    close=187.62,
    sma_fast=175.4,
    sma_slow=150.25,
    volume_ratio=1.83,
    atr=5.1,
    return_63=0.214,
    next_earnings=pd.Timestamp("2026-11-19"),
    headlines=[HEADLINE],
)
VALID = json.dumps(
    {
        "verdict": "skip",
        "confidence": "medium",
        "reasons": ["Guidance cut"],
        "risks": [],
        "news_used": [0],
    }
)


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def ask(self, system, prompt):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Answer(reply, 1_000, 200, "fake-model")


def reviewer(model, spent=0.0):
    return Reviewer(model, AI, lambda: spent)


def test_prompt_holds_only_the_given_data():
    assert build_prompt(ITEM) == (
        "Candidate: NVDA (NVIDIA Corporation), sector Technology\n"
        "Day: 2026-10-02, after the close\n"
        "Close 187.62; 50-day average 175.40; 200-day average 150.25\n"
        "Breakout: highest close of the last 20 trading days, on 1.8 times average volume\n"
        "ATR(14) 5.10; 3-month return +21.4%\n"
        "Next earnings date: 2026-11-19\n"
        "\n"
        "Headlines, newest first:\n"
        "[0] 2026-10-01 Reuters: Nvidia wins order\n"
        "    A large cloud order."
    )


def test_prompt_without_news_or_earnings_date():
    item = ReviewInput(**{**ITEM.__dict__, "headlines": [], "next_earnings": None})
    text = build_prompt(item)
    assert "Next earnings date: not known" in text
    assert text.endswith("No recent headlines.")


def test_valid_answer_is_reviewed_with_its_cost():
    review = reviewer(FakeModel(VALID)).review(ITEM)
    assert (review.status, review.verdict, review.confidence) == ("reviewed", "skip", "medium")
    assert (review.reasons, review.risks, review.news_used) == (["Guidance cut"], [], [0])
    assert review.cost == pytest.approx(1_000 * 4 / 1e6 + 200 * 20 / 1e6)
    assert (review.model, review.answer, review.prompt) == ("fake-model", VALID, build_prompt(ITEM))


def test_invalid_answer_is_retried_once():
    model = FakeModel("not json", VALID)
    review = reviewer(model).review(ITEM)
    assert review.status == "reviewed" and model.calls == 2
    assert review.cost == pytest.approx(2 * 0.008)


def test_invalid_twice_is_flagged():
    review = reviewer(FakeModel("{}", "not json")).review(ITEM)
    assert (review.status, review.verdict, review.note) == ("failed", "flag", "review failed")


@pytest.mark.parametrize(
    "answer",
    [
        {"verdict": "buy", "confidence": "high", "reasons": [], "risks": [], "news_used": []},
        {
            "verdict": "skip",
            "confidence": "high",
            "reasons": ["a", "b", "c", "d"],
            "risks": [],
            "news_used": [],
        },
        {"verdict": "skip", "confidence": "high", "reasons": [], "risks": [], "news_used": [3]},
    ],
)
def test_out_of_bounds_answers_are_invalid(answer):
    text = json.dumps(answer)
    assert reviewer(FakeModel(text, text)).review(ITEM).status == "failed"


def test_unavailable_model_is_not_reviewed():
    review = reviewer(FakeModel(ModelUnavailable("HTTP 529: overloaded"))).review(ITEM)
    assert (review.status, review.verdict) == ("not reviewed", "approve")
    assert review.note == "Claude unavailable: HTTP 529: overloaded"


def test_cap_reached_skips_the_call():
    model = FakeModel(VALID)
    review = reviewer(model, spent=5.0).review(ITEM)
    assert (review.status, review.verdict) == ("not reviewed", "approve")
    assert review.note == "monthly cap of US$5.00 reached"
    assert model.calls == 0


def test_no_model_is_not_reviewed():
    review = reviewer(None).review(ITEM)
    assert (review.status, review.note) == ("not reviewed", "no Claude API key")


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(messages):
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))


RESPONSE = SimpleNamespace(
    content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=VALID),
    ],
    usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    model="claude-opus-5-5",
)


def test_claude_model_asks_for_json_with_fallbacks():
    messages = FakeMessages(RESPONSE)
    answer = ClaudeModel(AI, fake_client(messages)).ask("system", "prompt")
    assert answer == Answer(VALID, 1200, 300, "claude-opus-5-5")
    [call] = messages.calls
    assert (call["model"], call["max_tokens"], call["system"]) == (
        "claude-opus-5-5",
        4000,
        "system",
    )
    assert call["messages"] == [{"role": "user", "content": "prompt"}]
    assert call["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"


def test_claude_model_without_fallbacks_or_effort():
    messages = FakeMessages(RESPONSE)
    ClaudeModel(AiSettings(model="claude-haiku-4-5", effort=""), fake_client(messages)).ask(
        "s", "p"
    )
    [call] = messages.calls
    assert "betas" not in call and "fallbacks" not in call
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_api_errors_become_model_unavailable():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    messages = FakeMessages(error=anthropic.APIConnectionError(request=request))
    with pytest.raises(ModelUnavailable, match="connection failed"):
        ClaudeModel(AI, fake_client(messages)).ask("s", "p")
```

(If `httpx2` or the `APIConnectionError(request=...)` signature differs in the installed SDK,
check `help(anthropic.APIConnectionError)` and build the request the way it asks.)

Add to `tests/test_store_cache.py` (`from datetime import datetime`, `SimpleNamespace` already
imported):

```python
def test_reviews_and_monthly_spend(store):
    day = pd.Timestamp("2026-10-02")
    review = SimpleNamespace(
        ticker="NVDA",
        status="reviewed",
        verdict="skip",
        confidence="medium",
        reasons=["Guidance cut"],
        risks=[],
        news_used=[0],
        note="",
        cost=0.04,
        model="claude-opus-5-5",
        prompt="p",
        answer="{}",
    )
    store.save_review(day, review, late=True)
    store.save_review(day, review, late=False)
    assert store.ai_spent_since(datetime(2000, 1, 1)) == pytest.approx(0.08)
    assert store.ai_spent_since(datetime(2100, 1, 1)) == 0.0
    [first, _] = store.reviews_on(day)
    assert (first["ticker"], first["verdict"], first["reasons"], first["late"]) == (
        "NVDA",
        "skip",
        ["Guidance cut"],
        True,
    )
```

`tests/live/test_claude_live.py`:

```python
"""Calls Claude (costs about US$0.05); needs ANTHROPIC_API_KEY. Run: pytest -m live"""

import pytest
from dotenv import load_dotenv

from market_agent.reviewer import ClaudeModel, Reviewer
from market_agent.settings import AiSettings
from tests.test_reviewer import ITEM

pytestmark = pytest.mark.live


def test_real_review():
    load_dotenv(".env")
    review = Reviewer(ClaudeModel(AiSettings()), AiSettings(), lambda: 0.0).review(ITEM)
    assert review.status == "reviewed", review.answer
    assert review.verdict in ("approve", "skip", "flag")
    assert 0 < review.cost < 0.2
```

(If `from tests.test_reviewer import ITEM` fails because `tests` is not a package, copy the
`HEADLINE` and `ITEM` definitions into this file instead.)

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_reviewer.py tests/test_store_cache.py -q`
Expected: FAIL with `No module named 'market_agent.reviewer'` and no attribute `save_review`.

- [ ] **Step 3: Implement**

`src/market_agent/reviewer.py`:

```python
"""Claude reviews the top candidates for the rules+ai portfolio (spec section 7)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
import pandas as pd

from market_agent.data.sources import Headline
from market_agent.settings import AiSettings

VERDICTS = ("approve", "skip", "flag")
CONFIDENCE = ("low", "medium", "high")
MAX_ITEMS = 3
# Models that take server-side refusal fallbacks (a false positive is retried on another model).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

SYSTEM = """You review swing-trade candidates for a paper-trading experiment (simulated money).
A rules-based screen found each candidate: an uptrend and a 20-day closing high on at least 1.5
times average volume. Decide whether the information given argues against buying it at
tomorrow's open.

- Judge only from the information in the message. Never invent facts, prices or events.
- If none of the headlines matter, include "no relevant news" in reasons.
- verdict: "approve" to buy, "skip" to leave it out, "flag" to buy but point out a concern.
- You cannot change the position size, stop or target.
- reasons and risks: at most 3 short strings each. news_used: the numbers of the headlines you
  relied on (empty if none)."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "news_used": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["verdict", "confidence", "reasons", "risks", "news_used"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ReviewInput:
    ticker: str
    company: str
    sector: str
    day: pd.Timestamp
    close: float
    sma_fast: float
    sma_slow: float
    volume_ratio: float
    atr: float
    return_63: float
    next_earnings: pd.Timestamp | None
    headlines: list[Headline]


@dataclass(frozen=True)
class Review:
    ticker: str
    status: str  # "reviewed" | "failed" | "not reviewed"
    verdict: str  # not reviewed counts as "approve"; failed as "flag"
    confidence: str | None
    reasons: list[str]
    risks: list[str]
    news_used: list[int]
    note: str  # why it was not reviewed or failed
    cost: float  # US$
    model: str | None
    prompt: str
    answer: str | None


@dataclass(frozen=True)
class Answer:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ModelUnavailable(Exception):
    """The Claude API could not be reached or refused the request."""


class Model(Protocol):
    def ask(self, system: str, prompt: str) -> Answer: ...


class ClaudeModel:
    def __init__(self, settings: AiSettings, client: Any = None):
        self._settings = settings
        self._client = client if client is not None else anthropic.Anthropic()

    def ask(self, system: str, prompt: str) -> Answer:
        s = self._settings
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": SCHEMA}}
        if s.effort:
            output_config["effort"] = s.effort
        request: dict[str, Any] = {
            "model": s.model,
            "max_tokens": s.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        try:
            if s.model in FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request
                )
            else:
                response = self._client.messages.create(**request)
        except anthropic.APIStatusError as exc:
            raise ModelUnavailable(f"HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelUnavailable(f"connection failed: {exc}") from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return Answer(text, usage.input_tokens, usage.output_tokens, response.model)


def build_prompt(item: ReviewInput) -> str:
    earnings = f"{item.next_earnings:%Y-%m-%d}" if item.next_earnings is not None else "not known"
    lines = [
        f"Candidate: {item.ticker} ({item.company}), sector {item.sector}",
        f"Day: {item.day:%Y-%m-%d}, after the close",
        f"Close {item.close:.2f}; 50-day average {item.sma_fast:.2f}; "
        f"200-day average {item.sma_slow:.2f}",
        "Breakout: highest close of the last 20 trading days, "
        f"on {item.volume_ratio:.1f} times average volume",
        f"ATR(14) {item.atr:.2f}; 3-month return {item.return_63:+.1%}",
        f"Next earnings date: {earnings}",
        "",
    ]
    if not item.headlines:
        lines.append("No recent headlines.")
    else:
        lines.append("Headlines, newest first:")
        for index, h in enumerate(item.headlines):
            lines.append(f"[{index}] {h.published:%Y-%m-%d} {h.source}: {h.headline}")
            if h.summary:
                lines.append(f"    {h.summary}")
    return "\n".join(lines)


def _strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= MAX_ITEMS
        and all(isinstance(v, str) for v in value)
    )


def parse_answer(text: str, headline_count: int) -> dict[str, Any] | None:
    """The validated answer, or None if it breaks any rule of the schema or the spec."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    news = data.get("news_used")
    valid = (
        data.get("verdict") in VERDICTS
        and data.get("confidence") in CONFIDENCE
        and _strings(data.get("reasons"))
        and _strings(data.get("risks"))
        and isinstance(news, list)
        and all(isinstance(i, int) and 0 <= i < headline_count for i in news)
    )
    if not valid:
        return None
    return {key: data[key] for key in ("verdict", "confidence", "reasons", "risks", "news_used")}


class Reviewer:
    def __init__(self, model: Model | None, settings: AiSettings, spent: Callable[[], float]):
        """spent: US$ already spent on reviews this calendar month."""
        self._model = model
        self._settings = settings
        self._spent = spent

    def _cost(self, answer: Answer) -> float:
        s = self._settings
        return (answer.input_tokens * s.input_price + answer.output_tokens * s.output_price) / 1e6

    def _not_reviewed(self, item: ReviewInput, prompt: str, note: str, cost: float = 0.0) -> Review:
        return Review(
            item.ticker, "not reviewed", "approve", None, [], [], [], note, cost, None, prompt, None
        )

    def review(self, item: ReviewInput) -> Review:
        prompt = build_prompt(item)
        if self._model is None:
            return self._not_reviewed(item, prompt, "no Claude API key")
        cap = self._settings.monthly_cap
        if self._spent() >= cap:
            return self._not_reviewed(item, prompt, f"monthly cap of US${cap:.2f} reached")
        cost = 0.0
        answer: Answer | None = None
        for _ in range(2):  # one retry for an invalid answer
            try:
                answer = self._model.ask(SYSTEM, prompt)
            except ModelUnavailable as exc:
                return self._not_reviewed(item, prompt, f"Claude unavailable: {exc}", cost)
            cost += self._cost(answer)
            parsed = parse_answer(answer.text, len(item.headlines))
            if parsed is not None:
                return Review(
                    item.ticker,
                    "reviewed",
                    note="",
                    cost=cost,
                    model=answer.model,
                    prompt=prompt,
                    answer=answer.text,
                    **parsed,
                )
        assert answer is not None
        return Review(
            item.ticker,
            "failed",
            "flag",
            None,
            [],
            [],
            [],
            "review failed",
            cost,
            answer.model,
            prompt,
            answer.text,
        )
```

`store.py` (`datetime` is already imported):

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass. Then (costs about US$0.05) `pytest -m live tests/live/test_claude_live.py -v`:
1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/reviewer.py src/market_agent/store.py tests/test_reviewer.py tests/test_store_cache.py tests/live/test_claude_live.py
git commit -m "Add the Claude candidate reviewer with validation, retry and monthly cap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Daily report and Telegram

**Files:**
- Create: `src/market_agent/report.py`, `src/market_agent/notify.py`
- Test: `tests/test_report.py`, `tests/test_notify.py`, `tests/live/test_telegram_live.py`

**Interfaces:**
- Consumes: `Candidate`, `Review` (Task 9), `Portfolio`.
- Produces: `PortfolioLine(name, equity, start_equity, open_positions, halted)`;
  `build_report(day, lines, spy_return, since, candidates, reviews: Mapping[str, Review],
  fills: list[str], warnings: list[str]) -> str`; `fills_on(p: Portfolio, day) -> list[str]`;
  `Telegram(token, chat_id, post=...).send(text)`; `NoTelegram().send(text)` (always raises);
  `TelegramError`; `split_message(text, limit=4000) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
import pandas as pd

from market_agent.portfolio import Portfolio, Position, Trade
from market_agent.report import PortfolioLine, build_report, fills_on
from market_agent.reviewer import Review
from market_agent.strategy import Candidate

DAY = pd.Timestamp("2026-10-02")


def candidate(ticker):
    return Candidate(ticker, DAY, 100.0, 2.0, 0.2, 2.0, True)


def review(ticker, status, verdict, confidence=None, reasons=(), note=""):
    return Review(
        ticker, status, verdict, confidence, list(reasons), [], [], note, 0.0, None, "", None
    )


def test_report_text():
    text = build_report(
        DAY,
        [
            PortfolioLine("rules-only", 10_234.0, 10_000.0, 4, False),
            PortfolioLine("rules+ai", 9_100.0, 10_000.0, 3, True),
        ],
        0.018,
        pd.Timestamp("2026-09-14"),
        [candidate("NVDA"), candidate("XOM"), candidate("ABC"), candidate("DEF")],
        {
            "NVDA": review("NVDA", "reviewed", "approve", "high", ["Strong demand in recent news"]),
            "XOM": review("XOM", "reviewed", "skip", "medium", ["Oil prices falling"]),
            "ABC": review("ABC", "not reviewed", "approve", note="monthly cap of US$5.00 reached"),
        },
        ["rules-only bought 10 NVDA at 120.50"],
        ["1 share had no usable price"],
    )
    assert text == (
        "Market agent, 2026-10-02 (paper trading, simulated money)\n"
        "\n"
        "rules-only: $10,234 (+2.3%), 4 open\n"
        "rules+ai:   $9,100 (-9.0%), 3 open, circuit breaker on\n"
        "SPY: +1.8% since 2026-09-14\n"
        "\n"
        "Candidates (4):\n"
        "- NVDA: approve (high): Strong demand in recent news\n"
        "- XOM: skip (medium): Oil prices falling\n"
        "- ABC: not reviewed (monthly cap of US$5.00 reached), treated as approved\n"
        "- DEF: not reviewed (below the daily review limit), treated as approved\n"
        "\n"
        "Fills:\n"
        "- rules-only bought 10 NVDA at 120.50\n"
        "\n"
        "Warnings:\n"
        "- 1 share had no usable price"
    )


def test_quiet_day_report():
    text = build_report(
        DAY, [PortfolioLine("rules-only", 10_000.0, 10_000.0, 0, False)], 0.0, DAY, [], {}, [], []
    )
    assert text.endswith("Candidates: none today")


def test_failed_review_line():
    text = build_report(
        DAY,
        [PortfolioLine("rules+ai", 10_000.0, 10_000.0, 0, False)],
        0.0,
        DAY,
        [candidate("NVDA")],
        {"NVDA": review("NVDA", "failed", "flag", note="review failed")},
        [],
        [],
    )
    assert "- NVDA: flag: review failed" in text


def test_fills_on_a_day():
    p = Portfolio("rules-only", 5_000.0)
    p.positions["NVDA"] = Position("NVDA", "Tech", 10, DAY, 120.5, 110.0, 140.0, 121.0)
    earlier = DAY - pd.Timedelta(days=7)
    p.trades.append(Trade("XOM", "Energy", earlier, 90.0, DAY, 98.1, 5, "target", 40.2))
    p.trades.append(Trade("OLD", "Energy", earlier, 90.0, earlier, 91.0, 5, "time", 3.0))
    assert fills_on(p, DAY) == [
        "rules-only bought 10 NVDA at 120.50",
        "rules-only sold 5 XOM at 98.10 (target), +40.20",
    ]
```

`tests/test_notify.py`:

```python
import urllib.error

import pytest

from market_agent.notify import NoTelegram, Telegram, TelegramError, split_message


def test_long_reports_are_split_on_lines():
    text = "\n".join(f"line {i:04d} " + "x" * 90 for i in range(100))
    chunks = split_message(text, limit=4000)
    assert "\n".join(chunks) == text
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert len(chunks) == 3


def test_a_too_long_line_is_cut():
    chunks = split_message("y" * 9000, limit=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]


def test_send_posts_each_chunk():
    calls = []

    def post(url, data):
        calls.append((url, data))
        return {"ok": True}

    Telegram("TOKEN", "42", post).send("hello")
    assert calls == [
        (
            "https://api.telegram.org/botTOKEN/sendMessage",
            {"chat_id": "42", "text": "hello", "disable_web_page_preview": "true"},
        )
    ]


def test_network_failures_raise_without_the_token():
    def post(url, data):
        raise urllib.error.URLError(f"cannot reach {url}")

    with pytest.raises(TelegramError) as info:
        Telegram("TOKEN", "42", post).send("hi")
    assert "TOKEN" not in str(info.value)


def test_api_errors_raise():
    def post(url, data):
        return {"ok": False, "description": "Bad Request: chat not found"}

    with pytest.raises(TelegramError, match="chat not found"):
        Telegram("TOKEN", "42", post).send("hi")


def test_no_telegram_explains():
    with pytest.raises(TelegramError, match="TELEGRAM_BOT_TOKEN"):
        NoTelegram().send("hi")
```

`tests/live/test_telegram_live.py`:

```python
"""Sends a real message; needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Run: pytest -m live"""

import os

import pytest
from dotenv import load_dotenv

from market_agent.notify import Telegram

pytestmark = pytest.mark.live


def test_real_message():
    load_dotenv(".env")
    Telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]).send(
        "Market agent: live test message"
    )
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_report.py tests/test_notify.py -q`
Expected: FAIL with `No module named 'market_agent.report'` / `'market_agent.notify'`.

- [ ] **Step 3: Implement**

`src/market_agent/report.py`:

```python
"""The daily report (spec section 9): plain text, for Telegram and `agent report`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from market_agent.portfolio import Portfolio
from market_agent.reviewer import Review
from market_agent.strategy import Candidate


@dataclass(frozen=True)
class PortfolioLine:
    name: str
    equity: float
    start_equity: float
    open_positions: int
    halted: bool


def _pct(value: float) -> str:
    return f"{100 * value:+.1f}%"


def _verdict(review: Review | None) -> str:
    if review is None:
        return "not reviewed (below the daily review limit), treated as approved"
    if review.status == "not reviewed":
        return f"not reviewed ({review.note}), treated as approved"
    if review.status == "failed":
        return f"flag: {review.note}"
    text = f"{review.verdict} ({review.confidence})"
    return f"{text}: {review.reasons[0]}" if review.reasons else text


def build_report(
    day: pd.Timestamp,
    lines: Sequence[PortfolioLine],
    spy_return: float,
    since: pd.Timestamp,
    candidates: Sequence[Candidate],
    reviews: Mapping[str, Review],
    fills: Sequence[str],
    warnings: Sequence[str],
) -> str:
    out = [f"Market agent, {day:%Y-%m-%d} (paper trading, simulated money)", ""]
    width = max(len(line.name) for line in lines) + 1
    for line in lines:
        text = (
            f"{line.name + ':':<{width}} ${line.equity:,.0f} "
            f"({_pct(line.equity / line.start_equity - 1)}), {line.open_positions} open"
        )
        out.append(text + (", circuit breaker on" if line.halted else ""))
    out.append(f"SPY: {_pct(spy_return)} since {since:%Y-%m-%d}")
    out.append("")
    if candidates:
        out.append(f"Candidates ({len(candidates)}):")
        out += [f"- {c.ticker}: {_verdict(reviews.get(c.ticker))}" for c in candidates]
    else:
        out.append("Candidates: none today")
    if fills:
        out += ["", "Fills:", *(f"- {fill}" for fill in fills)]
    if warnings:
        out += ["", "Warnings:", *(f"- {warning}" for warning in warnings)]
    return "\n".join(out)


def fills_on(p: Portfolio, day: pd.Timestamp) -> list[str]:
    """Buys filled at this day's open (including ones sold the same day), then sales."""
    bought = [pos for pos in p.positions.values() if pos.entry_day == day]
    bought += [t for t in p.trades if t.entry_day == day]
    lines = [f"{p.name} bought {b.shares:g} {b.ticker} at {b.entry_price:.2f}" for b in bought]
    lines += [
        f"{p.name} sold {t.shares:g} {t.ticker} at {t.exit_price:.2f} ({t.exit_reason}), "
        f"{t.pnl:+,.2f}"
        for t in p.trades
        if t.exit_day == day
    ]
    return lines
```

`src/market_agent/notify.py`:

```python
"""Telegram messages through the Bot API (plain HTTPS, no extra package)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

LIMIT = 4000  # Telegram allows 4096 characters per message


class TelegramError(Exception):
    """The message was not delivered."""


def _post(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(url, body, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:  # Telegram explains errors in a JSON body
        return json.load(exc)


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        for piece in [line[i : i + limit] for i in range(0, len(line), limit)] or [""]:
            extra = len(piece) + (1 if current else 0)
            if current and size + extra > limit:
                chunks.append("\n".join(current))
                current, size, extra = [], 0, len(piece)
            current.append(piece)
            size += extra
    chunks.append("\n".join(current))
    return chunks


class Telegram:
    def __init__(
        self,
        token: str,
        chat_id: str,
        post: Callable[[str, dict[str, str]], dict[str, Any]] = _post,
    ):
        self._token = token
        self._chat_id = chat_id
        self._post = post

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        for chunk in split_message(text):
            data = {"chat_id": self._chat_id, "text": chunk, "disable_web_page_preview": "true"}
            try:
                result = self._post(url, data)
            except (OSError, ValueError) as exc:  # URLError is an OSError
                raise TelegramError(str(exc).replace(self._token, "<token>")) from None
            if not result.get("ok"):
                raise TelegramError(result.get("description", "unknown Telegram error"))


class NoTelegram:
    def send(self, text: str) -> None:
        raise TelegramError("Telegram is not set up (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
```

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass. Then `pytest -m live tests/live/test_telegram_live.py -v`: 1 passed and
the message arrives.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/report.py src/market_agent/notify.py tests/test_report.py tests/test_notify.py tests/live/test_telegram_live.py
git commit -m "Add the daily report and Telegram sender

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The daily run and catch-up

**Files:**
- Create: `src/market_agent/daily.py`
- Test: `tests/test_daily.py`

**Interfaces:**
- Consumes: everything above: `TradingCalendar` (3), `portfolio_to_json` / `from_json` and the
  `Store` paper methods (4), `trade_day` (5), `PORTFOLIOS`, `restate`, `gone_lookup` (6),
  `check_day` (7), `NewsSource`, `Profile`, `load_profiles`, `next_report` (8), `Reviewer`,
  `ReviewInput`, `Review` (9), `build_report`, `fills_on`, `TelegramError` (10).
- Produces: `DayResult(day, status: "traded" | "no trading", report, alerts)`;
  `Market(panel, sessions, universe, earnings, profiles)`;
  `load_market(store, settings, sessions) -> Market`;
  `DailyRun(settings, store, reviewer, news).process(market, day, check, late) -> DayResult`;
  `pending_days(store, calendar, now) -> list[pd.Timestamp]`;
  `run_days(settings, store, calendar, runner, now, wait, refetch, sleep) -> list[DayResult]`;
  `send_results(store, results, telegram) -> list[str]` (problems; empty when all were sent).

- [ ] **Step 1: Write the failing tests**

`tests/test_daily.py`:

```python
from dataclasses import dataclass
from typing import Any

import pandas as pd

from helpers import make_bars, settings_with
from market_agent.daily import DailyRun, run_days, send_results
from market_agent.data.finnhub import NoNews
from market_agent.data.sources import EarningsHistory, Headline
from market_agent.notify import TelegramError
from market_agent.portfolio import portfolio_from_json
from market_agent.reviewer import Review
from market_agent.sessions import TradingCalendar
from market_agent.settings import Settings
from market_agent.store import Store

N = 320
SESSIONS = list(pd.bdate_range("2020-06-01", periods=N))


def aaa_bars():
    """Steady rise, then a breakout on triple volume at session 250 that runs to its target."""
    closes = [50 + 0.1 * i for i in range(250)] + [75 + 0.6 * i for i in range(N - 250)]
    volumes = [1e6] * 250 + [3e6] + [1e6] * (N - 251)
    return make_bars(closes, volumes, start="2020-06-01")


def spy_bars():
    return make_bars([400 + 0.2 * i for i in range(N)], start="2020-06-01")


def closing(day):
    return (day + pd.Timedelta(hours=20)).tz_localize("UTC")


def after_close(k):
    return closing(SESSIONS[k]) + pd.Timedelta(hours=2)


class FakeReviewer:
    def __init__(self, verdict="approve"):
        self.verdict = verdict
        self.items = []

    def review(self, item):
        self.items.append(item)
        return Review(
            item.ticker,
            "reviewed",
            self.verdict,
            "high",
            ["fine"],
            [],
            [],
            "",
            0.01,
            "fake",
            "prompt",
            "{}",
        )


@dataclass
class World:
    store: Store
    settings: Settings
    calendar: TradingCalendar
    reviewer: FakeReviewer
    runner: DailyRun


def make_world(path, frames=None, sessions=SESSIONS, verdict="approve", news: Any = None):
    path.mkdir(parents=True, exist_ok=True)
    store = Store(path / "market.db")
    for ticker, bars in (frames or {"AAA": aaa_bars(), "SPY": spy_bars()}).items():
        store.replace_prices(ticker, bars, "2026-10-01")
    store.replace_earnings("AAA", EarningsHistory([], SESSIONS[0]), "2026-10-01")
    (path / "members.csv").write_text('date,tickers\n2020-06-01,"AAA"\n', encoding="utf-8")
    settings = settings_with(
        data={"membership_csv": str(path / "members.csv"), "sectors_csv": str(path / "s.csv")}
    )
    calendar = TradingCalendar(sessions, [closing(s) for s in sessions])
    reviewer = FakeReviewer(verdict)
    runner = DailyRun(settings, store, reviewer, news or NoNews())
    return World(store, settings, calendar, reviewer, runner)


def run(world, now, wait=False, refetch=None, sleep=None):
    clock = now if callable(now) else (lambda: now)
    return run_days(
        world.settings,
        world.store,
        world.calendar,
        world.runner,
        now=clock,
        wait=wait,
        refetch=refetch or (lambda tickers: None),
        sleep=sleep or (lambda seconds: None),
    )


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += pd.Timedelta(seconds=seconds)


def state(world, day, name="rules-only"):
    return portfolio_from_json(world.store.paper_states(day)[name])


def test_first_run_processes_only_the_latest_closed_day(tmp_path):
    w = make_world(tmp_path)
    [result] = run(w, after_close(250))
    assert (result.day, result.status) == (SESSIONS[250], "traded")
    assert [o.ticker for o in state(w, SESSIONS[250]).orders] == ["AAA"]
    assert "Candidates (1):" in result.report
    assert "- AAA: approve (high): fine" in result.report
    assert w.store.daily_run(SESSIONS[250])["report"] == result.report
    assert [item.ticker for item in w.reviewer.items] == ["AAA"]


def test_second_run_on_the_same_day_does_nothing(tmp_path):
    w = make_world(tmp_path)
    run(w, after_close(250))
    assert run(w, after_close(250)) == []


def test_catch_up_matches_on_time_runs(tmp_path):
    on_time, caught_up = make_world(tmp_path / "a"), make_world(tmp_path / "b")
    for k in range(245, 276):
        run(on_time, after_close(k))
    run(caught_up, after_close(245))
    results = run(caught_up, after_close(275))
    assert len(results) == 30
    last = SESSIONS[275]
    assert on_time.store.paper_states(last) == caught_up.store.paper_states(last)
    p = state(on_time, last)
    assert p.trades and p.trades[0].ticker == "AAA"  # something actually happened
    assert on_time.store.reviews_on(SESSIONS[250])[0]["late"] is False
    assert caught_up.store.reviews_on(SESSIONS[250])[0]["late"] is True


def test_holidays_are_not_processed(tmp_path):
    sessions = [s for s in SESSIONS if s != SESSIONS[251]]
    w = make_world(tmp_path, sessions=sessions)
    run(w, after_close(250))
    results = run(w, after_close(252))
    assert [r.day for r in results] == [SESSIONS[252]]
    assert w.store.paper_states(SESSIONS[251]) == {}


def test_skipped_candidates_are_left_out_of_rules_ai(tmp_path):
    w = make_world(tmp_path, verdict="skip")
    run(w, after_close(250))
    assert [o.ticker for o in state(w, SESSIONS[250], "rules-only").orders] == ["AAA"]
    assert state(w, SESSIONS[250], "rules+ai").orders == []


def test_incomplete_latest_day_waits_then_does_not_trade(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[251])})
    run(w, after_close(250))
    clock, refetched = Clock(after_close(251)), []
    [result] = run(w, clock.now, wait=True, refetch=refetched.append, sleep=clock.sleep)
    assert result.status == "no trading"
    assert f"no SPY price for {SESSIONS[251]:%Y-%m-%d}" in result.report
    assert result.alerts == [result.report]
    assert refetched == [["SPY"]] * 8
    assert clock.t == after_close(251) + pd.Timedelta(hours=2)
    assert w.store.paper_states(SESSIONS[251]) == w.store.paper_states(SESSIONS[250])
    [next_day] = run(w, after_close(252))
    assert next_day.status == "traded"
    assert "AAA" in state(w, SESSIONS[252]).positions  # the waiting order filled


def test_late_data_is_used_when_it_arrives(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[251])})
    run(w, after_close(250))
    clock, refetched = Clock(after_close(251)), []

    def refetch(tickers):
        refetched.append(tickers)
        w.store.replace_prices("SPY", spy_bars(), "2026-10-02")

    [result] = run(w, clock.now, wait=True, refetch=refetch, sleep=clock.sleep)
    assert result.status == "traded" and len(refetched) == 1


def test_catch_up_leaves_an_incomplete_latest_day_for_later(tmp_path):
    w = make_world(tmp_path, frames={"AAA": aaa_bars(), "SPY": spy_bars().drop(SESSIONS[251])})
    run(w, after_close(250))
    assert run(w, after_close(251), wait=False) == []
    assert w.store.latest_paper_day() == SESSIONS[250]


class FakeNews:
    def fetch(self, ticker, start, end):
        midnight = SESSIONS[250].tz_localize("America/New_York")
        return [
            Headline(midnight + pd.Timedelta(hours=19), "Late", "After the run", ""),
            Headline(midnight + pd.Timedelta(hours=9), "Early", "Before the run", ""),
        ]


def test_reviews_only_see_news_from_before_the_run(tmp_path):
    w = make_world(tmp_path, news=FakeNews())
    run(w, after_close(250))
    [item] = w.reviewer.items
    assert [h.headline for h in item.headlines] == ["Before the run"]


class FakeTelegram:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    def send(self, text):
        if self.fail:
            raise TelegramError("network down")
        self.sent.append(text)


def test_reports_are_marked_sent_only_when_telegram_works(tmp_path):
    w = make_world(tmp_path)
    results = run(w, after_close(250))
    assert send_results(w.store, results, FakeTelegram(fail=True)) == ["network down"]
    assert w.store.daily_run(SESSIONS[250])["sent"] is False
    telegram = FakeTelegram()
    assert send_results(w.store, results, telegram) == []
    assert telegram.sent == [results[0].report]
    assert w.store.daily_run(SESSIONS[250])["sent"] is True


def test_long_catch_up_sends_one_summary(tmp_path):
    w = make_world(tmp_path)
    run(w, after_close(250))
    results = run(w, after_close(255))
    telegram = FakeTelegram()
    send_results(w.store, results, telegram)
    assert len(results) == 5
    assert telegram.sent[0].startswith("Caught up 4 missed trading days")
    assert telegram.sent[1] == results[-1].report
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_daily.py -q`
Expected: FAIL with `No module named 'market_agent.daily'`.

- [ ] **Step 3: Implement `src/market_agent/daily.py`**

```python
"""The daily paper-trading run and catch-up (spec section 8)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from market_agent.broker import Simulator
from market_agent.checks import DayCheck, check_day
from market_agent.data.sources import NewsSource, Profile
from market_agent.earnings import EarningsCalendar
from market_agent.notify import TelegramError
from market_agent.panel import Panel
from market_agent.paper import PORTFOLIOS, gone_lookup, restate
from market_agent.portfolio import Portfolio, portfolio_from_json, portfolio_to_json
from market_agent.report import PortfolioLine, build_report, fills_on
from market_agent.reviewer import Review, ReviewInput
from market_agent.sessions import TradingCalendar
from market_agent.settings import Settings
from market_agent.store import Store
from market_agent.strategy import Candidate, screen
from market_agent.trading import trade_day
from market_agent.universe import Universe, load_profiles

# News published after 18:00 New York time on day D could not have been seen by D's run.
NEWS_CUTOFF_HOURS = 18
SUMMARY_AFTER = 3  # a catch-up of more days than this sends one summary, not every report


@dataclass(frozen=True)
class DayResult:
    day: pd.Timestamp
    status: str  # "traded" | "no trading"
    report: str
    alerts: list[str]


@dataclass(frozen=True)
class Market:
    panel: Panel
    sessions: list[pd.Timestamp]
    universe: Universe
    earnings: EarningsCalendar
    profiles: dict[str, Profile]


def load_market(store: Store, settings: Settings, sessions: list[pd.Timestamp]) -> Market:
    """Everything in the cache up to the last session given. Days are processed one by one,
    and every step reads only data up to its own day, so later days do not leak in."""
    frames = {t: f for t in store.tickers_with_prices() if (f := store.load_prices(t)) is not None}
    return Market(
        panel=Panel(
            frames, pd.DatetimeIndex(sessions), settings.strategy, settings.data.max_daily_jump
        ),
        sessions=list(sessions),
        universe=Universe.from_csv(Path(settings.data.membership_csv)),
        earnings=EarningsCalendar(store.load_earnings(), list(sessions)),
        profiles=load_profiles(Path(settings.data.sectors_csv)),
    )


def check_market(market: Market, settings: Settings, day: pd.Timestamp) -> DayCheck:
    return check_day(
        market.panel,
        market.universe.members(day),
        day,
        [d for d in market.sessions if d <= day],
        settings.data.benchmark,
        settings.paper.max_missing_share,
        settings.paper.delisted_after_days,
    )


def review_warnings(reviews: Mapping[str, Review], late: bool) -> list[str]:
    out = []
    missed = [r for r in reviews.values() if r.status == "not reviewed"]
    failed = [r for r in reviews.values() if r.status == "failed"]
    if missed:
        out.append(f"{len(missed)} not reviewed ({missed[0].note}); treated as approved")
    if failed:
        out.append(f"{len(failed)} reviews failed; flagged and still bought by rules+ai")
    if late and reviews:
        out.append("Reviewed late (catch-up): these verdicts did not exist before the orders")
    return out


class DailyRun:
    def __init__(self, settings: Settings, store: Store, reviewer: Any, news: NewsSource):
        """reviewer: anything with review(ReviewInput) -> Review."""
        self.settings = settings
        self.store = store
        self.reviewer = reviewer
        self.news = news

    def _previous_states(self) -> dict[str, str]:
        previous = self.store.latest_paper_day()
        if previous is not None:
            return self.store.paper_states(previous)
        cash = self.settings.risk.starting_cash
        return {name: portfolio_to_json(Portfolio(name, cash)) for name in PORTFOLIOS}

    def process(self, market: Market, day: pd.Timestamp, check: DayCheck, late: bool) -> DayResult:
        s = self.settings
        states = self._previous_states()
        if not check.ok:
            text = (
                f"No trading on {day:%Y-%m-%d}: {check.reason}. "
                "Orders placed earlier wait for the next trading day."
            )
            self.store.save_day(day, states, "no trading", text, [text])
            return DayResult(day, "no trading", text, [text])

        portfolios = {name: portfolio_from_json(states[name]) for name in PORTFOLIOS}
        warnings: list[str] = []
        alerts: list[str] = []
        if check.excluded:
            warnings.append(
                f"{len(check.excluded)} shares had no usable price and were left out today: "
                + ", ".join(check.excluded)
            )
        candidates = screen(
            day,
            market.panel.snapshot(day),
            market.universe.members(day),
            market.earnings,
            s.strategy,
        )
        reviews = self._review(market, day, candidates[: s.ai.max_reviews_per_day], late, warnings)
        warnings += review_warnings(reviews, late)
        if any(r.note.startswith("monthly cap") for r in reviews.values()):
            alerts.append(
                f"Claude's monthly cap of US${s.ai.monthly_cap:.2f} is reached: candidates are "
                "not reviewed until next month."
            )

        sim = Simulator(s.risk, s.strategy)  # halts on the circuit breaker
        sessions = [d for d in market.sessions if d <= day]
        last_day = gone_lookup(market.panel, sessions, s.paper.delisted_after_days)
        sectors = {ticker: p.sector for ticker, p in market.profiles.items()}
        fills: list[str] = []
        for name, portfolio in portfolios.items():
            warnings += [f"{name}: {note}" for note in restate(portfolio, market.panel.bar)]
            was_halted = portfolio.halted
            taken = candidates
            if name == "rules+ai":
                taken = [c for c in candidates if self._verdict(reviews, c) != "skip"]
            trade_day(sim, portfolio, day, market.panel, last_day, taken, sectors, s)
            fills += fills_on(portfolio, day)
            if portfolio.halted and not was_halted:
                alerts.append(
                    f"{name}: {portfolio.events[-1]}. No new positions until "
                    f"`agent reset-breaker {name}`."
                )

        first = self.store.first_paper_day() or day
        spy_return = self._spy_return(market, first, day)
        lines = [
            PortfolioLine(
                name, p.equity_history[-1][1], s.risk.starting_cash, len(p.positions), p.halted
            )
            for name, p in portfolios.items()
        ]
        report = build_report(day, lines, spy_return, first, candidates, reviews, fills, warnings)
        new_states = {name: portfolio_to_json(p) for name, p in portfolios.items()}
        self.store.save_day(day, new_states, "traded", report, alerts)
        return DayResult(day, "traded", report, alerts)

    @staticmethod
    def _verdict(reviews: Mapping[str, Review], candidate: Candidate) -> str:
        review = reviews.get(candidate.ticker)
        return review.verdict if review is not None else "approve"

    def _spy_return(self, market: Market, first: pd.Timestamp, day: pd.Timestamp) -> float:
        benchmark = self.settings.data.benchmark
        start, end = market.panel.bar(benchmark, first), market.panel.bar(benchmark, day)
        return end.close / start.close - 1 if start and end else 0.0

    def _review(
        self,
        market: Market,
        day: pd.Timestamp,
        candidates: list[Candidate],
        late: bool,
        warnings: list[str],
    ) -> dict[str, Review]:
        if not candidates:
            return {}
        ai = self.settings.ai
        snap = market.panel.snapshot(day)
        cutoff = (day + pd.Timedelta(hours=NEWS_CUTOFF_HOURS)).tz_localize("America/New_York")
        start = (day - pd.Timedelta(days=ai.news_days)).date()
        reviews = {}
        for c in candidates:
            try:
                found = self.news.fetch(c.ticker, start, day.date())
            except Exception as exc:
                warnings.append(f"News for {c.ticker} unavailable ({exc})")
                found = []
            headlines = [h for h in found if h.published <= cutoff][: ai.max_headlines]
            profile = market.profiles.get(c.ticker, Profile("Unknown", ""))
            row = snap.loc[c.ticker]
            item = ReviewInput(
                ticker=c.ticker,
                company=profile.name or c.ticker,
                sector=profile.sector,
                day=day,
                close=c.close,
                sma_fast=float(row["sma_fast"]),
                sma_slow=float(row["sma_slow"]),
                volume_ratio=c.volume_ratio,
                atr=c.atr,
                return_63=c.score,
                next_earnings=market.earnings.next_report(c.ticker, day),
                headlines=headlines,
            )
            review = self.reviewer.review(item)
            self.store.save_review(day, review, late)
            reviews[c.ticker] = review
        return reviews


def pending_days(store: Store, calendar: TradingCalendar, now: pd.Timestamp) -> list[pd.Timestamp]:
    """Closed sessions not processed yet. The very first run starts with the latest one."""
    latest = calendar.latest_closed(now)
    if latest is None:
        return []
    last = store.latest_paper_day()
    return [latest] if last is None else calendar.between(last, latest)


def run_days(
    settings: Settings,
    store: Store,
    calendar: TradingCalendar,
    runner: DailyRun,
    now: Callable[[], pd.Timestamp],
    wait: bool,
    refetch: Callable[[list[str]], None],
    sleep: Callable[[float], None],
) -> list[DayResult]:
    """Process every pending day in order. Only the latest day can be waited for; with
    wait=False an incomplete latest day is left for the next run (its data may still come)."""
    days = pending_days(store, calendar, now())
    if not days:
        return []
    deadline = now() + pd.Timedelta(hours=settings.paper.retry_hours)
    every = settings.paper.retry_every_minutes
    market = load_market(store, settings, calendar.up_to(days[-1]))
    results = []
    for day in days:
        latest = day == days[-1]
        check = check_market(market, settings, day)
        while not check.ok and latest and wait and now() < deadline:
            print(
                f"Data for {day:%Y-%m-%d} is incomplete ({check.reason}); "
                f"trying again in {every:g} minutes"
            )
            sleep(every * 60)
            refetch([*check.excluded, settings.data.benchmark])
            market = load_market(store, settings, calendar.up_to(days[-1]))
            check = check_market(market, settings, day)
        if not check.ok and latest and not wait:
            print(
                f"Data for {day:%Y-%m-%d} is not complete yet ({check.reason}); "
                "the next run will process it."
            )
            break
        results.append(runner.process(market, day, check, late=not latest))
    return results


def send_results(store: Store, results: list[DayResult], telegram: Any) -> list[str]:
    """Send the reports and alerts; marks each report sent. Returns what went wrong."""
    messages: list[tuple[pd.Timestamp | None, str]] = []
    shown = results
    if len(results) > SUMMARY_AFTER:
        missed = ", ".join(f"{r.day:%Y-%m-%d}" for r in results[:-1])
        messages.append(
            (
                None,
                f"Caught up {len(results) - 1} missed trading days ({missed}). Their "
                "reports are saved: `agent report --day YYYY-MM-DD`.",
            )
        )
        shown = results[-1:]
    messages += [(r.day, r.report) for r in shown]
    messages += [(None, alert) for r in results for alert in r.alerts if alert != r.report]
    for day, text in messages:
        try:
            telegram.send(text)
        except TelegramError as exc:
            return [str(exc)]
        if day is not None:
            store.mark_sent(day)
    return []
```

Note: a "no trading" day's alert is the report itself, so it is sent once (the `alert !=
r.report` filter).

- [ ] **Step 4: Run the tests**

Run: `pytest -q`
Expected: all pass. If `test_catch_up_matches_on_time_runs` fails, compare the two JSON
states: any difference means some step read data beyond its day.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent/daily.py tests/test_daily.py
git commit -m "Add the daily paper-trading run with catch-up and data waiting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Commands, docs and the breaker reset

**Files:**
- Modify: `src/market_agent/cli.py`, `src/market_agent/paper.py`,
  `src/market_agent/data/cache.py`, `README.md`, `CLAUDE.md`
- Test: `tests/test_cli.py`, `tests/test_store_cache.py`

**Interfaces:**
- Consumes: `run_days`, `send_results`, `DailyRun` (11), `Reviewer`, `ClaudeModel` (9),
  `Telegram`, `NoTelegram` (10), `FinnhubNews`, `NoNews` (8), `TradingCalendar` (3).
- Produces: commands `agent run-daily`, `agent catch-up`, `agent reset-breaker <portfolio>`,
  `agent report [--day YYYY-MM-DD]`; factories in `cli` replaced in tests:
  `trading_calendar(settings)`, `news_source()`, `claude_model(settings)`, `telegram()`,
  `now()`; `PaperError`, `reset_breaker(store, name) -> str` in `paper.py`;
  `PriceCache.update(ticker, start, force=False)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store_cache.py`:

```python
def test_price_cache_force_downloads_again(store):
    source = FakePrices({"AAPL": make_bars([10, 11])})
    cache = PriceCache(store, source, today=lambda: date(2026, 10, 2))
    cache.update("AAPL", date(2014, 1, 1))
    cache.update("AAPL", date(2014, 1, 1), force=True)
    assert len(source.calls) == 2
```

Add to `tests/test_cli.py` (imports: `from market_agent.data.finnhub import NoNews`,
`from market_agent.portfolio import portfolio_from_json, portfolio_to_json`,
`from market_agent.sessions import TradingCalendar`):

```python
SESSIONS = list(pd.bdate_range("2020-06-01", periods=N))


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def daily_setup(tmp_path, monkeypatch, now_index=255):
    setup(tmp_path, monkeypatch)
    telegram = FakeTelegram()
    closes = [(d + pd.Timedelta(hours=20)).tz_localize("UTC") for d in SESSIONS]
    monkeypatch.setattr(cli, "trading_calendar", lambda settings: TradingCalendar(SESSIONS, closes))
    monkeypatch.setattr(cli, "now", lambda: closes[now_index] + pd.Timedelta(hours=2))
    monkeypatch.setattr(cli, "news_source", NoNews)
    monkeypatch.setattr(cli, "claude_model", lambda settings: None)
    monkeypatch.setattr(cli, "telegram", lambda: telegram)
    return telegram


def test_run_daily_trades_reports_and_sends(tmp_path, monkeypatch, capsys):
    telegram = daily_setup(tmp_path, monkeypatch)
    day = f"{SESSIONS[255]:%Y-%m-%d}"
    assert cli.main(["run-daily"]) == 0
    assert f"{day}: traded" in capsys.readouterr().out
    assert telegram.sent[0].startswith(f"Market agent, {day}")
    assert cli.main(["run-daily"]) == 0
    assert "Nothing to do" in capsys.readouterr().out
    assert cli.main(["report"]) == 0
    assert capsys.readouterr().out.startswith(f"Market agent, {day}")
    assert cli.main(["catch-up"]) == 0


def test_report_before_any_run(tmp_path, monkeypatch, capsys):
    daily_setup(tmp_path, monkeypatch)
    assert cli.main(["report"]) == 1
    assert "No saved report" in capsys.readouterr().err


def test_reset_breaker(tmp_path, monkeypatch, capsys):
    daily_setup(tmp_path, monkeypatch)
    cli.main(["run-daily"])
    assert cli.main(["reset-breaker", "rules+ai"]) == 1
    assert "The circuit breaker is not on for rules+ai" in capsys.readouterr().err
    store = Store(tmp_path / "data" / "market.db")
    day = store.latest_paper_day()
    p = portfolio_from_json(store.paper_states(day)["rules+ai"])
    p.halted = p.breaker_tripped = True
    store.replace_paper_state("rules+ai", day, portfolio_to_json(p))
    store.close()
    assert cli.main(["reset-breaker", "rules+ai"]) == 0
    store = Store(tmp_path / "data" / "market.db")
    p = portfolio_from_json(store.paper_states(day)["rules+ai"])
    store.close()
    assert not p.halted and p.peak == p.equity_history[-1][1]
    assert p.events[-1].endswith("circuit breaker reset by the owner at equity 10,000")
```

- [ ] **Step 2: Run them to see them fail**

Run: `pytest tests/test_cli.py tests/test_store_cache.py -q`
Expected: FAIL (unexpected keyword `force`; `invalid choice: 'run-daily'`; no attribute
`trading_calendar`).

- [ ] **Step 3: Implement**

`data/cache.py`, `PriceCache.update`:

```python
    def update(self, ticker: str, start: date, force: bool = False) -> bool:
        """Make sure the cache holds `ticker` up to today. Returns False if there is no data.

        force: download again even if it was downloaded today (the day's data was late).
        """
        today = self._today()
        stamp = today.isoformat()
        if not force and self._store.fetched_on(ticker) == stamp:
            return self._store.load_prices(ticker) is not None
```

(rest unchanged).

`paper.py`, append (import `Store`, `portfolio_from_json`, `portfolio_to_json`):

```python
class PaperError(Exception):
    """A paper-trading command can't be done; the message says why."""


def reset_breaker(store: Store, name: str) -> str:
    """The owner's reset (spec section 5): the current equity becomes the new peak."""
    day = store.latest_paper_day()
    if day is None:
        raise PaperError("Paper trading has not started yet: run `agent run-daily` first.")
    p = portfolio_from_json(store.paper_states(day)[name])
    if not p.halted:
        raise PaperError(f"The circuit breaker is not on for {name}.")
    equity = p.equity_history[-1][1] if p.equity_history else p.cash
    p.halted = False
    p.breaker_tripped = False
    p.peak = equity
    p.events.append(f"{day:%Y-%m-%d}: circuit breaker reset by the owner at equity {equity:,.0f}")
    store.replace_paper_state(name, day, portfolio_to_json(p))
    return f"Circuit breaker reset for {name}; new positions are allowed from the next run."
```

`cli.py`: update the module docstring to list the new commands, add imports (`os`,
`datetime`, `TradingCalendar`, `DailyRun`, `run_days`, `send_results`, `FinnhubNews`, `NoNews`,
`Reviewer`, `ClaudeModel`, `Telegram`, `NoTelegram`, `PORTFOLIOS`, `PaperError`,
`reset_breaker`), then add the factories next to the existing ones:

```python
def trading_calendar(settings: Settings) -> TradingCalendar:
    start = pd.Timestamp(settings.data.history_start)
    return TradingCalendar.nyse(start, pd.Timestamp.today().normalize() + pd.Timedelta(days=7))


def news_source():
    key = os.environ.get("FINNHUB_API_KEY")
    return FinnhubNews(key) if key else NoNews()


def claude_model(settings: Settings):
    return ClaudeModel(settings.ai) if os.environ.get("ANTHROPIC_API_KEY") else None


def telegram():
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    return Telegram(token, chat) if token and chat else NoTelegram()


def now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")
```

The parser gains:

```python
    commands.add_parser(
        "run-daily", help="trade the latest US trading day (and any missed ones) on paper"
    )
    commands.add_parser("catch-up", help="process missed trading days without waiting for data")
    reset = commands.add_parser("reset-breaker", help="turn a portfolio's circuit breaker off")
    reset.add_argument("portfolio", choices=PORTFOLIOS)
    report = commands.add_parser("report", help="show a saved daily report")
    report.add_argument("--day", type=date.fromisoformat, help="YYYY-MM-DD (default: latest)")
```

and the dispatch:

```python
        if args.command == "run-daily":
            return daily_command(settings, store, wait=True)
        if args.command == "catch-up":
            return daily_command(settings, store, wait=False)
        if args.command == "reset-breaker":
            return reset_command(store, args.portfolio)
        if args.command == "report":
            return report_command(store, args.day)
```

The command functions:

```python
def refetch(settings: Settings, store: Store, tickers: list[str]) -> None:
    prices = PriceCache(store, price_source())
    for ticker in tickers:
        try:
            with_retries(
                ticker, lambda t=ticker: prices.update(t, settings.data.history_start, True)
            )
        except Exception as exc:
            log.warning("%s: download failed again (%s)", ticker, exc)


def month_start() -> datetime:
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def daily_command(settings: Settings, store: Store, wait: bool) -> int:
    if fetch(settings, store) != 0:
        print("Some downloads failed; the data check decides whether the day can be traded.")
    reviewer = Reviewer(
        claude_model(settings), settings.ai, lambda: store.ai_spent_since(month_start())
    )
    runner = DailyRun(settings, store, reviewer, news_source())
    results = run_days(
        settings,
        store,
        trading_calendar(settings),
        runner,
        now=now,
        wait=wait,
        refetch=lambda tickers: refetch(settings, store, tickers),
        sleep=pause,
    )
    if not results:
        print("Nothing to do: every closed trading day is already processed.")
        return 0
    for result in results:
        print(f"{result.day:%Y-%m-%d}: {result.status}")
    print()
    print(results[-1].report)
    for problem in send_results(store, results, telegram()):
        print(f"Telegram: {problem}. The report is saved; see `agent report`.", file=sys.stderr)
    return 0


def reset_command(store: Store, name: str) -> int:
    try:
        print(reset_breaker(store, name))
    except PaperError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def report_command(store: Store, day: date | None) -> int:
    run = store.daily_run(pd.Timestamp(day)) if day else store.latest_daily_run()
    if run is None:
        print("No saved report for that day. Run `agent run-daily` first.", file=sys.stderr)
        return 1
    print(run["report"])
    if not run["sent"]:
        print("\n(not sent to Telegram)")
    return 0
```

Note `now=now` and `sleep=pause` are looked up when `daily_command` runs, so tests that
replace `cli.now` / `cli.pause` take effect.

`README.md`: add after "Use":

````markdown
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
````

`CLAUDE.md`: under Architecture, add a bullet:

```markdown
- **Paper trading** (`daily.py`): `run_days` processes each closed NYSE session not yet in
  `paper_states`, in order (catch-up), and only the latest day may wait for late data.
  `DailyRun.process` checks the data (`checks.py`), screens, has Claude review the top
  candidates (`reviewer.py`, cost-capped), then per portfolio runs `paper.restate` (Yahoo
  re-adjusts history after splits/dividends) and `trading.trade_day`, the same step the backtest
  uses. Each day saves a JSON snapshot per portfolio plus the report in one transaction. Paper
  portfolios halt on the circuit breaker until `agent reset-breaker`. News for day D is cut off
  at 18:00 New York time so catch-up runs see what an on-time run would have.
```

and under Commands, the four new commands.

- [ ] **Step 4: Run the tests**

Run: `pytest -q` and `ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add src/market_agent tests README.md CLAUDE.md
git commit -m "Add run-daily, catch-up, reset-breaker and report commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Schedule it and do the first real run

**Files:**
- Create: `scripts/schedule-daily.ps1`
- Modify: `.gitignore`, `README.md`

**Interfaces:**
- Consumes: `agent run-daily` (Task 12).
- Produces: a Windows scheduled task "Market agent daily run" (06:30 Tue–Sat, runs late if the
  computer was off), appending output to `data\daily.log`.

- [ ] **Step 1: Write the script**

`scripts/schedule-daily.ps1`:

```powershell
# Registers the daily paper-trading run: 06:30 Tuesday-Saturday (after each US trading day,
# Malaysia time). If the computer is off then, Windows runs it as soon as it is back on, and
# `agent run-daily` catches up any missed days. Run once from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\schedule-daily.ps1
$repo = Split-Path -Parent $PSScriptRoot
$command = "set PYTHONUTF8=1&& `"$repo\.venv\Scripts\agent.exe`" run-daily >> `"$repo\data\daily.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $command" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday, Wednesday, Thursday, Friday, Saturday -At 06:30
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 4)
Register-ScheduledTask -TaskName "Market agent daily run" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Output "Registered 'Market agent daily run' (06:30 Tue-Sat). Log: $repo\data\daily.log"
```

`.gitignore`: add `data/*.log`.

`README.md`, in "Paper trading", add:

```markdown
To run it every trading day by itself (06:30 Tuesday–Saturday Malaysia time, or as soon as the
computer is back on), run once:
`powershell -ExecutionPolicy Bypass -File scripts\schedule-daily.ps1`. Output goes to
`data\daily.log`. Remove it with `Unregister-ScheduledTask "Market agent daily run"`.
```

- [ ] **Step 2: First real run, by hand**

Run: `.venv\Scripts\agent.exe run-daily`
Expected: the fetch (about 15 minutes), then `YYYY-MM-DD: traded` for the latest closed US
session, the report printed, and the same report on the owner's phone. Check the stored
review cost: `.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/market.db'); print(c.execute('select ticker, status, verdict, cost from reviews').fetchall())"`.
If there were candidates, each has status `reviewed` and a cost around US$0.01–0.06.

- [ ] **Step 3: Register and test the scheduled task**

Run: `powershell -ExecutionPolicy Bypass -File scripts\schedule-daily.ps1`
Then: `Start-ScheduledTask "Market agent daily run"`, wait a minute, and read
`data\daily.log`: it ends with "Nothing to do: every closed trading day is already processed."

- [ ] **Step 4: Commit**

```bash
git add scripts/schedule-daily.ps1 .gitignore README.md
git commit -m "Schedule the daily run with Windows Task Scheduler

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
