from datetime import date

import pandas as pd

from market_agent.data.finnhub import FinnhubNews, NoNews


def test_finnhub_news_becomes_headlines_newest_first():
    urls = []

    def get_json(url):
        urls.append(url)
        return [
            {
                "datetime": 1759329000,
                "source": "Reuters",
                "headline": " Nvidia wins order ",
                "summary": "A large cloud order.",
                "url": "https://example.com/a",
            },
            {"datetime": 1759415400, "source": "CNBC", "headline": "Later story", "summary": ""},
            {"datetime": 1759415500, "source": "X", "headline": "", "summary": "no headline"},
        ]

    news = FinnhubNews("KEY", get_json).fetch("NVDA", date(2026, 9, 25), date(2026, 10, 2))
    assert [h.headline for h in news] == ["Later story", "Nvidia wins order"]
    assert news[1].published == pd.Timestamp(1759329000, unit="s", tz="UTC")
    assert (news[1].source, news[1].summary) == ("Reuters", "A large cloud order.")
    assert urls == [
        "https://finnhub.io/api/v1/company-news?symbol=NVDA&from=2026-09-25&to=2026-10-02&token=KEY"
    ]


def test_no_news():
    assert NoNews().fetch("NVDA", date(2026, 9, 25), date(2026, 10, 2)) == []
