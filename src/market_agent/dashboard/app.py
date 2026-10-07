"""Read-only dashboard (spec section 9). Started by `agent dashboard`, which sets
MARKET_AGENT_DB and MARKET_AGENT_CONFIG; run on its own it uses data/market.db."""

import os
import sqlite3
from pathlib import Path

import streamlit as st

from market_agent.dashboard.backtests import backtest_curve, backtest_runs, backtest_settings
from market_agent.dashboard.display import as_percent, dates_only, value_chart
from market_agent.dashboard.explain import (
    comparison_summary,
    order_explanations,
    position_explanations,
    trade_explanations,
)
from market_agent.dashboard.paper import (
    closed_trades,
    equity_curves,
    open_positions,
    performance,
    today,
)
from market_agent.dashboard.reviews import review_table, what_if
from market_agent.settings import Settings, load_settings
from market_agent.store import Store

PAGES = ("Overview", "Today", "Positions and trades", "AI review", "Backtest")
NO_PAPER = "No paper trading yet. Run `agent run-daily` to start both portfolios."


def md(text: str) -> str:
    """Escape dollar signs, which Streamlit would otherwise render as LaTeX."""
    return text.replace("$", "\\$")


def explanations(items) -> None:
    for item in items:
        with st.expander(md(item.title)):
            st.markdown(md(item.text))


def overview(store: Store, settings: Settings) -> None:
    st.header("Overview")
    curves = equity_curves(store, settings)
    if curves.empty:
        st.info(NO_PAPER)
        return
    with st.container(border=True):
        st.markdown(md(comparison_summary(store, settings) or ""))
    st.caption(
        f"Value of each paper portfolio since {curves.index[0]:%Y-%m-%d}, against the same "
        f"US${settings.risk.starting_cash:,.0f} in {settings.data.benchmark}."
    )
    st.altair_chart(value_chart(curves))
    st.subheader("Performance")
    columns = ["total_return", "max_drawdown", "win_rate", "avg_win", "avg_loss"]
    st.dataframe(as_percent(performance(store, settings), columns))


def today_page(store: Store, settings: Settings) -> None:
    st.header("Today")
    days = store.paper_days()
    if not days:
        st.info(NO_PAPER)
        return
    day = st.selectbox("Day", list(reversed(days)), format_func=lambda d: f"{d:%Y-%m-%d}")
    view = today(store, day)
    if view is None:
        st.info("No report saved for that day.")
        return
    sent = "sent to Telegram" if view.sent else "not sent to Telegram"
    st.caption(f"Status: {view.status}; report {sent}.")
    for alert in view.alerts:
        st.warning(alert)
    st.subheader("Candidates and AI review")
    if view.reviews.empty:
        st.write("No candidates were reviewed that day.")
    else:
        st.dataframe(dates_only(view.reviews), hide_index=True)
    st.subheader("Orders for the next open")
    if view.orders.empty:
        st.write("No orders.")
    else:
        st.dataframe(view.orders, hide_index=True)
        st.markdown("**Why**")
        for line in order_explanations(store, settings, view.day):
            st.markdown(md(f"- {line}"))
    st.subheader("Report")
    st.code(view.report, language=None)


def positions_page(store: Store, settings: Settings) -> None:
    st.header("Positions and trades")
    st.subheader("Open positions")
    positions = open_positions(store)
    if positions.empty:
        st.write("No open positions.")
    else:
        st.dataframe(dates_only(positions), hide_index=True)
        st.caption("Why each share was bought")
        explanations(position_explanations(store, settings))
    st.subheader("Closed trades")
    trades = closed_trades(store)
    if trades.empty:
        st.write("No closed trades yet.")
    else:
        st.dataframe(dates_only(as_percent(trades, ["return"])), hide_index=True)
        st.caption("Why each share was sold")
        explanations(trade_explanations(store, settings))


def reviews_page(store: Store, settings: Settings) -> None:
    st.header("AI review")
    table = review_table(store)
    if table.empty:
        st.info("No Claude reviews yet.")
        return
    st.caption(f"{len(table)} reviews, US${table['cost'].sum():.2f} spent in total.")
    st.dataframe(dates_only(table), hide_index=True)
    st.subheader("What if the skipped candidates had been bought")
    st.caption(
        "Each candidate Claude skipped, traded by the rules alone: bought at the next open, "
        "same stop, target and time exit."
    )
    skipped = what_if(store, settings)
    if skipped.empty:
        st.write("Claude has not skipped any candidate yet.")
    else:
        st.dataframe(dates_only(as_percent(skipped, ["return"])), hide_index=True)


def backtest_page(store: Store, settings: Settings) -> None:
    st.header("Backtest")
    runs = backtest_runs(store)
    if runs.empty:
        st.info("No backtests yet. Run `agent backtest --period tuning`.")
        return
    columns = [
        "total_return",
        "cagr",
        "max_drawdown",
        "win_rate",
        "spy_total_return",
        "spy_cagr",
        "spy_max_drawdown",
    ]
    st.dataframe(dates_only(as_percent(runs, columns)), hide_index=True)
    labels = {
        r["id"]: f"#{r['id']} {r['period']}, {r['start']:%Y-%m-%d} to {r['end']:%Y-%m-%d}"
        for r in runs.to_dict("records")
    }
    run_id = st.selectbox("Run", list(labels), format_func=labels.get)
    st.altair_chart(value_chart(backtest_curve(store, settings, run_id)))
    st.subheader("Settings used")
    st.json(backtest_settings(store, run_id))


def main() -> None:
    st.set_page_config(page_title="Market agent", layout="wide")
    st.sidebar.title("Market agent")
    st.sidebar.caption("Paper trading with simulated money. This dashboard only reads.")
    if snapshot_taken := os.environ.get("MARKET_AGENT_SNAPSHOT"):  # set by the hosted app
        st.sidebar.caption(f"Data as of {snapshot_taken}")
    page = st.sidebar.radio("Page", PAGES)
    db = Path(os.environ.get("MARKET_AGENT_DB", "data/market.db"))
    if not db.exists():
        st.error(
            f"No database at {db}. Start the dashboard from the market-agent folder "
            "with `agent dashboard`."
        )
        return
    settings = load_settings(Path(os.environ.get("MARKET_AGENT_CONFIG", "config.yaml")))
    store = Store(db, read_only=True)
    try:
        pages = {
            "Overview": overview,
            "Today": today_page,
            "Positions and trades": positions_page,
            "AI review": reviews_page,
            "Backtest": backtest_page,
        }
        pages[page](store, settings)
    except sqlite3.OperationalError:
        # A daily run is writing, or one was interrupted mid-write (a read-only connection
        # can't roll that back); the next `agent` command repairs it.
        st.error(
            "The database is being written or was left mid-write by an interrupted run. "
            "Wait a minute and refresh; if it persists, run `agent report` once to repair it."
        )
    finally:
        store.close()


main()
