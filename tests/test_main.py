import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import newsbot.main as main_module
from newsbot.config import ModelSpec
from newsbot.main import _configure_utf8_console, build_parser
from newsbot.pipeline import RunStats
from newsbot.telegram import TelegramClient


def test_cli_exposes_safe_operating_modes() -> None:
    parser = build_parser()

    args = parser.parse_args(["--dry-run", "--limit", "1"])

    assert args.dry_run is True
    assert args.limit == 1
    assert parser.parse_args(["--show-chat-id"]).show_chat_id is True
    assert parser.parse_args(["--setup-github"]).setup_github is True


@pytest.mark.asyncio
async def test_setup_flag_runs_without_regular_config(monkeypatch, tmp_path) -> None:
    calls = 0

    async def fake_setup(client) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(main_module, "run_setup", fake_setup, raising=False)
    monkeypatch.chdir(tmp_path)

    result = await main_module.async_main(["--setup-github"])

    assert result == 0
    assert calls == 1


@pytest.mark.asyncio
async def test_http_client_identifies_as_browser_for_news_sites(monkeypatch, tmp_path) -> None:
    observed_user_agent = ""

    async def inspect_client(client) -> None:
        nonlocal observed_user_agent
        observed_user_agent = client.headers["user-agent"]

    monkeypatch.setattr(main_module, "run_setup", inspect_client)
    monkeypatch.chdir(tmp_path)

    await main_module.async_main(["--setup-github"])

    assert observed_user_agent.startswith("Mozilla/5.0")
    assert "Chrome/" in observed_user_agent


def test_cli_configures_reconfigurable_windows_streams_as_utf8() -> None:
    class Stream:
        def __init__(self) -> None:
            self.encoding: str | None = None

        def reconfigure(self, *, encoding: str) -> None:
            self.encoding = encoding

    stdout = Stream()
    stderr = Stream()

    _configure_utf8_console(stdout, stderr)

    assert stdout.encoding == "utf-8"
    assert stderr.encoding == "utf-8"


def test_cli_logging_does_not_expose_telegram_token(monkeypatch, caplog) -> None:
    fake_token = "123456789:AA" + "x" * 32
    httpx_logger = logging.getLogger("httpx")
    previous_level = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)
    caplog.set_level(logging.INFO)

    async def fake_main() -> int:
        return 0

    async def request_updates() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True, "result": []})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await TelegramClient(client, fake_token, "").list_chat_ids()

    monkeypatch.setattr(main_module, "async_main", fake_main)
    try:
        with pytest.raises(SystemExit) as exit_info:
            main_module.main()
        assert exit_info.value.code == 0
        asyncio.run(request_updates())
    finally:
        httpx_logger.setLevel(previous_level)

    assert fake_token not in caplog.text


def _settings(tmp_path: Path, *, chat_id: str = "111") -> SimpleNamespace:
    return SimpleNamespace(
        feeds=(),
        gemini_api_key="gemini-test",
        telegram_bot_token="telegram-test",
        telegram_chat_id=chat_id,
        models=(ModelSpec("gemini-3.1-flash-lite", 10, 450),),
        max_seen_articles=10_000,
        seen_retention_days=7,
        state_path=tmp_path / "seen.json",
    )


@pytest.mark.asyncio
async def test_main_wires_subscription_service_catalog_and_feeds(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    telegram = object()
    subscriptions = object()

    class FakePipeline:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def run(self, **kwargs) -> RunStats:
            return RunStats()

    monkeypatch.setattr(main_module, "load_settings", lambda *args, **kwargs: _settings(tmp_path))
    monkeypatch.setattr(main_module, "GeminiSummarizer", lambda **kwargs: object())
    monkeypatch.setattr(main_module, "TelegramClient", lambda *args, **kwargs: telegram)
    catalogs = []

    def fake_subscriptions(client, catalog):
        catalogs.append(catalog)
        return subscriptions

    monkeypatch.setattr(main_module, "SubscriptionService", fake_subscriptions, raising=False)
    monkeypatch.setattr(main_module, "Pipeline", FakePipeline)
    monkeypatch.chdir(tmp_path)

    result = await main_module.async_main([])

    assert result == 0
    assert captured["telegram"] is telegram
    assert captured["subscriptions"] is subscriptions
    assert captured["feeds"] == ()
    assert len(catalogs) == 1 and len(catalogs[0]) == 0
    assert "initial_chat_id" not in captured


@pytest.mark.asyncio
async def test_dry_run_does_not_wire_subscriptions(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def run(self, **kwargs) -> RunStats:
            return RunStats()

    def unexpected_subscription(client, catalog):
        raise AssertionError("dry-run must not create a subscription service")

    monkeypatch.setattr(main_module, "load_settings", lambda *args, **kwargs: _settings(tmp_path))
    monkeypatch.setattr(main_module, "SubscriptionService", unexpected_subscription, raising=False)
    monkeypatch.setattr(main_module, "Pipeline", FakePipeline)
    monkeypatch.chdir(tmp_path)

    result = await main_module.async_main(["--dry-run"])

    assert result == 0
    assert captured["telegram"] is None
    assert captured["subscriptions"] is None
