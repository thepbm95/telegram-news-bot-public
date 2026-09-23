from pathlib import Path

import pytest

from newsbot.config import ConfigError, ModelSpec, load_settings


ENV = {
    "GEMINI_API_KEY": "gemini-test",
    "TELEGRAM_BOT_TOKEN": "telegram-test",
    "TELEGRAM_CHAT_ID": "123456",
}


def test_loads_expected_feeds_and_limits() -> None:
    settings = load_settings(ENV, Path("config/feeds.toml"))

    assert len(settings.feeds) == 55
    assert len({feed.id for feed in settings.feeds}) == 55
    sources = [feed.source for feed in settings.feeds]
    assert [(source, sources.count(source)) for source in dict.fromkeys(sources)] == [
        ("VnExpress", 17),
        ("Dân trí", 18),
        ("CafeBiz", 3),
        ("GenK", 8),
        ("Kenh14", 9),
    ]
    assert settings.feeds[0].id == "vne-thoi-su"
    assert settings.models == (
        ModelSpec("gemini-3.1-flash-lite", 10, 450),
        ModelSpec("gemini-3.5-flash-lite", 10, 450),
        ModelSpec("gemma-4-31b-it", 3, 300),
    )
    assert settings.max_seen_articles == 10_000
    assert settings.seen_retention_days == 7


def test_missing_secret_fails_fast() -> None:
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        load_settings({}, Path("config/feeds.toml"))


def test_dry_run_can_load_without_secrets() -> None:
    settings = load_settings({}, Path("config/feeds.toml"), require_secrets=False)

    assert settings.gemini_api_key == ""
    assert settings.telegram_bot_token == ""
    assert settings.telegram_chat_id == ""



def test_custom_gemini_models() -> None:
    settings = load_settings({**ENV, "GEMINI_MODELS": " a:5:20 , b:1:2 "}, Path("config/feeds.toml"))

    assert settings.models == (ModelSpec("a", 5, 20), ModelSpec("b", 1, 2))


@pytest.mark.parametrize("value", ["bad", "a:5", "a:0:5", "a:x:5", ":1:1", "a:1:1,a:2:2"])
def test_invalid_gemini_models_fail(value: str) -> None:
    with pytest.raises(ConfigError, match="GEMINI_MODELS"):
        load_settings({**ENV, "GEMINI_MODELS": value}, Path("config/feeds.toml"))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('id = ""', "missing: id"),
        ('id = "same"', "ids must be unique"),
    ],
)
def test_feed_ids_are_required_and_unique(tmp_path: Path, body: str, message: str) -> None:
    feeds = tmp_path / "feeds.toml"
    entries = []
    for index in range(2):
        entries.append(
            "[[feeds]]\n"
            f"{body}\n"
            f'name = "Feed {index}"\nsource = "S"\ncategory = "C"\n'
            f'url = "https://example.test/{index}.rss"\n'
        )
    feeds.write_text("\n".join(entries), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_settings(ENV, feeds)


def test_chat_id_is_optional_since_subscribers_use_start() -> None:
    env = {key: value for key, value in ENV.items() if key != "TELEGRAM_CHAT_ID"}

    settings = load_settings(env, Path("config/feeds.toml"))

    assert settings.telegram_chat_id == ""
