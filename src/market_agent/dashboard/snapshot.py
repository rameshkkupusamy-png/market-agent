"""A small copy of the database for the hosted dashboard, and how it travels.

`agent publish-dashboard` exports the snapshot (the full database minus the prices and earnings
the dashboard never reads) and force-pushes it as the only commit of the `dashboard-data` branch,
so the repo's history doesn't grow. The hosted app (`streamlit_app.py`) downloads it from there.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from market_agent.portfolio import portfolio_from_json
from market_agent.store import Store

BRANCH = "dashboard-data"
FILE = "dashboard.db"
# The snapshot commit is data, not code: give it a fixed author so it works on any computer.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Market agent",
    "GIT_AUTHOR_EMAIL": "market-agent@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "Market agent",
    "GIT_COMMITTER_EMAIL": "market-agent@users.noreply.github.com",
}

Getter = Callable[[str, dict[str, str]], bytes]


class SnapshotError(Exception):
    """The snapshot couldn't be published or downloaded."""


@dataclass(frozen=True)
class ExportResult:
    tickers: list[str]  # the shares whose prices were kept
    size: int  # bytes


def dashboard_tickers(store: Store, benchmark: str) -> list[str]:
    """Every share the dashboard can show: held, ordered or traded on any paper day, or reviewed."""
    tickers = {benchmark} | {r["ticker"] for r in store.all_reviews()}
    for day in store.paper_days():
        for state in store.paper_states(day).values():
            p = portfolio_from_json(state)
            tickers |= set(p.positions) | {o.ticker for o in p.orders}
            tickers |= {t.ticker for t in p.trades}
    return sorted(tickers)


def _read_only(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(db).resolve().as_uri()}?mode=ro", uri=True)


def export_snapshot(
    db: Path, out: Path, benchmark: str, now: pd.Timestamp | None = None
) -> ExportResult:
    """Write the snapshot to `out`, replacing it only once the new one is complete."""
    store = Store(db, read_only=True)
    try:
        tickers = dashboard_tickers(store, benchmark)
    finally:
        store.close()
    taken = (now or pd.Timestamp.now(tz="UTC")).tz_convert("UTC").isoformat()
    tmp = out.with_name(out.name + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        with closing(_read_only(db)) as src, closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst)
            marks = ", ".join("?" * len(tickers))
            with dst:
                dst.execute(f"DELETE FROM prices WHERE ticker NOT IN ({marks})", tickers)
                dst.execute(f"DELETE FROM price_meta WHERE ticker NOT IN ({marks})", tickers)
                dst.execute("DELETE FROM earnings")
                dst.execute("DELETE FROM earnings_meta")
                dst.execute("CREATE TABLE snapshot_info (taken_at TEXT)")
                dst.execute("INSERT INTO snapshot_info VALUES (?)", (taken,))
            dst.execute("VACUUM")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return ExportResult(tickers, out.stat().st_size)


def snapshot_time(db: Path) -> pd.Timestamp | None:
    """When the snapshot was taken (UTC); None for a full database."""
    with closing(_read_only(db)) as conn:
        try:
            row = conn.execute("SELECT taken_at FROM snapshot_info").fetchone()
        except sqlite3.OperationalError:
            return None
    return pd.Timestamp(row[0]) if row else None


def _git(step: str, args: list[str], cwd: Path, stdin: str | None = None) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=cwd,
            input=stdin,
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, **GIT_IDENTITY},
        )
    except subprocess.CalledProcessError as exc:
        raise SnapshotError(f"git {step} failed: {exc.stderr.strip()}") from exc
    return done.stdout.strip()


def publish_snapshot(snapshot: Path, repo: Path, remote: str = "origin") -> None:
    """Force-push `snapshot` as the single commit of the data branch. The checkout, its index
    and its branches are left alone."""
    blob = _git("hash-object", ["hash-object", "-w", str(snapshot)], repo)
    # NUL-terminated (-z): in text mode Windows would turn a newline into \r\n and the \r would
    # end up in the file name.
    tree = _git("mktree", ["mktree", "-z"], repo, stdin=f"100644 blob {blob}\t{FILE}\0")
    commit = _git("commit-tree", ["commit-tree", tree, "-m", "Dashboard snapshot"], repo)
    _git("push", ["push", "--force", "--quiet", remote, f"{commit}:refs/heads/{BRANCH}"], repo)


def http_get(url: str, headers: dict[str, str]) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        return r.read()


def download_snapshot(repo: str, token: str, dest: Path, get: Getter = http_get) -> Path:
    """Fetch the snapshot from the data branch of `repo` ("owner/name") into `dest`."""
    url = f"https://api.github.com/repos/{repo}/contents/{FILE}?ref={BRANCH}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw"}
    try:
        data = get(url, headers)
    except urllib.error.HTTPError as exc:
        raise SnapshotError(
            f"GitHub answered {exc.code} ({exc.reason}) for {repo}. Check that the token can "
            f"read the repo's contents and that `agent publish-dashboard` has pushed the "
            f"{BRANCH} branch."
        ) from exc
    except urllib.error.URLError as exc:
        raise SnapshotError(f"Couldn't reach GitHub ({exc.reason}).") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest
