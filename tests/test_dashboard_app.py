import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import market_agent.dashboard
from helpers import dashboard_db
from market_agent.store import Store

APP = Path(market_agent.dashboard.__file__).parent / "app.py"
PAGES = ("Overview", "Today", "Positions and trades", "AI review", "Backtest")


def open_page(monkeypatch, db, page=None):
    monkeypatch.setenv("MARKET_AGENT_DB", str(db))
    monkeypatch.setenv("MARKET_AGENT_CONFIG", str(Path(db).parent / "no-config.yaml"))
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    if page is not None:
        at.sidebar.radio[0].set_value(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders(tmp_path, monkeypatch, page):
    at = open_page(monkeypatch, dashboard_db(tmp_path / "market.db"), page)
    assert not at.exception
    assert at.header[0].value == page


def test_today_shows_the_saved_report(tmp_path, monkeypatch):
    at = open_page(monkeypatch, dashboard_db(tmp_path / "market.db"), "Today")
    assert any(code.value.startswith("Market agent, 2026-09-25") for code in at.code)
    assert any("circuit breaker" in w.value for w in at.warning)


@pytest.mark.parametrize("page", PAGES)
def test_pages_before_any_paper_run(tmp_path, monkeypatch, page):
    Store(tmp_path / "market.db").close()  # an empty database: no prices, runs or reviews
    at = open_page(monkeypatch, tmp_path / "market.db", page)
    assert not at.exception
    assert at.header[0].value == page


def test_missing_database_is_explained(tmp_path, monkeypatch):
    db = tmp_path / "elsewhere" / "market.db"
    at = open_page(monkeypatch, db)
    assert not at.exception
    assert "No database at" in at.error[0].value
    assert not db.parent.exists()


def test_dashboard_never_changes_the_database(tmp_path, monkeypatch):
    db = dashboard_db(tmp_path / "market.db")
    before = db.read_bytes()
    for page in PAGES:
        open_page(monkeypatch, db, page)
    assert db.read_bytes() == before


def test_database_busy_or_mid_write_is_explained(tmp_path, monkeypatch):
    import market_agent.dashboard.paper as paper

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(paper, "equity_curves", locked)
    at = open_page(monkeypatch, dashboard_db(tmp_path / "market.db"), "Overview")
    assert not at.exception
    assert "being written" in at.error[0].value
