from datetime import UTC, datetime

import pytest

from newsbot.models import ArticleCandidate, ExtractedArticle
from newsbot.state import BotState, UsageState
from newsbot.config import ModelSpec
from newsbot.summarizers import (
    GeminiSummarizer,
    LocalSummarizer,
    ModelSlot,
    QuotaExceeded,
    QuotaGuard,
    SlidingWindowRateLimiter,
    SummarizationError,
    build_slots,
    summary_bounds,
)


NOW = datetime(2026, 9, 18, 16, tzinfo=UTC)


def slot(model: str = "gemini-3.1-flash-lite", rpd: int = 450) -> ModelSlot:
    return ModelSlot(model, QuotaGuard(model, rpd), SlidingWindowRateLimiter(max_calls=10, period_seconds=60))


def article_with_words(word_count: int) -> ExtractedArticle:
    candidate = ArticleCandidate(
        article_id="id",
        source="VnExpress",
        category="Thời sự",
        title="Tiêu đề thử nghiệm",
        url="https://example.test/article",
        published_at=None,
        rss_summary="",
    )
    text = " ".join(f"từ{index}" for index in range(word_count))
    return ExtractedArticle(candidate, text, word_count)


def test_summary_bounds_follow_percentage_and_hard_cap() -> None:
    assert summary_bounds(500) == (100, 125)
    assert summary_bounds(1_000) == (200, 250)
    assert summary_bounds(2_000) == (300, 300)
    assert summary_bounds(4_000) == (300, 300)


def test_local_summary_never_exceeds_maximum() -> None:
    sentences = [
        f"Câu số {index} cung cấp dữ kiện quan trọng về sự việc đang được trình bày."
        for index in range(500)
    ]
    candidate = article_with_words(1).candidate
    text = " ".join(sentences)
    article = ExtractedArticle(candidate, text, len(text.split()))

    result = LocalSummarizer().summarize(article)

    assert result.provider == "local"
    assert 1 <= result.output_words <= 300
    assert result.input_words == article.word_count


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.now += seconds


@pytest.mark.asyncio
async def test_rate_limiter_never_exceeds_ten_calls_per_minute() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(max_calls=10, period_seconds=60, clock=clock, sleep=clock.sleep)

    for _ in range(11):
        await limiter.acquire()

    assert clock.slept == 60


def test_quota_guard_resets_on_new_pacific_day_and_stops_at_limit() -> None:
    state = BotState(usage=UsageState(quota_day="2026-09-17", requests={"m": 450, "other": 3}))
    guard = QuotaGuard("m", max_requests=2)
    now = datetime(2026, 9, 18, 16, tzinfo=UTC)

    guard.before_request(state, now)
    guard.before_request(state, now)

    assert state.usage.requests == {"m": 2}
    with pytest.raises(QuotaExceeded):
        guard.before_request(state, now)


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeModels:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, str]] = []

    async def generate_content(self, *, model: str, contents: str) -> FakeResponse:
        self.calls.append({"model": model, "contents": contents})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(str(outcome))


class FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.aio = type("Aio", (), {"models": FakeModels(outcomes)})()


class FakeApiError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


@pytest.mark.asyncio
async def test_gemini_retries_rate_limit_counts_requests_and_trims_output() -> None:
    sentence = " ".join(["tóm-tắt"] * 49 + ["xong."])
    client = FakeClient([FakeApiError(429), FakeApiError(429), " ".join([sentence] * 7)])
    state = BotState()
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    summarizer = GeminiSummarizer(
        api_key="test-key",
        slots=[slot()],
        client=client,
        sleep=fake_sleep,
        now=lambda: datetime(2026, 9, 18, 16, tzinfo=UTC),
    )

    result = await summarizer.summarize(article_with_words(2_000), state)

    assert result.provider == "gemini-3.1-flash-lite"
    assert result.output_words == 300
    assert state.usage.requests == {"gemini-3.1-flash-lite": 3}
    assert sleep_calls == [2, 4]
    assert len(client.aio.models.calls) == 3
    assert client.aio.models.calls[0]["model"] == "gemini-3.1-flash-lite"
    assert "300 từ" in client.aio.models.calls[0]["contents"]


@pytest.mark.asyncio
async def test_gemini_overflow_is_trimmed_at_a_complete_sentence() -> None:
    first = " ".join(["ý"] * 79 + ["một."])
    second = " ".join(["ý"] * 79 + ["hai."])
    third = " ".join(["chi-tiết"] * 29 + ["ba."])
    client = FakeClient([f"{first} {second} {third}"])
    summarizer = GeminiSummarizer(
        api_key="test-key",
        slots=[slot()],
        client=client,
        now=lambda: datetime(2026, 9, 18, 16, tzinfo=UTC),
    )

    result = await summarizer.summarize(article_with_words(698), BotState())

    assert result.output_words == 160
    assert result.text.endswith("hai.")
    assert "chi-tiết" not in result.text


@pytest.mark.asyncio
async def test_gemini_prompt_requests_complete_coverage_without_cutting_sentences() -> None:
    client = FakeClient(["Bản tóm tắt hoàn chỉnh."])
    summarizer = GeminiSummarizer(
        api_key="test-key",
        slots=[slot()],
        client=client,
        now=lambda: datetime(2026, 9, 18, 16, tzinfo=UTC),
    )

    await summarizer.summarize(article_with_words(698), BotState())

    prompt = client.aio.models.calls[0]["contents"]
    assert "Bao quát đủ các ý chính" in prompt
    assert "không cắt giữa câu" in prompt
    assert "Mục tiêu khoảng 157 từ" in prompt


def summarizer_for(client: "FakeClient", slots: list[ModelSlot]) -> GeminiSummarizer:
    async def no_sleep(seconds: float) -> None:
        return None

    return GeminiSummarizer(api_key="test-key", slots=slots, client=client, sleep=no_sleep, now=lambda: NOW)


@pytest.mark.asyncio
async def test_exhausted_model_is_skipped_without_a_request() -> None:
    client = FakeClient(["Tóm tắt từ model hai."])
    state = BotState(usage=UsageState(quota_day="2026-09-18", requests={"one": 1}))

    result = await summarizer_for(client, [slot("one", rpd=1), slot("two")]).summarize(article_with_words(400), state)

    assert result.provider == "two"
    assert [call["model"] for call in client.aio.models.calls] == ["two"]
    assert state.usage.requests == {"one": 1, "two": 1}


@pytest.mark.asyncio
async def test_transient_errors_retry_twice_then_move_to_next_model() -> None:
    client = FakeClient([FakeApiError(503), FakeApiError(429), FakeApiError(500), "Tóm tắt dự phòng."])

    result = await summarizer_for(client, [slot("one"), slot("two")]).summarize(article_with_words(400), BotState())

    assert result.provider == "two"
    assert [call["model"] for call in client.aio.models.calls] == ["one", "one", "one", "two"]


@pytest.mark.asyncio
async def test_non_retryable_error_moves_to_next_model_immediately() -> None:
    client = FakeClient([FakeApiError(400), "Tóm tắt dự phòng."])

    result = await summarizer_for(client, [slot("one"), slot("two")]).summarize(article_with_words(400), BotState())

    assert result.provider == "two"
    assert [call["model"] for call in client.aio.models.calls] == ["one", "two"]


@pytest.mark.asyncio
async def test_all_models_failing_raises_for_local_fallback() -> None:
    client = FakeClient([FakeApiError(400), FakeApiError(400)])

    with pytest.raises(SummarizationError):
        await summarizer_for(client, [slot("one"), slot("two")]).summarize(article_with_words(400), BotState())


def test_build_slots_follows_model_specs() -> None:
    slots = build_slots((ModelSpec("a", 10, 450), ModelSpec("gemma-4-31b-it", 3, 300)))

    assert [(item.model, item.quota_guard.max_requests, item.rate_limiter.max_calls) for item in slots] == [
        ("a", 450, 10),
        ("gemma-4-31b-it", 300, 3),
    ]
