from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Protocol

from newsbot.catalog import (
    COMMAND_HINT,
    MAX_SELECTION,
    Catalog,
    SelectionError,
    describe_feeds,
    format_menu,
    format_removal_menu,
    format_selection,
    parse_numbers,
    parse_selection,
)
from newsbot.state import BotState, Subscriber
from newsbot.telegram import (
    TelegramApiError,
    TelegramAuthError,
    TelegramCommand,
    TelegramForbiddenError,
)


LOGGER = logging.getLogger(__name__)

# Shown by Telegram when a user types "/".
COMMAND_MENU = [
    ("start", "Đăng ký và xem danh sách chuyên mục"),
    ("chon", "Chọn lại toàn bộ chuyên mục"),
    ("them", "Thêm chuyên mục"),
    ("bo", "Bỏ bớt chuyên mục"),
    ("danhsach", "Xem chuyên mục đang nhận"),
    ("stop", "Dừng nhận tin"),
]


class TelegramProtocol(Protocol):
    async def get_commands(self, offset: int) -> tuple[list[TelegramCommand], int]: ...

    async def send_text(self, text: str, *, chat_id: str | None = None) -> object: ...


@dataclass(slots=True)
class SubscriptionStats:
    commands: int = 0
    subscribed: int = 0
    unsubscribed: int = 0
    selections: int = 0
    invalid_selections: int = 0
    menus_sent: int = 0
    confirmations_failed: int = 0


class SubscriptionService:
    def __init__(
        self,
        telegram: TelegramProtocol,
        catalog: Catalog,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_selection: int = MAX_SELECTION,
    ) -> None:
        self.telegram = telegram
        self.catalog = catalog
        self.now = now
        self.max_selection = max_selection

    def _timestamp(self) -> str:
        value = self.now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()

    def _subscribe(self, state: BotState, chat_id: str, stats: SubscriptionStats) -> Subscriber:
        subscriber = state.subscribers.get(chat_id)
        if subscriber is None:
            subscriber = state.subscribers[chat_id] = Subscriber()
            stats.subscribed += 1
        return subscriber

    def _menu(self, subscriber: Subscriber | None = None, *, announcement: bool = False) -> list[str]:
        return format_menu(
            self.catalog,
            announcement=announcement,
            max_items=self.max_selection,
            selected=subscriber.feeds if subscriber else (),
            mode=subscriber.mode if subscriber else "replace",
        )

    def _choose(self, subscriber: Subscriber, mode: str, text: str) -> list[str]:
        """Feed ids the subscriber should follow after applying ``text`` in ``mode``."""
        if mode == "replace":
            return parse_selection(text, self.catalog, self.max_selection)
        requested = parse_numbers(text, self.catalog)
        current = list(subscriber.feeds)
        if mode == "add":
            chosen = [*current, *(feed_id for feed_id in requested if feed_id not in subscriber.feeds)]
            if len(chosen) > self.max_selection:
                raise SelectionError(
                    f"Bạn đang nhận {len(current)} chuyên mục; thêm {len(chosen) - len(current)} mục sẽ thành "
                    f"{len(chosen)}, vượt mức tối đa {self.max_selection}. Gõ /bo để bớt trước."
                )
            return chosen
        missing = [feed_id for feed_id in requested if feed_id not in subscriber.feeds]
        if missing:
            numbers = ", ".join(str(self.catalog.number_of(feed_id)) for feed_id in missing)
            raise SelectionError(f"Số {numbers} không có trong danh sách bạn đang nhận. Gõ /danhsach để xem.")
        return [feed_id for feed_id in current if feed_id not in requested]

    def _apply(self, subscriber: Subscriber, mode: str, text: str, stats: SubscriptionStats) -> list[str]:
        try:
            feed_ids = self._choose(subscriber, mode, text)
        except SelectionError as exc:
            stats.invalid_selections += 1
            return [f"{exc}\nLựa chọn cũ được giữ nguyên. Gõ /chon để xem lại danh sách."]
        selected_at = self._timestamp()
        subscriber.feeds = {feed_id: subscriber.feeds.get(feed_id, selected_at) for feed_id in feed_ids}
        subscriber.mode = "replace"
        subscriber.menu_pending = False
        stats.selections += 1
        if not feed_ids:
            return ["Bạn đã bỏ hết chuyên mục nên tạm thời không nhận tin. Gõ /them hoặc /chon để chọn lại."]
        return [format_selection(self.catalog, feed_ids)]

    def _handle(self, state: BotState, command: TelegramCommand, stats: SubscriptionStats) -> list[str]:
        if command.name in {"start", "chon"}:
            subscriber = self._subscribe(state, command.chat_id, stats)
            subscriber.mode = "replace"
            return self._menu(subscriber)

        if command.name == "select":
            subscriber = self._subscribe(state, command.chat_id, stats)
            return self._apply(subscriber, subscriber.mode, command.text, stats)

        if command.name == "them":
            subscriber = self._subscribe(state, command.chat_id, stats)
            if command.text:
                return self._apply(subscriber, "add", command.text, stats)
            if len(subscriber.feeds) >= self.max_selection:
                return [f"Bạn đã chọn đủ {self.max_selection} chuyên mục. Gõ /bo để bớt trước khi thêm."]
            subscriber.mode = "add"
            return self._menu(subscriber)

        if command.name == "bo":
            subscriber = state.subscribers.get(command.chat_id)
            if subscriber is None or not subscriber.feeds:
                return ["Bạn chưa chọn chuyên mục nào. Gõ /chon để xem danh sách."]
            if command.text:
                return self._apply(subscriber, "remove", command.text, stats)
            subscriber.mode = "remove"
            return [format_removal_menu(self.catalog, subscriber.feeds)]

        if command.name == "danhsach":
            subscriber = state.subscribers.get(command.chat_id)
            if subscriber is None or not subscriber.feeds:
                return ["Bạn chưa chọn chuyên mục nào. Gõ /chon để xem danh sách."]
            return [
                f"Bạn đang nhận tin {len(subscriber.feeds)} chuyên mục:\n"
                f"{describe_feeds(self.catalog, subscriber.feeds)}\n\n"
                f"{COMMAND_HINT}"
            ]

        if command.name == "stop":
            if state.subscribers.pop(command.chat_id, None) is not None:
                stats.unsubscribed += 1
            return ["Đã dừng nhận tin. Gõ /start nếu muốn đăng ký lại."]

        return []

    async def _send(
        self,
        state: BotState,
        chat_id: str,
        texts: list[str],
        stats: SubscriptionStats,
    ) -> bool:
        for text in texts:
            try:
                await self.telegram.send_text(text, chat_id=chat_id)
            except TelegramAuthError:
                raise
            except TelegramForbiddenError:
                state.subscribers.pop(chat_id, None)
                stats.confirmations_failed += 1
                LOGGER.warning("Telegram subscriber is unavailable; subscription removed")
                return False
            except TelegramApiError:
                stats.confirmations_failed += 1
                LOGGER.warning("Telegram subscription reply failed temporarily")
                return False
        return True

    async def _publish_command_menu(self) -> None:
        set_commands = getattr(self.telegram, "set_commands", None)
        if set_commands is None:
            return
        try:
            await set_commands(COMMAND_MENU)
        except TelegramAuthError:
            raise
        except TelegramApiError:
            LOGGER.warning("Unable to update Telegram command menu")

    async def sync(self, state: BotState) -> SubscriptionStats:
        stats = SubscriptionStats()
        await self._publish_command_menu()
        try:
            commands, next_offset = await self.telegram.get_commands(state.telegram_update_offset)
        except TelegramAuthError:
            raise
        except TelegramApiError:
            LOGGER.warning("Unable to read Telegram subscription commands; using saved subscribers")
            commands, next_offset = [], state.telegram_update_offset

        state.telegram_update_offset = max(state.telegram_update_offset, next_offset)
        for command in commands:
            stats.commands += 1
            replies = self._handle(state, command, stats)
            if not replies:
                continue
            sent = await self._send(state, command.chat_id, replies, stats)
            subscriber = state.subscribers.get(command.chat_id)
            if sent and command.name in {"start", "chon"} and subscriber is not None:
                subscriber.menu_pending = False
                stats.menus_sent += 1

        # One-time announcement for subscribers migrated from the single-list schema.
        for chat_id, subscriber in list(state.subscribers.items()):
            if not subscriber.menu_pending:
                continue
            if await self._send(state, chat_id, self._menu(announcement=True), stats):
                subscriber.menu_pending = False
                stats.menus_sent += 1
        return stats
