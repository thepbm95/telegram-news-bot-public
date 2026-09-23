from __future__ import annotations

import getpass
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from newsbot.telegram import TelegramClient


class SetupError(RuntimeError):
    """Raised when interactive setup cannot finish safely."""


class SecretStore(Protocol):
    def repo_name(self) -> str: ...

    def set_secret(self, name: str, value: str, repo: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SetupResult:
    bot_username: str
    chat_id: str
    repo: str


def _repo_from_remote(remote: str) -> str:
    value = remote.strip()
    if "://" in value:
        path = urlsplit(value).path
    elif ":" in value:
        path = value.split(":", 1)[1]
    else:
        raise ValueError("Unsupported Git remote URL")
    parts = [part for part in path.strip("/").removesuffix(".git").split("/") if part]
    if len(parts) < 2:
        raise ValueError("Git remote URL is missing owner or repository")
    return f"{parts[-2]}/{parts[-1]}"


class GithubSecretStore:
    def __init__(
        self,
        runner: Callable[..., Any] = subprocess.run,
        project_root: Path | None = None,
    ) -> None:
        self.runner = runner
        self.project_root = project_root or Path(__file__).resolve().parents[2]

    def repo_name(self) -> str:
        try:
            result = self.runner(
                ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                check=True,
                capture_output=True,
                text=True,
            )
            repo = str(result.stdout).strip()
            if repo:
                return repo
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

        try:
            result = self.runner(
                ["git", "-C", str(self.project_root), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            )
            return _repo_from_remote(str(result.stdout))
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
            raise SetupError("Không xác định được GitHub repository từ package đã cài") from exc

    def set_secret(self, name: str, value: str, repo: str) -> None:
        try:
            self.runner(
                ["gh", "secret", "set", name, "--repo", repo],
                input=value,
                check=True,
                capture_output=True,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise SetupError(f"Không thể lưu GitHub Secret {name}") from exc


async def run_setup(
    client: httpx.AsyncClient,
    *,
    secret_store: SecretStore | None = None,
    secret_prompt: Callable[[str], str] = getpass.getpass,
    wait_for_user: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> SetupResult:
    store = secret_store or GithubSecretStore()
    telegram_token = secret_prompt("Dán Telegram token MỚI (sẽ được ẩn): ").strip()
    if not telegram_token:
        raise SetupError("Telegram token không được để trống")

    telegram = TelegramClient(client, telegram_token, "")
    username = await telegram.get_bot_username()
    output(f"Đã xác minh token thuộc bot @{username}.")

    chat_ids = await telegram.list_chat_ids()
    if not chat_ids:
        output(f"Mở https://t.me/{username}, bấm Start và gửi một tin nhắn.")
        wait_for_user("Sau khi gửi xong, nhấn Enter để tiếp tục...")
        chat_ids = await telegram.list_chat_ids()
    if not chat_ids:
        raise SetupError(f"Bot @{username} vẫn chưa nhận được tin nhắn riêng")
    if len(chat_ids) != 1:
        raise SetupError("Bot có nhiều chat riêng; không thể tự chọn chat an toàn")

    gemini_key = secret_prompt("Dán Gemini API key MỚI (sẽ được ẩn): ").strip()
    if not gemini_key:
        raise SetupError("Gemini API key không được để trống")

    chat_id = chat_ids[0]
    repo = store.repo_name()
    for name, value in (
        ("GEMINI_API_KEY", gemini_key),
        ("TELEGRAM_BOT_TOKEN", telegram_token),
        ("TELEGRAM_CHAT_ID", chat_id),
    ):
        store.set_secret(name, value, repo)

    output(f"Đã gắn an toàn 3 GitHub Secrets vào {repo}.")
    return SetupResult(bot_username=username, chat_id=chat_id, repo=repo)
