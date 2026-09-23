from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging

import pytest

from newsbot.extractors import ExtractionError
from newsbot.feeds import FeedFetchResult, deduplicate_candidates
from newsbot.models import ArticleCandidate, ExtractedArticle, FeedConfig, SummaryResult
from newsbot.pipeline import Pipeline
from newsbot.state import BotState, SeenEntry, Subscriber
from newsbot.subscriptions import SubscriptionStats
from newsbot.summarizers import SummarizationError
from newsbot.telegram import TelegramApiError, TelegramAuthError, TelegramForbiddenError


START = datetime(2026, 9, 23, 12, tzinfo=UTC)
FEEDS = (
    FeedConfig("VnExpress Thời sự", "VnExpress", "Thời sự", "https://example.test/a.rss", "a"),
    FeedConfig("VnExpress Pháp luật", "VnExpress", "Pháp luật", "https://example.test/b.rss", "b"),
    FeedConfig("Kenh14 Star", "Kenh14", "Star", "https://example.test/c.rss", "c"),
)


def article(article_id: str, *feed_ids: str, minute: int = 0) -> ArticleCandidate:
    return ArticleCandidate(
        article_id=article_id,
        source="VnExpress",
        category="Thời sự",
        title=f"Bài {article_id}",
        url=f"https://example.test/{article_id}",
        published_at=START + timedelta(minutes=minute),
        rss_summary=f"Mô tả RSS {article_id}",
        feed_ids=feed_ids,
    )


def selected(*feed_ids: str, at: datetime = START - timedelta(days=1)) -> Subscriber:
    return Subscriber(feeds={feed_id: at.isoformat() for feed_id in feed_ids})


class Clock:
    def __init__(self) -> None:
        self.value = START

    def __call__(self) -> datetime:
        return self.value

    def advance(self, minutes: int = 15) -> None:
        self.value += timedelta(minutes=minutes)


class MemoryStateStore:
    def __init__(self, state: BotState) -> None:
        self.state = deepcopy(state)
        self.save_calls = 0

    def load(self) -> BotState:
        return deepcopy(self.state)

    def save(self, state: BotState) -> None:
        self.save_calls += 1
        self.state = deepcopy(state)


class FakeFeeds:
    """RSS content per feed id; a feed mapped to None fails to load."""

    def __init__(self, content: dict[str, list[ArticleCandidate] | None]) -> None:
        self.content = content
        self.requests: list[set[str]] = []

    async def __call__(self, feeds: tuple[FeedConfig, ...]) -> FeedFetchResult:
        self.requests.append({feed.id for feed in feeds})
        candidates: list[ArticleCandidate] = []
        ok: set[str] = set()
        for feed in feeds:
            items = self.content.get(feed.id)
            if items:
                ok.add(feed.id)
                candidates.extend(replace(item, feed_ids=(feed.id,)) for item in items)
        return FeedFetchResult(deduplicate_candidates(candidates), ok)


class FakeGemini:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def summarize(self, extracted: ExtractedArticle, state: BotState) -> SummaryResult:
        self.calls.append(extracted.candidate.article_id)
        if self.fail:
            raise SummarizationError("quota")
        return SummaryResult("Tóm tắt Gemini", "gemini-3.1-flash-lite", extracted.word_count, 3)


class FakeLocal:
    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, extracted: ExtractedArticle) -> SummaryResult:
        self.calls += 1
        return SummaryResult("Tóm tắt cục bộ", "local", extracted.word_count, 4)


class FakeArticleLoader:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    async def __call__(self, candidate: ArticleCandidate) -> ExtractedArticle:
        self.calls.append(candidate.article_id)
        if self.fail:
            raise ExtractionError("content")
        return ExtractedArticle(candidate, "Nội dung đầy đủ của bài viết " * 20, 120)


class FakeTelegram:
    def __init__(self, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, text: str, *, chat_id: str) -> None:
        error = self.errors.get(chat_id)
        if error is not None:
            raise error
        self.sent.append((chat_id, text))

    def recipients(self) -> list[str]:
        return [chat_id for chat_id, _ in self.sent]


class Harness:
    def __init__(
        self,
        state: BotState,
        content: dict[str, list[ArticleCandidate] | None],
        *,
        telegram_errors: dict[str, Exception] | None = None,
        gemini_fail: bool = False,
        extraction_fail: bool = False,
        subscriptions: object | None = None,
    ) -> None:
        self.clock = Clock()
        self.store = MemoryStateStore(state)
        self.feeds = FakeFeeds(content)
        self.telegram = FakeTelegram(telegram_errors)
        self.gemini = FakeGemini(gemini_fail)
        self.local = FakeLocal()
        self.loader = FakeArticleLoader(extraction_fail)
        self.pipeline = Pipeline(
            state_store=self.store,
            feeds=FEEDS,
            feed_loader=self.feeds,
            article_loader=self.loader,
            gemini=self.gemini,
            local=self.local,
            telegram=self.telegram,
            subscriptions=subscriptions,
            now=self.clock,
        )

    async def run(self, **kwargs):
        stats = await self.pipeline.run(**kwargs)
        self.clock.advance()
        return stats

    @property
    def state(self) -> BotState:
        return self.store.state


def bootstrapped(*subscribers: tuple[str, Subscriber], feeds: set[str] = {"a", "b", "c"}) -> BotState:
    return BotState(subscribers=dict(subscribers), bootstrapped_feeds=set(feeds))


@pytest.mark.asyncio
async def test_no_selected_feed_means_no_rss_request() -> None:
    harness = Harness(BotState(subscribers={"111": Subscriber()}), {"a": [article("x")]})

    stats = await harness.run()

    assert harness.feeds.requests == []
    assert stats.active_feeds == 0
    assert harness.telegram.sent == []


@pytest.mark.asyncio
async def test_only_selected_feeds_are_fetched() -> None:
    state = BotState(subscribers={"111": selected("a"), "222": selected("c")})
    harness = Harness(state, {"a": [article("x")], "b": [article("y")], "c": [article("z")]})

    await harness.run()

    assert harness.feeds.requests == [{"a", "c"}]


@pytest.mark.asyncio
async def test_first_fetch_of_feed_bootstraps_without_sending() -> None:
    harness = Harness(BotState(subscribers={"111": selected("a")}), {"a": [article("old-1"), article("old-2")]})

    stats = await harness.run()

    assert stats.bootstrapped == 2
    assert harness.gemini.calls == []
    assert harness.telegram.sent == []
    assert harness.state.bootstrapped_feeds == {"a"}
    assert harness.state.seen["old-1"].delivered_to == ["111"]


@pytest.mark.asyncio
async def test_new_article_after_bootstrap_goes_only_to_matching_subscriber() -> None:
    harness = Harness(
        bootstrapped(("111", selected("a")), ("222", selected("c"))),
        {"a": [article("new")], "c": []},
    )

    stats = await harness.run()

    assert harness.telegram.recipients() == ["111"]
    assert stats.sent == 1
    assert stats.summaries_by_provider == {"gemini-3.1-flash-lite": 1}
    assert harness.state.seen["new"].feeds == ["a"]
    assert harness.state.seen["new"].pending_message is None


@pytest.mark.asyncio
async def test_article_in_two_feeds_is_summarized_once_for_both_selectors() -> None:
    shared = article("shared")
    harness = Harness(
        bootstrapped(("111", selected("a")), ("222", selected("b"))),
        {"a": [shared], "b": [shared]},
    )

    await harness.run()

    assert harness.gemini.calls == ["shared"]
    assert sorted(harness.telegram.recipients()) == ["111", "222"]
    assert harness.state.seen["shared"].feeds == ["a", "b"]


@pytest.mark.asyncio
async def test_subscriber_does_not_get_articles_seen_before_choosing_the_feed() -> None:
    harness = Harness(bootstrapped(("111", selected("a"))), {"a": [article("first")]})
    await harness.run()
    harness.state.subscribers["222"] = selected("a", at=harness.clock.value)
    harness.feeds.content["a"] = [article("first"), article("second", minute=5)]

    await harness.run()

    assert harness.telegram.recipients() == ["111", "111", "222"]
    assert harness.state.seen["second"].delivered_to == ["111", "222"]


@pytest.mark.asyncio
async def test_feed_dropped_by_everyone_bootstraps_again_when_reselected() -> None:
    harness = Harness(bootstrapped(("111", selected("a")), feeds={"a"}), {"a": [article("one")]})
    await harness.run()
    harness.state.subscribers["111"] = selected("c")
    harness.feeds.content["c"] = [article("star")]
    await harness.run()
    assert "a" not in harness.state.bootstrapped_feeds

    harness.state.subscribers["111"] = selected("a", at=harness.clock.value)
    harness.feeds.content["a"] = [article("missed-while-inactive")]
    stats = await harness.run()

    assert stats.bootstrapped == 1
    assert harness.telegram.recipients() == ["111"]
    assert harness.gemini.calls == ["one"]


@pytest.mark.asyncio
async def test_failed_feed_is_not_marked_bootstrapped() -> None:
    harness = Harness(BotState(subscribers={"111": selected("a", "c")}), {"a": None, "c": [article("z")]})

    stats = await harness.run()

    assert harness.state.bootstrapped_feeds == {"c"}
    assert stats.feeds_failed == 1


@pytest.mark.asyncio
async def test_last_seen_is_refreshed_so_long_lived_rss_items_are_not_pruned() -> None:
    harness = Harness(bootstrapped(("111", selected("a"))), {"a": [article("x")]})
    await harness.run()
    first_seen = harness.state.seen["x"].first_seen_at
    harness.clock.advance(60 * 24 * 10)

    await harness.run()

    entry = harness.state.seen["x"]
    assert entry.first_seen_at == first_seen
    assert entry.last_seen_at == (harness.clock.value - timedelta(minutes=15)).isoformat()
    assert len(harness.telegram.sent) == 1


@pytest.mark.asyncio
async def test_items_of_a_failing_feed_are_kept_until_it_recovers() -> None:
    harness = Harness(bootstrapped(("111", selected("a"))), {"a": [article("x")]})
    await harness.run()
    harness.feeds.content["a"] = None
    harness.clock.advance(60 * 24 * 10)
    await harness.run()
    assert "x" in harness.state.seen

    harness.feeds.content["a"] = [article("x")]
    await harness.run()

    assert len(harness.telegram.sent) == 1


@pytest.mark.asyncio
async def test_temporary_failure_retries_only_missing_recipient_without_resummarizing() -> None:
    harness = Harness(
        bootstrapped(("111", selected("a")), ("222", selected("a"))),
        {"a": [article("x")]},
        telegram_errors={"222": TelegramApiError("temporary")},
    )
    await harness.run()
    assert harness.state.seen["x"].delivered_to == ["111"]
    harness.telegram.errors.clear()
    harness.feeds.content["a"] = []

    stats = await harness.run()

    assert harness.gemini.calls == ["x"]
    assert harness.telegram.recipients() == ["111", "222"]
    assert harness.state.seen["x"].pending_message is None
    assert stats.sent == 1


@pytest.mark.asyncio
async def test_forbidden_recipient_is_removed_without_blocking_others() -> None:
    harness = Harness(
        bootstrapped(("111", selected("a")), ("222", selected("a"))),
        {"a": [article("x")]},
        telegram_errors={"111": TelegramForbiddenError("blocked")},
    )

    await harness.run()

    assert harness.telegram.recipients() == ["222"]
    assert set(harness.state.subscribers) == {"222"}
    assert harness.state.seen["x"].pending_message is None


@pytest.mark.asyncio
async def test_bot_auth_failure_preserves_pending_state_before_stopping() -> None:
    harness = Harness(
        bootstrapped(("111", selected("a"))),
        {"a": [article("x")]},
        telegram_errors={"111": TelegramAuthError("bad token")},
    )

    with pytest.raises(TelegramAuthError):
        await harness.run()

    assert harness.state.seen["x"].pending_message is not None
    assert harness.state.seen["x"].delivered_to == []


@pytest.mark.asyncio
async def test_recipient_failure_log_does_not_expose_full_chat_id(caplog) -> None:
    full_chat_id = "1234567890123"
    harness = Harness(
        bootstrapped((full_chat_id, selected("a"))),
        {"a": [article("x")]},
        telegram_errors={full_chat_id: TelegramApiError("temporary")},
    )
    caplog.set_level(logging.WARNING)

    await harness.run()

    assert full_chat_id not in caplog.text


@pytest.mark.asyncio
async def test_limit_processes_oldest_new_articles_and_leaves_rest_for_next_run() -> None:
    harness = Harness(
        bootstrapped(("111", selected("a"))),
        {"a": [article("late", minute=9), article("early", minute=1)]},
    )

    await harness.run(limit=1)

    assert harness.gemini.calls == ["early"]
    assert "late" not in harness.state.seen

    await harness.run()

    assert harness.gemini.calls == ["early", "late"]


@pytest.mark.asyncio
async def test_subscription_sync_runs_before_feed_selection() -> None:
    class SelectFeed:
        async def sync(self, state: BotState) -> SubscriptionStats:
            state.subscribers["111"] = selected("c")
            return SubscriptionStats()

    harness = Harness(BotState(), {"c": [article("z")]}, subscriptions=SelectFeed())

    await harness.run()

    assert harness.feeds.requests == [{"c"}]


@pytest.mark.asyncio
async def test_dry_run_fetches_every_feed_without_sending_or_saving() -> None:
    harness = Harness(BotState(), {"a": [article("x")], "b": [article("y")]})

    stats = await harness.run(dry_run=True)

    assert harness.feeds.requests == [{"a", "b", "c"}]
    assert stats.candidates == 2
    assert harness.loader.calls == ["y", "x"] or harness.loader.calls == ["x", "y"]
    assert harness.store.save_calls == 0
    assert harness.telegram.sent == []


@pytest.mark.asyncio
async def test_gemini_failure_uses_local_fallback() -> None:
    harness = Harness(bootstrapped(("111", selected("a"))), {"a": [article("x")]}, gemini_fail=True)

    stats = await harness.run()

    assert harness.local.calls == 1
    assert stats.local_fallback == 1
    assert "Tóm tắt cục bộ" in harness.telegram.sent[0][1]


@pytest.mark.asyncio
async def test_extraction_failure_sends_rss_description_without_ai() -> None:
    harness = Harness(bootstrapped(("111", selected("a"))), {"a": [article("x")]}, extraction_fail=True)

    await harness.run()

    assert harness.gemini.calls == []
    assert "Không lấy được toàn văn. Mô tả RSS x" in harness.telegram.sent[0][1]


@pytest.mark.asyncio
async def test_migrated_history_is_never_resent() -> None:
    state = BotState(
        subscribers={"111": selected("a", at=START - timedelta(minutes=1))},
        seen={
            "old": SeenEntry(
                url="https://example.test/old",
                first_seen_at=(START - timedelta(days=2)).isoformat(),
                last_seen_at=(START - timedelta(days=2)).isoformat(),
            )
        },
    )
    harness = Harness(state, {"a": [article("old")]})

    await harness.run()
    harness.feeds.content["a"] = [article("old")]
    await harness.run()

    assert harness.telegram.sent == []
    assert harness.state.seen["old"].feeds == ["a"]
