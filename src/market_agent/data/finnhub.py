"""Company news from Finnhub's free tier: headlines and summaries. Personal use only."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from market_agent.data.sources import Headline

API = "https://finnhub.io/api/v1/company-news"


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


class FinnhubNews:
    def __init__(self, api_key: str, get_json: Callable[[str], Any] = _get_json):
        self._key = api_key
        self._get_json = get_json

    def fetch(self, ticker: str, start: date, end: date) -> list[Headline]:
        query = urllib.parse.urlencode(
            {"symbol": ticker, "from": start.isoformat(), "to": end.isoformat(), "token": self._key}
        )
        items = self._get_json(f"{API}?{query}") or []
        headlines = [
            Headline(
                published=pd.Timestamp(item["datetime"], unit="s", tz="UTC"),
                source=item.get("source", ""),
                headline=item["headline"].strip(),
                summary=(item.get("summary") or "").strip(),
            )
            for item in items
            if (item.get("headline") or "").strip()
        ]
        return sorted(headlines, key=lambda h: h.published, reverse=True)


class NoNews:
    """Used when no news key is set: reviews then say there is no relevant news."""

    def fetch(self, ticker: str, start: date, end: date) -> list[Headline]:
        return []
