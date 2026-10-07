from types import SimpleNamespace

import pandas as pd
import pytest

from helpers import make_bars, settings_with
from market_agent.dashboard.explain import (
    comparison_summary,
    order_explanations,
    position_explanations,
    trade_explanations,
)
from market_agent.portfolio import Order, Portfolio, Position, Trade, portfolio_to_json
from market_agent.settings import Settings
from market_agent.store import Store

# Short windows so 30 days of prices are enough for every indicator.
S = settings_with(
    strategy={
        "sma_fast": 5,
        "sma_slow": 10,
        "breakout_days": 5,
        "volume_days": 5,
        "atr_days": 5,
        "rank_days": 10,
    }
)
DAYS = pd.bdate_range("2026-09-01", periods=30)


def plain(day):
    return f"{day.day} {day:%b %Y}"


def review(ticker, verdict, confidence, reasons):
    return SimpleNamespace(
        ticker=ticker,
        status="reviewed",
        verdict=verdict,
        confidence=confidence,
        reasons=reasons,
        risks=[],
        news_used=[],
        note="",
        cost=0.01,
        model="claude-opus-5-5",
        prompt="",
        answer="{}",
    )


def rising(start, volume_spike_at=None):
    volumes = [1_000_000.0] * len(DAYS)
    if volume_spike_at is not None:
        volumes[volume_spike_at] = 2_000_000.0
    return make_bars([start + i for i in range(len(DAYS))], volumes, start=str(DAYS[0].date()))


@pytest.fixture
def store(tmp_path):
    """NTAP broke out on DAYS[25] (close 125, 2x volume); both portfolios bought it on DAYS[26].
    Claude approved NTAP, skipped DDD (rules-only bought it alone) and skipped CCC today."""
    path = tmp_path / "market.db"
    s = Store(path)
    s.replace_prices("NTAP", rising(100, volume_spike_at=25), "2026-10-12")
    s.replace_prices("DDD", rising(40), "2026-10-12")
    s.replace_prices("CCC", rising(60), "2026-10-12")
    s.replace_prices("SPY", rising(400), "2026-10-12")
    ntap = Position("NTAP", "Tech", 4, DAYS[26], 126.0, 120.0, 138.0, 129.0, 3, DAYS[29])
    ddd = Position("DDD", "Energy", 10, DAYS[27], 67.0, 64.0, 73.0, 68.0, 2, DAYS[29])
    rules = Portfolio(
        "rules-only",
        8_000.0,
        positions={"NTAP": ntap, "DDD": ddd},
        orders=[
            Order("CCC", "buy", 5, "breakout", DAYS[29], 2.0, "Tech", ref_close=89.0),
            Order("NTAP", "buy", 2, "breakout", DAYS[29], 2.0, "Tech", ref_close=129.0),
        ],
        trades=[Trade("BBB", "Energy", DAYS[5], 50.0, DAYS[9], 48.0, 20, "stop", -40.0)],
        equity_history=[(DAYS[27], 10_050.0), (DAYS[29], 10_100.0)],
        peak=10_100.0,
    )
    ai = Portfolio(
        "rules+ai",
        9_000.0,
        positions={"NTAP": ntap},
        orders=[
            Order("OLD", "sell", 3, "time", DAYS[29]),
            Order("NTAP", "buy", 2, "breakout", DAYS[29], 2.0, "Tech", ref_close=129.0),
        ],
        trades=[Trade("XYZ", "Tech", DAYS[2], 30.0, DAYS[22], 33.0, 10, "time", 30.0)],
        equity_history=[(DAYS[27], 10_000.0), (DAYS[29], 10_020.0)],
        peak=10_020.0,
    )
    states = {p.name: portfolio_to_json(p) for p in (rules, ai)}
    s.save_day(DAYS[29], states, "traded", "report", [])
    s.save_review(DAYS[25], review("NTAP", "approve", "medium", ["Strong demand"]), late=False)
    s.save_review(DAYS[26], review("DDD", "skip", "high", ["Too volatile"]), late=False)
    s.save_review(DAYS[29], review("CCC", "skip", "high", ["Lawsuit"]), late=False)
    flag = review("NTAP", "flag", "medium", ["Strong trend"])
    flag.risks = ["Extended above its average"]
    s.save_review(DAYS[29], flag, late=False)
    s.close()
    ro = Store(path, read_only=True)
    yield ro
    ro.close()


@pytest.fixture
def empty(tmp_path):
    Store(tmp_path / "empty.db").close()
    s = Store(tmp_path / "empty.db", read_only=True)
    yield s
    s.close()


def by_ticker(explanations):
    return {e.ticker: e for e in explanations}


def test_summary_compares_both_portfolios_with_spy(store):
    text = comparison_summary(store, S)
    assert f"Since {plain(DAYS[27])} (2 trading days)" in text
    assert "rules-only **$10,100 (+1.0%)**" in text
    assert "rules + AI **$10,020 (+0.2%)**" in text
    assert "SPY **$10,047 (+0.5%)**" in text  # 400+29 against 400+27
    assert "Rules-only is ahead, 0.5 points above SPY." in text
    assert "So far Claude's reviews have cost $80 compared with the rules alone." in text
    assert "Claude has skipped 2 candidates (CCC, DDD)." in text
    assert "too few to judge" in text


def test_summary_lists_each_portfolio(store):
    text = comparison_summary(store, S)
    assert "rules-only: 2 open positions, 1 closed trade (0 won)" in text
    assert "rules + AI: 1 open position, 1 closed trade (1 won), best XYZ +10.0%" in text


def test_summary_without_paper_trading(empty):
    assert comparison_summary(empty, S) is None


def test_position_held_by_both_explains_signal_review_and_exits(store):
    ntap = by_ticker(position_explanations(store, S))["NTAP"]
    assert ntap.title == f"NTAP, bought {plain(DAYS[26])} at $126.00 (both portfolios)"
    assert (
        f"On {plain(DAYS[25])} it closed at about $125.00, its highest close in 5 days" in ntap.text
    )
    assert "above its 5-day average (about $123.00) and 10-day average (about $120.50)" in ntap.text
    assert "on 2.0× its usual volume" in ntap.text
    assert "Claude approved it (medium confidence): Strong demand." in ntap.text
    assert "Stop $120.00, target $138.00" in ntap.text
    assert "sold after 20 trading days" in ntap.text
    assert "Held 3 of 20 trading days; last close $129.00 (+2.4%)." in ntap.text


def test_position_held_by_rules_only_explains_the_skip(store):
    ddd = by_ticker(position_explanations(store, S))["DDD"]
    assert ddd.title.endswith("(rules-only)")
    assert (
        "Rules + AI didn't buy it: Claude skipped it (high confidence): Too volatile." in ddd.text
    )


def test_signal_without_enough_history_says_so(store):
    ntap = by_ticker(position_explanations(store, Settings()))["NTAP"]
    assert "Not enough price history to show the 50-day and 200-day averages." in ntap.text


def test_trades_say_why_they_were_sold(store):
    trades = by_ticker(trade_explanations(store, S))
    assert trades["BBB"].title == f"BBB, sold {plain(DAYS[9])} at $48.00 (rules-only)"
    assert "The price fell to the stop set when it was bought." in trades["BBB"].text
    assert "Result: -$40.00 (-4.0%)." in trades["BBB"].text
    assert "20 trading days passed without reaching the stop or the target." in (trades["XYZ"].text)
    assert "Result: +$30.00 (+10.0%)." in trades["XYZ"].text


def test_orders_for_the_next_open_are_explained(store):
    lines = order_explanations(store, S, DAYS[29])
    [ccc] = [line for line in lines if "CCC" in line]
    assert ccc.startswith("**rules-only buys 5 CCC:**")
    assert f"On {plain(DAYS[29])} it closed at about $89.00" in ccc
    assert "Rules + AI won't buy it: Claude skipped it (high confidence): Lawsuit." in ccc
    [old] = [line for line in lines if "OLD" in line]
    assert old == (
        "**rules + AI sells 3 OLD:** held 20 trading days without reaching the stop or the "
        "target, so it is sold at the next open."
    )


def test_nothing_to_explain_without_paper_trading(empty):
    assert position_explanations(empty, S) == []
    assert trade_explanations(empty, S) == []
    assert order_explanations(empty, S, DAYS[29]) == []


def test_an_order_both_portfolios_placed_is_explained_once(store):
    lines = order_explanations(store, S, DAYS[29])
    [ntap] = [line for line in lines if "NTAP" in line]
    assert ntap.startswith("**Both portfolios buy 2 NTAP:**")
    assert (
        "For rules + AI, Claude flagged it for a closer look (medium confidence), which doesn't "
        "stop a purchase: Strong trend. Risks it noted: Extended above its average." in ntap
    )
