from __future__ import annotations

import asyncio
import calendar
import email.utils
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from newsbot.models import ArticleCandidate, FeedConfig


LOGGER = logging.getLogger(__name__)
TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}


SHORT_OFFSET = re.compile(r"([+-]\d{2})$")


class FeedParseError(ValueError):
    """Raised when an RSS response cannot produce valid entries."""


@dataclass(slots=True)
class FeedFetchResult:
    candidates: list[ArticleCandidate] = field(default_factory=list)
    ok_feed_ids: set[str] = field(default_factory=set)


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_KEYS
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(query, doseq=True),
            "",
        )
    )


def make_article_id(url: str) -> str:
    return sha256(canonicalize_url(url).encode("utf-8")).hexdigest()


def _plain_text(html: str) -> str:
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    text = re.sub(r"\s+([,.;:!?%)\]])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def _published_at(entry: feedparser.FeedParserDict) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is not None:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    raw = str(entry.get("published") or entry.get("updated") or "").strip()
    if not raw:
        return None
    # CafeBiz, GenK and Kenh14 write offsets as "+07", which feedparser rejects.
    try:
        value = email.utils.parsedate_to_datetime(SHORT_OFFSET.sub(r"\g<1>00", raw))
    except (TypeError, ValueError):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_feed(xml: bytes, feed: FeedConfig) -> list[ArticleCandidate]:
    parsed = feedparser.parse(xml)
    candidates: list[ArticleCandidate] = []
    for entry in parsed.entries:
        title = _plain_text(str(entry.get("title", "")))
        raw_url = str(entry.get("link", "")).strip()
        if not title or not raw_url:
            continue
        url = canonicalize_url(raw_url)
        candidates.append(
            ArticleCandidate(
                article_id=make_article_id(url),
                source=feed.source,
                category=feed.category,
                title=title,
                url=url,
                published_at=_published_at(entry),
                rss_summary=_plain_text(str(entry.get("summary", entry.get("description", "")))),
                feed_ids=(feed.id,) if feed.id else (),
            )
        )
    if parsed.bozo and not candidates:
        raise FeedParseError(f"Unable to parse feed: {feed.name}")
    return candidates


def deduplicate_candidates(candidates: list[ArticleCandidate]) -> list[ArticleCandidate]:
    unique: dict[str, ArticleCandidate] = {}
    for candidate in candidates:
        existing = unique.get(candidate.article_id)
        if existing is None:
            unique[candidate.article_id] = candidate
            continue
        merged = tuple(dict.fromkeys((*existing.feed_ids, *candidate.feed_ids)))
        if merged != existing.feed_ids:
            unique[candidate.article_id] = replace(existing, feed_ids=merged)
    far_future = datetime.max.replace(tzinfo=UTC)
    return sorted(
        unique.values(),
        key=lambda item: (item.published_at or far_future, item.article_id),
    )


async def _fetch_feed(
    client: httpx.AsyncClient,
    feed: FeedConfig,
    *,
    attempts: int = 3,
) -> list[ArticleCandidate]:
    for attempt in range(attempts):
        try:
            response = await client.get(feed.url, timeout=15.0)
            response.raise_for_status()
            return parse_feed(response.content, feed)
        except (httpx.HTTPError, FeedParseError) as exc:
            if attempt == attempts - 1:
                detail = (
                    f"HTTP {exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else type(exc).__name__
                )
                LOGGER.warning("Feed failed after %s attempts: %s (%s)", attempts, feed.name, detail)
                return []
            await asyncio.sleep(0.25 * (2**attempt))
    return []


async def fetch_feeds(
    client: httpx.AsyncClient,
    feeds: tuple[FeedConfig, ...],
    *,
    attempts: int = 3,
) -> FeedFetchResult:
    results = await asyncio.gather(*(_fetch_feed(client, feed, attempts=attempts) for feed in feeds))
    return FeedFetchResult(
        candidates=deduplicate_candidates([candidate for result in results for candidate in result]),
        ok_feed_ids={feed.id for feed, result in zip(feeds, results) if result},
    )


async def fetch_all_feeds(
    client: httpx.AsyncClient,
    feeds: tuple[FeedConfig, ...],
) -> list[ArticleCandidate]:
    return (await fetch_feeds(client, feeds)).candidates
