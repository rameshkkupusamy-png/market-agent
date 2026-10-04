"""Telegram messages through the Bot API (plain HTTPS, no extra package)."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

LIMIT = 4000  # Telegram allows 4096 characters per message


class TelegramError(Exception):
    """The message was not delivered."""


def _post(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    try:
        with urllib.request.urlopen(url, body, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:  # Telegram explains errors in a JSON body
        return json.load(exc)


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        for piece in [line[i : i + limit] for i in range(0, len(line), limit)] or [""]:
            extra = len(piece) + (1 if current else 0)
            if current and size + extra > limit:
                chunks.append("\n".join(current))
                current, size, extra = [], 0, len(piece)
            current.append(piece)
            size += extra
    chunks.append("\n".join(current))
    return chunks


class Telegram:
    def __init__(
        self,
        token: str,
        chat_id: str,
        post: Callable[[str, dict[str, str]], dict[str, Any]] = _post,
    ):
        self._token = token
        self._chat_id = chat_id
        self._post = post

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        for chunk in split_message(text):
            data = {"chat_id": self._chat_id, "text": chunk, "disable_web_page_preview": "true"}
            try:
                result = self._post(url, data)
            except (
                OSError,
                ValueError,
                http.client.HTTPException,
            ) as exc:
                raise TelegramError(str(exc).replace(self._token, "<token>")) from None
            if not isinstance(result, dict):
                raise TelegramError("unexpected Telegram response")
            if not result.get("ok"):
                raise TelegramError(result.get("description", "unknown Telegram error"))


class NoTelegram:
    def send(self, text: str) -> None:
        raise TelegramError("Telegram is not set up (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
