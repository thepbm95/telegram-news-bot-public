# Telegram Multi-Subscriber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mở rộng bot từ một chat cố định thành nhiều chat riêng tự đăng ký bằng `/start` và hủy bằng `/stop`, không gửi backlog, không gọi Gemini lặp lại và không gửi trùng cho người đã nhận.

**Architecture:** State JSON được nâng lên schema v2 để lưu subscriber, Telegram update offset, tiến độ giao theo từng chat và bản tin đang chờ retry. Mỗi workflow đồng bộ command trước, retry bản tin đã cache, rồi mới đọc RSS và chỉ tóm tắt một lần cho mọi người nhận. Telegram vẫn dùng Bot API trực tiếp và GitHub Actions vẫn là nơi chạy/lưu state, không thêm database hay dịch vụ mới.

**Tech Stack:** Python 3.12, `httpx`, dataclasses, JSON state, `pytest`, GitHub Actions, Telegram Bot API, Gemini 3.1 Flash Lite.

**Spec:** `docs/superpowers/specs/2026-09-19-telegram-multi-subscriber-design.md`

## Global Constraints

- Chỉ nhận command và gửi tin trong chat Telegram riêng.
- `/start`, `/start payload` và `/start@bot_username` đăng ký; `/stop` và `/stop@bot_username` hủy đăng ký.
- Bất kỳ tài khoản nào tìm thấy bot đều có thể đăng ký; phiên bản này không có allowlist hay giao diện quản trị.
- Người mới chỉ nhận bài xuất hiện sau khi đăng ký; không gửi lại backlog.
- Mỗi bài chỉ gọi Gemini một lần trong luồng bình thường, kể cả khi một người phải retry ở workflow sau.
- Tóm tắt giữ quy tắc `min(20–25% số từ bài gốc, 300 từ)` và model `gemini-3.1-flash-lite` với giới hạn nội bộ 10 RPM, 450 RPD.
- State giữ tối đa 2.000 bài hoặc 30 ngày; subscriber và Telegram offset không bị prune.
- Lịch GitHub Actions vẫn chạy ở phút 7, 22, 37 và 52 mỗi giờ.
- Không thêm database, web server, webhook, Telegram framework hoặc runtime dependency mới.
- Không ghi bot token, Gemini key hoặc chat ID đầy đủ vào source, test output hay log.
- `TELEGRAM_CHAT_ID` hiện tại chỉ dùng để seed tài khoản gốc đúng một lần khi migrate schema v1.
- Mỗi task phải cập nhật `PROJECT_STATUS.md`, chạy test liên quan và commit riêng trước khi chuyển task.

## Review Focus

- Update Telegram thiếu `message`, thiếu `chat`, có text không phải chuỗi hoặc không phải chat riêng phải bị bỏ qua nhưng offset vẫn tiến; test tại P20.
- Nhiều command của cùng một chat trong một batch phải được xử lý đúng thứ tự, để command cuối quyết định trạng thái cuối; test tại P21.
- Một recipient trả 403 phải bị gỡ đăng ký mà không cản recipient khác nhận cùng bài; test tại P22.
- Bản tin pending phải retry được khi bài đã rời RSS, không tải lại bài và không gọi Gemini lại; test tại P22.
- Một tài khoản `/start` sau giai đoạn không có subscriber phải không nhận các bài đã ghi nhận trước đó nhưng vẫn nhận bài mới tiếp theo; test tại P22.

## File Map

- `src/newsbot/state.py`: schema v2, đọc schema v1, serialize, prune và dữ liệu giao theo recipient.
- `tests/test_state.py`: migration, validation, round-trip và bảo toàn metadata khi prune.
- `src/newsbot/telegram.py`: command model/parser, update offset, gửi tới chat chỉ định và phân loại 401/403/lỗi tạm thời.
- `tests/test_telegram.py`: HTTP payload, command filtering, offset và exception taxonomy.
- `src/newsbot/subscriptions.py`: seed tài khoản gốc, xử lý `/start`/`/stop`, backfill chống backlog và confirmation.
- `tests/test_subscriptions.py`: idempotency, command ordering, failure handling và không tự seed lại.
- `src/newsbot/pipeline.py`: retry pending trước RSS, tóm tắt một lần, giao theo recipient và thống kê.
- `tests/test_pipeline.py`: delivery fan-out, cache retry, 403 isolation, zero-subscriber và dry-run.
- `src/newsbot/main.py`: tạo `SubscriptionService`, truyền chat seed và giữ setup/dry-run tương thích.
- `tests/test_main.py`: kiểm tra dependency wiring và dry-run không đổi state.
- `src/newsbot/setup.py`: giữ nguyên luồng setup ban đầu; chỉ sửa nếu interface Telegram yêu cầu tương thích.
- `tests/test_setup.py`: bảo vệ setup vẫn tìm đúng chat ban đầu và gắn đúng ba secrets.
- `README.md`: cách đăng ký tài khoản thứ hai, độ trễ command, `/stop` và giới hạn open registration.
- `tests/test_security.py`: bảo vệ log/repository không lộ token hoặc chat ID đầy đủ.
- `PROJECT_STATUS.md`: trạng thái P19–P24, lỗi, bằng chứng test và checkpoint bàn giao.
- `state/seen.json`: không sửa thủ công; workflow đầu tiên sau deploy tự migrate và commit schema v2.

---

### Task P19: State schema v2 và migration an toàn

**Files:**
- Modify: `src/newsbot/state.py`
- Modify: `tests/test_state.py`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: schema v1 hiện có gồm `initialized`, `seen[url, sent_at]` và `usage`.
- Produces: `SeenEntry(url, sent_at, delivered_to, pending_message)`, `BotState(..., subscriptions_initialized, telegram_update_offset, subscribers)`, `StateStore.load() -> BotState`, `StateStore.save(state) -> None`.

- [x] **Step 1: Chuyển P18 sang `DONE`, P19 sang `IN_PROGRESS` và viết test migration schema v1**

```python
def test_schema_one_loads_as_uninitialized_subscription_state(tmp_path) -> None:
    path = tmp_path / "seen.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "initialized": True,
        "seen": {"a": {"url": "https://example.test/a", "sent_at": "2026-09-19T00:00:00+00:00"}},
        "usage": {"quota_day": "2026-09-19", "gemini_requests": 7},
    }), encoding="utf-8")

    state = StateStore(path).load()

    assert state.subscriptions_initialized is False
    assert state.telegram_update_offset == 0
    assert state.subscribers == []
    assert state.seen["a"].delivered_to == []
    assert state.seen["a"].pending_message is None
    assert state.usage.gemini_requests == 7
```

- [x] **Step 2: Viết test schema v2 round-trip, validation và prune metadata**

```python
def test_schema_two_round_trip_preserves_subscriptions_and_pending_message(tmp_path) -> None:
    state = BotState(
        initialized=True,
        subscriptions_initialized=True,
        telegram_update_offset=51,
        subscribers=["222", "111"],
        seen={"a": SeenEntry(
            url="https://example.test/a",
            sent_at="2026-09-19T00:00:00+00:00",
            delivered_to=["111"],
            pending_message="Bản tin đang chờ",
        )},
        usage=UsageState("2026-09-19", 7),
    )
    store = StateStore(tmp_path / "seen.json")

    store.save(state)

    loaded = store.load()
    assert loaded == state
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["subscribers"] == ["111", "222"]


def test_prune_preserves_subscription_metadata() -> None:
    now = datetime(2026, 9, 19, tzinfo=UTC)
    state = BotState(
        subscriptions_initialized=True,
        telegram_update_offset=99,
        subscribers=["111"],
        seen={"recent": SeenEntry("https://example.test/recent", now.isoformat(), ["111"], None)},
    )
    pruned = prune_seen(state, now)
    assert pruned.subscribers == ["111"]
    assert pruned.telegram_update_offset == 99
    assert pruned.seen["recent"].delivered_to == ["111"]
```

Thêm các case invalid: `telegram_update_offset < 0`, subscriber không phải chuỗi, `delivered_to` không phải list chuỗi và `pending_message` không phải `str | None` đều phải raise `StateError`.

- [x] **Step 3: Chạy RED test**

Run: `python -m pytest tests/test_state.py -v`

Expected: FAIL vì schema v2 fields chưa tồn tại và loader hiện từ chối schema v1 sau khi hằng số đổi.

- [x] **Step 4: Implement dataclass và loader hai phiên bản**

```python
SCHEMA_VERSION = 2


@dataclass(slots=True)
class SeenEntry:
    url: str
    sent_at: str
    delivered_to: list[str] = field(default_factory=list)
    pending_message: str | None = None


@dataclass(slots=True)
class BotState:
    initialized: bool = False
    subscriptions_initialized: bool = False
    telegram_update_offset: int = 0
    subscribers: list[str] = field(default_factory=list)
    seen: dict[str, SeenEntry] = field(default_factory=dict)
    usage: UsageState = field(default_factory=UsageState)
```

`_state_from_dict` chấp nhận `schema_version` bằng 1 hoặc 2. Với v1, tạo các field mới theo mặc định nhưng giữ nguyên seen và quota. Với v2, kiểm tra kiểu toàn bộ field; chuẩn hóa `subscribers` và `delivered_to` bằng `sorted(set(...))` trước khi trả state. `StateStore.save` luôn ghi schema 2 và sắp xếp các danh sách ID ổn định.

- [x] **Step 5: Cập nhật `prune_seen` để trả đủ metadata**

```python
return BotState(
    initialized=state.initialized,
    subscriptions_initialized=state.subscriptions_initialized,
    telegram_update_offset=state.telegram_update_offset,
    subscribers=list(state.subscribers),
    seen=kept,
    usage=state.usage,
)
```

- [x] **Step 6: Chạy test P19 và toàn bộ suite**

Run: `python -m pytest tests/test_state.py tests/test_summarizers.py -v`

Run: `python -m pytest -q`

Expected: state/summarizer tests pass; toàn bộ suite pass; không có thay đổi ngoài file dự kiến.

- [x] **Step 7: Ghi bằng chứng, chuyển P19 sang `DONE`, P20 sang `IN_PROGRESS` và commit**

```bash
git add src/newsbot/state.py tests/test_state.py PROJECT_STATUS.md
git commit -m "feat: migrate delivery state to subscriber schema"
```

---

### Task P20: Telegram commands, recipient targeting và error taxonomy

**Files:**
- Modify: `src/newsbot/telegram.py`
- Modify: `tests/test_telegram.py`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: `ArticleCandidate`, `SummaryResult`, Telegram Bot API JSON.
- Produces: `TelegramCommand(update_id: int, chat_id: str, name: str)`, `TelegramForbiddenError`, `TelegramClient.get_commands(offset)`, `TelegramClient.send_text(text, chat_id=...)`, `TelegramClient.send_message(text, chat_id=...)`, `TelegramClient.send_article(candidate, summary, chat_id=None)`.

- [x] **Step 1: Viết test phân loại 401, 403 và gửi tới chat chỉ định**

```python
@pytest.mark.asyncio
async def test_telegram_distinguishes_bot_auth_from_recipient_forbidden() -> None:
    responses = iter([
        httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"}),
        httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden"}),
    ])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: next(responses))) as http:
        client = TelegramClient(http, "token", "111")
        with pytest.raises(TelegramAuthError):
            await client.send_text("a")
        with pytest.raises(TelegramForbiddenError):
            await client.send_text("b", chat_id="222")


@pytest.mark.asyncio
async def test_send_text_uses_explicit_recipient() -> None:
    payloads = []
    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await TelegramClient(http, "token", "111").send_text("Tin", chat_id="222")
    assert payloads[0]["chat_id"] == "222"
```

- [x] **Step 2: Viết test command parsing và offset trên mọi update**

```python
@pytest.mark.asyncio
async def test_get_commands_filters_private_commands_but_advances_past_every_update() -> None:
    updates = [
        {"update_id": 10, "channel_post": {"text": "/start"}},
        {"update_id": 11, "message": {"chat": {"id": -5, "type": "group"}, "text": "/start"}},
        {"update_id": 12, "message": {"chat": {"id": 42, "type": "private"}, "text": 7}},
        {"update_id": 13, "message": {"chat": {"id": 42, "type": "private"}, "text": "/start payload"}},
        {"update_id": 14, "message": {"chat": {"id": 42, "type": "private"}, "text": "/stop@tin_tuc_bot"}},
    ]
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload == {"offset": 8, "timeout": 0, "allowed_updates": ["message"]}
        return httpx.Response(200, json={"ok": True, "result": updates})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        commands, next_offset = await TelegramClient(http, "token", "").get_commands(8)
    assert [(item.update_id, item.chat_id, item.name) for item in commands] == [
        (13, "42", "start"),
        (14, "42", "stop"),
    ]
    assert next_offset == 15
```

Handler phải assert request `getUpdates` chứa `offset=8`, `timeout=0` và `allowed_updates=["message"]`. Bổ sung `/START`, text thường, `/starter` và message thiếu `chat` để khóa parser.

- [x] **Step 3: Chạy RED test**

Run: `python -m pytest tests/test_telegram.py -v`

Expected: FAIL vì chưa có `TelegramCommand`, `TelegramForbiddenError`, `get_commands` và tham số recipient.

- [x] **Step 4: Implement command model, parser và exception taxonomy**

```python
@dataclass(frozen=True, slots=True)
class TelegramCommand:
    update_id: int
    chat_id: str
    name: str


class TelegramForbiddenError(TelegramApiError):
    """Raised when a recipient blocked or cannot receive from the bot."""


def _parse_command(text: str) -> str | None:
    token = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    base = token.split("@", 1)[0]
    return base[1:] if base in {"/start", "/stop"} else None
```

Trong `_request`, map 401 sang `TelegramAuthError`, 403 sang `TelegramForbiddenError`, còn 429/network/5xx sang `TelegramApiError`. Không đưa URL có token vào exception hoặc log.

- [x] **Step 5: Implement targeted send và update reader**

```python
async def send_text(self, text: str, *, chat_id: str | None = None) -> Any:
    recipient = chat_id or self.chat_id
    if not recipient:
        raise TelegramAuthError("Telegram chat ID is missing")
    return await self._request("sendMessage", {
        "chat_id": recipient,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    })


async def send_message(self, text: str, *, chat_id: str) -> None:
    chunks = split_message(text, limit=3850)
    for index, chunk in enumerate(chunks, start=1):
        rendered = chunk if len(chunks) == 1 else f"({index}/{len(chunks)})\n{chunk}"
        await self.send_text(rendered, chat_id=chat_id)
```

`send_article` giữ tương thích bằng cách gọi `send_message(format_article_message(...), chat_id=chat_id or self.chat_id)`. `get_commands(offset)` tính `next_offset` từ mọi `update_id` nguyên hợp lệ trước khi lọc command. `list_chat_ids()` tiếp tục hoạt động cho `--setup-github`.

- [x] **Step 6: Chạy test Telegram, setup và security log**

Run: `python -m pytest tests/test_telegram.py tests/test_setup.py tests/test_main.py::test_cli_logging_does_not_expose_telegram_token -v`

Expected: all selected tests pass; setup cũ không cần thay đổi hành vi.

- [x] **Step 7: Ghi bằng chứng, chuyển P20 sang `DONE`, P21 sang `IN_PROGRESS` và commit**

```bash
git add src/newsbot/telegram.py tests/test_telegram.py PROJECT_STATUS.md
git commit -m "feat: read Telegram subscription commands"
```

---

### Task P21: Subscription service và chống backlog

**Files:**
- Create: `src/newsbot/subscriptions.py`
- Create: `tests/test_subscriptions.py`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: `BotState`, `SeenEntry`, `TelegramCommand`, `TelegramClient.get_commands`, `TelegramClient.send_text`.
- Produces: `initialize_subscriptions(state, initial_chat_id) -> bool`, `SubscriptionStats`, `SubscriptionService.sync(state) -> SubscriptionStats`.

- [x] **Step 1: Viết test seed schema v1 đúng một lần**

```python
def test_initialize_subscriptions_seeds_original_chat_and_backfills_seen_once() -> None:
    state = BotState(
        initialized=True,
        seen={"old": SeenEntry("https://example.test/old", NOW.isoformat())},
    )
    assert initialize_subscriptions(state, "111") is True
    assert state.subscriptions_initialized is True
    assert state.subscribers == ["111"]
    assert state.seen["old"].delivered_to == ["111"]

    state.subscribers.clear()
    assert initialize_subscriptions(state, "111") is False
    assert state.subscribers == []
```

- [x] **Step 2: Viết test `/start`, `/stop`, idempotency và command ordering**

```python
class FakeTelegram:
    def __init__(
        self,
        *,
        commands: list[TelegramCommand] | None = None,
        next_offset: int = 0,
        get_error: Exception | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.commands = commands or []
        self.next_offset = next_offset
        self.get_error = get_error
        self.send_error = send_error
        self.confirmations: list[tuple[str, str]] = []

    async def get_commands(self, offset: int) -> tuple[list[TelegramCommand], int]:
        if self.get_error is not None:
            raise self.get_error
        return self.commands, self.next_offset

    async def send_text(self, text: str, *, chat_id: str | None = None) -> None:
        if self.send_error is not None:
            raise self.send_error
        assert chat_id is not None
        self.confirmations.append((chat_id, text))


@pytest.mark.asyncio
async def test_start_then_stop_in_same_batch_leaves_chat_unsubscribed() -> None:
    telegram = FakeTelegram(commands=[
        TelegramCommand(20, "222", "start"),
        TelegramCommand(21, "222", "stop"),
    ], next_offset=22)
    state = BotState(
        subscriptions_initialized=True,
        seen={"old": SeenEntry("https://example.test/old", NOW.isoformat())},
    )

    stats = await SubscriptionService(telegram).sync(state)

    assert state.subscribers == []
    assert state.telegram_update_offset == 22
    assert state.seen["old"].delivered_to == ["222"]
    assert [text for _, text in telegram.confirmations] == [
        "Đã đăng ký nhận các tin mới.",
        "Đã dừng nhận tin.",
    ]
    assert stats.commands == 2
```

Thêm test `/start` hai lần không nhân đôi subscriber/delivered list; `/stop` hai lần vẫn trả confirmation trạng thái; subscriber được sort ổn định.

- [x] **Step 3: Viết test lỗi update/confirmation và cleanup pending**

```python
@pytest.mark.asyncio
async def test_update_failure_keeps_existing_state() -> None:
    state = BotState(subscriptions_initialized=True, telegram_update_offset=8, subscribers=["111"])
    telegram = FakeTelegram(get_error=TelegramApiError("temporary"))
    stats = await SubscriptionService(telegram).sync(state)
    assert state.telegram_update_offset == 8
    assert state.subscribers == ["111"]
    assert stats.commands == 0


@pytest.mark.asyncio
async def test_confirmation_failure_does_not_roll_back_offset_or_command() -> None:
    state = BotState(subscriptions_initialized=True)
    telegram = FakeTelegram(
        commands=[TelegramCommand(30, "222", "start")],
        next_offset=31,
        send_error=TelegramApiError("temporary"),
    )
    stats = await SubscriptionService(telegram).sync(state)
    assert state.telegram_update_offset == 31
    assert state.subscribers == ["222"]
    assert stats.confirmations_failed == 1
```

Thêm test `/stop` làm `pending_message=None` khi không còn subscriber đang hoạt động nào thiếu bài. `TelegramAuthError` phải propagate; `TelegramForbiddenError` khi confirmation phải loại chat vừa `/start` khỏi subscriber.

- [x] **Step 4: Chạy RED test**

Run: `python -m pytest tests/test_subscriptions.py -v`

Expected: FAIL importing `newsbot.subscriptions`.

- [x] **Step 5: Implement seed, sync và cleanup**

```python
@dataclass(slots=True)
class SubscriptionStats:
    commands: int = 0
    subscribed: int = 0
    unsubscribed: int = 0
    confirmations_failed: int = 0


def initialize_subscriptions(state: BotState, initial_chat_id: str) -> bool:
    if state.subscriptions_initialized:
        return False
    state.subscriptions_initialized = True
    if initial_chat_id:
        state.subscribers = sorted(set([*state.subscribers, initial_chat_id]))
        for entry in state.seen.values():
            entry.delivered_to = sorted(set([*entry.delivered_to, initial_chat_id]))
    return True
```

`SubscriptionService.sync` gọi `get_commands(state.telegram_update_offset)`, cập nhật offset trước confirmation, xử lý command tuần tự, backfill toàn bộ `seen` khi `/start`, và không log chat ID. Bắt lỗi temporary/403 theo spec nhưng để 401 propagate.

- [x] **Step 6: Chạy test P21 và toàn bộ suite**

Run: `python -m pytest tests/test_subscriptions.py tests/test_state.py tests/test_telegram.py -v`

Run: `python -m pytest -q`

Expected: all tests pass; không có request mạng thật.

- [x] **Step 7: Ghi bằng chứng, chuyển P21 sang `DONE`, P22 sang `IN_PROGRESS` và commit**

```bash
git add src/newsbot/subscriptions.py tests/test_subscriptions.py PROJECT_STATUS.md
git commit -m "feat: manage Telegram news subscriptions"
```

---

### Task P22: Pipeline fan-out, pending retry và recipient isolation

**Files:**
- Modify: `src/newsbot/pipeline.py`
- Modify: `tests/test_pipeline.py`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: `initialize_subscriptions`, `SubscriptionService.sync`, `format_article_message`, `TelegramClient.send_message`, schema v2.
- Produces: `RunStats.recipient_deliveries`, `RunStats.recipient_failures`, `Pipeline.run()` với migration/command/retry/RSS sequence.

- [x] **Step 1: Đổi test helpers sang interface nhiều recipient**

```python
def candidate_with_id(article_id: str) -> ArticleCandidate:
    return ArticleCandidate(
        article_id=article_id,
        source="VnExpress",
        category="Thời sự",
        title=f"Bài {article_id}",
        url=f"https://example.test/{article_id}",
        published_at=NOW,
        rss_summary=f"Mô tả {article_id}",
    )


class FakeArticleLoader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, candidate: ArticleCandidate) -> ExtractedArticle:
        self.calls.append(candidate.article_id)
        return ExtractedArticle(candidate, "Nội dung đầy đủ " * 20, 80)


class FakeTelegram:
    def __init__(self, errors: dict[str, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, text: str, *, chat_id: str) -> None:
        error = self.errors.get(chat_id)
        if error is not None:
            raise error
        self.sent.append((chat_id, text))
```

Mở rộng `pipeline_factory` để nhận `state: BotState | None`, `subscribers: list[str] | None`, `candidate_ids: list[str]`, `telegram_errors: dict[str, Exception] | None`; trả thêm `FakeArticleLoader`. State do factory tạo phải có `subscriptions_initialized=True` để test delivery không vô tình chạy migration.

- [x] **Step 2: Viết test một summary fan-out tới hai subscriber**

```python
@pytest.mark.asyncio
async def test_one_summary_is_sent_to_two_subscribers() -> None:
    pipeline, store, telegram, gemini, _, _ = pipeline_factory(
        initialized=True,
        subscribers=["111", "222"],
        candidate_ids=["id-0"],
    )
    stats = await pipeline.run()
    assert gemini.calls == 1
    assert [chat_id for chat_id, _ in telegram.sent] == ["111", "222"]
    assert store.saved.seen["id-0"].delivered_to == ["111", "222"]
    assert store.saved.seen["id-0"].pending_message is None
    assert stats.sent == 1
    assert stats.recipient_deliveries == 2
```

`pipeline_factory` truyền `initial_chat_id=""` và `subscriptions=None`; subscription behavior riêng đã được khóa ở P21.

- [x] **Step 3: Viết test pending retry không cần RSS/Gemini**

```python
@pytest.mark.asyncio
async def test_pending_message_retries_when_article_is_absent_from_feed() -> None:
    state = BotState(
        initialized=True,
        subscriptions_initialized=True,
        subscribers=["111", "222"],
        seen={"gone": SeenEntry(
            "https://example.test/gone",
            NOW.isoformat(),
            delivered_to=["111"],
            pending_message="Bản tin đã cache",
        )},
    )
    pipeline, store, telegram, gemini, _, loader = pipeline_factory(state=state, candidate_ids=[])
    stats = await pipeline.run()
    assert telegram.sent == [("222", "Bản tin đã cache")]
    assert gemini.calls == 0
    assert loader.calls == []
    assert store.saved.seen["gone"].pending_message is None
    assert stats.recipient_deliveries == 1
```

- [x] **Step 4: Viết test recipient failure isolation và no-subscriber path**

```python
@pytest.mark.asyncio
async def test_forbidden_recipient_is_removed_without_blocking_other_recipient() -> None:
    pipeline, store, telegram, _, _, _ = pipeline_factory(
        initialized=True,
        subscribers=["111", "222"],
        candidate_ids=["id-0"],
        telegram_errors={"111": TelegramForbiddenError("blocked")},
    )
    stats = await pipeline.run()
    assert [chat_id for chat_id, _ in telegram.sent] == ["222"]
    assert store.saved.subscribers == ["222"]
    assert store.saved.seen["id-0"].delivered_to == ["222"]
    assert stats.recipient_failures == 1


@pytest.mark.asyncio
async def test_no_subscribers_records_article_without_loading_or_summarizing() -> None:
    pipeline, store, telegram, gemini, _, loader = pipeline_factory(
        initialized=True,
        subscribers=[],
        candidate_ids=["id-0"],
    )
    await pipeline.run()
    assert loader.calls == []
    assert gemini.calls == 0
    assert telegram.sent == []
    assert "id-0" in store.saved.seen
```

Thêm test lỗi `TelegramApiError` ở recipient 222: recipient 111 chỉ nhận một lần, `pending_message` còn lại, lượt chạy sau chỉ gửi 222 và không gọi Gemini lại. `TelegramAuthError` phải dừng run sau khi state cục bộ đã lưu. Dùng `caplog` với chat ID dài giả để assert log lỗi không chứa ID đầy đủ.

- [x] **Step 5: Viết test người đăng ký sau giai đoạn trống không nhận backlog**

```python
@pytest.mark.asyncio
async def test_new_subscriber_skips_articles_recorded_while_no_one_subscribed() -> None:
    first, first_store, _, _, _, _ = pipeline_factory(
        initialized=True,
        subscribers=[],
        candidate_ids=["old"],
    )
    await first.run()
    state = first_store.saved

    class CommandTelegram:
        async def get_commands(self, offset: int) -> tuple[list[TelegramCommand], int]:
            return [TelegramCommand(40, "222", "start")], 41

        async def send_text(self, text: str, *, chat_id: str | None = None) -> None:
            return None

    await SubscriptionService(CommandTelegram()).sync(state)
    second, _, second_telegram, _, _, _ = pipeline_factory(
        state=state,
        candidate_ids=["old", "new"],
    )
    await second.run()
    assert len(second_telegram.sent) == 1
    assert "Bài new" in second_telegram.sent[0][1]
```

- [x] **Step 6: Chạy RED test**

Run: `python -m pytest tests/test_pipeline.py -v`

Expected: FAIL vì pipeline vẫn dùng một `chat_id`, chỉ đánh dấu seen sau delivery và chưa có pending retry.

- [x] **Step 7: Implement pipeline sequence và stats**

```python
@dataclass(slots=True)
class RunStats:
    feeds_ok: int = 0
    feeds_failed: int = 0
    candidates: int = 0
    bootstrapped: int = 0
    sent: int = 0
    recipient_deliveries: int = 0
    recipient_failures: int = 0
    local_fallback: int = 0
    skipped: int = 0
    failed: int = 0
```

Thêm protocol và constructor fields chính xác:

```python
class TelegramProtocol(Protocol):
    async def send_message(self, text: str, *, chat_id: str) -> None: ...


class SubscriptionProtocol(Protocol):
    async def sync(self, state: BotState) -> SubscriptionStats: ...


# Hai tham số mới của Pipeline.__init__
subscriptions: SubscriptionProtocol | None = None
initial_chat_id: str = ""
```

Thứ tự non-dry run bắt buộc:

```python
state = self.state_store.load()
initialize_subscriptions(state, self.initial_chat_id)
try:
    if self.subscriptions is not None:
        await self.subscriptions.sync(state)
finally:
    self._save(state)
await self._retry_pending(state, stats)
candidates = await self.feed_loader()
```

Khi tạo bài mới có subscriber, format message rồi ghi `SeenEntry(..., delivered_to=[], pending_message=message)` và save trước lần send đầu. `_deliver_entry` lặp snapshot subscriber, save ngay sau mỗi success, loại subscriber khi 403, giữ pending khi lỗi temporary và xóa pending khi không còn subscriber hoạt động bị thiếu. Khi không có subscriber, ghi entry không pending và không gọi article loader/Gemini.

`stats.sent` đếm số article ID có ít nhất một delivery thành công trong run, còn `recipient_deliveries` đếm từng chat thành công. Bootstrap và các bài không được chọn bởi `--limit` phải có `delivered_to=list(state.subscribers)` để tài khoản hiện tại không nhận backlog. Dry-run giữ nguyên sequence cũ: không seed, không đọc command, không retry, không save.

- [x] **Step 8: Chạy pipeline tests, full suite và kiểm tra state không đổi bởi test**

Run: `python -m pytest tests/test_pipeline.py tests/test_subscriptions.py -v`

Run: `python -m pytest -q`

Run: `git diff --exit-code -- state/seen.json`

Expected: all tests pass; live state file unchanged.

- [x] **Step 9: Ghi bằng chứng, chuyển P22 sang `DONE`, P23 sang `IN_PROGRESS` và commit**

```bash
git add src/newsbot/pipeline.py tests/test_pipeline.py PROJECT_STATUS.md
git commit -m "feat: deliver each article to all subscribers"
```

---

### Task P23: Main wiring, backward compatibility và hướng dẫn vận hành

**Files:**
- Modify: `src/newsbot/main.py`
- Modify if required: `src/newsbot/setup.py`
- Modify: `tests/test_main.py`
- Modify if required: `tests/test_setup.py`
- Modify: `README.md`
- Modify: `PROJECT_STATUS.md`

**Interfaces:**
- Consumes: `SubscriptionService`, `Pipeline(..., subscriptions, initial_chat_id)`, settings/secrets hiện có.
- Produces: CLI production tự đồng bộ subscription; setup, `--show-chat-id`, `--dry-run` và `--limit` vẫn tương thích.

- [x] **Step 1: Viết test main chỉ bật subscription trong non-dry run**

```python
@pytest.mark.asyncio
async def test_main_wires_subscription_service_and_original_chat_seed(monkeypatch, tmp_path) -> None:
    captured = {}
    settings = SimpleNamespace(
        feeds=(), gemini_api_key="gemini-test", telegram_bot_token="telegram-test",
        telegram_chat_id="111", model="gemini-3.1-flash-lite",
        max_gemini_rpm=10, max_gemini_rpd=450, max_seen_articles=2_000,
        seen_retention_days=30, state_path=tmp_path / "seen.json",
    )

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)
        async def run(self, **kwargs):
            return RunStats()

    monkeypatch.setattr(main_module, "Pipeline", FakePipeline)
    monkeypatch.setattr(main_module, "SubscriptionService", lambda telegram: ("subscriptions", telegram))
    monkeypatch.setattr(main_module, "load_settings", lambda *args, **kwargs: settings)
    monkeypatch.chdir(tmp_path)

    assert await main_module.async_main([]) == 0
    assert captured["initial_chat_id"] == "111"
    assert captured["subscriptions"][0] == "subscriptions"
```

Thêm `from types import SimpleNamespace` và import `RunStats` ở đầu test. Dry-run case dùng cùng `settings`, gọi `async_main(["--dry-run"])` và assert hai giá trị dependency rỗng như trên.

Thêm dry-run case assert `subscriptions is None`, `initial_chat_id == ""` và state không được migrate/save.

- [x] **Step 2: Chạy RED test**

Run: `python -m pytest tests/test_main.py -v`

Expected: FAIL vì main chưa import/tạo `SubscriptionService` và Pipeline chưa nhận hai dependency mới.

- [x] **Step 3: Wire production dependencies**

```python
subscriptions = None
initial_chat_id = ""
if not args.dry_run:
    gemini = GeminiSummarizer(...)
    telegram = TelegramClient(client, settings.telegram_bot_token, settings.telegram_chat_id)
    subscriptions = SubscriptionService(telegram)
    initial_chat_id = settings.telegram_chat_id

pipeline = Pipeline(
    state_store=StateStore(settings.state_path),
    feed_loader=lambda: fetch_all_feeds(client, settings.feeds),
    article_loader=lambda candidate: fetch_and_extract(client, candidate),
    gemini=gemini,
    local=LocalSummarizer(),
    telegram=telegram,
    subscriptions=subscriptions,
    initial_chat_id=initial_chat_id,
    now=lambda: datetime.now(UTC),
    max_seen_articles=settings.max_seen_articles,
    retention_days=settings.seen_retention_days,
)
```

Không thêm secret. Giữ `TELEGRAM_CHAT_ID` bắt buộc để migration tài khoản gốc an toàn. Chỉ chỉnh `setup.py` nếu method signature mới làm test setup thất bại; hành vi setup và ba secret không đổi.

- [x] **Step 4: Cập nhật README bằng luồng người dùng chính xác**

README phải nói rõ:

1. Tài khoản gốc tiếp tục nhận sau deploy, không nhận lại backlog.
2. Tài khoản thứ hai mở bot, gửi `/start`, chờ tối đa một chu kỳ khoảng 15 phút và chỉ nhận bài mới sau đó.
3. `/stop` dừng nhận; `/start` lần nữa đăng ký lại nhưng không phát lại lịch sử.
4. Mỗi bài chỉ dùng một lần tóm tắt cho mọi subscriber.
5. Ai tìm thấy username bot đều có thể `/start`; muốn đóng đăng ký thì phải triển khai allowlist ở phiên bản khác.
6. 403 của một chat chỉ gỡ chat đó; 401 của token dừng workflow.

- [x] **Step 5: Chạy compatibility tests và CLI**

Run: `python -m pytest tests/test_main.py tests/test_setup.py tests/test_config.py -v`

Run: `news-bot --help`

Run: `python -m pytest -q`

Expected: all tests pass; CLI cũ còn đủ bốn flag; setup vẫn gắn ba secrets qua stdin.

- [x] **Step 6: Ghi bằng chứng, chuyển P23 sang `DONE`, P24 sang `IN_PROGRESS` và commit**

```bash
git add src/newsbot/main.py src/newsbot/setup.py tests/test_main.py tests/test_setup.py README.md PROJECT_STATUS.md
git commit -m "docs: wire and explain Telegram subscriptions"
```

Chỉ stage `setup.py`/`test_setup.py` nếu hai file thực sự thay đổi.

---

### Task P24: Verification, deploy và nghiệm thu hai tài khoản

**Files:**
- Modify: `tests/test_security.py`
- Modify: `PROJECT_STATUS.md`
- Runtime state change by workflow: `state/seen.json`

**Interfaces:**
- Consumes: toàn bộ P19–P23, private GitHub repository và secrets hiện có.
- Produces: bằng chứng tự động, workflow schema-v2 xanh và nghiệm thu thật `/start`/`/stop`.

- [x] **Step 1: Bổ sung security regression cho chat ID và pending cache**

```python
def test_serialized_pending_cache_has_no_article_body_fields(tmp_path) -> None:
    state = BotState(
        seen={"a": SeenEntry(
            "https://example.test/a",
            "2026-09-19T00:00:00+00:00",
            pending_message="[Nguồn • Mục]\n\nTiêu đề\n\nTóm tắt\n\nĐọc bài gốc: https://example.test/a",
        )},
    )
    path = tmp_path / "seen.json"
    StateStore(path).save(state)
    raw = json.loads(path.read_text(encoding="utf-8"))
    entry = raw["seen"]["a"]
    assert "article_body" not in entry
    assert "content" not in entry
    assert set(entry) == {"url", "sent_at", "delivered_to", "pending_message"}
```

Runtime log masking được khóa bằng test `caplog` tại P22; P24 chỉ kiểm tra shape state serialize để không tạo helper trùng chỉ phục vụ test.

- [x] **Step 2: Chạy verification gate cục bộ**

Run: `python -m compileall -q src`

Run: `python -m pytest -q`

Run: `git grep -n -E "AIza[0-9A-Za-z_-]{20,}|[0-9]{6,}:[A-Za-z0-9_-]{30,}" -- ':!docs/superpowers/plans/*' ':!tests/test_security.py'`

Run: `news-bot --dry-run --limit 1`

Run: `git diff --exit-code -- state/seen.json`

Expected: compile/test/dry-run pass; secret scan không có kết quả; dry-run không đổi live state.

- [x] **Step 3: Review diff và commit verification**

Ghi số test pass và kết quả các lệnh vào P24, rồi commit test/tracker trước deploy.

```bash
git add tests/test_security.py PROJECT_STATUS.md
git commit -m "test: verify multi-subscriber delivery"
```

- [x] **Step 4: Đồng bộ state mới nhất và push không ghi đè workflow**

Run: `git fetch origin`

Run: `git rebase origin/feat/telegram-news-bot`

Run: `git push origin HEAD:feat/telegram-news-bot`

Expected: fast-forward push. Nếu workflow vừa commit state và push bị từ chối, ghi lỗi P24, fetch/rebase lại; không force-push.

- [x] **Step 5: Chạy workflow migration và kiểm tra schema mà không in chat ID**

Run: `gh workflow run news-bot.yml --ref feat/telegram-news-bot -f dry_run=false -f limit=1`

Run: `$runId = gh run list --workflow news-bot.yml --branch feat/telegram-news-bot --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId'`

Run: `gh run watch $runId --exit-status`

Sau khi workflow xanh, pull/rebase commit state và chỉ kiểm tra metadata:

```powershell
$state = Get-Content -Raw -Encoding utf8 state/seen.json | ConvertFrom-Json
"schema=$($state.schema_version) subscribers=$($state.subscribers.Count) offset=$($state.telegram_update_offset)"
```

Expected: `schema=2`, subscriber count ít nhất 1, không có backlog hàng loạt và log không chứa chat ID đầy đủ.

- [x] **Step 6: Nghiệm thu tài khoản thứ hai với `/start`**

Từ tài khoản Telegram thứ hai, mở bot và gửi `/start`. Chờ workflow kế tiếp hoặc chạy workflow thủ công không giới hạn. Xác nhận trong tối đa một chu kỳ:

- Bot trả `Đã đăng ký nhận các tin mới.`
- Tài khoản thứ hai không nhận bất kỳ bài cũ nào.
- Bài RSS mới tiếp theo xuất hiện trên cả tài khoản gốc và tài khoản thứ hai với cùng nội dung.
- Log run cho bài đó chỉ tăng một Gemini request nhưng có hai recipient deliveries.

- [x] **Step 7: Nghiệm thu `/stop` và đăng ký lại**

Từ tài khoản thứ hai gửi `/stop`, chờ workflow và xác nhận `Đã dừng nhận tin.`. Chạy/đợi một bài mới để xác nhận chỉ tài khoản gốc nhận. Sau đó gửi `/start` lần nữa nếu muốn giữ cả hai tài khoản hoạt động và xác nhận không có backlog.

- [x] **Step 8: Hoàn tất tracker và commit trạng thái tài liệu**

Chuyển P24 và dự án sang `DONE`, đặt `Bước đang thực hiện` thành không có, ghi run ID, số test, kết quả hai tài khoản và mọi lỗi đã đóng.

```bash
git add PROJECT_STATUS.md
git commit -m "docs: complete multi-subscriber rollout"
git pull --rebase origin feat/telegram-news-bot
git push origin HEAD:feat/telegram-news-bot
```

## Final Acceptance Gate

Chỉ tuyên bố P17 hoàn tất khi mọi điều kiện sau đều đạt:

```text
1. Schema v1 migrate sang v2 mà không mất seen/quota và tài khoản gốc không nhận backlog.
2. /start và /stop idempotent, offset tăng qua mọi update, chỉ chat private được xử lý.
3. Một bài mới gọi Gemini một lần và cùng bản tin đến hai subscriber.
4. Recipient lỗi temporary được retry từ pending_message; recipient đã thành công không nhận trùng.
5. Recipient 403 bị gỡ riêng; Telegram 401 dừng workflow.
6. Không subscriber thì bài vẫn được ghi nhận nhưng không tải toàn văn, không gọi Gemini và không gửi.
7. State vẫn bị giới hạn 2.000 bài/30 ngày và không chứa toàn văn bài báo.
8. compileall, toàn bộ pytest, secret scan, dry-run và GitHub Actions đều đạt.
9. Nghiệm thu thật /start, nhận bài mới, /stop và đăng ký lại đạt trên tài khoản thứ hai.
10. PROJECT_STATUS.md không có hai bước IN_PROGRESS và chứa bằng chứng run cuối.
```
