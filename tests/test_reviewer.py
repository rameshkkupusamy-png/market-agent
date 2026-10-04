import json
from types import SimpleNamespace

import anthropic
import httpx2
import pandas as pd
import pytest

from market_agent.data.sources import Headline
from market_agent.reviewer import (
    SCHEMA,
    Answer,
    ClaudeModel,
    ModelUnavailable,
    Reviewer,
    ReviewInput,
    build_prompt,
)
from market_agent.settings import AiSettings

AI = AiSettings()
HEADLINE = Headline(
    pd.Timestamp("2026-10-01 14:30", tz="UTC"),
    "Reuters",
    "Nvidia wins order",
    "A large cloud order.",
)
ITEM = ReviewInput(
    ticker="NVDA",
    company="NVIDIA Corporation",
    sector="Technology",
    day=pd.Timestamp("2026-10-02"),
    close=187.62,
    sma_fast=175.4,
    sma_slow=150.25,
    volume_ratio=1.83,
    atr=5.1,
    return_63=0.214,
    next_earnings=pd.Timestamp("2026-11-19"),
    headlines=[HEADLINE],
)
VALID = json.dumps(
    {
        "verdict": "skip",
        "confidence": "medium",
        "reasons": ["Guidance cut"],
        "risks": [],
        "news_used": [0],
    }
)


class FakeModel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def ask(self, system, prompt):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Answer(reply, 1_000, 200, "fake-model")


def reviewer(model, spent=0.0):
    return Reviewer(model, AI, lambda: spent)


def test_prompt_holds_only_the_given_data():
    assert build_prompt(ITEM) == (
        "Candidate: NVDA (NVIDIA Corporation), sector Technology\n"
        "Day: 2026-10-02, after the close\n"
        "Close 187.62; 50-day average 175.40; 200-day average 150.25\n"
        "Breakout: highest close of the last 20 trading days, on 1.8 times average volume\n"
        "ATR(14) 5.10; 3-month return +21.4%\n"
        "Next earnings date: 2026-11-19\n"
        "\n"
        "Headlines, newest first:\n"
        "[0] 2026-10-01 Reuters: Nvidia wins order\n"
        "    A large cloud order."
    )


def test_prompt_without_news_or_earnings_date():
    item = ReviewInput(**{**ITEM.__dict__, "headlines": [], "next_earnings": None})
    text = build_prompt(item)
    assert "Next earnings date: not known" in text
    assert text.endswith("No recent headlines.")


def test_valid_answer_is_reviewed_with_its_cost():
    review = reviewer(FakeModel(VALID)).review(ITEM)
    assert (review.status, review.verdict, review.confidence) == ("reviewed", "skip", "medium")
    assert (review.reasons, review.risks, review.news_used) == (["Guidance cut"], [], [0])
    assert review.cost == pytest.approx(1_000 * 4 / 1e6 + 200 * 20 / 1e6)
    assert (review.model, review.answer, review.prompt) == ("fake-model", VALID, build_prompt(ITEM))


def test_invalid_answer_is_retried_once():
    model = FakeModel("not json", VALID)
    review = reviewer(model).review(ITEM)
    assert review.status == "reviewed" and model.calls == 2
    assert review.cost == pytest.approx(2 * 0.008)


def test_invalid_twice_is_flagged():
    review = reviewer(FakeModel("{}", "not json")).review(ITEM)
    assert (review.status, review.verdict, review.note) == ("failed", "flag", "review failed")


@pytest.mark.parametrize(
    "answer",
    [
        {"verdict": "buy", "confidence": "high", "reasons": [], "risks": [], "news_used": []},
        {
            "verdict": "skip",
            "confidence": "high",
            "reasons": ["a", "b", "c", "d"],
            "risks": [],
            "news_used": [],
        },
        {"verdict": "skip", "confidence": "high", "reasons": [], "risks": [], "news_used": [3]},
    ],
)
def test_out_of_bounds_answers_are_invalid(answer):
    text = json.dumps(answer)
    assert reviewer(FakeModel(text, text)).review(ITEM).status == "failed"


def test_unavailable_model_is_not_reviewed():
    review = reviewer(FakeModel(ModelUnavailable("HTTP 529: overloaded"))).review(ITEM)
    assert (review.status, review.verdict) == ("not reviewed", "approve")
    assert review.note == "Claude unavailable: HTTP 529: overloaded"


def test_cap_reached_skips_the_call():
    model = FakeModel(VALID)
    review = reviewer(model, spent=5.0).review(ITEM)
    assert (review.status, review.verdict) == ("not reviewed", "approve")
    assert review.note == "monthly cap of US$5.00 reached"
    assert model.calls == 0


def test_no_model_is_not_reviewed():
    review = reviewer(None).review(ITEM)
    assert (review.status, review.note) == ("not reviewed", "no Claude API key")


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def fake_client(messages):
    return SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages))


RESPONSE = SimpleNamespace(
    content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text=VALID),
    ],
    usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
    model="claude-opus-5-5",
)


def test_claude_model_asks_for_json_with_fallbacks():
    messages = FakeMessages(RESPONSE)
    answer = ClaudeModel(AI, fake_client(messages)).ask("system", "prompt")
    assert answer == Answer(VALID, 1200, 300, "claude-opus-5-5")
    [call] = messages.calls
    assert (call["model"], call["max_tokens"], call["system"]) == (
        "claude-opus-5-5",
        4000,
        "system",
    )
    assert call["messages"] == [{"role": "user", "content": "prompt"}]
    assert call["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"


def test_claude_model_without_fallbacks_or_effort():
    messages = FakeMessages(RESPONSE)
    ClaudeModel(AiSettings(model="claude-haiku-4-5", effort=""), fake_client(messages)).ask(
        "s", "p"
    )
    [call] = messages.calls
    assert "betas" not in call and "fallbacks" not in call
    assert call["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}


def test_api_errors_become_model_unavailable():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    messages = FakeMessages(error=anthropic.APIConnectionError(request=request))
    with pytest.raises(ModelUnavailable, match="connection failed"):
        ClaudeModel(AI, fake_client(messages)).ask("s", "p")
