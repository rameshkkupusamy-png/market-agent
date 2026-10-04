"""Sends a real message; needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID. Run: pytest -m live"""

import os

import pytest
from dotenv import load_dotenv

from market_agent.notify import Telegram

pytestmark = pytest.mark.live


def test_real_message():
    load_dotenv(".env")
    Telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]).send(
        "Market agent: live test message"
    )
