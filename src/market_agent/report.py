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
    orders: Sequence[str],
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
    if orders:
        out += ["", "Orders for the next open:", *(f"- {order}" for order in orders)]
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


def orders_for_next_open(p: Portfolio) -> list[str]:
    """Orders placed after today's close, to be filled at the next session's open."""
    return [f"{p.name} {o.side}s {o.shares:g} {o.ticker} ({o.reason})" for o in p.orders]
