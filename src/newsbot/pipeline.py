from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Protocol

from newsbot.catalog import eligible_recipients
from newsbot.extractors import ExtractionError
from newsbot.feeds import FeedFetchResult
from newsbot.models import ArticleCandidate, ExtractedArticle, FeedConfig, SummaryResult
from newsbot.state import BotState, SeenEntry, parse_timestamp, prune_seen
from newsbot.subscriptions import SubscriptionStats
from newsbot.summarizers import SummarizationError
from newsbot.telegram import (
    TelegramApiError,
    TelegramAuthError,
    TelegramForbiddenError,
    format_article_message,
)


LOGGER = logging.getLogger(__name__)
# Refreshing last_seen_at more often would rewrite the state on every run without news,
# while pruning only needs day-level precision.
LAST_SEEN_REFRESH = timedelta(hours=1)


class StateStoreProtocol(Protocol):
    def load(self) -> BotState: ...

    def save(self, state: BotState) -> None: ...


class GeminiProtocol(Protocol):
    async def summarize(self, article: ExtractedArticle, state: BotState) -> SummaryResult: ...


class LocalProtocol(Protocol):
    def summarize(self, article: ExtractedArticle) -> SummaryResult: ...


class TelegramProtocol(Protocol):
    async def send_message(self, text: str, *, chat_id: str) -> None: ...


class SubscriptionProtocol(Protocol):
    async def sync(self, state: BotState) -> SubscriptionStats: ...


@dataclass(slots=True)
class RunStats:
    active_feeds: int = 0
    feeds_ok: int = 0
    feeds_failed: int = 0
    candidates: int = 0
    bootstrapped: int = 0
    sent: int = 0
    recipient_deliveries: int = 0
    recipient_failures: int = 0
    local_fallback: int = 0
    summaries_by_provider: dict[str, int] = field(default_factory=dict)
    skipped: int = 0
    failed: int = 0


def _published_key(candidate: ArticleCandidate) -> tuple[datetime, str]:
    published = candidate.published_at or datetime.min.replace(tzinfo=UTC)
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    return published.astimezone(UTC), candidate.article_id


class Pipeline:
    def __init__(
        self,
        *,
        state_store: StateStoreProtocol,
        feeds: tuple[FeedConfig, ...],
        feed_loader: Callable[[tuple[FeedConfig, ...]], Awaitable[FeedFetchResult]],
        article_loader: Callable[[ArticleCandidate], Awaitable[ExtractedArticle]],
        gemini: GeminiProtocol | None,
        local: LocalProtocol,
        telegram: TelegramProtocol | None,
        subscriptions: SubscriptionProtocol | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_seen_articles: int = 10_000,
        retention_days: int = 7,
    ) -> None:
        self.state_store = state_store
        self.feeds = feeds
        self.feed_loader = feed_loader
        self.article_loader = article_loader
        self.gemini = gemini
        self.local = local
        self.telegram = telegram
        self.subscriptions = subscriptions
        self.now = now
        self.max_seen_articles = max_seen_articles
        self.retention_days = retention_days
        # Followed feeds whose entries have not been refreshed by a successful fetch this run.
        self._unrefreshed_feeds: set[str] = set()

    def _timestamp(self) -> str:
        value = self.now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()

    def _record(self, state: BotState, candidate: ArticleCandidate, seen_at: str) -> SeenEntry:
        entry = SeenEntry(
            url=candidate.url,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            feeds=list(candidate.feed_ids),
        )
        state.seen[candidate.article_id] = entry
        return entry

    def _save(self, state: BotState) -> BotState:
        state.seen = prune_seen(
            state,
            self.now(),
            max_items=self.max_seen_articles,
            retention_days=self.retention_days,
            protected_feeds=self._unrefreshed_feeds,
        )
        self.state_store.save(state)
        return state

    async def _dry_run(
        self,
        candidates: list[ArticleCandidate],
        stats: RunStats,
        limit: int | None,
    ) -> RunStats:
        selected = sorted(candidates, key=_published_key, reverse=True)
        if limit is not None:
            selected = selected[:limit]
        for candidate in selected:
            try:
                article = await self.article_loader(candidate)
                LOGGER.info(
                    "Dry-run extracted %s words: %s (%s)",
                    article.word_count,
                    candidate.title,
                    candidate.url,
                )
                stats.skipped += 1
            except ExtractionError:
                LOGGER.warning("Dry-run extraction failed: %s (%s)", candidate.title, candidate.url)
                stats.failed += 1
        return stats

    async def _build_message(
        self,
        candidate: ArticleCandidate,
        state: BotState,
        stats: RunStats,
    ) -> str:
        try:
            article = await self.article_loader(candidate)
        except ExtractionError:
            fallback_text = candidate.rss_summary or "Không lấy được toàn văn của bài viết."
            summary = SummaryResult(
                text=f"Không lấy được toàn văn. {fallback_text}".strip(),
                provider="rss",
                input_words=len(fallback_text.split()),
                output_words=len(fallback_text.split()),
            )
        else:
            try:
                if self.gemini is None:
                    raise SummarizationError("Gemini is not configured")
                summary = await self.gemini.summarize(article, state)
            except SummarizationError:
                summary = self.local.summarize(article)
                stats.local_fallback += 1
        stats.summaries_by_provider[summary.provider] = stats.summaries_by_provider.get(summary.provider, 0) + 1
        return format_article_message(candidate, summary)

    async def _deliver_entry(self, article_id: str, state: BotState, stats: RunStats) -> bool:
        entry = state.seen[article_id]
        if entry.pending_message is None:
            return False

        delivered_any = False
        for chat_id in eligible_recipients(entry, state.subscribers):
            try:
                if self.telegram is None:
                    raise TelegramAuthError("Telegram is not configured")
                await self.telegram.send_message(entry.pending_message, chat_id=chat_id)
            except TelegramAuthError:
                raise
            except TelegramForbiddenError:
                state.subscribers.pop(chat_id, None)
                stats.recipient_failures += 1
                stats.failed += 1
                LOGGER.warning("Telegram recipient is unavailable; subscription removed")
                self._save(state)
            except TelegramApiError:
                stats.recipient_failures += 1
                stats.failed += 1
                LOGGER.warning("Telegram delivery failed temporarily; recipient remains pending")
            else:
                entry.delivered_to = sorted(set([*entry.delivered_to, chat_id]))
                stats.recipient_deliveries += 1
                delivered_any = True
                self._save(state)

        if not eligible_recipients(entry, state.subscribers):
            entry.pending_message = None
            self._save(state)
        return delivered_any

    async def _retry_pending(self, state: BotState, stats: RunStats) -> None:
        for article_id, entry in list(state.seen.items()):
            if entry.pending_message is None:
                continue
            if await self._deliver_entry(article_id, state, stats):
                stats.sent += 1

    def _classify(
        self,
        state: BotState,
        result: FeedFetchResult,
        stats: RunStats,
    ) -> list[ArticleCandidate]:
        """Refresh known entries, bootstrap first-time feeds and return genuinely new articles."""
        seen_at = self._timestamp()
        refresh_before = parse_timestamp(seen_at) - LAST_SEEN_REFRESH
        new_items: list[ArticleCandidate] = []
        for candidate in result.candidates:
            entry = state.seen.get(candidate.article_id)
            if entry is not None:
                if parse_timestamp(entry.last_seen_at) <= refresh_before:
                    entry.last_seen_at = seen_at
                entry.feeds = list(dict.fromkeys((*entry.feeds, *candidate.feed_ids)))
                continue
            if not state.bootstrapped_feeds.intersection(candidate.feed_ids):
                entry = self._record(state, candidate, seen_at)
                entry.delivered_to = eligible_recipients(entry, state.subscribers)
                stats.bootstrapped += 1
                continue
            new_items.append(candidate)
        state.bootstrapped_feeds |= result.ok_feed_ids
        return new_items

    async def run(self, *, dry_run: bool = False, limit: int | None = None) -> RunStats:
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")

        state = self.state_store.load()
        if dry_run:
            result = await self.feed_loader(self.feeds)
            stats = RunStats(
                active_feeds=len(self.feeds),
                feeds_ok=len(result.ok_feed_ids),
                feeds_failed=len(self.feeds) - len(result.ok_feed_ids),
                candidates=len(result.candidates),
            )
            unseen = [item for item in result.candidates if item.article_id not in state.seen]
            return await self._dry_run(unseen, stats, limit)

        stats = RunStats()
        self._unrefreshed_feeds = set(state.bootstrapped_feeds)
        try:
            if self.subscriptions is not None:
                await self.subscriptions.sync(state)
            self._save(state)

            await self._retry_pending(state, stats)

            active_ids = {feed_id for subscriber in state.subscribers.values() for feed_id in subscriber.feeds}
            # A feed nobody follows must bootstrap again when re-selected, so its backlog is not sent.
            state.bootstrapped_feeds &= active_ids
            active = tuple(feed for feed in self.feeds if feed.id in active_ids)
            stats.active_feeds = len(active)
            if not active:
                return stats

            result = await self.feed_loader(active)
            stats.feeds_ok = len(result.ok_feed_ids)
            stats.feeds_failed = len(active) - len(result.ok_feed_ids)
            stats.candidates = len(result.candidates)
            new_items = sorted(self._classify(state, result, stats), key=_published_key)
            self._unrefreshed_feeds = state.bootstrapped_feeds - result.ok_feed_ids
            self._save(state)
            if limit is not None:
                new_items = new_items[:limit]

            for candidate in new_items:
                entry = self._record(state, candidate, self._timestamp())
                if not eligible_recipients(entry, state.subscribers):
                    self._save(state)
                    continue
                entry.pending_message = await self._build_message(candidate, state, stats)
                self._save(state)
                if await self._deliver_entry(candidate.article_id, state, stats):
                    stats.sent += 1
            return stats
        finally:
            self._save(state)
