import sqlite3
import subprocess
import urllib.error
from contextlib import closing

import pandas as pd
import pytest

from helpers import dashboard_db, make_bars
from market_agent.dashboard.explain import position_explanations
from market_agent.dashboard.paper import equity_curves
from market_agent.dashboard.snapshot import (
    SnapshotError,
    download_snapshot,
    export_snapshot,
    publish_snapshot,
    snapshot_time,
)
from market_agent.settings import Settings
from market_agent.store import Store

S = Settings()
TAKEN = pd.Timestamp("2026-10-07 06:42", tz="UTC")


def rows(db, sql):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def source(tmp_path):
    db = dashboard_db(tmp_path / "market.db")
    store = Store(db)
    store.replace_prices("ZZZ", make_bars([10.0] * 30), "2026-09-25")  # never on the dashboard
    store.close()
    return db


def test_export_keeps_only_the_prices_the_dashboard_reads(source, tmp_path):
    out = tmp_path / "dashboard.db"
    result = export_snapshot(source, out, "SPY", now=TAKEN)
    kept = {t for (t,) in rows(out, "SELECT DISTINCT ticker FROM prices")}
    assert kept == {"AAA", "SPY"}  # held, reviewed, ordered or traded, plus the benchmark
    assert result.tickers == ["AAA", "BBB", "CCC", "SPY"]
    assert result.size == out.stat().st_size
    for table in ("paper_states", "daily_runs", "reviews", "backtests", "backtest_equity"):
        assert rows(out, f"SELECT COUNT(*) FROM {table}") == rows(
            source, f"SELECT COUNT(*) FROM {table}"
        )
    assert rows(out, "SELECT COUNT(*) FROM earnings") == [(0,)]
    assert snapshot_time(out) == TAKEN


def test_dashboard_reads_an_export_like_the_full_database(source, tmp_path):
    out = tmp_path / "dashboard.db"
    export_snapshot(source, out, "SPY", now=TAKEN)
    full, small = Store(source, read_only=True), Store(out, read_only=True)
    pd.testing.assert_frame_equal(equity_curves(small, S), equity_curves(full, S))
    assert position_explanations(small, S) == position_explanations(full, S)
    full.close()
    small.close()


def test_export_replaces_the_previous_snapshot_without_leftovers(source, tmp_path):
    out = tmp_path / "dashboard.db"
    out.write_bytes(b"old snapshot")
    export_snapshot(source, out, "SPY", now=TAKEN)
    assert snapshot_time(out) == TAKEN
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dashboard.db", "market.db"]


def test_snapshot_time_of_a_full_database_is_unknown(source):
    assert snapshot_time(source) is None


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_publish_force_pushes_one_commit_and_leaves_the_checkout_alone(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    git("init", "--bare", str(origin), cwd=tmp_path)
    git("init", str(work), cwd=tmp_path)
    git("remote", "add", "origin", str(origin), cwd=work)
    snapshot = work / "dashboard.db"
    snapshot.write_bytes(b"day one")
    publish_snapshot(snapshot, work)
    snapshot.write_bytes(b"day two")
    publish_snapshot(snapshot, work)
    assert git("rev-list", "--count", "dashboard-data", cwd=origin) == "1"
    assert git("show", "dashboard-data:dashboard.db", cwd=origin) == "day two"
    assert git("status", "--porcelain", "--untracked-files=no", cwd=work) == ""


def test_publish_reports_a_failed_push(tmp_path):
    work = tmp_path / "work"
    git("init", str(work), cwd=tmp_path)  # no remote
    snapshot = work / "dashboard.db"
    snapshot.write_bytes(b"day one")
    with pytest.raises(SnapshotError, match="push"):
        publish_snapshot(snapshot, work)


class FakeGet:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_download_fetches_the_snapshot_branch(tmp_path):
    get = FakeGet(b"snapshot bytes")
    dest = tmp_path / "cache" / "dashboard.db"
    assert download_snapshot("owner/repo", "tok", dest, get=get) == dest
    assert dest.read_bytes() == b"snapshot bytes"
    [(url, headers)] = get.calls
    assert url == "https://api.github.com/repos/owner/repo/contents/dashboard.db?ref=dashboard-data"
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Accept"] == "application/vnd.github.raw"


def test_download_failure_is_explained(tmp_path):
    missing = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
    with pytest.raises(SnapshotError, match="404"):
        download_snapshot("owner/repo", "tok", tmp_path / "d.db", get=FakeGet(missing))
    assert not (tmp_path / "d.db").exists()
