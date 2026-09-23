from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

import httpx

from newsbot.catalog import Catalog
from newsbot.config import ConfigError, load_settings
from newsbot.extractors import USER_AGENT, fetch_and_extract
from newsbot.feeds import fetch_feeds
from newsbot.pipeline import Pipeline
from newsbot.setup import SetupError, run_setup
from newsbot.state import StateStore
from newsbot.subscriptions import SubscriptionService
from newsbot.summarizers import GeminiSummarizer, LocalSummarizer, build_slots
from newsbot.telegram import TelegramClient


def _configure_utf8_console(stdout: object, stderr: object) -> None:
    for stream in (stdout, stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gửi tóm tắt tin mới tới Telegram")
    parser.add_argument("--dry-run", action="store_true", help="chỉ đọc RSS và kiểm tra trích xuất")
    parser.add_argument("--limit", type=int, help="giới hạn số bài xử lý trong lần chạy")
    parser.add_argument("--show-chat-id", action="store_true", help="in chat ID riêng tư từ Telegram")
    parser.add_argument("--setup-github", action="store_true", help="tự động cấu hình GitHub Secrets")
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 1:
        raise ConfigError("--limit must be positive")

    if args.setup_github:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            await run_setup(client)
        return 0

    settings = load_settings(
        os.environ,
        Path("config/feeds.toml"),
        require_secrets=not (args.dry_run or args.show_chat_id),
    )
    if args.show_chat_id and not settings.telegram_bot_token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required with --show-chat-id")

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        if args.show_chat_id:
            telegram = TelegramClient(client, settings.telegram_bot_token, "")
            chat_ids = await telegram.list_chat_ids()
            if not chat_ids:
                print("Không tìm thấy chat riêng. Hãy nhắn /start cho bot rồi chạy lại.")
            else:
                for chat_id in chat_ids:
                    print(chat_id)
            return 0

        gemini = None
        telegram = None
        subscriptions = None
        if not args.dry_run:
            gemini = GeminiSummarizer(
                api_key=settings.gemini_api_key,
                slots=build_slots(settings.models),
            )
            telegram = TelegramClient(client, settings.telegram_bot_token, settings.telegram_chat_id)
            subscriptions = SubscriptionService(telegram, Catalog(settings.feeds))

        pipeline = Pipeline(
            state_store=StateStore(
                settings.state_path,
                primary_model=settings.models[0].name,
                known_feed_ids={feed.id for feed in settings.feeds},
            ),
            feeds=settings.feeds,
            feed_loader=lambda feeds: fetch_feeds(client, feeds),
            article_loader=lambda candidate: fetch_and_extract(client, candidate),
            gemini=gemini,
            local=LocalSummarizer(),
            telegram=telegram,
            subscriptions=subscriptions,
            now=lambda: datetime.now(UTC),
            max_seen_articles=settings.max_seen_articles,
            retention_days=settings.seen_retention_days,
        )
        stats = await pipeline.run(dry_run=args.dry_run, limit=args.limit)
        print(json.dumps(asdict(stats), ensure_ascii=False, sort_keys=True))
        return 0


def main() -> None:
    _configure_utf8_console(sys.stdout, sys.stderr)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        raise SystemExit(asyncio.run(async_main()))
    except (ConfigError, SetupError) as exc:
        raise SystemExit(f"Lỗi cấu hình: {exc}") from exc


if __name__ == "__main__":
    main()
