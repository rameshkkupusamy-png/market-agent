import http.client
import urllib.error

import pytest

from market_agent.notify import NoTelegram, Telegram, TelegramError, split_message


def test_long_reports_are_split_on_lines():
    text = "\n".join(f"line {i:04d} " + "x" * 90 for i in range(100))
    chunks = split_message(text, limit=4000)
    assert "\n".join(chunks) == text
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert len(chunks) == 3


def test_a_too_long_line_is_cut():
    chunks = split_message("y" * 9000, limit=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]


def test_send_posts_each_chunk():
    calls = []

    def post(url, data):
        calls.append((url, data))
        return {"ok": True}

    Telegram("TOKEN", "42", post).send("hello")
    assert calls == [
        (
            "https://api.telegram.org/botTOKEN/sendMessage",
            {"chat_id": "42", "text": "hello", "disable_web_page_preview": "true"},
        )
    ]


def test_network_failures_raise_without_the_token():
    def post(url, data):
        raise urllib.error.URLError(f"cannot reach {url}")

    with pytest.raises(TelegramError) as info:
        Telegram("TOKEN", "42", post).send("hi")
    assert "TOKEN" not in str(info.value)


def test_api_errors_raise():
    def post(url, data):
        return {"ok": False, "description": "Bad Request: chat not found"}

    with pytest.raises(TelegramError, match="chat not found"):
        Telegram("TOKEN", "42", post).send("hi")


def test_no_telegram_explains():
    with pytest.raises(TelegramError, match="TELEGRAM_BOT_TOKEN"):
        NoTelegram().send("hi")


def test_protocol_errors_raise_telegram_error():
    def post(url, data):
        raise http.client.IncompleteRead(b"")

    with pytest.raises(TelegramError):
        Telegram("TOKEN", "42", post).send("hi")


def test_unexpected_response_raises_telegram_error():
    def post(url, data):
        return ["not", "a", "dict"]

    with pytest.raises(TelegramError, match="unexpected Telegram response"):
        Telegram("TOKEN", "42", post).send("hi")
