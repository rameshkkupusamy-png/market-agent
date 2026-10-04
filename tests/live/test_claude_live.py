"""Calls Claude (costs about US$0.05); needs ANTHROPIC_API_KEY. Run: pytest -m live"""

import pytest
from dotenv import load_dotenv

from market_agent.reviewer import ClaudeModel, Reviewer
from market_agent.settings import AiSettings
from tests.test_reviewer import ITEM

pytestmark = pytest.mark.live


def test_real_review():
    load_dotenv(".env")
    review = Reviewer(ClaudeModel(AiSettings()), AiSettings(), lambda: 0.0).review(ITEM)
    assert review.status == "reviewed", review.answer
    assert review.verdict in ("approve", "skip", "flag")
    assert 0 < review.cost < 0.2
