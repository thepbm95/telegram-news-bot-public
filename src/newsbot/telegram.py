from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from newsbot.catalog import is_selection_text
from newsbot.models import ArticleCandidate, SummaryResult


COMMANDS = {"start", "stop", "chon", "them", "bo", "danhsach"}
COMMANDS_WITH_ARGUMENTS = {"them", "bo"}


class TelegramApiError(RuntimeError):
    """Raised when Telegram rejects or cannot process a request."""


class TelegramAuthError(TelegramApiError):
    """Raised when the bot token is invalid."""


class TelegramForbiddenError(TelegramApiError):
    """Raised when a recipient blocked or cannot receive from the bot."""


@dataclass(frozen=True, slots=True)
class TelegramCommand:
    update_id: int
    chat_id: str
    name: str
    text: str = ""


def format_article_message(candidate: ArticleCandidate, summary: SummaryResult) -> str:
    return (
        f"[{candidate.source} • {candidate.category}]\n\n"
        f"{candidate.title}\n\n"
        f"{summary.text}\n\n"
        f"Đọc bài gốc: {candidate.url}"
    )


def _split_words(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(word[:limit])
            word = word[limit:]
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks


def split_message(text: str, limit: int = 3900) -> list[str]:
    if limit <= 0:
        raise ValueError("Message limit must be positive")
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_words(paragraph, limit))
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def _command_arguments(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


def _parse_command(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    if not stripped.startswith("/"):
        return "select" if is_selection_text(stripped) else None
    token = stripped.split(maxsplit=1)[0].lower()
    name = token.split("@", 1)[0][1:]
    return name if name in COMMANDS else None


class TelegramClient:
    def __init__(self, client: httpx.AsyncClient, token: str, chat_id: str) -> None:
        self.client = client
        self.token = token
        self.chat_id = chat_id

    async def _request(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            response = await self.client.post(url, json=payload, timeout=20.0)
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramApiError("Telegram request failed") from exc

        error_code = data.get("error_code", response.status_code)
        description = str(data.get("description", "Telegram API error"))
        if response.status_code == 401 or error_code == 401:
            raise TelegramAuthError(f"Telegram authentication error {error_code}: {description}")
        if response.status_code == 403 or error_code == 403:
            raise TelegramForbiddenError(f"Telegram recipient error {error_code}: {description}")
        if response.is_error or data.get("ok") is not True:
            raise TelegramApiError(f"Telegram API error {error_code}: {description}")
        return data.get("result")

    async def send_text(self, text: str, *, chat_id: str | None = None) -> Any:
        recipient = chat_id or self.chat_id
        if not recipient:
            raise TelegramAuthError("Telegram chat ID is missing")
        return await self._request(
            "sendMessage",
            {
                "chat_id": recipient,
                "text": text,
                "link_preview_options": {"is_disabled": True},
            },
        )

    async def send_message(self, text: str, *, chat_id: str) -> None:
        chunks = split_message(text, limit=3850)
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            text = chunk if total == 1 else f"({index}/{total})\n{chunk}"
            await self.send_text(text, chat_id=chat_id)

    async def send_article(
        self,
        candidate: ArticleCandidate,
        summary: SummaryResult,
        *,
        chat_id: str | None = None,
    ) -> None:
        recipient = chat_id or self.chat_id
        if not recipient:
            raise TelegramAuthError("Telegram chat ID is missing")
        await self.send_message(format_article_message(candidate, summary), chat_id=recipient)

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Publish the menu Telegram shows when a user types "/"."""
        await self._request(
            "setMyCommands",
            {"commands": [{"command": name, "description": description} for name, description in commands]},
        )

    async def get_bot_username(self) -> str:
        result = await self._request("getMe", {})
        username = result.get("username") if isinstance(result, dict) else None
        if not isinstance(username, str) or not username:
            raise TelegramApiError("Telegram getMe response is missing the bot username")
        return username

    async def list_chat_ids(self) -> list[str]:
        updates = await self._request("getUpdates", {})
        chat_ids: set[str] = set()
        for update in updates or []:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            if isinstance(chat, dict) and chat.get("type") == "private" and "id" in chat:
                chat_ids.add(str(chat["id"]))
        return sorted(chat_ids, key=int)

    async def get_commands(self, offset: int) -> tuple[list[TelegramCommand], int]:
        updates = await self._request(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 0,
                "allowed_updates": ["message"],
            },
        )
        commands: list[TelegramCommand] = []
        next_offset = offset
        for update in updates or []:
            if not isinstance(update, dict):
                continue
            update_id = update.get("update_id")
            if type(update_id) is not int:
                continue
            next_offset = max(next_offset, update_id + 1)
            message = update.get("message")
            chat = message.get("chat") if isinstance(message, dict) else None
            text = message.get("text") if isinstance(message, dict) else None
            if not isinstance(chat, dict) or chat.get("type") != "private":
                continue
            if "id" not in chat or not isinstance(text, str):
                continue
            name = _parse_command(text)
            if name is None:
                continue
            commands.append(
                TelegramCommand(
                    update_id,
                    str(chat["id"]),
                    name,
                    text.strip()
                    if name == "select"
                    else _command_arguments(text) if name in COMMANDS_WITH_ARGUMENTS else "",
                )
            )
        return commands, next_offset
