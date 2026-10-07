"""streamlit_app.py: the dashboard as Streamlit Community Cloud runs it, over a snapshot it
downloads from the dashboard-data branch (faked here: no network)."""

import urllib.error
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from helpers import dashboard_db
from market_agent.dashboard import snapshot
from market_agent.dashboard.snapshot import export_snapshot

APP = Path(__file__).parents[1] / "streamlit_app.py"
TAKEN = pd.Timestamp("2026-10-06 22:42", tz="UTC")  # 7 Oct 06:42 in Kuala Lumpur


@pytest.fixture
def snapshot_bytes(tmp_path):
    out = tmp_path / "dashboard.db"
    export_snapshot(dashboard_db(tmp_path / "market.db"), out, "SPY", now=TAKEN)
    return out.read_bytes()


def hosted(monkeypatch, tmp_path, get, token):
    monkeypatch.setenv("MARKET_AGENT_CACHE", str(tmp_path / "cache"))
    for name in ("MARKET_AGENT_DB", "MARKET_AGENT_SNAPSHOT"):  # the app sets both
        monkeypatch.setenv(name, "")  # recorded, so they are restored after the test
        monkeypatch.delenv(name)
    monkeypatch.setattr(snapshot, "http_get", get)
    at = AppTest.from_file(str(APP), default_timeout=60)
    if token:
        at.secrets["github_token"] = token  # a different token per test: no shared cache
        at.secrets["github_repo"] = "owner/repo"
    return at.run()


def test_hosted_app_shows_the_downloaded_snapshot(tmp_path, monkeypatch, snapshot_bytes):
    calls = []

    def get(url, headers):
        calls.append(url)
        return snapshot_bytes

    at = hosted(monkeypatch, tmp_path, get, token="token-one")
    assert not at.exception
    assert at.header[0].value == "Overview"
    assert any(c.value == "Data as of 7 Oct 2026, 06:42" for c in at.sidebar.caption)
    assert calls == [
        "https://api.github.com/repos/owner/repo/contents/dashboard.db?ref=dashboard-data"
    ]


def test_hosted_app_explains_missing_secrets(tmp_path, monkeypatch):
    at = hosted(monkeypatch, tmp_path, get=None, token=None)
    assert not at.exception
    assert "github_token" in at.error[0].value


def test_hosted_app_explains_a_failed_download(tmp_path, monkeypatch):
    def refused(url, headers):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    at = hosted(monkeypatch, tmp_path, refused, token="token-two")
    assert not at.exception
    assert "401" in at.error[0].value
