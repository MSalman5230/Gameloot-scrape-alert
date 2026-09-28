import httpx
import pytest
import respx

from stockwatch.config import Config
from stockwatch.notify import LogNotifier, TelegramNotifier, build_notifier
from stockwatch.notify.telegram import split_message

API = "https://api.telegram.org/botTOKEN/sendMessage"


def test_short_message_is_not_split():
    assert split_message("hello") == ["hello"]


def test_split_on_line_boundaries():
    text = "\n".join(f"line {i:04d}" for i in range(1000))
    parts = split_message(text, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert "\n".join(parts) == text


def test_overlong_line_is_hard_split():
    parts = split_message("a" * 250 + "\nshort", limit=100)
    assert parts == ["a" * 100, "a" * 100, "a" * 50 + "\nshort"]


@pytest.fixture
async def notifier():
    n = TelegramNotifier("TOKEN", ["1", "2"], retries=3, retry_delay=0)
    yield n
    await n.aclose()


async def test_sends_to_every_chat(notifier):
    with respx.mock:
        route = respx.post(API).mock(return_value=httpx.Response(200, json={"ok": True}))
        await notifier.send("hi")
    assert [c.request.read() for c in route.calls] == [
        b'{"chat_id":"1","text":"hi","disable_web_page_preview":true}',
        b'{"chat_id":"2","text":"hi","disable_web_page_preview":true}',
    ]


async def test_retries_on_rate_limit(notifier):
    limited = httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}})
    with respx.mock:
        route = respx.post(API).mock(side_effect=[limited, httpx.Response(200), httpx.Response(200)])
        await notifier.send("hi")
    assert route.call_count == 3


async def test_permanent_error_is_raised_but_other_chats_still_get_it(notifier):
    with respx.mock:
        route = respx.post(API).mock(
            side_effect=[httpx.Response(400, json={"description": "chat not found"}), httpx.Response(200)]
        )
        with pytest.raises(RuntimeError, match="chat not found"):
            await notifier.send("hi")
    assert route.call_count == 2


def test_build_notifier_falls_back_to_log():
    assert isinstance(build_notifier(Config(_env_file=None, telegram_bot_token="")), LogNotifier)
    config = Config(_env_file=None, telegram_bot_token="t", telegram_chat_ids=" 1, -1002 ,")
    assert config.chat_ids == ["1", "-1002"]
    assert isinstance(build_notifier(config), TelegramNotifier)
