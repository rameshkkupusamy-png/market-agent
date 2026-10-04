"""Reaches Finnhub; needs FINNHUB_API_KEY in the environment or .env. Run: pytest -m live"""

import os
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv

from market_agent.data.finnhub import FinnhubNews

pytestmark = pytest.mark.live


def test_real_company_news():
    load_dotenv(".env")
    end = date.today()
    news = FinnhubNews(os.environ["FINNHUB_API_KEY"]).fetch("AAPL", end - timedelta(days=7), end)
    assert news and news[0].headline
    assert news[0].published >= news[-1].published
