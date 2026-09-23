from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from newsbot.models import FeedConfig


DEFAULT_GEMINI_MODELS = "gemini-3.1-flash-lite:10:450,gemini-3.5-flash-lite:10:450,gemma-4-31b-it:3:300"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    rpm: int
    rpd: int


@dataclass(frozen=True, slots=True)
class Settings:
    feeds: tuple[FeedConfig, ...]
    gemini_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    models: tuple[ModelSpec, ...] = ()
    max_seen_articles: int = 10_000
    seen_retention_days: int = 7
    state_path: Path = Path("state/seen.json")


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _parse_models(raw: str) -> tuple[ModelSpec, ...]:
    models: list[ModelSpec] = []
    for item in raw.split(","):
        parts = [part.strip() for part in item.split(":")]
        if len(parts) != 3 or not parts[0]:
            raise ConfigError("GEMINI_MODELS entries must look like model:rpm:rpd")
        try:
            rpm, rpd = int(parts[1]), int(parts[2])
        except ValueError as exc:
            raise ConfigError("GEMINI_MODELS rpm and rpd must be integers") from exc
        if rpm <= 0 or rpd <= 0:
            raise ConfigError("GEMINI_MODELS rpm and rpd must be positive")
        models.append(ModelSpec(parts[0], rpm, rpd))
    if len({model.name for model in models}) != len(models):
        raise ConfigError("GEMINI_MODELS must not repeat a model")
    return tuple(models)


def _load_feeds(path: Path) -> tuple[FeedConfig, ...]:
    try:
        with path.open("rb") as handle:
            raw: dict[str, Any] = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Unable to read feed configuration: {path}") from exc

    entries = raw.get("feeds")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("Feed configuration must contain at least one [[feeds]] entry")

    feeds: list[FeedConfig] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ConfigError(f"Feed entry {index} must be a table")
        values = {key: str(entry.get(key, "")).strip() for key in ("id", "name", "source", "category", "url")}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise ConfigError(f"Feed entry {index} is missing: {', '.join(missing)}")
        feeds.append(FeedConfig(**values))

    urls = [feed.url for feed in feeds]
    if len(urls) != len(set(urls)):
        raise ConfigError("Feed URLs must be unique")
    ids = [feed.id for feed in feeds]
    if len(ids) != len(set(ids)):
        raise ConfigError("Feed ids must be unique")
    return tuple(feeds)


def load_settings(
    env: Mapping[str, str],
    feeds_path: Path = Path("config/feeds.toml"),
    *,
    require_secrets: bool = True,
) -> Settings:
    secrets = {
        "GEMINI_API_KEY": env.get("GEMINI_API_KEY", "").strip(),
        "TELEGRAM_BOT_TOKEN": env.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "TELEGRAM_CHAT_ID": env.get("TELEGRAM_CHAT_ID", "").strip(),
    }
    if require_secrets:
        # TELEGRAM_CHAT_ID is optional: subscribers register themselves with /start.
        missing = [name for name, value in secrets.items() if not value and name != "TELEGRAM_CHAT_ID"]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")

    return Settings(
        feeds=_load_feeds(feeds_path),
        gemini_api_key=secrets["GEMINI_API_KEY"],
        telegram_bot_token=secrets["TELEGRAM_BOT_TOKEN"],
        telegram_chat_id=secrets["TELEGRAM_CHAT_ID"],
        models=_parse_models(env.get("GEMINI_MODELS", "").strip() or DEFAULT_GEMINI_MODELS),
        max_seen_articles=_positive_int(env, "MAX_SEEN_ARTICLES", 10_000),
        seen_retention_days=_positive_int(env, "SEEN_RETENTION_DAYS", 7),
        state_path=Path(env.get("STATE_PATH", "state/seen.json")),
    )
