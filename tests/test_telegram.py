import json

import httpx
import pytest

from newsbot.models import ArticleCandidate, SummaryResult
from newsbot.telegram import (
    TelegramApiError,
    TelegramAuthError,
    TelegramClient,
    TelegramForbiddenError,
    format_article_message,
    split_message,
)


def candidate() -> ArticleCandidate:
    return ArticleCandidate(
        article_id="id",
        source="VnExpress",
        category="Thời sự",
        title="Tiêu đề thử nghiệm",
        url="https://example.test/article",
        published_at=None,
        rss_summary="",
    )


def test_format_article_message_contains_required_fields() -> None:
    summary = SummaryResult("Nội dung tóm tắt.", "gemini", 1_000, 200)

    message = format_article_message(candidate(), summary)

    assert message.startswith("[VnExpress • Thời sự]")
    assert "Tiêu đề thử nghiệm" in message
    assert "Nội dung tóm tắt." in message
    assert message.endswith("Đọc bài gốc: https://example.test/article")


def test_split_message_respects_limit_and_preserves_words() -> None:
    text = "Đoạn một.\n\n" + ("nội dung " * 800)

    chunks = split_message(text, limit=3900)

    assert len(chunks) > 1
    assert all(len(chunk) <= 3900 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


@pytest.mark.asyncio
async def test_telegram_401_raises_bot_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "123")
        with pytest.raises(TelegramAuthError, match="401"):
            await client.send_text("Tin thử")


@pytest.mark.asyncio
async def test_telegram_403_raises_recipient_forbidden_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "123")
        with pytest.raises(TelegramForbiddenError, match="403"):
            await client.send_text("Tin thử")


@pytest.mark.asyncio
async def test_send_text_uses_explicit_recipient_instead_of_default() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "111")
        await client.send_text("Tin thử", chat_id="222")

    assert payloads == [
        {
            "chat_id": "222",
            "text": "Tin thử",
            "link_preview_options": {"is_disabled": True},
        }
    ]


@pytest.mark.asyncio
async def test_send_message_splits_all_chunks_for_explicit_recipient() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(payloads)}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "111")
        await client.send_message("nội dung " * 1_000, chat_id="222")

    assert len(payloads) > 1
    assert all(payload["chat_id"] == "222" for payload in payloads)
    assert all(len(str(payload["text"])) <= 3900 for payload in payloads)


@pytest.mark.asyncio
async def test_send_article_posts_all_chunks_with_preview_disabled() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(payloads)}})

    long_summary = SummaryResult("nội dung " * 1_000, "local", 5_000, 1_000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "123")
        await client.send_article(candidate(), long_summary)

    assert len(payloads) > 1
    assert all(payload["chat_id"] == "123" for payload in payloads)
    assert all(payload["link_preview_options"] == {"is_disabled": True} for payload in payloads)
    assert all(len(str(payload["text"])) <= 3900 for payload in payloads)


@pytest.mark.asyncio
async def test_list_chat_ids_returns_unique_private_chat_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 42, "type": "private"}}},
                    {"update_id": 2, "message": {"chat": {"id": 42, "type": "private"}}},
                    {"update_id": 3, "channel_post": {"chat": {"id": -10, "type": "channel"}}},
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "")
        chat_ids = await client.list_chat_ids()

    assert chat_ids == ["42"]


@pytest.mark.asyncio
async def test_get_commands_filters_malformed_and_non_private_updates_but_advances_offset() -> None:
    updates = [
        {"update_id": 10, "channel_post": {"text": "/start"}},
        {"update_id": 11, "message": {"chat": {"id": -5, "type": "group"}, "text": "/start"}},
        {"update_id": 12, "message": {"chat": {"id": 42, "type": "private"}, "text": 7}},
        {"update_id": 13, "message": {"chat": {"id": 42, "type": "private"}, "text": "/start payload"}},
        {"update_id": 14, "message": {"chat": {"id": 42, "type": "private"}, "text": "/stop@tin_tuc_bot"}},
        {"update_id": 15, "message": {"chat": {"id": 42, "type": "private"}, "text": "/starter"}},
        {"update_id": 16, "message": {"chat": {"id": 42, "type": "private"}, "text": "xin chào"}},
        {"update_id": 17, "message": {"text": "/start"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "offset": 8,
            "timeout": 0,
            "allowed_updates": ["message"],
        }
        return httpx.Response(200, json={"ok": True, "result": updates})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        commands, next_offset = await TelegramClient(http, "token", "").get_commands(8)

    assert [(item.update_id, item.chat_id, item.name) for item in commands] == [
        (13, "42", "start"),
        (14, "42", "stop"),
    ]
    assert next_offset == 18


@pytest.mark.asyncio
async def test_get_commands_accepts_case_insensitive_command_and_keeps_offset_when_empty() -> None:
    responses = iter(
        [
            [{"update_id": 20, "message": {"chat": {"id": 7, "type": "private"}, "text": "/START"}}],
            [],
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": next(responses)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "")
        commands, next_offset = await client.get_commands(20)
        empty, unchanged_offset = await client.get_commands(next_offset)

    assert [(item.chat_id, item.name) for item in commands] == [("7", "start")]
    assert next_offset == 21
    assert empty == []
    assert unchanged_offset == 21


@pytest.mark.asyncio
async def test_get_bot_username_verifies_token_owner() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 99, "is_bot": True, "username": "tin_tuc_bot"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "")
        username = await client.get_bot_username()

    assert username == "tin_tuc_bot"


@pytest.mark.asyncio
async def test_non_auth_api_failure_raises_general_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = TelegramClient(http, "token", "123")
        with pytest.raises(TelegramApiError, match="429"):
            await client.send_text("Tin thử")


@pytest.mark.asyncio
async def test_get_commands_reads_category_commands_and_numeric_selection() -> None:
    texts = ["/chon@news_bot", "/danhsach", " 1 2, 20-22 ", "xin chào", "/unknown", "/stop"]
    updates = [
        {"update_id": 30 + index, "message": {"chat": {"id": 7, "type": "private"}, "text": text}}
        for index, text in enumerate(texts)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": updates})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        commands, next_offset = await TelegramClient(http, "token", "").get_commands(30)

    assert [(item.name, item.text) for item in commands] == [
        ("chon", ""),
        ("danhsach", ""),
        ("select", "1 2, 20-22"),
        ("stop", ""),
    ]
    assert next_offset == 36


@pytest.mark.asyncio
async def test_get_commands_keeps_arguments_of_them_and_bo() -> None:
    texts = ["/them 18 20-22", "/bo@news_bot 3", "/them"]
    updates = [
        {"update_id": 40 + index, "message": {"chat": {"id": 7, "type": "private"}, "text": text}}
        for index, text in enumerate(texts)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": updates})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        commands, _ = await TelegramClient(http, "token", "").get_commands(40)

    assert [(item.name, item.text) for item in commands] == [("them", "18 20-22"), ("bo", "3"), ("them", "")]


@pytest.mark.asyncio
async def test_set_commands_posts_bot_command_list() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await TelegramClient(http, "token", "").set_commands([("them", "Thêm chuyên mục")])

    assert requests[0].url.path.endswith("/setMyCommands")
    assert json.loads(requests[0].content) == {"commands": [{"command": "them", "description": "Thêm chuyên mục"}]}
