# Telegram News Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Xây dựng bot cá nhân phát hiện bài mới từ bảy RSS VnExpress/Dân trí, tóm tắt tối đa 300 từ và gửi vào chat Telegram bằng hạ tầng miễn phí.

**Architecture:** Một CLI Python 3.12 chạy theo lịch GitHub Actions. Pipeline tải RSS, chuẩn hóa và chống trùng bằng state JSON, trích toàn văn, ưu tiên Gemini 3.1 Flash Lite, chuyển sang tóm tắt cục bộ khi cần, rồi chỉ đánh dấu bài đã gửi sau khi Telegram xác nhận thành công.

**Tech Stack:** Python 3.12, `httpx`, `feedparser`, `beautifulsoup4`, `lxml`, `google-genai`, `pytest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-18-telegram-news-bot-design.md`

## Global Constraints

- Theo dõi đúng 4 RSS VnExpress và 3 RSS Dân trí đã ghi trong spec.
- Gửi vào một chat riêng qua `TELEGRAM_CHAT_ID`.
- Tóm tắt bằng `min(20–25% số từ bài gốc, 300 từ)` và không thêm dữ kiện.
- Model mặc định là `gemini-3.1-flash-lite`; giới hạn nội bộ 10 RPM và 450 RPD.
- State giữ tối đa 2.000 bài hoặc 30 ngày.
- Lần chạy đầu chỉ ghi nhận backlog, không gửi bài cũ.
- Chỉ đánh dấu seen sau khi Telegram gửi thành công.
- Không ghi secrets hoặc toàn văn bài báo vào repository/log/state.
- Mỗi task phải cập nhật `PROJECT_STATUS.md` trước và sau khi thực hiện.

## File Map

- `pyproject.toml`: metadata, dependencies, CLI và cấu hình pytest.
- `config/feeds.toml`: bảy RSS và metadata nguồn/chuyên mục.
- `src/newsbot/models.py`: dataclass dùng chung giữa các module.
- `src/newsbot/config.py`: đọc TOML, biến môi trường và kiểm tra cấu hình.
- `src/newsbot/feeds.py`: tải/parse RSS, chuẩn hóa URL và gộp bài.
- `src/newsbot/extractors.py`: tải HTML và tách nội dung chính.
- `src/newsbot/state.py`: state JSON, quota counter, prune và atomic save.
- `src/newsbot/summarizers.py`: giới hạn độ dài, Gemini và fallback cục bộ.
- `src/newsbot/telegram.py`: định dạng, chia tin và gọi Bot API.
- `src/newsbot/pipeline.py`: điều phối end-to-end và thống kê.
- `src/newsbot/main.py`: CLI, dependency wiring và exit code.
- `.github/workflows/news-bot.yml`: lịch chạy, test, bot run và commit state.
- `state/seen.json`: state khởi tạo không chứa bài.
- `tests/fixtures/`: RSS/HTML tối giản không chứa toàn văn báo thật.
- `tests/`: unit/integration test bằng fake HTTP/API.
- `README.md`: thiết lập Gemini, Telegram, GitHub Secrets và vận hành.

---

### Task P05: Khung Python và cấu hình

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `config/feeds.toml`
- Create: `src/newsbot/__init__.py`
- Create: `src/newsbot/models.py`
- Create: `src/newsbot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `FeedConfig`, `Settings`, `load_settings(env, feeds_path, require_secrets=True)`.
- Consumes: không có code ứng dụng trước đó.

- [x] **Step 1: Đánh dấu P05 là `IN_PROGRESS` trong `PROJECT_STATUS.md` và viết test cấu hình thất bại**

```python
from pathlib import Path

import pytest

from newsbot.config import ConfigError, load_settings


def test_loads_exactly_seven_feeds() -> None:
    env = {
        "GEMINI_API_KEY": "gemini-test",
        "TELEGRAM_BOT_TOKEN": "telegram-test",
        "TELEGRAM_CHAT_ID": "123456",
    }
    settings = load_settings(env, Path("config/feeds.toml"))
    assert len(settings.feeds) == 7
    assert settings.model == "gemini-3.1-flash-lite"
    assert settings.max_gemini_rpm == 10
    assert settings.max_gemini_rpd == 450
    assert settings.max_seen_articles == 2_000


def test_missing_secret_fails_fast() -> None:
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_settings({}, Path("config/feeds.toml"))
```

- [x] **Step 2: Chạy test để xác nhận thất bại đúng lý do**

Run: `python -m pytest tests/test_config.py -v`

Expected: collection/import fails because package/config chưa tồn tại.

- [x] **Step 3: Tạo project metadata, feed TOML và implementation tối thiểu**

`models.py` phải cung cấp các type ổn định sau:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FeedConfig:
    name: str
    source: str
    category: str
    url: str


@dataclass(frozen=True, slots=True)
class ArticleCandidate:
    article_id: str
    source: str
    category: str
    title: str
    url: str
    published_at: datetime | None
    rss_summary: str


@dataclass(frozen=True, slots=True)
class ExtractedArticle:
    candidate: ArticleCandidate
    text: str
    word_count: int


@dataclass(frozen=True, slots=True)
class SummaryResult:
    text: str
    provider: str
    input_words: int
    output_words: int
```

`config.py` phải dùng `tomllib`, nhận mapping env để test không sửa process environment, và trả về:

```python
@dataclass(frozen=True, slots=True)
class Settings:
    feeds: tuple[FeedConfig, ...]
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    model: str = "gemini-3.1-flash-lite"
    max_gemini_rpm: int = 10
    max_gemini_rpd: int = 450
    max_seen_articles: int = 2_000
    seen_retention_days: int = 30
    state_path: Path = Path("state/seen.json")
```

`config/feeds.toml` phải chứa bảy `[[feeds]]` với URL chính xác trong spec. `pyproject.toml` phải khai báo Python `>=3.12`, package trong `src`, dependencies runtime và nhóm `dev` gồm `pytest`, `pytest-asyncio`.

- [x] **Step 4: Cài dependencies và chạy test**

Run: `python -m pip install -e ".[dev]"`

Run: `python -m pytest tests/test_config.py -v`

Expected: 2 tests pass.

- [x] **Step 5: Cập nhật tracker và commit**

Ghi bằng chứng test vào P05, chuyển P05 thành `DONE`, P06 thành `IN_PROGRESS`.

```bash
git add pyproject.toml .gitignore .env.example config src tests PROJECT_STATUS.md
git commit -m "feat: scaffold news bot configuration"
```

---

### Task P06: RSS, chuẩn hóa URL và gộp bài

**Files:**
- Create: `src/newsbot/feeds.py`
- Create: `tests/fixtures/vnexpress-feed.xml`
- Create: `tests/fixtures/dantri-feed.xml`
- Test: `tests/test_feeds.py`

**Interfaces:**
- Consumes: `FeedConfig`, `ArticleCandidate`.
- Produces: `canonicalize_url(url: str) -> str`, `make_article_id(url: str) -> str`, `parse_feed(xml: bytes, feed: FeedConfig) -> list[ArticleCandidate]`, `fetch_all_feeds(client, feeds) -> list[ArticleCandidate]`.

- [x] **Step 1: Viết test parse, URL và dedupe thất bại**

```python
from pathlib import Path

from newsbot.feeds import canonicalize_url, deduplicate_candidates, parse_feed
from newsbot.models import FeedConfig


def test_canonicalize_removes_tracking_and_fragment() -> None:
    url = "https://vnexpress.net/a-123.html?utm_source=rss#box_comment"
    assert canonicalize_url(url) == "https://vnexpress.net/a-123.html"


def test_parse_feed_preserves_source_and_category() -> None:
    feed = FeedConfig("VnExpress Thời sự", "VnExpress", "Thời sự", "https://example.test/rss")
    items = parse_feed(Path("tests/fixtures/vnexpress-feed.xml").read_bytes(), feed)
    assert len(items) == 2
    assert items[0].source == "VnExpress"
    assert items[0].category == "Thời sự"
    assert items[0].article_id


def test_deduplicate_same_url_across_categories() -> None:
    feed = FeedConfig("VnExpress", "VnExpress", "Thời sự", "https://example.test/rss")
    items = parse_feed(Path("tests/fixtures/vnexpress-feed.xml").read_bytes(), feed)
    assert len(deduplicate_candidates([items[0], items[0]])) == 1
```

- [x] **Step 2: Chạy test và xác nhận import/function chưa tồn tại**

Run: `python -m pytest tests/test_feeds.py -v`

Expected: FAIL importing `newsbot.feeds`.

- [x] **Step 3: Implement parser và fetcher**

```python
TRACKING_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


def canonicalize_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key.lower() not in TRACKING_KEYS]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query), ""))


def make_article_id(url: str) -> str:
    return sha256(canonicalize_url(url).encode("utf-8")).hexdigest()
```

`parse_feed` dùng `feedparser.loads`, bỏ entry thiếu title/link, chuyển ngày về UTC khi có thể, làm sạch HTML trong RSS summary bằng BeautifulSoup. `fetch_all_feeds` dùng một `httpx.AsyncClient`, timeout 15 giây, retry mỗi feed tối đa hai lần và tiếp tục khi một feed lỗi. Kết quả được dedupe theo `article_id` rồi sắp theo `published_at`, bài không có ngày đứng cuối.

- [x] **Step 4: Chạy test module và toàn bộ suite**

Run: `python -m pytest tests/test_feeds.py -v`

Run: `python -m pytest -q`

Expected: all tests pass.

- [x] **Step 5: Cập nhật P06 và commit**

```bash
git add src/newsbot/feeds.py tests/fixtures tests/test_feeds.py PROJECT_STATUS.md
git commit -m "feat: ingest and deduplicate news feeds"
```

---

### Task P07: Trích nội dung VnExpress và Dân trí

**Files:**
- Create: `src/newsbot/extractors.py`
- Create: `tests/fixtures/vnexpress-article.html`
- Create: `tests/fixtures/dantri-article.html`
- Test: `tests/test_extractors.py`

**Interfaces:**
- Consumes: `ArticleCandidate`.
- Produces: `extract_article(candidate, html: str) -> ExtractedArticle`, `fetch_and_extract(client, candidate) -> ExtractedArticle`.

- [x] **Step 1: Viết test trích xuất thất bại**

```python
from pathlib import Path

import pytest

from newsbot.extractors import ExtractionError, extract_article
from newsbot.models import ArticleCandidate


def candidate(source: str) -> ArticleCandidate:
    return ArticleCandidate("id", source, "Thời sự", "Tiêu đề", "https://example.test/a", None, "Mô tả RSS")


def test_extracts_vnexpress_main_text_only() -> None:
    html = Path("tests/fixtures/vnexpress-article.html").read_text(encoding="utf-8")
    article = extract_article(candidate("VnExpress"), html)
    assert article.text.startswith("Đoạn mở đầu")
    assert "Nội dung chính cần giữ" in article.text
    assert "Tin liên quan" not in article.text
    assert article.word_count >= 40


def test_extracts_dantri_main_text_only() -> None:
    html = Path("tests/fixtures/dantri-article.html").read_text(encoding="utf-8")
    article = extract_article(candidate("Dân trí"), html)
    assert "Tin liên quan" not in article.text
    assert "Nội dung Dân trí" in article.text


def test_rejects_page_without_article_text() -> None:
    with pytest.raises(ExtractionError, match="content"):
        extract_article(candidate("VnExpress"), "<html><nav>Menu</nav></html>")
```

- [x] **Step 2: Chạy test để thấy module chưa tồn tại**

Run: `python -m pytest tests/test_extractors.py -v`

Expected: FAIL importing `newsbot.extractors`.

- [x] **Step 3: Implement selector có thứ tự và bộ làm sạch**

```python
CONTENT_SELECTORS = {
    "VnExpress": ("article.fck_detail", ".fck_detail"),
    "Dân trí": (".singular-content", "article .singular-content", "article"),
}

REMOVE_SELECTORS = (
    "script", "style", "noscript", "iframe", "figure", ".related-news",
    ".box-tinlienquan", ".ads", ".advertisement", "[data-role='related-news']",
)
```

Chọn selector đầu tiên có ít nhất 40 từ; nếu không có thì thử `article`, rồi ném `ExtractionError`. Chỉ ghép text từ `p` và heading con, chuẩn hóa khoảng trắng, bỏ đoạn trùng liên tiếp. `fetch_and_extract` tải URL với User-Agent `PersonalTelegramNewsBot/1.0`, timeout 20 giây và không retry lỗi HTTP 4xx ngoài 429.

- [x] **Step 4: Chạy test**

Run: `python -m pytest tests/test_extractors.py -v`

Run: `python -m pytest -q`

Expected: all tests pass.

- [x] **Step 5: Cập nhật P07 và commit**

```bash
git add src/newsbot/extractors.py tests/fixtures tests/test_extractors.py PROJECT_STATUS.md
git commit -m "feat: extract article text from supported sources"
```

---

### Task P08: State, khởi tạo và chống gửi trùng

**Files:**
- Create: `src/newsbot/state.py`
- Create: `state/seen.json`
- Test: `tests/test_state.py`

**Interfaces:**
- Produces: `SeenEntry`, `UsageState`, `BotState`, `StateStore.load()`, `StateStore.save(state)`, `prune_seen(state, now, max_items=2000, retention_days=30)`.
- Consumes: canonical `article_id` và URL từ P06.

- [x] **Step 1: Viết test state thất bại**

```python
from datetime import UTC, datetime, timedelta

from newsbot.state import BotState, SeenEntry, StateStore, prune_seen


def test_missing_file_loads_empty_state(tmp_path) -> None:
    state = StateStore(tmp_path / "seen.json").load()
    assert state.initialized is False
    assert state.seen == {}


def test_atomic_round_trip(tmp_path) -> None:
    path = tmp_path / "seen.json"
    store = StateStore(path)
    state = BotState(initialized=True, seen={"a": SeenEntry("https://example.test/a", "2026-09-18T00:00:00+00:00")})
    store.save(state)
    assert store.load() == state


def test_prune_keeps_at_most_two_thousand_recent_items() -> None:
    now = datetime(2026, 9, 18, tzinfo=UTC)
    seen = {
        str(index): SeenEntry(f"https://example.test/{index}", (now - timedelta(minutes=index)).isoformat())
        for index in range(2_100)
    }
    state = prune_seen(BotState(initialized=True, seen=seen), now)
    assert len(state.seen) == 2_000
    assert "0" in state.seen
    assert "2099" not in state.seen
```

- [x] **Step 2: Chạy test và xác nhận thất bại**

Run: `python -m pytest tests/test_state.py -v`

Expected: FAIL importing `newsbot.state`.

- [x] **Step 3: Implement JSON schema và atomic save**

```json
{
  "schema_version": 1,
  "initialized": false,
  "seen": {},
  "usage": {"quota_day": "", "gemini_requests": 0}
}
```

`StateStore.save` ghi UTF-8 vào file cùng thư mục có hậu tố `.tmp`, flush, rồi dùng `Path.replace`. Loader kiểm tra type và schema; JSON hỏng phải ném `StateError` thay vì trả state rỗng. `prune_seen` loại entry quá 30 ngày trước, sau đó giữ 2.000 entry mới nhất.

- [x] **Step 4: Chạy test**

Run: `python -m pytest tests/test_state.py -v`

Run: `python -m pytest -q`

Expected: all tests pass.

- [x] **Step 5: Cập nhật P08 và commit**

```bash
git add src/newsbot/state.py state/seen.json tests/test_state.py PROJECT_STATUS.md
git commit -m "feat: persist bounded delivery state"
```

---

### Task P09: Gemini, độ dài và tóm tắt cục bộ

**Files:**
- Create: `src/newsbot/summarizers.py`
- Test: `tests/test_summarizers.py`

**Interfaces:**
- Consumes: `ExtractedArticle`, `SummaryResult`, `BotState.usage`.
- Produces: `summary_bounds(word_count)`, `GeminiSummarizer.summarize(article)`, `LocalSummarizer.summarize(article)`, `QuotaGuard`, `SlidingWindowRateLimiter`.

- [x] **Step 1: Viết test giới hạn và fallback thất bại**

```python
import pytest

from newsbot.models import ArticleCandidate, ExtractedArticle
from newsbot.summarizers import LocalSummarizer, SlidingWindowRateLimiter, summary_bounds


def test_summary_bounds() -> None:
    assert summary_bounds(500) == (100, 125)
    assert summary_bounds(1_000) == (200, 250)
    assert summary_bounds(2_000) == (300, 300)
    assert summary_bounds(4_000) == (300, 300)


def test_local_summary_never_exceeds_maximum() -> None:
    text = " ".join(f"Câu số {index} cung cấp dữ kiện quan trọng." for index in range(500))
    candidate = ArticleCandidate("id", "VnExpress", "Thời sự", "Tiêu đề", "https://example.test/a", None, "")
    article = ExtractedArticle(candidate, text, len(text.split()))
    result = LocalSummarizer().summarize(article)
    assert result.provider == "local"
    assert result.output_words <= 300


@pytest.mark.asyncio
async def test_rate_limiter_never_exceeds_ten_calls_per_minute(fake_clock) -> None:
    limiter = SlidingWindowRateLimiter(max_calls=10, period_seconds=60, clock=fake_clock)
    for call_number in range(11):
        await limiter.acquire()
        if call_number < 10:
            assert fake_clock.slept == 0
    assert fake_clock.slept >= 60
```

- [x] **Step 2: Chạy test và xác nhận thất bại**

Run: `python -m pytest tests/test_summarizers.py -v`

Expected: FAIL importing `newsbot.summarizers`.

- [x] **Step 3: Implement bounds, prompt, Gemini adapter và fallback**

```python
def summary_bounds(word_count: int) -> tuple[int, int]:
    if word_count <= 0:
        return 0, 0
    lower = min(300, max(1, math.ceil(word_count * 0.20)))
    upper = min(300, max(lower, math.floor(word_count * 0.25)))
    return lower, upper
```

Prompt phải có title, source, category, min/max words và toàn văn; yêu cầu văn bản thuần, không mở đầu kiểu “Bài viết cho biết”, không thêm kiến thức ngoài nguồn. Sau response, chuẩn hóa khoảng trắng và cắt tại biên từ nếu vượt `upper`.

Gemini adapter dùng `google.genai.Client(api_key=api_key)` và `client.aio.models.generate_content(model=model, contents=prompt)`. `QuotaGuard` reset counter khi ngày Pacific thay đổi, không cho gọi khi counter đạt 450, và tăng counter cho mỗi request thực sự gửi kể cả request lỗi. `SlidingWindowRateLimiter` giữ timestamp của các request trong 60 giây gần nhất và chờ khi đã có 10 request.

`GeminiSummarizer` retry tối đa hai lần cho `429`, timeout và `5xx`; ưu tiên `Retry-After`, nếu không có thì chờ 2 và 4 giây. Không retry `400`, `401` hoặc `403`. Test dùng fake clock/client để xác nhận đúng ba lần gọi tối đa và không sleep thật.

Fallback tách câu bằng regex, chấm điểm theo tần suất từ có ít nhất hai ký tự, chọn câu điểm cao nhưng trả lại theo thứ tự gốc cho tới `upper` từ.

- [x] **Step 4: Chạy unit test với fake Gemini client**

Bổ sung fake response để kiểm tra prompt, model và trimming mà không gọi mạng.

Run: `python -m pytest tests/test_summarizers.py -v`

Run: `python -m pytest -q`

Expected: all tests pass; zero external requests.

- [x] **Step 5: Cập nhật P09 và commit**

```bash
git add src/newsbot/summarizers.py tests/test_summarizers.py PROJECT_STATUS.md
git commit -m "feat: summarize articles with Gemini fallback"
```

---

### Task P10: Telegram formatting và delivery

**Files:**
- Create: `src/newsbot/telegram.py`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Consumes: `ArticleCandidate`, `SummaryResult`.
- Produces: `format_article_message(candidate, summary)`, `split_message(text, limit=3900)`, `TelegramClient.send_article(candidate: ArticleCandidate, summary: SummaryResult) -> None`, `TelegramClient.list_chat_ids()`.

- [x] **Step 1: Viết test chia tin và HTTP failure thất bại**

```python
import pytest

from newsbot.telegram import TelegramApiError, TelegramClient, split_message


def test_split_message_respects_limit_and_preserves_text() -> None:
    text = "Đoạn một.\n\n" + ("nội dung " * 800)
    chunks = split_message(text, limit=3900)
    assert all(len(chunk) <= 3900 for chunk in chunks)
    normalized = lambda value: value.replace("\n", "").replace(" ", "")
    assert normalized("".join(chunks)) == normalized(text)


@pytest.mark.asyncio
async def test_telegram_error_is_not_reported_as_success(fake_http) -> None:
    fake_http.queue_json({"ok": False, "error_code": 403, "description": "Forbidden"})
    client = TelegramClient(fake_http, "token", "123")
    with pytest.raises(TelegramApiError, match="403"):
        await client.send_text("Tin thử")
```

- [x] **Step 2: Chạy test và xác nhận thất bại**

Run: `python -m pytest tests/test_telegram.py -v`

Expected: FAIL importing `newsbot.telegram`.

- [x] **Step 3: Implement Telegram Bot API trực tiếp**

```python
def format_article_message(candidate: ArticleCandidate, summary: SummaryResult) -> str:
    return (
        f"[{candidate.source} • {candidate.category}]\n\n"
        f"{candidate.title}\n\n"
        f"{summary.text}\n\n"
        f"Đọc bài gốc: {candidate.url}"
    )
```

`send_text` POST JSON tới `https://api.telegram.org/bot{token}/sendMessage` với `chat_id`, `text`, và `link_preview_options.is_disabled=true`. Không log URL vì chứa token. `send_article` gửi tuần tự các chunk và đánh số `(1/N)`. `list_chat_ids` gọi `getUpdates`, lấy các `message.chat.id` duy nhất để hỗ trợ cấu hình ban đầu.

- [x] **Step 4: Chạy test**

Run: `python -m pytest tests/test_telegram.py -v`

Run: `python -m pytest -q`

Expected: all tests pass.

- [x] **Step 5: Cập nhật P10 và commit**

```bash
git add src/newsbot/telegram.py tests/test_telegram.py PROJECT_STATUS.md
git commit -m "feat: deliver summaries through Telegram"
```

---

### Task P11: Pipeline và CLI dry-run

**Files:**
- Create: `src/newsbot/pipeline.py`
- Create: `src/newsbot/main.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: toàn bộ interface P05–P10.
- Produces: `RunStats`, `Pipeline.run(dry_run=False, limit=None)`, console command `news-bot`.

- [x] **Step 1: Viết test bootstrap và delivery semantics thất bại**

```python
import pytest


@pytest.mark.asyncio
async def test_first_run_marks_backlog_without_sending(pipeline_factory) -> None:
    pipeline, state_store, telegram = pipeline_factory(initialized=False, candidate_count=2)
    stats = await pipeline.run()
    assert stats.bootstrapped == 2
    assert telegram.sent == []
    assert state_store.saved.initialized is True
    assert len(state_store.saved.seen) == 2


@pytest.mark.asyncio
async def test_first_manual_limited_run_sends_only_latest_article(pipeline_factory) -> None:
    pipeline, state_store, telegram = pipeline_factory(initialized=False, candidate_count=3)
    stats = await pipeline.run(limit=1)
    assert stats.sent == 1
    assert len(telegram.sent) == 1
    assert len(state_store.saved.seen) == 3


@pytest.mark.asyncio
async def test_marks_seen_only_after_successful_delivery(pipeline_factory) -> None:
    pipeline, state_store, telegram = pipeline_factory(initialized=True, candidate_count=1)
    telegram.fail = True
    stats = await pipeline.run()
    assert stats.failed == 1
    assert state_store.saved.seen == {}


@pytest.mark.asyncio
async def test_dry_run_does_not_send_or_save(pipeline_factory) -> None:
    pipeline, state_store, telegram = pipeline_factory(initialized=True, candidate_count=1)
    await pipeline.run(dry_run=True, limit=1)
    assert telegram.sent == []
    assert state_store.save_calls == 0
    assert pipeline.gemini.calls == 0
```

- [x] **Step 2: Chạy test và xác nhận thất bại**

Run: `python -m pytest tests/test_pipeline.py tests/test_main.py -v`

Expected: FAIL importing pipeline/main.

- [x] **Step 3: Implement orchestration và CLI**

`RunStats` là dataclass mutable với các field số nguyên mặc định `0`: `feeds_ok`, `feeds_failed`, `candidates`, `bootstrapped`, `sent`, `local_fallback`, `skipped`, `failed`.

Pipeline sequence phải đúng:

```python
state = state_store.load()
candidates = await feed_loader()
new_items = [item for item in candidates if item.article_id not in state.seen]
if dry_run:
    selected = apply_limit(new_items or candidates, limit)
    for candidate in selected:
        await article_loader(candidate)
    return RunStats(candidates=len(candidates), skipped=len(selected))
if not state.initialized:
    state.initialized = True
    if limit is None:
        mark_all_seen(state, candidates, now)
        state_store.save(prune_seen(state, now))
        return RunStats(bootstrapped=len(candidates), candidates=len(candidates))
    selected = set(item.article_id for item in newest_first(candidates)[:limit])
    mark_all_seen(state, [item for item in candidates if item.article_id not in selected], now)
    new_items = [item for item in newest_first(candidates) if item.article_id in selected]
for candidate in apply_limit(new_items, limit):
    article = await article_loader(candidate)
    summary = await summarize_with_fallback(article, state)
    if not dry_run:
        await telegram.send_article(candidate, summary)
        mark_seen(state, candidate, now)
if not dry_run:
    state_store.save(prune_seen(state, now))
```

Exception được bắt theo từng bài. `ExtractionError` tạo `SummaryResult` từ RSS summary với provider `rss`. Telegram auth error dừng run; lỗi delivery tạm thời ghi failed và tiếp tục bài khác.

CLI flags:

- `--dry-run`: không Gemini, không Telegram, không save state; in bài sẽ xử lý.
- `--limit N`: giới hạn số bài ứng viên; khi chạy thủ công lần đầu, đánh dấu backlog nhưng vẫn gửi tối đa N bài mới nhất để kiểm thử.
- `--show-chat-id`: gọi `list_chat_ids` rồi thoát.

- [x] **Step 4: Chạy test và CLI help**

Run: `python -m pytest tests/test_pipeline.py tests/test_main.py -v`

Run: `news-bot --help`

Run: `python -m pytest -q`

Expected: all tests pass; help lists three flags.

- [x] **Step 5: Cập nhật P11 và commit**

```bash
git add src/newsbot/pipeline.py src/newsbot/main.py tests/test_pipeline.py tests/test_main.py PROJECT_STATUS.md
git commit -m "feat: orchestrate news delivery pipeline"
```

---

### Task P12: GitHub Actions và state commit

**Files:**
- Create: `.github/workflows/news-bot.yml`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: console script `news-bot`, `state/seen.json`.
- Produces: scheduled/manual workflow with serialized execution and state persistence.

- [x] **Step 1: Viết structural test thất bại**

```python
from pathlib import Path


def test_workflow_has_safety_controls() -> None:
    text = Path(".github/workflows/news-bot.yml").read_text(encoding="utf-8")
    assert "7,22,37,52 * * * *" in text
    assert "workflow_dispatch:" in text
    assert "contents: write" in text
    assert "cancel-in-progress: false" in text
    assert "GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}" in text
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in text
    assert "TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}" in text
```

- [x] **Step 2: Chạy test và xác nhận file chưa tồn tại**

Run: `python -m pytest tests/test_workflow.py -v`

Expected: FAIL with file not found.

- [x] **Step 3: Tạo workflow**

```yaml
name: Telegram news bot
on:
  schedule:
    - cron: "7,22,37,52 * * * *"
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Run without Gemini, Telegram, or state changes"
        required: false
        default: false
        type: boolean
      limit:
        description: "Maximum articles for this run; 0 means unlimited"
        required: false
        default: "0"
permissions:
  contents: write
concurrency:
  group: telegram-news-bot
  cancel-in-progress: false
```

Job dùng Ubuntu, Python 3.12, pip cache, `pip install -e ".[dev]"`, chạy `pytest -q`, sau đó chạy bot. Với schedule dùng `news-bot`; manual ghép `--dry-run` và `--limit` từ inputs. Cuối job, nếu `state/seen.json` đổi thì commit bằng `github-actions[bot]` và push với message `chore: update delivery state`.

- [x] **Step 4: Chạy test cấu trúc và toàn bộ suite**

Run: `python -m pytest tests/test_workflow.py -v`

Run: `python -m pytest -q`

Expected: all tests pass.

- [x] **Step 5: Cập nhật P12 và commit**

```bash
git add .github/workflows/news-bot.yml tests/test_workflow.py PROJECT_STATUS.md
git commit -m "ci: schedule telegram news bot"
```

---

### Task P13: Kiểm thử toàn bộ và kiểm tra secret

**Files:**
- Create: `tests/test_security.py`

**Interfaces:**
- Consumes: toàn bộ repository.
- Produces: reproducible verification commands and no-secret gate.

- [x] **Step 1: Viết test secret hygiene**

```python
from pathlib import Path


def test_example_env_contains_names_not_credentials() -> None:
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=" in text
    assert "TELEGRAM_BOT_TOKEN=" in text
    assert "TELEGRAM_CHAT_ID=" in text
    assert "AIza" not in text
    assert ":AA" not in text


def test_state_does_not_store_article_bodies() -> None:
    text = Path("state/seen.json").read_text(encoding="utf-8")
    assert '"text"' not in text
    assert '"content"' not in text
```

- [x] **Step 2: Chạy test bảo mật và sửa nếu thất bại**

Run: `python -m pytest tests/test_security.py -v`

Expected: 2 tests pass.

- [x] **Step 3: Chạy toàn bộ verification gate**

Run: `python -m compileall -q src`

Run: `python -m pytest -q`

Run: `git grep -n -E "AIza[0-9A-Za-z_-]{20,}|[0-9]{6,}:[A-Za-z0-9_-]{30,}" -- ':!docs/superpowers/plans/*' ':!tests/test_security.py'`

Expected: compile succeeds; all tests pass; grep returns no matches.

- [x] **Step 4: Chạy live dry-run không dùng secrets**

Run: `news-bot --dry-run --limit 1`

Expected: tải RSS và trích tối đa một bài, không gọi Gemini/Telegram, không đổi `state/seen.json`.

Run: `git diff --exit-code -- state/seen.json`

Expected: exit 0.

- [x] **Step 5: Cập nhật P13 và commit**

Ghi số test pass và kết quả dry-run vào tracker.

```bash
git add tests/test_security.py PROJECT_STATUS.md
git commit -m "test: verify bot end to end"
```

---

### Task P14: README, cấu hình thật và bàn giao

**Files:**
- Create: `README.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: CLI và workflow hoàn chỉnh.
- Produces: hướng dẫn vận hành từ tài khoản mới đến lần gửi thật đầu tiên.

- [x] **Step 1: Viết README với đúng trình tự an toàn**

README phải bao gồm:

1. Mục tiêu và bảy nguồn tin.
2. Tạo Gemini API key miễn phí và kiểm tra quota của `gemini-3.1-flash-lite`.
3. Tạo bot bằng BotFather, gửi `/start`, chạy `news-bot --show-chat-id` để lấy chat ID.
4. Tạo GitHub repository public và thêm ba Actions secrets.
5. Chạy workflow thủ công với `dry_run=true`, `limit=1`.
6. Chạy workflow thật với `limit=1` và kiểm tra đúng một tin Telegram.
7. Để schedule tự chạy sau khi kiểm tra thành công.
8. Cách đọc Actions logs, retry, đổi model và tắt workflow khẩn cấp.
9. Hạn chế và điều khoản sử dụng nội dung.

- [x] **Step 2: Kiểm tra command trong README tồn tại**

Run: `news-bot --help`

Run: `python -m pytest -q`

Expected: command/options trong README khớp CLI; all tests pass.

- [x] **Step 3: Kiểm tra lần cuối trước khi dùng secrets thật**

Run: `git status --short`

Run: `git log --oneline -15`

Run: `git grep -n -E "AIza[0-9A-Za-z_-]{20,}|[0-9]{6,}:[A-Za-z0-9_-]{30,}" -- ':!docs/superpowers/plans/*' ':!tests/test_security.py'`

Expected: chỉ README/tracker là thay đổi dự kiến; lịch sử có commit từng task; không có secret.

- [x] **Step 4: Bàn giao thao tác cần người dùng thực hiện**

Không yêu cầu người dùng gửi secrets qua chat. Hướng dẫn họ nhập trực tiếp `GEMINI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` vào GitHub repository settings, rồi chạy workflow thủ công.

- [x] **Step 5: Hoàn tất tracker và commit**

Chuyển P14 thành `DONE`, trạng thái dự án thành `READY_FOR_USER_SECRETS`, ghi rõ bước tiếp theo là thêm GitHub Secrets và chạy manual workflow.

```bash
git add README.md PROJECT_STATUS.md
git commit -m "docs: add setup and operations guide"
```

## Final Acceptance Gate

Sau P14, chỉ tuyên bố MVP hoàn thành khi các lệnh sau cùng đạt:

```bash
python -m compileall -q src
python -m pytest -q
news-bot --dry-run --limit 1
git diff --exit-code -- state/seen.json
git status --short
```

Kết quả phải được ghi vào `PROJECT_STATUS.md`. Việc gửi thật một bài là checkpoint có secrets và cần người dùng thao tác trực tiếp trong GitHub; không đưa secrets vào chat.
