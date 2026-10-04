"""Claude reviews the top candidates for the rules+ai portfolio (spec section 7)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
import pandas as pd

from market_agent.data.sources import Headline
from market_agent.settings import AiSettings

VERDICTS = ("approve", "skip", "flag")
CONFIDENCE = ("low", "medium", "high")
MAX_ITEMS = 3
RETRY_SEPARATOR = "\n\n--- retry ---\n\n"  # joins the answers of a retried review
# Models that take server-side refusal fallbacks (a false positive is retried on another model).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}

SYSTEM = """You review swing-trade candidates for a paper-trading experiment (simulated money).
A rules-based screen found each candidate: an uptrend and a 20-day closing high on at least 1.5
times average volume. Decide whether the information given argues against buying it at
tomorrow's open.

- Judge only from the information in the message. Never invent facts, prices or events.
- If none of the headlines matter, include "no relevant news" in reasons.
- verdict: "approve" to buy, "skip" to leave it out, "flag" to buy but point out a concern.
- You cannot change the position size, stop or target.
- reasons and risks: at most 3 short strings each. news_used: the numbers of the headlines you
  relied on (empty if none)."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "news_used": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["verdict", "confidence", "reasons", "risks", "news_used"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ReviewInput:
    ticker: str
    company: str
    sector: str
    day: pd.Timestamp
    close: float
    sma_fast: float
    sma_slow: float
    volume_ratio: float
    atr: float
    return_63: float
    next_earnings: pd.Timestamp | None
    headlines: list[Headline]


@dataclass(frozen=True)
class Review:
    ticker: str
    status: str  # "reviewed" | "failed" | "not reviewed"
    verdict: str  # not reviewed counts as "approve"; failed as "flag"
    confidence: str | None
    reasons: list[str]
    risks: list[str]
    news_used: list[int]
    note: str  # why it was not reviewed or failed
    cost: float  # US$
    model: str | None
    prompt: str
    answer: str | None


@dataclass(frozen=True)
class Answer:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ModelUnavailable(Exception):
    """The Claude API could not be reached or refused the request."""


class Model(Protocol):
    def ask(self, system: str, prompt: str) -> Answer: ...


class ClaudeModel:
    def __init__(self, settings: AiSettings, client: Any = None):
        self._settings = settings
        self._client = client if client is not None else anthropic.Anthropic()

    def ask(self, system: str, prompt: str) -> Answer:
        s = self._settings
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": SCHEMA}}
        if s.effort:
            output_config["effort"] = s.effort
        request: dict[str, Any] = {
            "model": s.model,
            "max_tokens": s.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output_config,
        }
        try:
            if s.model in FALLBACK_MODELS:
                response = self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **request
                )
            else:
                response = self._client.messages.create(**request)
        except anthropic.APIStatusError as exc:
            raise ModelUnavailable(f"HTTP {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelUnavailable(f"connection failed: {exc}") from exc
        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        return Answer(text, usage.input_tokens, usage.output_tokens, response.model)


def build_prompt(item: ReviewInput) -> str:
    earnings = f"{item.next_earnings:%Y-%m-%d}" if item.next_earnings is not None else "not known"
    lines = [
        f"Candidate: {item.ticker} ({item.company}), sector {item.sector}",
        f"Day: {item.day:%Y-%m-%d}, after the close",
        f"Close {item.close:.2f}; 50-day average {item.sma_fast:.2f}; "
        f"200-day average {item.sma_slow:.2f}",
        "Breakout: highest close of the last 20 trading days, "
        f"on {item.volume_ratio:.1f} times average volume",
        f"ATR(14) {item.atr:.2f}; 3-month return {item.return_63:+.1%}",
        f"Next earnings date: {earnings}",
        "",
    ]
    if not item.headlines:
        lines.append("No recent headlines.")
    else:
        lines.append("Headlines, newest first:")
        for index, h in enumerate(item.headlines):
            lines.append(f"[{index}] {h.published:%Y-%m-%d} {h.source}: {h.headline}")
            if h.summary:
                lines.append(f"    {h.summary}")
    return "\n".join(lines)


def _strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= MAX_ITEMS
        and all(isinstance(v, str) for v in value)
    )


def parse_answer(text: str, headline_count: int) -> dict[str, Any] | None:
    """The validated answer, or None if it breaks any rule of the schema or the spec."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    news = data.get("news_used")
    valid = (
        data.get("verdict") in VERDICTS
        and data.get("confidence") in CONFIDENCE
        and _strings(data.get("reasons"))
        and _strings(data.get("risks"))
        and isinstance(news, list)
        and all(isinstance(i, int) and 0 <= i < headline_count for i in news)
    )
    if not valid:
        return None
    return {key: data[key] for key in ("verdict", "confidence", "reasons", "risks", "news_used")}


class Reviewer:
    def __init__(self, model: Model | None, settings: AiSettings, spent: Callable[[], float]):
        """spent: US$ already spent on reviews this calendar month."""
        self._model = model
        self._settings = settings
        self._spent = spent

    def _cost(self, answer: Answer) -> float:
        s = self._settings
        return (answer.input_tokens * s.input_price + answer.output_tokens * s.output_price) / 1e6

    def _not_reviewed(
        self,
        item: ReviewInput,
        prompt: str,
        note: str,
        cost: float = 0.0,
        model: str | None = None,
        answer: str | None = None,
    ) -> Review:
        return Review(
            item.ticker,
            "not reviewed",
            "approve",
            None,
            [],
            [],
            [],
            note,
            cost,
            model,
            prompt,
            answer,
        )

    def review(self, item: ReviewInput) -> Review:
        prompt = build_prompt(item)
        if self._model is None:
            return self._not_reviewed(item, prompt, "no Claude API key")
        cap = self._settings.monthly_cap
        if self._spent() >= cap:
            return self._not_reviewed(item, prompt, f"monthly cap of US${cap:.2f} reached")
        cost = 0.0
        answer: Answer | None = None
        texts: list[str] = []
        for _ in range(2):  # one retry for an invalid answer
            try:
                answer = self._model.ask(SYSTEM, prompt)
            except ModelUnavailable as exc:
                return self._not_reviewed(
                    item,
                    prompt,
                    f"Claude unavailable: {exc}",
                    cost,
                    answer.model if answer else None,
                    RETRY_SEPARATOR.join(texts) if texts else None,
                )
            cost += self._cost(answer)
            texts.append(answer.text)
            parsed = parse_answer(answer.text, len(item.headlines))
            if parsed is not None:
                return Review(
                    item.ticker,
                    "reviewed",
                    note="",
                    cost=cost,
                    model=answer.model,
                    prompt=prompt,
                    answer=RETRY_SEPARATOR.join(texts),
                    **parsed,
                )
        assert answer is not None
        return Review(
            item.ticker,
            "failed",
            "flag",
            None,
            [],
            [],
            [],
            "review failed",
            cost,
            answer.model,
            prompt,
            RETRY_SEPARATOR.join(texts),
        )
