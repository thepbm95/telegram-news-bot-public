from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsbot.catalog import Catalog
from newsbot.config import load_settings
from newsbot.state import BotState, Subscriber
from newsbot.subscriptions import SubscriptionService
from newsbot.telegram import (
    TelegramApiError,
    TelegramAuthError,
    TelegramCommand,
    TelegramForbiddenError,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
EARLIER = (NOW - timedelta(days=1)).isoformat()
CATALOG = Catalog(load_settings({}, Path("config/feeds.toml"), require_secrets=False).feeds)


class FakeTelegram:
    def __init__(
        self,
        *,
        commands: list[TelegramCommand] | None = None,
        next_offset: int = 0,
        get_error: Exception | None = None,
        send_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.commands = commands or []
        self.next_offset = next_offset
        self.get_error = get_error
        self.send_errors = send_errors or {}
        self.requested_offsets: list[int] = []
        self.sent: list[tuple[str, str]] = []

    async def get_commands(self, offset: int) -> tuple[list[TelegramCommand], int]:
        self.requested_offsets.append(offset)
        if self.get_error is not None:
            raise self.get_error
        return self.commands, self.next_offset

    async def send_text(self, text: str, *, chat_id: str | None = None) -> None:
        assert chat_id is not None
        error = self.send_errors.get(chat_id)
        if error is not None:
            raise error
        self.sent.append((chat_id, text))

    def texts_for(self, chat_id: str) -> str:
        return "\n".join(text for recipient, text in self.sent if recipient == chat_id)


def service(telegram: FakeTelegram) -> SubscriptionService:
    return SubscriptionService(telegram, CATALOG, now=lambda: NOW)


@pytest.mark.asyncio
async def test_start_registers_without_feeds_and_sends_menu() -> None:
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "start")], next_offset=11)
    state = BotState()

    stats = await service(telegram).sync(state)

    assert state.subscribers == {"222": Subscriber()}
    assert state.telegram_update_offset == 11
    assert stats.subscribed == 1
    assert stats.menus_sent == 1
    assert "1. Thời sự" in telegram.texts_for("222")
    assert "Kenh14" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_selection_replaces_previous_choice_and_keeps_existing_timestamps() -> None:
    state = BotState(
        subscribers={"222": Subscriber(feeds={"vne-thoi-su": EARLIER, "vne-the-gioi": EARLIER})}
    )
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "select", "1, 18")], next_offset=11)

    stats = await service(telegram).sync(state)

    assert state.subscribers["222"].feeds == {"vne-thoi-su": EARLIER, "dt-thoi-su": NOW.isoformat()}
    assert stats.selections == 1
    reply = telegram.texts_for("222")
    assert "Đã lưu 2 chuyên mục" in reply
    assert "VnExpress – Thời sự (1)" in reply
    assert "Dân trí – Thời sự (18)" in reply


@pytest.mark.asyncio
async def test_selection_from_unknown_chat_subscribes_it() -> None:
    state = BotState()
    telegram = FakeTelegram(commands=[TelegramCommand(10, "333", "select", "2")])

    await service(telegram).sync(state)

    assert state.subscribers["333"].feeds == {"vne-the-gioi": NOW.isoformat()}


@pytest.mark.asyncio
async def test_invalid_selection_keeps_previous_choice_and_explains() -> None:
    state = BotState(subscribers={"222": Subscriber(feeds={"vne-thoi-su": EARLIER})})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "select", "1-11")])

    stats = await service(telegram).sync(state)

    assert state.subscribers["222"].feeds == {"vne-thoi-su": EARLIER}
    assert stats.invalid_selections == 1
    assert "tối đa 10" in telegram.texts_for("222")
    assert "Lựa chọn cũ được giữ nguyên" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_danhsach_lists_current_choice_or_explains_when_empty() -> None:
    state = BotState(
        subscribers={"222": Subscriber(feeds={"k14-star": EARLIER}), "333": Subscriber()}
    )
    telegram = FakeTelegram(
        commands=[TelegramCommand(10, "222", "danhsach"), TelegramCommand(11, "333", "danhsach")]
    )

    await service(telegram).sync(state)

    assert "Kenh14 – Star (47)" in telegram.texts_for("222")
    assert "chưa chọn chuyên mục nào" in telegram.texts_for("333")


@pytest.mark.asyncio
async def test_stop_removes_subscriber_and_selection() -> None:
    state = BotState(subscribers={"222": Subscriber(feeds={"vne-thoi-su": EARLIER})})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "stop"), TelegramCommand(11, "222", "stop")])

    stats = await service(telegram).sync(state)

    assert state.subscribers == {}
    assert stats.unsubscribed == 1
    assert "Đã dừng nhận tin" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_migrated_subscribers_get_the_announcement_exactly_once() -> None:
    state = BotState(subscribers={"111": Subscriber(menu_pending=True), "222": Subscriber(menu_pending=True)})
    telegram = FakeTelegram(send_errors={"222": TelegramApiError("temporary")})

    first = await service(telegram).sync(state)

    assert "tạm dừng gửi tin" in telegram.texts_for("111")
    assert state.subscribers["111"].menu_pending is False
    assert state.subscribers["222"].menu_pending is True
    assert first.confirmations_failed == 1

    telegram.send_errors.clear()
    telegram.sent.clear()
    await service(telegram).sync(state)

    assert telegram.texts_for("111") == ""
    assert "tạm dừng gửi tin" in telegram.texts_for("222")
    assert state.subscribers["222"].menu_pending is False


@pytest.mark.asyncio
async def test_choosing_before_announcement_cancels_it() -> None:
    state = BotState(subscribers={"111": Subscriber(menu_pending=True)})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "111", "select", "3")])

    await service(telegram).sync(state)

    assert "tạm dừng gửi tin" not in telegram.texts_for("111")
    assert state.subscribers["111"] == Subscriber(feeds={"vne-kinh-doanh": NOW.isoformat()})


@pytest.mark.asyncio
async def test_forbidden_reply_removes_subscriber() -> None:
    state = BotState(subscribers={"111": Subscriber(menu_pending=True)})
    telegram = FakeTelegram(send_errors={"111": TelegramForbiddenError("blocked")})

    await service(telegram).sync(state)

    assert state.subscribers == {}


@pytest.mark.asyncio
async def test_update_failure_keeps_state_but_still_sends_pending_menus(caplog) -> None:
    state = BotState(telegram_update_offset=5, subscribers={"111": Subscriber(menu_pending=True)})
    telegram = FakeTelegram(get_error=TelegramApiError("temporary"))

    await service(telegram).sync(state)

    assert state.telegram_update_offset == 5
    assert state.subscribers["111"].menu_pending is False
    assert "Unable to read Telegram subscription commands" in caplog.text


@pytest.mark.asyncio
async def test_auth_failure_propagates() -> None:
    state = BotState()
    telegram = FakeTelegram(
        commands=[TelegramCommand(30, "222", "start")],
        next_offset=31,
        send_errors={"222": TelegramAuthError("bad token")},
    )

    with pytest.raises(TelegramAuthError):
        await service(telegram).sync(state)
    assert state.telegram_update_offset == 31


class CommandMenuTelegram(FakeTelegram):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.menus: list[list[tuple[str, str]]] = []
        self.menu_error: Exception | None = None

    async def set_commands(self, commands: list[tuple[str, str]]) -> None:
        if self.menu_error is not None:
            raise self.menu_error
        self.menus.append(commands)


@pytest.mark.asyncio
async def test_sync_publishes_slash_command_menu() -> None:
    telegram = CommandMenuTelegram()

    await service(telegram).sync(BotState())

    names = [name for name, _ in telegram.menus[0]]
    assert names == ["start", "chon", "them", "bo", "danhsach", "stop"]
    assert all(description for _, description in telegram.menus[0])


@pytest.mark.asyncio
async def test_command_menu_failure_does_not_block_sync(caplog) -> None:
    telegram = CommandMenuTelegram(commands=[TelegramCommand(10, "222", "start")])
    telegram.menu_error = TelegramApiError("temporary")

    await service(telegram).sync(BotState())

    assert "1. Thời sự" in telegram.texts_for("222")
    assert "Unable to update Telegram command menu" in caplog.text


@pytest.mark.asyncio
async def test_them_without_numbers_shows_named_menu_then_adds_reply() -> None:
    state = BotState(subscribers={"222": Subscriber(feeds={"vne-thoi-su": EARLIER})})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "them")])

    await service(telegram).sync(state)

    menu = telegram.texts_for("222")
    assert "✅ 1. Thời sự" in menu
    assert "18. Thời sự" in menu and "Dân trí" in menu
    assert "muốn THÊM" in menu
    assert state.subscribers["222"].mode == "add"

    telegram.sent.clear()
    telegram.commands = [TelegramCommand(11, "222", "select", "18")]
    await service(telegram).sync(state)

    assert state.subscribers["222"].feeds == {"vne-thoi-su": EARLIER, "dt-thoi-su": NOW.isoformat()}
    assert state.subscribers["222"].mode == "replace"
    assert "Đã lưu 2 chuyên mục" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_them_with_numbers_adds_immediately_and_respects_limit() -> None:
    feeds = {f"vne-{slug}": EARLIER for slug in ["thoi-su", "the-gioi", "kinh-doanh", "bat-dong-san", "giai-tri"]}
    state = BotState(subscribers={"222": Subscriber(feeds=dict(feeds))})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "them", "18-22")])

    await service(telegram).sync(state)
    assert len(state.subscribers["222"].feeds) == 10

    telegram.sent.clear()
    telegram.commands = [TelegramCommand(11, "222", "them", "23")]
    await service(telegram).sync(state)

    assert len(state.subscribers["222"].feeds) == 10
    assert "tối đa 10" in telegram.texts_for("222")
    assert "/bo" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_bo_without_numbers_lists_current_choice_then_removes_reply() -> None:
    state = BotState(subscribers={"222": Subscriber(feeds={"vne-thoi-su": EARLIER, "k14-star": EARLIER})})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "bo")])

    await service(telegram).sync(state)

    listing = telegram.texts_for("222")
    assert "muốn BỎ" in listing
    assert "VnExpress – Thời sự (1)" in listing
    assert "Kenh14 – Star (47)" in listing
    assert "Dân trí" not in listing

    telegram.sent.clear()
    telegram.commands = [TelegramCommand(11, "222", "select", "47")]
    await service(telegram).sync(state)

    assert state.subscribers["222"].feeds == {"vne-thoi-su": EARLIER}
    assert state.subscribers["222"].mode == "replace"


@pytest.mark.asyncio
async def test_bo_with_unselected_number_explains_and_keeps_choice() -> None:
    state = BotState(subscribers={"222": Subscriber(feeds={"vne-thoi-su": EARLIER})})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "bo", "2")])

    await service(telegram).sync(state)

    assert state.subscribers["222"].feeds == {"vne-thoi-su": EARLIER}
    assert "không có trong danh sách bạn đang nhận" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_bo_without_any_choice_points_to_chon() -> None:
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "bo")])

    await service(telegram).sync(BotState(subscribers={"222": Subscriber()}))

    assert "chưa chọn chuyên mục nào" in telegram.texts_for("222")


@pytest.mark.asyncio
async def test_chon_marks_current_choice_and_resets_mode() -> None:
    state = BotState(subscribers={"222": Subscriber(feeds={"k14-star": EARLIER}, mode="add")})
    telegram = FakeTelegram(commands=[TelegramCommand(10, "222", "chon")])

    await service(telegram).sync(state)

    assert "✅ 47. Star" in telegram.texts_for("222")
    assert "   1. Thời sự" not in telegram.texts_for("222")
    assert state.subscribers["222"].mode == "replace"
