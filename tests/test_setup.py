import subprocess
from pathlib import Path

import httpx
import pytest

from newsbot.setup import GithubSecretStore, run_setup


def test_github_secret_store_finds_package_remote_outside_repository() -> None:
    project_root = Path("C:/package-root")

    def runner(args, **kwargs):
        if args[:3] == ["gh", "repo", "view"]:
            raise subprocess.CalledProcessError(1, args)
        assert args == ["git", "-C", str(project_root), "remote", "get-url", "origin"]
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="https://github.com/thepbm95/telegram-news-bot.git\n",
        )

    store = GithubSecretStore(runner=runner, project_root=project_root)

    assert store.repo_name() == "thepbm95/telegram-news-bot"


def test_github_secret_store_passes_value_only_through_stdin() -> None:
    calls: list[dict[str, object]] = []

    def runner(args, **kwargs):
        calls.append({"args": args, **kwargs})
        if args[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(args, 0, stdout="thepbm95/telegram-news-bot\n")
        return subprocess.CompletedProcess(args, 0, stdout="")

    store = GithubSecretStore(runner=runner)
    repo = store.repo_name()
    store.set_secret("TELEGRAM_BOT_TOKEN", "telegram-secret", repo)

    secret_call = calls[1]
    assert secret_call["args"] == [
        "gh",
        "secret",
        "set",
        "TELEGRAM_BOT_TOKEN",
        "--repo",
        "thepbm95/telegram-news-bot",
    ]
    assert secret_call["input"] == "telegram-secret"
    assert "telegram-secret" not in " ".join(secret_call["args"])


@pytest.mark.asyncio
async def test_setup_verifies_bot_waits_for_message_and_sets_all_secrets() -> None:
    update_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal update_calls
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"id": 99, "is_bot": True, "username": "dcm_bot"}},
            )
        update_calls += 1
        updates = [] if update_calls == 1 else [
            {"update_id": 1, "message": {"chat": {"id": 123456, "type": "private"}}}
        ]
        return httpx.Response(200, json={"ok": True, "result": updates})

    class SecretStore:
        def __init__(self) -> None:
            self.saved: dict[str, str] = {}

        def repo_name(self) -> str:
            return "thepbm95/telegram-news-bot"

        def set_secret(self, name: str, value: str, repo: str) -> None:
            assert repo == "thepbm95/telegram-news-bot"
            self.saved[name] = value

    prompts = iter(["telegram-secret", "gemini-secret"])
    messages: list[str] = []
    waits: list[str] = []
    store = SecretStore()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await run_setup(
            client,
            secret_store=store,
            secret_prompt=lambda prompt: next(prompts),
            wait_for_user=lambda prompt: waits.append(prompt) or "",
            output=messages.append,
        )

    assert result.bot_username == "dcm_bot"
    assert result.chat_id == "123456"
    assert result.repo == "thepbm95/telegram-news-bot"
    assert store.saved == {
        "GEMINI_API_KEY": "gemini-secret",
        "TELEGRAM_BOT_TOKEN": "telegram-secret",
        "TELEGRAM_CHAT_ID": "123456",
    }
    assert len(waits) == 1
    assert any("@dcm_bot" in message for message in messages)
    assert all("telegram-secret" not in message for message in messages)
    assert all("gemini-secret" not in message for message in messages)
