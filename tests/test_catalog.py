from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsbot.catalog import (
    Catalog,
    SelectionError,
    eligible_recipients,
    format_menu,
    format_selection,
    is_selection_text,
    parse_selection,
)
from newsbot.config import load_settings
from newsbot.state import SeenEntry, Subscriber


NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
CATALOG = Catalog(load_settings({}, Path("config/feeds.toml"), require_secrets=False).feeds)


def test_parse_numbers_ranges_commas_and_duplicates() -> None:
    ids = parse_selection("1 2, 20 40-42 2", CATALOG)

    assert ids == [
        CATALOG.by_number(1).id,
        CATALOG.by_number(2).id,
        CATALOG.by_number(20).id,
        CATALOG.by_number(40).id,
        CATALOG.by_number(41).id,
        CATALOG.by_number(42).id,
    ]


def test_parse_accepts_spaces_around_dash_and_en_dash() -> None:
    assert parse_selection("3 – 5", CATALOG) == parse_selection("3-5", CATALOG)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("0", "Số 0 không có trong danh sách"),
        ("56", "Số 56 không có trong danh sách"),
        ("1-100000", "Số 100000 không có trong danh sách"),
        ("5-3", "Dải số 5-3 không hợp lệ"),
        ("1-11", "tối đa 10"),
        ("1-3-5", "Không hiểu"),
        ("abc", "trả lời bằng các số"),
    ],
)
def test_invalid_selections_explain_the_problem(text: str, message: str) -> None:
    with pytest.raises(SelectionError, match=message):
        parse_selection(text, CATALOG)


def test_exactly_ten_items_are_allowed() -> None:
    assert len(parse_selection("1-10", CATALOG)) == 10


@pytest.mark.parametrize(("text", "expected"), [("1 2", True), (" 3-4, 5 ", True), ("-", False), ("/chon", False), ("hi 1", False)])
def test_is_selection_text(text: str, expected: bool) -> None:
    assert is_selection_text(text) is expected


def test_menu_lists_every_newspaper_and_number() -> None:
    text = "\n".join(format_menu(CATALOG))

    for source in ["VnExpress", "Dân trí", "CafeBiz", "GenK", "Kenh14"]:
        assert f"\n{source}\n" in text
    assert "1. Thời sự" in text
    assert f"55. {CATALOG.by_number(55).category}" in text
    assert "tối đa 10 chuyên mục" in text


def test_announcement_menu_explains_the_pause() -> None:
    first = format_menu(CATALOG, announcement=True)[0]

    assert "CafeBiz, GenK và Kenh14" in first
    assert "tạm dừng gửi tin" in first


def test_menu_splits_between_newspapers_when_too_long() -> None:
    chunks = format_menu(CATALOG, limit=700)

    assert len(chunks) > 1
    assert all(len(chunk) <= 700 for chunk in chunks)
    assert chunks[1].startswith(("VnExpress", "Dân trí", "CafeBiz", "GenK", "Kenh14"))


def test_selection_confirmation_names_newspaper_category_and_number() -> None:
    text = format_selection(CATALOG, ["dt-thoi-su", "vne-thoi-su"])

    assert "Đã lưu 2 chuyên mục" in text
    assert text.index("VnExpress – Thời sự (1)") < text.index("Dân trí – Thời sự (18)")


def test_eligible_recipients_require_matching_feed_selected_before_first_seen() -> None:
    entry = SeenEntry(
        url="https://example.test/a",
        first_seen_at=NOW.isoformat(),
        last_seen_at=NOW.isoformat(),
        feeds=["vne-thoi-su", "vne-phap-luat"],
        delivered_to=["done"],
    )
    earlier = (NOW - timedelta(minutes=1)).isoformat()
    later = (NOW + timedelta(minutes=1)).isoformat()
    subscribers = {
        "match": Subscriber(feeds={"vne-phap-luat": earlier}),
        "same-time": Subscriber(feeds={"vne-thoi-su": NOW.isoformat()}),
        "too-late": Subscriber(feeds={"vne-thoi-su": later}),
        "other-feed": Subscriber(feeds={"dt-thoi-su": earlier}),
        "none": Subscriber(),
        "done": Subscriber(feeds={"vne-thoi-su": earlier}),
    }

    assert eligible_recipients(entry, subscribers) == ["match", "same-time"]
