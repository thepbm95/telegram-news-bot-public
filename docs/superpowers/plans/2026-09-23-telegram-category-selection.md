# Telegram Category Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each Telegram subscriber pick up to 10 of 55 catalog categories (5 newspapers) by replying with numbers, and deliver only matching articles while summarizing each article once through a free Gemini model chain.

**Architecture:** `config/feeds.toml` becomes the numbered catalog (`id` per feed). State schema v3 stores per-subscriber `{feed_id: selected_at}` plus per-article `feeds`, `first_seen_at`, `last_seen_at`. A new `catalog.py` owns menu text, selection parsing and recipient eligibility; the pipeline fetches only active feeds with per-feed bootstrap; the summarizer walks an ordered list of model slots before local fallback.

**Tech Stack:** Python 3.12, httpx, feedparser, BeautifulSoup/lxml, google-genai, pytest (asyncio via `asyncio.run` in tests as existing), GitHub Actions.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-23-telegram-category-selection-design.md`.
- Max 10 categories per subscriber; catalog numbers are 1-based positions in `config/feeds.toml` (55 entries).
- Selection text regex: only digits, whitespace, `,`, `-`, `–`, with at least one digit.
- A subscriber receives an article only if a selected feed is in `entry.feeds` and `entry.first_seen_at >= selected_at` for that feed, and the chat is not in `delivered_to`.
- Only active feeds are fetched (except `--dry-run`, which fetches all). A feed joins `bootstrapped_feeds` only when its fetch returned ≥1 candidate; feeds no subscriber selects any more are removed from `bootstrapped_feeds`.
- Prune: keep if `pending_message` or `last_seen_at >= now - SEEN_RETENTION_DAYS` (default 7); safety cap `MAX_SEEN_ARTICLES` default 10000 by `last_seen_at` desc.
- `GEMINI_MODELS` default `gemini-3.1-flash-lite:10:450,gemini-3.5-flash-lite:10:450,gemma-4-31b-it:3:300`; quota day in `America/Los_Angeles`.
- v2 migration: every subscriber → no feeds, `menu_pending=true`; entries get `feeds=[]`, `first_seen_at=last_seen_at=sent_at`, `pending_message=None`; `requests={first model: gemini_requests}`. Schema v1 no longer loads.
- No new runtime dependency, no secrets or full chat IDs in logs/tests.
- Each task: RED → GREEN → full suite `python -m pytest -q` → commit; update `PROJECT_STATUS.md` row with evidence.

## File Map

| File | Responsibility |
|---|---|
| `config/feeds.toml` | 55-entry catalog with `id`, `name`, `source`, `category`, `url` |
| `src/newsbot/models.py` | `FeedConfig.id`; `ArticleCandidate.feed_ids: tuple[str, ...]` |
| `src/newsbot/config.py` | Validate ids; parse `GEMINI_MODELS` into `ModelSpec` tuple |
| `src/newsbot/feeds.py` | `+07` dates; merge `feed_ids`; `fetch_feeds` returns `FeedFetchResult` |
| `src/newsbot/extractors.py` | CafeBiz/GenK/Kenh14 selectors |
| `src/newsbot/state.py` | Schema v3, v2 migration, `Subscriber`, `prune_seen` by `last_seen_at` |
| `src/newsbot/catalog.py` (new) | `Catalog`, `format_menu`, `parse_selection`, `format_selection`, `eligible_recipients` |
| `src/newsbot/telegram.py` | `TelegramCommand.text`; commands `start/stop/chon/danhsach/select` |
| `src/newsbot/subscriptions.py` | Command handling, selection apply, one-time announcement |
| `src/newsbot/summarizers.py` | `ModelSlot`, `GeminiSummarizer(slots=...)` chain, per-model `QuotaGuard` |
| `src/newsbot/pipeline.py` | Active feeds, per-feed bootstrap, eligibility, `last_seen_at` |
| `src/newsbot/main.py`, workflow, `.env.example`, `README.md` | Wiring and docs |

---

### Task P26: Catalog and RSS

**Files:** Modify `config/feeds.toml`, `src/newsbot/models.py`, `src/newsbot/config.py`, `src/newsbot/feeds.py`; Test `tests/test_config.py`, `tests/test_feeds.py`; fixture `tests/fixtures/vccorp-feed.xml`.

**Interfaces:**
- Produces: `FeedConfig(id, name, source, category, url)`; `ArticleCandidate(..., feed_ids: tuple[str, ...] = ())`; `ModelSpec(name: str, rpm: int, rpd: int)`; `Settings.models: tuple[ModelSpec, ...]`; `FeedFetchResult(candidates: list[ArticleCandidate], ok_feed_ids: set[str])`; `async fetch_feeds(client, feeds) -> FeedFetchResult`.

- [ ] Write failing tests: catalog has 55 unique ids and ordered sources VnExpress(17) → Dân trí(18) → CafeBiz(3) → GenK(8) → Kenh14(9); duplicate/empty id raises `ConfigError`; `GEMINI_MODELS` parses default and rejects `bad`; VCCorp fixture with `pubDate` `Wed, 23 Sep 2026 05:45:00 +07` yields `published_at == 2026-09-22T22:45Z`; `deduplicate_candidates` merges `feed_ids` of same URL in first-seen order; `fetch_feeds` reports ok ids only for feeds returning ≥1 candidate.
- [ ] Run, confirm failures.
- [ ] Implement: date fallback `re.sub(r"([+-]\d{2})$", r"\g<1>00", raw)` → `email.utils.parsedate_to_datetime`; candidate built with `feed_ids=(feed.id,)`; dedupe via `dataclasses.replace`; keep `fetch_all_feeds` as thin wrapper returning candidates.
- [ ] Full suite green; commit `feat: add 55-category catalog and feed ids`.

### Task P27: VCCorp extractor

**Files:** Modify `src/newsbot/extractors.py`; fixtures `tests/fixtures/{cafebiz,genk,kenh14}-article.html` (trimmed real pages); Test `tests/test_extractors.py`.

- [ ] Failing tests: each fixture extracts ≥ 200 words, starts with sapo text, excludes related-news box text found in fixture.
- [ ] Implement `CONTENT_SELECTORS[src] = (".detail-content", "[data-role='content']")`, `LEAD_SELECTORS[src] = ("[data-role='sapo']", ".knc-sapo", ".sapo")` for `CafeBiz`, `GenK`, `Kenh14`; extend `REMOVE_SELECTORS` with VCCorp related boxes identified from fixtures.
- [ ] Full suite; commit `feat: extract CafeBiz, GenK and Kenh14 articles`.

### Task P28: State v3

**Files:** Modify `src/newsbot/state.py`; Test `tests/test_state.py`.

**Interfaces:**
- Produces: `Subscriber(feeds: dict[str, str] = {}, menu_pending: bool = False)`; `SeenEntry(url, first_seen_at, last_seen_at, feeds: list[str], delivered_to: list[str], pending_message: str | None)`; `UsageState(quota_day: str, requests: dict[str, int])`; `BotState(telegram_update_offset, subscribers: dict[str, Subscriber], bootstrapped_feeds: set[str], seen, usage)`; `StateStore(path, primary_model="gemini-3.1-flash-lite", known_feed_ids: set[str] | None = None)`; `prune_seen(state, now, *, max_items=10_000, retention_days=7) -> BotState`.

- [ ] Failing tests: v3 round-trip; v2 migration (subscribers → `menu_pending`, entry fields, usage requests); v1 raises `StateError`; unknown feed ids dropped from subscriber selections; prune keeps pending and recent `last_seen_at`, drops stale, applies cap.
- [ ] Implement; full suite (older tests that construct v2 objects are updated in P29/P30 — mark them in this task by adjusting constructors so the suite stays green).
- [ ] Commit `feat: migrate state to category schema v3`.

### Task P29: Catalog menu and Telegram commands

**Files:** Create `src/newsbot/catalog.py`; Modify `src/newsbot/telegram.py`, `src/newsbot/subscriptions.py`; Test `tests/test_catalog.py`, `tests/test_telegram.py`, `tests/test_subscriptions.py`.

**Interfaces:**
- Produces: `Catalog(feeds: tuple[FeedConfig, ...])` with `.by_number(n)`, `.number_of(feed_id)`, `.ids`; `format_menu(catalog, *, announcement: bool = False) -> list[str]` (split between newspapers under 3800 chars); `SelectionError(ValueError)`; `parse_selection(text, catalog, max_items=10) -> list[str]`; `is_selection_text(text) -> bool`; `format_selection(catalog, feed_ids) -> str`; `eligible_recipients(entry, subscribers) -> list[str]`; `TelegramCommand(update_id, chat_id, name, text="")`; `SubscriptionService(telegram, catalog, now=...)`.

- [ ] Failing tests: parsing (`"1 2, 20 40-45"`, duplicates, `"0"`, `"56"`, `"5-3"`, 11 items, `"abc"`), menu lists every source header and `n. Category`, confirmation shows `VnExpress – Thời sự (1)`; `get_commands` yields `select` for numeric text and `chon`/`danhsach`; subscription sync: `/start` new user gets menu, selection replaces and keeps old timestamps, invalid selection reply keeps old, `/danhsach`, `/stop` deletes, `menu_pending` sends announcement once and retries after `TelegramApiError`, 403 removes.
- [ ] Implement; full suite; commit `feat: let subscribers choose categories`.

### Task P30: Pipeline by category

**Files:** Modify `src/newsbot/pipeline.py`; Test `tests/test_pipeline.py`.

**Interfaces:**
- Consumes: `FeedFetchResult`, `eligible_recipients`, v3 state.
- Produces: `Pipeline(state_store, feed_loader: Callable[[tuple[FeedConfig, ...]], Awaitable[FeedFetchResult]], feeds: tuple[FeedConfig, ...], article_loader, gemini, local, telegram, subscriptions=None, now=..., max_seen_articles=10_000, retention_days=7)`; `RunStats.summaries_by_provider: dict[str, int]`.

- [ ] Failing tests: no selections → feed_loader not called; first fetch of a feed bootstraps without Gemini/Telegram; next new article goes to selecting subscriber only; article in two feeds summarized once and sent to both selectors; feed deselected then reselected re-bootstraps; failed feed not bootstrapped; `last_seen_at` refreshed; pending retry uses eligibility; `--limit`; dry-run fetches all feeds and leaves state untouched.
- [ ] Implement; full suite; commit `feat: deliver articles by selected category`.

### Task P31: Free model chain

**Files:** Modify `src/newsbot/summarizers.py`; Test `tests/test_summarizers.py`.

**Interfaces:**
- Produces: `QuotaGuard(model: str, max_requests: int)`; `ModelSlot(model: str, quota_guard: QuotaGuard, rate_limiter: SlidingWindowRateLimiter)`; `GeminiSummarizer(*, api_key, slots: list[ModelSlot], client=None, sleep=..., now=...)`; `build_slots(models: tuple[ModelSpec, ...]) -> list[ModelSlot]`.

- [ ] Failing tests: exhausted first model skips to second without request; transient errors retried twice then next model; non-retryable goes to next immediately; all fail → `SummarizationError`; per-model counters reset on new Pacific day; provider equals model name.
- [ ] Implement; full suite; commit `feat: fall back across free Gemini models`.

### Task P32: Wiring, docs, deploy

**Files:** Modify `src/newsbot/main.py`, `.github/workflows/news-bot.yml`, `.env.example`, `README.md`, `tests/test_main.py`, `tests/test_workflow.py`.

- [ ] Failing tests: main builds catalog/slots from settings; workflow passes `GEMINI_MODELS: ${{ vars.GEMINI_MODELS || '' }}` (empty → default).
- [ ] Implement; README documents `/chon`, `/danhsach`, numbered reply, 10-category cap, model chain, no billing.
- [ ] Gate: `python -m pytest -q`, `python -m compileall -q src`, secret scan (`git grep -nE "AIza|[0-9]{8,}:[A-Za-z0-9_-]{30,}"`), local `news-bot --dry-run --limit 3`.
- [ ] Do not migrate live state locally; the workflow migrates on its first run. Push; run workflow manually; verify announcements sent (`menu_pending` all false in state) and no article sent.
- [ ] Commit, update status, hand off acceptance steps to user.
