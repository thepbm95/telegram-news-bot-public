from datetime import UTC, datetime
from pathlib import Path
import logging

import httpx
import pytest

from newsbot.feeds import (
    _fetch_feed,
    canonicalize_url,
    deduplicate_candidates,
    fetch_feeds,
    make_article_id,
    parse_feed,
)
from newsbot.models import FeedConfig


FIXTURES = Path(__file__).parent / "fixtures"


def test_canonicalize_removes_tracking_and_fragment_but_keeps_other_query() -> None:
    url = "https://VNEXPRESS.NET/a-123.html?utm_source=rss&keep=1#box_comment"

    assert canonicalize_url(url) == "https://vnexpress.net/a-123.html?keep=1"


def test_article_id_is_stable_for_tracking_variants() -> None:
    clean = "https://vnexpress.net/a-123.html"
    tracked = "https://vnexpress.net/a-123.html?utm_campaign=news#comments"

    assert make_article_id(clean) == make_article_id(tracked)


def test_parse_feed_preserves_source_category_and_plain_summary() -> None:
    feed = FeedConfig("VnExpress Thời sự", "VnExpress", "Thời sự", "https://example.test/rss")

    items = parse_feed((FIXTURES / "vnexpress-feed.xml").read_bytes(), feed)

    assert len(items) == 2
    assert items[0].source == "VnExpress"
    assert items[0].category == "Thời sự"
    assert items[0].url == "https://vnexpress.net/bai-thu-nhat-123.html?keep=1"
    assert items[0].rss_summary == "Mô tả bài thứ nhất."
    assert items[0].published_at is not None


def test_parse_dantri_feed_uses_same_contract() -> None:
    feed = FeedConfig("Dân trí Thế giới", "Dân trí", "Thế giới", "https://example.test/rss")

    items = parse_feed((FIXTURES / "dantri-feed.xml").read_bytes(), feed)

    assert [item.title for item in items] == ["Bài thế giới thử nghiệm"]
    assert items[0].url == "https://dantri.com.vn/the-gioi/bai-thu-nghiem-202609180930.htm"
    assert items[0].rss_summary == "Nội dung mô tả Dân trí."


def test_deduplicate_same_article_across_categories() -> None:
    feed = FeedConfig("VnExpress", "VnExpress", "Thời sự", "https://example.test/rss")
    items = parse_feed((FIXTURES / "vnexpress-feed.xml").read_bytes(), feed)

    deduplicated = deduplicate_candidates([items[0], items[0], items[1]])

    assert [item.title for item in deduplicated] == ["Bài thời sự thứ nhất", "Bài thời sự thứ hai"]


@pytest.mark.asyncio
async def test_failed_feed_log_includes_http_status(caplog) -> None:
    feed = FeedConfig("VnExpress Thời sự", "VnExpress", "Thời sự", "https://example.test/rss")
    transport = httpx.MockTransport(lambda request: httpx.Response(403, request=request))
    caplog.set_level(logging.WARNING, logger="newsbot.feeds")

    async with httpx.AsyncClient(transport=transport) as client:
        items = await _fetch_feed(client, feed, attempts=1)

    assert items == []
    assert "HTTP 403" in caplog.text


def test_parse_vccorp_feed_with_short_timezone_offset() -> None:
    feed = FeedConfig("GenK AI", "GenK", "AI", "https://genk.vn/rss/ai.rss", "genk-ai")

    items = parse_feed((FIXTURES / "vccorp-feed.xml").read_bytes(), feed)

    assert len(items) == 1
    assert items[0].feed_ids == ("genk-ai",)
    assert items[0].published_at == datetime(2026, 9, 22, 22, 45, tzinfo=UTC)
    assert items[0].rss_summary == "Mô tả bài AI."


def test_deduplicate_merges_feed_ids_in_order() -> None:
    first = FeedConfig("VnExpress Thời sự", "VnExpress", "Thời sự", "https://example.test/a", "vne-thoi-su")
    second = FeedConfig("VnExpress Pháp luật", "VnExpress", "Pháp luật", "https://example.test/b", "vne-phap-luat")
    xml = (FIXTURES / "vnexpress-feed.xml").read_bytes()

    merged = deduplicate_candidates([*parse_feed(xml, first), *parse_feed(xml, second), *parse_feed(xml, first)])

    assert len(merged) == 2
    assert all(item.feed_ids == ("vne-thoi-su", "vne-phap-luat") for item in merged)
    assert all(item.category == "Thời sự" for item in merged)


@pytest.mark.asyncio
async def test_fetch_feeds_reports_only_feeds_with_items() -> None:
    ok = FeedConfig("OK", "VnExpress", "Thời sự", "https://example.test/ok", "ok")
    empty = FeedConfig("Empty", "VnExpress", "Thời sự", "https://example.test/empty", "empty")
    broken = FeedConfig("Broken", "VnExpress", "Thời sự", "https://example.test/broken", "broken")
    body = (FIXTURES / "vnexpress-feed.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ok":
            return httpx.Response(200, content=body, request=request)
        if request.url.path == "/empty":
            return httpx.Response(200, content=b"<rss><channel></channel></rss>", request=request)
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_feeds(client, (ok, empty, broken), attempts=1)

    assert result.ok_feed_ids == {"ok"}
    assert len(result.candidates) == 2
