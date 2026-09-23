from __future__ import annotations

import asyncio
import math
import re
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any
from zoneinfo import ZoneInfo

from google import genai

from newsbot.config import ModelSpec
from newsbot.models import ExtractedArticle, SummaryResult
from newsbot.state import BotState


LOGGER = logging.getLogger(__name__)


PACIFIC = ZoneInfo("America/Los_Angeles")
WORD_PATTERN = re.compile(r"[^\W\d_]{2,}", flags=re.UNICODE)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
SENTENCE_END = re.compile(r"[.!?…](?:[\"'”’»)}\]]*)$")
STOP_WORDS = {
    "bị",
    "bởi",
    "các",
    "cho",
    "có",
    "của",
    "đã",
    "đang",
    "đến",
    "được",
    "khi",
    "không",
    "là",
    "một",
    "những",
    "này",
    "theo",
    "trong",
    "từ",
    "và",
    "với",
}


class SummarizationError(RuntimeError):
    """Raised when an external summary cannot be produced."""


class QuotaExceeded(SummarizationError):
    """Raised before a request would exceed the configured daily quota."""


def summary_bounds(word_count: int) -> tuple[int, int]:
    if word_count <= 0:
        return 0, 0
    lower = min(300, max(1, math.ceil(word_count * 0.20)))
    upper = min(300, max(lower, math.floor(word_count * 0.25)))
    return lower, upper


def _trim_words(text: str, maximum: int) -> str:
    words = text.split()
    return " ".join(words[:maximum])


def _trim_complete_sentences(text: str, maximum: int) -> str:
    if len(text.split()) <= maximum:
        return text if SENTENCE_END.search(text.rstrip()) else ""

    selected: list[str] = []
    selected_words = 0
    for sentence in SENTENCE_SPLIT.split(text):
        sentence = sentence.strip()
        if not sentence or not SENTENCE_END.search(sentence):
            break
        sentence_words = len(sentence.split())
        if selected_words + sentence_words > maximum:
            break
        selected.append(sentence)
        selected_words += sentence_words
    return " ".join(selected)


def _normalize_summary(text: str) -> str:
    paragraphs = [re.sub(r"\s+", " ", paragraph).strip() for paragraph in text.splitlines()]
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


class LocalSummarizer:
    def summarize(self, article: ExtractedArticle) -> SummaryResult:
        _, maximum = summary_bounds(article.word_count)
        sentences = [sentence.strip() for sentence in SENTENCE_SPLIT.split(article.text) if sentence.strip()]
        tokens_by_sentence = [WORD_PATTERN.findall(sentence.lower()) for sentence in sentences]
        frequencies = Counter(
            token
            for tokens in tokens_by_sentence
            for token in tokens
            if token not in STOP_WORDS
        )
        ranked = sorted(
            range(len(sentences)),
            key=lambda index: (
                sum(frequencies[token] for token in tokens_by_sentence[index])
                / max(1, len(tokens_by_sentence[index])),
                -index,
            ),
            reverse=True,
        )

        selected: list[int] = []
        selected_words = 0
        for index in ranked:
            sentence_words = len(sentences[index].split())
            if selected_words + sentence_words <= maximum:
                selected.append(index)
                selected_words += sentence_words
        if not selected and sentences:
            text = _trim_words(sentences[0], maximum)
        else:
            text = " ".join(sentences[index] for index in sorted(selected))
            text = _trim_words(text, maximum)
        if not text:
            text = _trim_words(article.text, maximum)
        return SummaryResult(
            text=text,
            provider="local",
            input_words=article.word_count,
            output_words=len(text.split()),
        )


class QuotaGuard:
    def __init__(self, model: str, max_requests: int = 450) -> None:
        self.model = model
        self.max_requests = max_requests

    def _reset_if_new_day(self, state: BotState, now: datetime) -> None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        quota_day = now.astimezone(PACIFIC).date().isoformat()
        if state.usage.quota_day != quota_day:
            state.usage.quota_day = quota_day
            state.usage.requests = {}

    def exhausted(self, state: BotState, now: datetime) -> bool:
        self._reset_if_new_day(state, now)
        return state.usage.requests.get(self.model, 0) >= self.max_requests

    def before_request(self, state: BotState, now: datetime) -> None:
        if self.exhausted(state, now):
            raise QuotaExceeded(f"{self.model} daily request reserve reached")
        state.usage.requests[self.model] = state.usage.requests.get(self.model, 0) + 1


class SlidingWindowRateLimiter:
    def __init__(
        self,
        *,
        max_calls: int,
        period_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.clock = clock
        self.sleep = sleep
        self.calls: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = self.clock()
            while self.calls and now - self.calls[0] >= self.period_seconds:
                self.calls.popleft()
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return
            wait_seconds = self.period_seconds - (now - self.calls[0])
            await self.sleep(max(0.0, wait_seconds))


def _error_code(exc: Exception) -> int | None:
    for name in ("code", "status_code"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _retryable(exc: Exception) -> bool:
    code = _error_code(exc)
    return code == 429 or (code is not None and code >= 500) or isinstance(exc, (TimeoutError, asyncio.TimeoutError))


def _prompt(article: ExtractedArticle, minimum: int, maximum: int) -> str:
    target = round((minimum + maximum) / 2)
    return (
        "Hãy tóm tắt bài báo tiếng Việt dưới đây. Chỉ sử dụng dữ kiện có trong nội dung; "
        "không suy diễn, không thêm kiến thức bên ngoài, không viết lời dẫn như 'Bài viết cho biết'.\n"
        f"Độ dài yêu cầu: từ {minimum} đến {maximum} từ; tuyệt đối không quá {maximum} từ.\n"
        f"Mục tiêu khoảng {target} từ. Bao quát đủ các ý chính có trong bài: diễn biến, nguyên nhân, "
        "số liệu, tác động, phản ứng và kết luận hoặc dự báo quan trọng.\n"
        "Kết thúc bằng một câu hoàn chỉnh; nếu cần rút gọn, bỏ chi tiết phụ, không cắt giữa câu.\n"
        "Trả về văn bản thuần, các đoạn ngắn, không Markdown.\n\n"
        f"Nguồn: {article.candidate.source}\n"
        f"Chuyên mục: {article.candidate.category}\n"
        f"Tiêu đề: {article.candidate.title}\n\n"
        f"Nội dung:\n{article.text}"
    )


@dataclass(slots=True)
class ModelSlot:
    model: str
    quota_guard: QuotaGuard
    rate_limiter: SlidingWindowRateLimiter


def build_slots(models: Iterable[ModelSpec]) -> list[ModelSlot]:
    return [
        ModelSlot(
            model=spec.name,
            quota_guard=QuotaGuard(spec.name, spec.rpd),
            rate_limiter=SlidingWindowRateLimiter(max_calls=spec.rpm, period_seconds=60),
        )
        for spec in models
    ]


class GeminiSummarizer:
    """Try each free model in order; the caller falls back to the local summarizer."""

    def __init__(
        self,
        *,
        api_key: str,
        slots: list[ModelSlot],
        client: Any | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not slots:
            raise ValueError("At least one Gemini model is required")
        self.slots = slots
        self.client = client or genai.Client(api_key=api_key)
        self.sleep = sleep
        self.now = now

    async def _summarize_with(
        self,
        slot: ModelSlot,
        prompt: str,
        article: ExtractedArticle,
        maximum: int,
        state: BotState,
    ) -> SummaryResult:
        last_error: Exception | None = None
        for attempt in range(3):
            slot.quota_guard.before_request(state, self.now())
            await slot.rate_limiter.acquire()
            try:
                response = await self.client.aio.models.generate_content(model=slot.model, contents=prompt)
                raw_text = getattr(response, "text", "") or ""
                text = _trim_complete_sentences(_normalize_summary(raw_text), maximum)
                if not text:
                    raise SummarizationError("Gemini returned no complete summary within the word limit")
                return SummaryResult(
                    text=text,
                    provider=slot.model,
                    input_words=article.word_count,
                    output_words=len(text.split()),
                )
            except QuotaExceeded:
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 2 or not _retryable(exc):
                    break
                await self.sleep(2 ** (attempt + 1))
        raise SummarizationError(f"{slot.model} summary failed") from last_error

    async def summarize(self, article: ExtractedArticle, state: BotState) -> SummaryResult:
        minimum, maximum = summary_bounds(article.word_count)
        prompt = _prompt(article, minimum, maximum)
        last_error: Exception | None = None
        for slot in self.slots:
            if slot.quota_guard.exhausted(state, self.now()):
                continue
            try:
                return await self._summarize_with(slot, prompt, article, maximum, state)
            except SummarizationError as exc:
                last_error = exc
                LOGGER.warning("Gemini model %s failed; trying the next model", slot.model)
        raise SummarizationError("All Gemini models failed or reached their daily reserve") from last_error
