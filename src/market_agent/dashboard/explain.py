"""Plain-English explanations for the dashboard: how the portfolios compare with each other
and SPY, and why each share was bought or sold.

The signal behind a buy isn't saved, so it is rebuilt from the cached prices of the signal day.
Yahoo re-adjusts past prices after dividends, which is why the figures say "about".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from market_agent.dashboard.paper import equity_curves, latest_portfolios
from market_agent.indicators import add_indicators
from market_agent.paper import PORTFOLIOS
from market_agent.portfolio import Portfolio, Trade, portfolio_from_json
from market_agent.settings import Settings, StrategySettings
from market_agent.store import Store

NAMES = {"rules-only": "rules-only", "rules+ai": "rules + AI"}
TOO_FEW_DAYS = 20


@dataclass(frozen=True)
class Explanation:
    ticker: str
    title: str
    text: str


def plain_day(day: pd.Timestamp) -> str:
    return f"{day.day} {day:%b %Y}"


def _name(portfolio: str) -> str:
    return NAMES.get(portfolio, portfolio)


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _money(value: float) -> str:
    return f"{'-' if value < 0 else '+'}${abs(value):,.2f}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _who(holders: list[str]) -> str:
    return "both portfolios" if len(holders) > 1 else _name(holders[0])


# --- the comparison ---------------------------------------------------------------------------


def _portfolio_line(portfolio: Portfolio) -> str:
    trades = portfolio.trades
    won = sum(1 for t in trades if t.pnl > 0)
    line = (
        f"{_name(portfolio.name)}: {_plural(len(portfolio.positions), 'open position')}, "
        f"{_plural(len(trades), 'closed trade')} ({won} won)"
    )
    if trades:
        best = max(trades, key=_trade_return)
        worst = min(trades, key=_trade_return)
        if _trade_return(best) > 0:
            line += f", best {best.ticker} {_trade_return(best):+.1%}"
        if _trade_return(worst) < 0:
            line += f", worst {worst.ticker} {_trade_return(worst):+.1%}"
    return line


def comparison_summary(store: Store, settings: Settings) -> str | None:
    """Markdown: each portfolio and SPY since paper trading began, who leads, what Claude's
    reviews changed, then one line per portfolio. None before the first paper day."""
    curves = equity_curves(store, settings)
    if curves.empty:
        return None
    start = settings.risk.starting_cash
    results = []
    for column in curves.columns:
        values = curves[column].dropna()
        if not values.empty:
            results.append((column, float(values.iloc[-1]), float(values.iloc[-1]) / start - 1))
    days = len(curves.index)
    shown = ", ".join(
        f"{_name(name)} **${value:,.0f} ({ret:+.1%})**" for name, value, ret in results
    )
    sentences = [f"Since {plain_day(curves.index[0])} ({_plural(days, 'trading day')}): {shown}."]

    ranked = sorted(results, key=lambda r: r[2], reverse=True)
    if len(ranked) > 1:
        gap = 100 * (ranked[0][2] - ranked[1][2])
        if round(gap, 1) == 0:
            sentences.append(
                f"{_capital(_name(ranked[0][0]))} and {_name(ranked[1][0])} are level."
            )
        else:
            sentences.append(
                f"{_capital(_name(ranked[0][0]))} is ahead, {gap:.1f} points above "
                f"{_name(ranked[1][0])}."
            )

    values = {name: value for name, value, _ in results}
    if "rules-only" in values and "rules+ai" in values:
        difference = values["rules+ai"] - values["rules-only"]
        if abs(difference) < 0.5:
            sentences.append("So far Claude's reviews have made no difference.")
        else:
            verb = "added" if difference > 0 else "cost"
            sentences.append(
                f"So far Claude's reviews have {verb} ${abs(difference):,.0f} compared with the "
                "rules alone."
            )
    skipped = sorted({r["ticker"] for r in store.all_reviews() if r["verdict"] == "skip"})
    if skipped:
        sentences.append(
            f"Claude has skipped {_plural(len(skipped), 'candidate')} ({', '.join(skipped)})."
        )
    if days < TOO_FEW_DAYS:
        sentences.append(f"That is under {TOO_FEW_DAYS} trading days, too few to judge.")

    lines = [f"- {_portfolio_line(p)}" for p in latest_portfolios(store).values()]
    return " ".join(sentences) + "\n\n" + "\n".join(lines)


# --- why a share was bought --------------------------------------------------------------------


def _reviews(store: Store) -> dict[tuple[pd.Timestamp, str], dict[str, Any]]:
    """The latest review of each ticker on each day."""
    found: dict[tuple[pd.Timestamp, str], dict[str, Any]] = {}
    for r in store.all_reviews():  # newest first
        found.setdefault((pd.Timestamp(r["day"]), r["ticker"]), r)
    return found


def _verdict(review: dict[str, Any]) -> str:
    reasons = "; ".join(review["reasons"] or [])
    because = f": {reasons}" if reasons else ""
    confidence = f" ({review['confidence']} confidence)" if review["confidence"] else ""
    risks = "; ".join(review.get("risks") or [])
    noted = f" Risks it noted: {risks}." if risks else ""
    verdict = review["verdict"]
    if verdict == "approve":
        return f"Claude approved it{confidence}{because}."
    if verdict == "skip":
        return f"Claude skipped it{confidence}{because}.{noted}"
    if verdict == "flag":
        return (
            f"Claude flagged it for a closer look{confidence}, which doesn't stop a "
            f"purchase{because}.{noted}"
        )
    return "Claude's review didn't complete, so it was treated as approved."


def _signal_day(store: Store, ticker: str, entry_day: pd.Timestamp) -> pd.Timestamp | None:
    bars = store.load_prices(ticker)
    if bars is None:
        return None
    before = bars.index[bars.index < entry_day]
    return before[-1] if len(before) else None


def signal_text(store: Store, settings: Settings, ticker: str, day: pd.Timestamp | None) -> str:
    """What the breakout rule saw on the signal day, from today's cached prices."""
    bars = store.load_prices(ticker)
    if day is None or bars is None or day not in bars.index:
        return "The prices from the signal day are no longer cached, so the signal can't be shown."
    s = settings.strategy
    row = add_indicators(bars, s).loc[day]
    close = row["close"]
    parts = [f"On {plain_day(day)} it closed at about ${close:,.2f}"]
    if close >= row["high_close"]:
        parts.append(f"its highest close in {s.breakout_days} days")
    else:
        parts.append(f"below its {s.breakout_days}-day high of about ${row['high_close']:,.2f}")
    fast, slow = row["sma_fast"], row["sma_slow"]
    if pd.notna(fast) and pd.notna(slow):
        rel_fast = "above" if close > fast else "below"
        rel_slow = "above" if close > slow else "below"
        slow_side = "" if rel_fast == rel_slow else f"{rel_slow} its "
        parts.append(
            f"{rel_fast} its {s.sma_fast}-day average (about ${fast:,.2f}) and "
            f"{slow_side}{s.sma_slow}-day average (about ${slow:,.2f})"
        )
    if row["avg_volume_prev"] > 0:
        parts.append(f"on {row['volume'] / row['avg_volume_prev']:.1f}× its usual volume")
    text = ", ".join(parts) + "."
    if pd.isna(fast) or pd.isna(slow):
        text += (
            f" Not enough price history to show the {s.sma_fast}-day and {s.sma_slow}-day averages."
        )
    if pd.notna(row["ret_rank"]):
        change = "risen" if row["ret_rank"] >= 0 else "fallen"
        moved = abs(row["ret_rank"])
        text += f" Over the {s.rank_days} trading days before, it had {change} {moved:.0%}."
    return text


def _ai_text(holders: list[str], review: dict[str, Any] | None, planned: bool = False) -> str:
    """What Claude's review meant for rules + AI, given which portfolios bought the share."""
    did, didnt = ("will buy", "won't buy") if planned else ("bought", "didn't buy")
    if "rules+ai" in holders:
        prefix = "" if len(holders) > 1 or planned else "Rules-only didn't buy it. "
        if review is None:
            return f"{prefix}Claude didn't review it, so rules + AI treated it as approved."
        return f"{prefix}For rules + AI, {_verdict(review)}"
    if review is not None and review["verdict"] == "skip":
        return f"Rules + AI {didnt} it: {_verdict(review)}"
    if planned:
        return ""
    return f"Rules + AI {didnt} it; Claude didn't skip it, so a position or risk limit stopped it."


def _exit_plan(settings: Settings, stop: float, target: float) -> str:
    s = settings.strategy
    return (
        f"Stop ${stop:,.2f}, target ${target:,.2f}, set {s.stop_atr:g} and {s.target_atr:g} "
        "times the share's average daily range (ATR) from the entry price. If neither is "
        f"reached, it's sold after {s.max_hold_days} trading days."
    )


def position_explanations(store: Store, settings: Settings) -> list[Explanation]:
    """One explanation per open position; a share both portfolios hold appears once."""
    groups: dict[tuple, list[str]] = {}
    positions = {}
    for name, portfolio in latest_portfolios(store).items():
        for pos in portfolio.positions.values():
            key = (pos.ticker, pos.entry_day, round(pos.entry_price, 4))
            groups.setdefault(key, []).append(name)
            positions.setdefault(key, pos)
    reviews = _reviews(store)
    out = []
    for key in sorted(groups, key=lambda k: (k[1], k[0]), reverse=True):
        pos, holders = positions[key], groups[key]
        day = _signal_day(store, pos.ticker, pos.entry_day)
        review = reviews.get((day, pos.ticker)) if day is not None else None
        change = pos.last_close / pos.entry_price - 1
        signal = signal_text(store, settings, pos.ticker, day)
        why = " ".join(part for part in (signal, _ai_text(holders, review)) if part)
        status = (
            f"Held {pos.days_held} of {settings.strategy.max_hold_days} trading days; "
            f"last close ${pos.last_close:,.2f} ({change:+.1%})."
        )
        text = f"{why}\n\n{_exit_plan(settings, pos.stop, pos.target)} {status}"
        title = (
            f"{pos.ticker}, bought {plain_day(pos.entry_day)} at ${pos.entry_price:,.2f} "
            f"({_who(holders)})"
        )
        out.append(Explanation(pos.ticker, title, text))
    return out


# --- why a share was sold ----------------------------------------------------------------------


def _trade_return(trade: Trade) -> float:
    return trade.pnl / (trade.entry_price * trade.shares)


def _exit_text(settings: Settings, reason: str) -> str:
    return {
        "stop": "The price fell to the stop set when it was bought.",
        "target": "The price reached the target set when it was bought.",
        "time": (
            f"{settings.strategy.max_hold_days} trading days passed without reaching the stop "
            "or the target."
        ),
        "delisted": "The share stopped trading, so it was closed at its last price.",
    }.get(reason, f"Sold ({reason}).")


def trade_explanations(store: Store, settings: Settings) -> list[Explanation]:
    """One explanation per closed trade, newest first; a trade both portfolios made appears once."""
    groups: dict[tuple, list[str]] = {}
    trades = {}
    for name, portfolio in latest_portfolios(store).items():
        for t in portfolio.trades:
            key = (t.ticker, t.entry_day, t.exit_day, round(t.exit_price, 4))
            groups.setdefault(key, []).append(name)
            trades.setdefault(key, t)
    out = []
    for key in sorted(groups, key=lambda k: (k[2], k[0]), reverse=True):
        t, holders = trades[key], groups[key]
        day = _signal_day(store, t.ticker, t.entry_day)
        text = (
            f"{_exit_text(settings, t.exit_reason)} "
            f"Result: {_money(t.pnl)} ({_trade_return(t):+.1%}).\n\n"
            f"Bought {plain_day(t.entry_day)} at ${t.entry_price:,.2f}. "
            f"{signal_text(store, settings, t.ticker, day)}"
        )
        title = (
            f"{t.ticker}, sold {plain_day(t.exit_day)} at ${t.exit_price:,.2f} ({_who(holders)})"
        )
        out.append(Explanation(t.ticker, title, text))
    return out


# --- orders for the next open ------------------------------------------------------------------


def order_explanations(store: Store, settings: Settings, day: pd.Timestamp) -> list[str]:
    """One markdown line per order placed on `day` for the next open; an order both portfolios
    placed appears once."""
    states = store.paper_states(day)
    groups: dict[tuple, list[str]] = {}
    for name in PORTFOLIOS:
        if name in states:
            for o in portfolio_from_json(states[name]).orders:
                key = (o.side, o.ticker, o.shares, o.reason, pd.Timestamp(o.created))
                groups.setdefault(key, []).append(name)
    reviews = _reviews(store)
    lines = []
    for (side, ticker, shares, reason, created), holders in groups.items():
        both = len(holders) > 1
        who = "Both portfolios" if both else _name(holders[0])
        verb = side if both else f"{side}s"
        if side == "buy":
            signal = signal_text(store, settings, ticker, created)
            ai = _ai_text(holders, reviews.get((created, ticker)), planned=True)
            why = " ".join(part for part in (signal, ai) if part)
        elif reason == "time":
            why = (
                f"held {settings.strategy.max_hold_days} trading days without reaching the "
                "stop or the target, so it is sold at the next open."
            )
        else:
            why = f"{reason}."
        lines.append(f"**{who} {verb} {shares} {ticker}:** {why}")
    return lines


# --- how the agent works -----------------------------------------------------------------------


def _number(value: float) -> str:
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}".rstrip("0").rstrip(".")


def _screening_table(s: StrategySettings) -> str:
    rows = [
        (
            "Breakout",
            f"it closes at its highest close of the last {s.breakout_days} days",
            "the price is pushing to new highs",
        ),
        (
            "Uptrend",
            f"it closes above its {s.sma_slow}-day average, and its {s.sma_fast}-day average "
            f"is above its {s.sma_slow}-day average",
            "only buy shares already in a longer uptrend",
        ),
        (
            "Volume",
            f"that day's volume is at least {s.volume_ratio:g}× its average of the previous "
            f"{s.volume_days} days",
            "the move has real buying behind it",
        ),
        ("Price", f"it closes above ${_number(s.min_price)}", "no very cheap shares"),
        (
            "Liquidity",
            f"it trades over ${s.min_traded_value / 1e6:,.0f} million a day on average",
            "easy to buy and sell",
        ),
        (
            "Earnings",
            f"no earnings report in the next {s.earnings_buffer_days} trading days",
            "avoids surprise jumps on results",
        ),
    ]
    lines = ["| Rule | What it checks | Why |", "|---|---|---|"]
    return "\n".join(lines + [f"| {rule} | {check} | {why} |" for rule, check, why in rows])


def how_it_works(settings: Settings) -> str:
    """Markdown: the rules both portfolios follow, with every number taken from the settings."""
    s, r = settings.strategy, settings.risk
    cash = r.starting_cash
    loss = r.risk_per_trade * cash
    cap = r.max_position_pct * cash
    return f"""\
Both portfolios follow the same rules. Rules + AI adds one step: Claude reviews each candidate
before it is bought (see the last section).

### 1. Screening: which shares qualify

Each evening after the US market closes, the agent checks every share that was in the S&P 500
that day. A share becomes a **candidate** only if all of these hold:

{_screening_table(s)}

Candidates are ranked so that the shares that rose most over the last {s.rank_days} trading
days come first.

### 2. Limits: which candidates are bought

Going down the ranked list, a candidate is passed over when:

- the portfolio already holds it;
- {r.max_positions} positions are already open;
- {r.max_per_sector} positions are already open in its sector;
- {s.max_new_per_day} new buys are already planned that day;
- the circuit breaker is on: the portfolio fell {r.breaker_drawdown:.0%} below its peak, so it
  buys nothing new until the owner resets it (`agent reset-breaker`).

### 3. How many shares

The number of shares is the smallest of three:

- **Risk:** if the stop is hit, lose at most ${_number(loss)} ({r.risk_per_trade:.0%} of
  ${_number(cash)}). The stop sits {s.stop_atr:g} ATR below the entry; ATR is the share's
  average daily price range over {s.atr_days} days.
- **Size cap:** one position is at most ${_number(cap)} ({r.max_position_pct:.0%}) of the
  portfolio.
- **Cash:** what the portfolio has left.

These amounts use the starting ${_number(cash)}; as the portfolio's value changes, they change
with it.

### 4. The buy and the exits

- The order is placed after the close and filled at the **next day's opening price**, never
  at a price the agent had already seen.
- Every buy and sell pays {r.slippage:.1%} slippage and a ${_number(r.commission)} commission.
- At the fill, the **stop** is set {s.stop_atr:g} ATR below the opening price and the
  **target** {s.target_atr:g} ATR above it.

The share is sold when the price falls to the stop, when it reaches the target, or when
{s.max_hold_days} trading days pass without either (then at the next open).

### Claude's review (rules + AI only)

| Verdict | Meaning | Rules + AI | Rules-only |
|---|---|---|---|
| **approve** | no reason to avoid it | buys | buys |
| **flag** | a concern worth noting, not enough to block it | still buys | buys |
| **skip** | it should be left out, e.g. a pending takeover | doesn't buy | buys |

Confidence (low, medium, high) shows how sure Claude was; it doesn't change what happens. When
the monthly cost cap is reached or there is no API key, the candidate isn't reviewed and counts
as approved. When the review fails, it counts as flagged.
"""
