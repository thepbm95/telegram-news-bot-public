from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = 2
DEFAULT_PRIMARY_MODEL = "gemini-3.1-flash-lite"
# How the next plain-number reply is applied: replace the choice, add to it or remove from it.
SELECTION_MODES = ("replace", "add", "remove")


class StateError(ValueError):
    """Raised when persisted delivery state is invalid."""


@dataclass(slots=True)
class Subscriber:
    # feed id -> ISO timestamp of the moment the subscriber selected that feed.
    feeds: dict[str, str] = field(default_factory=dict)
    menu_pending: bool = False
    mode: str = "replace"


@dataclass(slots=True)
class SeenEntry:
    url: str
    first_seen_at: str
    last_seen_at: str
    feeds: list[str] = field(default_factory=list)
    delivered_to: list[str] = field(default_factory=list)
    pending_message: str | None = None


@dataclass(slots=True)
class UsageState:
    quota_day: str = ""
    requests: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class BotState:
    telegram_update_offset: int = 0
    subscribers: dict[str, Subscriber] = field(default_factory=dict)
    bootstrapped_feeds: set[str] = field(default_factory=set)
    seen: dict[str, SeenEntry] = field(default_factory=dict)
    usage: UsageState = field(default_factory=UsageState)


def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise StateError("Invalid state timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise StateError(f"Invalid state file {label}")
    return list(dict.fromkeys(value))


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise StateError(f"Invalid state file {label}")
    parse_timestamp(value)
    return value


def _offset(raw: dict[str, Any]) -> int:
    value = raw.get("telegram_update_offset")
    if type(value) is not int or value < 0:
        raise StateError("Invalid state file Telegram update offset")
    return value


def _pending(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise StateError("Invalid state file pending message")
    return value


def _quota_day(raw_usage: dict[str, Any]) -> str:
    value = raw_usage.get("quota_day", "")
    if not isinstance(value, str):
        raise StateError("Invalid state file usage")
    return value


def _from_v3(raw: dict[str, Any]) -> BotState:
    raw_subscribers = raw.get("subscribers")
    if not isinstance(raw_subscribers, dict):
        raise StateError("Invalid state file subscriber list")
    subscribers: dict[str, Subscriber] = {}
    for chat_id, value in raw_subscribers.items():
        if not isinstance(value, dict) or not isinstance(value.get("feeds"), dict):
            raise StateError("Invalid state file subscriber")
        if not isinstance(value.get("menu_pending"), bool):
            raise StateError("Invalid state file subscriber")
        mode = value.get("mode", "replace")
        if mode not in SELECTION_MODES:
            raise StateError("Invalid state file subscriber mode")
        feeds = {}
        for feed_id, selected_at in value["feeds"].items():
            feeds[feed_id] = _timestamp(selected_at, "subscriber selection time")
        subscribers[chat_id] = Subscriber(feeds=feeds, menu_pending=value["menu_pending"], mode=mode)

    seen: dict[str, SeenEntry] = {}
    for article_id, value in raw["seen"].items():
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            raise StateError("Invalid state file seen entry")
        seen[article_id] = SeenEntry(
            url=value["url"],
            first_seen_at=_timestamp(value.get("first_seen_at"), "seen entry"),
            last_seen_at=_timestamp(value.get("last_seen_at"), "seen entry"),
            feeds=_string_list(value.get("feeds"), "seen feed list"),
            delivered_to=_string_list(value.get("delivered_to"), "seen delivery list"),
            pending_message=_pending(value.get("pending_message")),
        )

    raw_requests = raw["usage"].get("requests")
    if not isinstance(raw_requests, dict) or any(
        not isinstance(model, str) or type(count) is not int or count < 0
        for model, count in raw_requests.items()
    ):
        raise StateError("Invalid state file usage")

    return BotState(
        telegram_update_offset=_offset(raw),
        subscribers=subscribers,
        bootstrapped_feeds=set(_string_list(raw.get("bootstrapped_feeds"), "bootstrapped feeds")),
        seen=seen,
        usage=UsageState(quota_day=_quota_day(raw["usage"]), requests=dict(raw_requests)),
    )


def _from_v2(raw: dict[str, Any], primary_model: str) -> BotState:
    """Upgrade v2 state: everyone must choose categories again, history stays for dedupe."""
    subscribers = {
        chat_id: Subscriber(menu_pending=True)
        for chat_id in _string_list(raw.get("subscribers"), "subscriber list")
    }
    seen: dict[str, SeenEntry] = {}
    for article_id, value in raw["seen"].items():
        if not isinstance(value, dict) or not isinstance(value.get("url"), str):
            raise StateError("Invalid state file seen entry")
        sent_at = _timestamp(value.get("sent_at"), "seen entry")
        seen[article_id] = SeenEntry(
            url=value["url"],
            first_seen_at=sent_at,
            last_seen_at=sent_at,
            delivered_to=_string_list(value.get("delivered_to", []), "seen delivery list"),
        )

    requests = raw["usage"].get("gemini_requests", 0)
    if type(requests) is not int or requests < 0:
        raise StateError("Invalid state file usage")
    return BotState(
        telegram_update_offset=_offset(raw),
        subscribers=subscribers,
        seen=seen,
        usage=UsageState(
            quota_day=_quota_day(raw["usage"]),
            requests={primary_model: requests} if requests else {},
        ),
    )


def _state_from_dict(raw: Any, primary_model: str) -> BotState:
    if not isinstance(raw, dict):
        raise StateError("Invalid state file schema")
    if not isinstance(raw.get("seen"), dict) or not isinstance(raw.get("usage"), dict):
        raise StateError("Invalid state file structure")
    schema_version = raw.get("schema_version")
    if schema_version == SCHEMA_VERSION:
        return _from_v3(raw)
    if schema_version == LEGACY_SCHEMA_VERSION:
        return _from_v2(raw, primary_model)
    raise StateError("Invalid state file schema")


def forget_unknown_feeds(state: BotState, known_feed_ids: Iterable[str]) -> None:
    known = set(known_feed_ids)
    for subscriber in state.subscribers.values():
        subscriber.feeds = {
            feed_id: selected_at
            for feed_id, selected_at in subscriber.feeds.items()
            if feed_id in known
        }
    state.bootstrapped_feeds &= known


class StateStore:
    def __init__(
        self,
        path: Path,
        *,
        primary_model: str = DEFAULT_PRIMARY_MODEL,
        known_feed_ids: Iterable[str] | None = None,
    ) -> None:
        self.path = path
        self.primary_model = primary_model
        self.known_feed_ids = None if known_feed_ids is None else set(known_feed_ids)

    def load(self) -> BotState:
        if not self.path.exists():
            return BotState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            state = _state_from_dict(raw, self.primary_model)
        except (OSError, json.JSONDecodeError, StateError) as exc:
            raise StateError(f"Invalid state file: {self.path}") from exc
        if self.known_feed_ids is not None:
            forget_unknown_feeds(state, self.known_feed_ids)
        return state

    def save(self, state: BotState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "telegram_update_offset": state.telegram_update_offset,
            "subscribers": {
                chat_id: {
                    "feeds": dict(sorted(subscriber.feeds.items())),
                    "menu_pending": subscriber.menu_pending,
                    "mode": subscriber.mode,
                }
                for chat_id, subscriber in state.subscribers.items()
            },
            "bootstrapped_feeds": sorted(state.bootstrapped_feeds),
            "seen": {
                article_id: {
                    "url": entry.url,
                    "first_seen_at": entry.first_seen_at,
                    "last_seen_at": entry.last_seen_at,
                    "feeds": list(dict.fromkeys(entry.feeds)),
                    "delivered_to": sorted(set(entry.delivered_to)),
                    "pending_message": entry.pending_message,
                }
                for article_id, entry in state.seen.items()
            },
            "usage": {
                "quota_day": state.usage.quota_day,
                "requests": dict(sorted(state.usage.requests.items())),
            },
        }
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(self.path)


def prune_seen(
    state: BotState,
    now: datetime,
    *,
    max_items: int = 10_000,
    retention_days: int = 7,
    protected_feeds: Iterable[str] = (),
) -> dict[str, SeenEntry]:
    """Return the entries worth keeping.

    Entries still present in a followed RSS feed get a fresh ``last_seen_at`` on every
    run, so they never age out; dropping them would make the next run resend them.
    ``protected_feeds`` are followed feeds not refreshed yet (or failing), whose entries
    are kept regardless of age for the same reason.
    """
    protected = set(protected_feeds)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cutoff = now.astimezone(UTC) - timedelta(days=retention_days)
    kept = [
        (article_id, entry, parse_timestamp(entry.last_seen_at))
        for article_id, entry in state.seen.items()
        if entry.pending_message is not None
        or parse_timestamp(entry.last_seen_at) >= cutoff
        or protected.intersection(entry.feeds)
    ]
    kept.sort(key=lambda item: (item[1].pending_message is not None, item[2]), reverse=True)
    return {article_id: entry for article_id, entry, _ in kept[:max_items]}
