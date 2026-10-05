import pandas as pd

from market_agent.portfolio import Order, Portfolio, Position, Trade
from market_agent.report import PortfolioLine, build_report, fills_on, orders_for_next_open
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
        ["rules-only buys 8 XOM (breakout)"],
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
        "Orders for the next open:\n"
        "- rules-only buys 8 XOM (breakout)\n"
        "\n"
        "Warnings:\n"
        "- 1 share had no usable price"
    )


def test_quiet_day_report():
    text = build_report(
        DAY,
        [PortfolioLine("rules-only", 10_000.0, 10_000.0, 0, False)],
        0.0,
        DAY,
        [],
        {},
        [],
        [],
        [],
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


def test_orders_for_the_next_open():
    p = Portfolio("rules+ai", 5_000.0)
    p.orders.append(Order("EXPD", "buy", 5, "breakout", DAY))
    p.orders.append(Order("NTAP", "sell", 4, "time", DAY))
    assert orders_for_next_open(p) == [
        "rules+ai buys 5 EXPD (breakout)",
        "rules+ai sells 4 NTAP (time)",
    ]
