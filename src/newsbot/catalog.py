from __future__ import annotations

import re
from collections.abc import Iterable

from newsbot.models import FeedConfig
from newsbot.state import SeenEntry, Subscriber, parse_timestamp


MAX_SELECTION = 10
COMMAND_HINT = "Gõ /them để thêm, /bo để bớt, /chon để chọn lại, /danhsach để xem, /stop để dừng nhận tin."
MENU_CHUNK_LIMIT = 3800
SELECTION_TEXT = re.compile(r"^[\d\s,\-–]+$")
RANGE_TOKEN = re.compile(r"^(\d+)(?:-(\d+))?$")


class SelectionError(ValueError):
    """Raised with a user-facing Vietnamese message when a selection is invalid."""


class Catalog:
    def __init__(self, feeds: Iterable[FeedConfig]) -> None:
        self.feeds = tuple(feeds)
        self._numbers = {feed.id: number for number, feed in enumerate(self.feeds, start=1)}

    def __len__(self) -> int:
        return len(self.feeds)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(feed.id for feed in self.feeds)

    def by_number(self, number: int) -> FeedConfig:
        if not 1 <= number <= len(self.feeds):
            raise SelectionError(f"Số {number} không có trong danh sách (chỉ từ 1 đến {len(self.feeds)}).")
        return self.feeds[number - 1]

    def number_of(self, feed_id: str) -> int:
        return self._numbers[feed_id]


def is_selection_text(text: str) -> bool:
    stripped = text.strip()
    return bool(SELECTION_TEXT.match(stripped)) and any(char.isdigit() for char in stripped)


def parse_numbers(text: str, catalog: Catalog) -> list[str]:
    """Feed ids for the numbers and ranges in ``text``, in catalog order, without a size limit."""
    if not is_selection_text(text):
        raise SelectionError("Hãy trả lời bằng các số trong danh sách, ví dụ: 1 3 20-22.")
    normalized = re.sub(r"\s*[-–]\s*", "-", text.strip())
    numbers: set[int] = set()
    for token in filter(None, re.split(r"[\s,]+", normalized)):
        match = RANGE_TOKEN.match(token)
        if match is None:
            raise SelectionError(f"Không hiểu “{token}”. Hãy dùng số hoặc dải số như 20-22.")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end:
            raise SelectionError(f"Dải số {start}-{end} không hợp lệ: số đầu phải nhỏ hơn số cuối.")
        catalog.by_number(start)
        catalog.by_number(end)
        numbers.update(range(start, end + 1))
    if not numbers:
        raise SelectionError("Hãy chọn ít nhất 1 chuyên mục.")
    return [catalog.by_number(number).id for number in sorted(numbers)]


def parse_selection(text: str, catalog: Catalog, max_items: int = MAX_SELECTION) -> list[str]:
    feed_ids = parse_numbers(text, catalog)
    if len(feed_ids) > max_items:
        raise SelectionError(f"Bạn đã chọn {len(feed_ids)} chuyên mục; mỗi người được chọn tối đa {max_items}.")
    return feed_ids


def _source_groups(catalog: Catalog, selected: set[str]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for number, feed in enumerate(catalog.feeds, start=1):
        mark = "✅ " if feed.id in selected else ""
        groups.setdefault(feed.source, []).append(f"{mark}{number}. {feed.category}")
    return [f"{source}\n" + "\n".join(lines) for source, lines in groups.items()]


def format_menu(
    catalog: Catalog,
    *,
    announcement: bool = False,
    max_items: int = MAX_SELECTION,
    limit: int = MENU_CHUNK_LIMIT,
    selected: Iterable[str] = (),
    mode: str = "replace",
) -> list[str]:
    selected = set(selected)
    if mode == "add":
        intro = (
            f"Trả lời các số muốn THÊM (đang nhận {len(selected)}/{max_items}, "
            f"còn thêm được {max_items - len(selected)}).\n"
            "Các chuyên mục đang nhận vẫn được giữ nguyên. Ví dụ: 5 20-22"
        )
    else:
        intro = (
            f"Chọn tối đa {max_items} chuyên mục muốn nhận tin.\n"
            "Trả lời bằng các số, cách nhau dấu cách hoặc dấu phẩy. Có thể dùng dải số.\n"
            "Ví dụ: 1 3 20-22"
        )
        if selected:
            intro += "\nDanh sách bạn gửi sẽ thay toàn bộ lựa chọn cũ. Muốn thêm hoặc bỏ từng mục, dùng /them hoặc /bo."
    if selected:
        intro += "\n✅ là chuyên mục bạn đang nhận."
    if announcement:
        intro = (
            "Bot đã có thêm báo CafeBiz, GenK và Kenh14. Từ nay mỗi người tự chọn chuyên mục; "
            "bot tạm dừng gửi tin cho bạn cho tới khi bạn chọn.\n\n" + intro
        )
    chunks: list[str] = []
    current = intro
    for group in _source_groups(catalog, selected):
        candidate = f"{current}\n\n{group}"
        if len(candidate) > limit and current:
            chunks.append(current)
            current = group
        else:
            current = candidate
    chunks.append(current)
    return chunks


def describe_feeds(catalog: Catalog, feed_ids: Iterable[str]) -> str:
    known = sorted((catalog.number_of(feed_id), feed_id) for feed_id in feed_ids if feed_id in catalog.ids)
    lines = []
    for number, _ in known:
        feed = catalog.by_number(number)
        lines.append(f"• {feed.source} – {feed.category} ({number})")
    return "\n".join(lines)


def format_selection(catalog: Catalog, feed_ids: Iterable[str]) -> str:
    feed_ids = list(feed_ids)
    return (
        f"Đã lưu {len(feed_ids)} chuyên mục:\n"
        f"{describe_feeds(catalog, feed_ids)}\n\n"
        "Bot sẽ gửi tin mới của các chuyên mục này.\n"
        f"{COMMAND_HINT}"
    )


def format_removal_menu(catalog: Catalog, feed_ids: Iterable[str]) -> str:
    return (
        "Trả lời các số muốn BỎ, cách nhau dấu cách hoặc dấu phẩy. Ví dụ: 1 47\n\n"
        f"Bạn đang nhận:\n{describe_feeds(catalog, feed_ids)}"
    )


def eligible_recipients(entry: SeenEntry, subscribers: dict[str, Subscriber]) -> list[str]:
    """Subscribers who selected one of the entry's feeds before the bot first saw it."""
    first_seen = parse_timestamp(entry.first_seen_at)
    delivered = set(entry.delivered_to)
    recipients: list[str] = []
    for chat_id, subscriber in subscribers.items():
        if chat_id in delivered:
            continue
        for feed_id in entry.feeds:
            selected_at = subscriber.feeds.get(feed_id)
            if selected_at is not None and first_seen >= parse_timestamp(selected_at):
                recipients.append(chat_id)
                break
    return sorted(recipients)
