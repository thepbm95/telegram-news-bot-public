import json
from datetime import UTC, datetime, timedelta

import pytest

from newsbot.state import (
    BotState,
    SeenEntry,
    StateError,
    StateStore,
    Subscriber,
    UsageState,
    prune_seen,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)


def iso(value: datetime) -> str:
    return value.isoformat()


def entry(url: str, last_seen: datetime, *, pending: str | None = None) -> SeenEntry:
    return SeenEntry(
        url=url,
        first_seen_at=iso(last_seen),
        last_seen_at=iso(last_seen),
        feeds=["vne-thoi-su"],
        pending_message=pending,
    )


def test_missing_file_loads_empty_state(tmp_path) -> None:
    state = StateStore(tmp_path / "seen.json").load()

    assert state == BotState()


def test_atomic_round_trip_preserves_schema_three(tmp_path) -> None:
    path = tmp_path / "seen.json"
    state = BotState(
        telegram_update_offset=42,
        subscribers={"111": Subscriber(feeds={"vne-thoi-su": iso(NOW)}, menu_pending=False)},
        bootstrapped_feeds={"vne-thoi-su"},
        seen={"a": entry("https://example.test/a", NOW, pending="Tin chờ")},
        usage=UsageState(quota_day="2026-09-23", requests={"gemini-3.1-flash-lite": 5}),
    )

    StateStore(path).save(state)
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["schema_version"] == 3
    assert raw["subscribers"] == {
        "111": {"feeds": {"vne-thoi-su": iso(NOW)}, "menu_pending": False, "mode": "replace"}
    }
    assert StateStore(path).load() == state
    assert not path.with_suffix(".json.tmp").exists()


def test_schema_two_migrates_everyone_to_pending_menu(tmp_path) -> None:
    path = tmp_path / "seen.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "initialized": True,
                "subscriptions_initialized": True,
                "telegram_update_offset": 7,
                "subscribers": ["111", "222"],
                "seen": {
                    "a": {
                        "url": "https://example.test/a",
                        "sent_at": "2026-09-22T01:00:00+00:00",
                        "delivered_to": ["111"],
                        "pending_message": "Tin cũ đang chờ",
                    }
                },
                "usage": {"quota_day": "2026-09-22", "gemini_requests": 154},
            }
        ),
        encoding="utf-8",
    )

    state = StateStore(path, primary_model="primary").load()

    assert state.telegram_update_offset == 7
    assert state.subscribers == {
        "111": Subscriber(menu_pending=True),
        "222": Subscriber(menu_pending=True),
    }
    assert state.bootstrapped_feeds == set()
    assert state.seen["a"] == SeenEntry(
        url="https://example.test/a",
        first_seen_at="2026-09-22T01:00:00+00:00",
        last_seen_at="2026-09-22T01:00:00+00:00",
        feeds=[],
        delivered_to=["111"],
        pending_message=None,
    )
    assert state.usage == UsageState(quota_day="2026-09-22", requests={"primary": 154})


def test_schema_one_is_no_longer_accepted(tmp_path) -> None:
    path = tmp_path / "seen.json"
    path.write_text(
        json.dumps({"schema_version": 1, "initialized": True, "seen": {}, "usage": {}}),
        encoding="utf-8",
    )

    with pytest.raises(StateError):
        StateStore(path).load()


def test_unknown_feed_ids_are_dropped_on_load(tmp_path) -> None:
    path = tmp_path / "seen.json"
    StateStore(path).save(
        BotState(
            subscribers={"111": Subscriber(feeds={"vne-thoi-su": iso(NOW), "removed": iso(NOW)})},
            bootstrapped_feeds={"vne-thoi-su", "removed"},
        )
    )

    state = StateStore(path, known_feed_ids={"vne-thoi-su"}).load()

    assert state.subscribers["111"].feeds == {"vne-thoi-su": iso(NOW)}
    assert state.bootstrapped_feeds == {"vne-thoi-su"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update(telegram_update_offset=-1),
        lambda raw: raw.update(subscribers=["111"]),
        lambda raw: raw["subscribers"]["111"].update(menu_pending="yes"),
        lambda raw: raw["subscribers"]["111"]["feeds"].update(bad="not-a-time"),
        lambda raw: raw["seen"]["a"].update(feeds="vne-thoi-su"),
        lambda raw: raw["seen"]["a"].update(last_seen_at=None),
        lambda raw: raw["seen"]["a"].update(pending_message=3),
        lambda raw: raw["usage"].update(requests={"m": -1}),
    ],
)
def test_invalid_schema_three_fields_raise(tmp_path, mutate) -> None:
    path = tmp_path / "seen.json"
    StateStore(path).save(
        BotState(
            subscribers={"111": Subscriber(feeds={"vne-thoi-su": iso(NOW)})},
            seen={"a": entry("https://example.test/a", NOW)},
        )
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    mutate(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(StateError):
        StateStore(path).load()


def test_invalid_json_raises_instead_of_erasing_state(tmp_path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(StateError):
        StateStore(path).load()
    assert path.read_text(encoding="utf-8") == "{broken"


def test_prune_keeps_recently_seen_and_pending_entries() -> None:
    state = BotState(
        seen={
            "fresh": entry("https://example.test/fresh", NOW - timedelta(days=6)),
            "stale": entry("https://example.test/stale", NOW - timedelta(days=8)),
            "pending": entry("https://example.test/pending", NOW - timedelta(days=40), pending="Tin"),
        }
    )

    kept = prune_seen(state, NOW, retention_days=7)

    assert set(kept) == {"fresh", "pending"}


def test_prune_still_in_feed_entry_survives_even_if_first_seen_long_ago() -> None:
    old = entry("https://example.test/old", NOW)
    old.first_seen_at = iso(NOW - timedelta(days=90))

    assert set(prune_seen(BotState(seen={"old": old}), NOW)) == {"old"}


def test_prune_cap_drops_least_recently_seen_first() -> None:
    state = BotState(
        seen={
            str(index): entry(f"https://example.test/{index}", NOW - timedelta(hours=index))
            for index in range(5)
        }
    )

    assert set(prune_seen(state, NOW, max_items=3)) == {"0", "1", "2"}


def test_prune_keeps_entries_of_protected_feeds() -> None:
    stale = entry("https://example.test/stale", NOW - timedelta(days=30))

    assert set(prune_seen(BotState(seen={"s": stale}), NOW, protected_feeds={"vne-thoi-su"})) == {"s"}
    assert prune_seen(BotState(seen={"s": stale}), NOW, protected_feeds={"other"}) == {}


def test_subscriber_mode_round_trips_and_defaults_to_replace(tmp_path) -> None:
    path = tmp_path / "seen.json"
    StateStore(path).save(BotState(subscribers={"1": Subscriber(mode="add"), "2": Subscriber()}))
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["subscribers"]["2"]["mode"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    state = StateStore(path).load()

    assert state.subscribers["1"].mode == "add"
    assert state.subscribers["2"].mode == "replace"


def test_invalid_subscriber_mode_raises(tmp_path) -> None:
    path = tmp_path / "seen.json"
    StateStore(path).save(BotState(subscribers={"1": Subscriber()}))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["subscribers"]["1"]["mode"] = "weird"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(StateError):
        StateStore(path).load()
