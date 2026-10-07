"""The dashboard on Streamlit Community Cloud.

Downloads the snapshot that `agent publish-dashboard` pushes to the dashboard-data branch (kept
for an hour), then runs the normal dashboard over it. Secrets (app settings → Secrets):
    github_token = "..."   # fine-grained token: this repo, Contents read-only
    github_repo = "owner/market-agent"
"""

import os
import runpy
import tempfile
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

import market_agent.dashboard
from market_agent.dashboard import snapshot

APP = Path(market_agent.dashboard.__file__).parent / "app.py"
SHOWN_IN = "Asia/Kuala_Lumpur"  # where the owner reads the "Data as of" time


@st.cache_data(ttl=3600, show_spinner="Downloading the latest data…")
def latest_snapshot(repo: str, token: str) -> str:
    cache = Path(os.environ.get("MARKET_AGENT_CACHE", Path(tempfile.gettempdir()) / "market-agent"))
    dest = snapshot.download_snapshot(repo, token, cache / snapshot.FILE, get=snapshot.http_get)
    return str(dest)


def secret(name: str) -> str | None:
    try:
        return st.secrets.get(name)
    except StreamlitSecretNotFoundError:  # no secrets at all
        return None


def main() -> None:
    token, repo = secret("github_token"), secret("github_repo")
    if not token or not repo:
        st.error(
            "Add `github_token` and `github_repo` in the app's settings under Secrets. "
            "The token needs read-only access to this repo's contents."
        )
        return
    try:
        db = latest_snapshot(repo, token)
    except snapshot.SnapshotError as exc:
        st.error(f"Couldn't download the dashboard data. {exc}")
        return
    os.environ["MARKET_AGENT_DB"] = db
    taken = snapshot.snapshot_time(Path(db))
    if taken is not None:
        local = taken.tz_convert(SHOWN_IN)
        os.environ["MARKET_AGENT_SNAPSHOT"] = f"{local.day} {local:%b %Y, %H:%M}"
    runpy.run_path(str(APP), run_name="__main__")


main()
