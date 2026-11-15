"""Main entry point for LissSave Telegram Media Downloader Bot."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from loguru import logger

from assets.generate_banner import main as generate_banner_main
from bot.config import settings
from bot.database.db import db
from bot.handlers.admin import admin_router
from bot.handlers.callbacks import callbacks_router
from bot.handlers.user import user_router
from bot.middlewares.throttling import ThrottlingMiddleware

TDLIB_MEDIA_SUBDIRS = {
    "videos",
    "music",
    "documents",
    "temp",
    "photos",
    "voice",
    "animations",
    "video_notes",
}


def setup_logging() -> None:
    """Configure Loguru to output structured logs to stdout and a rotating file."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logger.add(
        settings.data_dir / "bot.log",
        level="INFO",
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        encoding="utf-8",
    )


def prepare_directories_and_assets() -> None:
    """Create data/temp directories, purge stale temp files, and verify welcome banner."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.temp_downloads_dir.exists():
        shutil.rmtree(settings.temp_downloads_dir, ignore_errors=True)
    settings.temp_downloads_dir.mkdir(parents=True, exist_ok=True)

    if not settings.welcome_banner_path.exists():
        try:
            generate_banner_main()
        except Exception as exc:
            logger.warning("Failed to generate welcome banner on startup: {}", exc)


def _purge_old_files_sync(max_age_tdlib_sec: int = 300, max_age_temp_sec: int = 1800) -> int:
    """Remove cached media from local telegram-bot-api (TDLib) and stale temp folders.

    Preserves TDLib binlog/sqlite state files while deleting uploaded video/audio copies.
    """
    now = time.time()
    removed_bytes = 0

    # 1. Clean TDLib cache directories (/var/lib/telegram-bot-api & ./data/telegram-bot-api-data)
    tdlib_roots = [
        Path("/var/lib/telegram-bot-api"),
        settings.data_dir / "telegram-bot-api-data",
    ]
    seen_inodes: set[tuple[int, int]] = set()

    for root in tdlib_roots:
        if not root.exists():
            continue
        for subdir in root.rglob("*"):
            if not subdir.is_dir() or subdir.name not in TDLIB_MEDIA_SUBDIRS:
                continue
            for file_path in subdir.rglob("*"):
                if not file_path.is_file():
                    continue
                try:
                    st = file_path.stat()
                    inode_key = (st.st_dev, st.st_ino)
                    if inode_key in seen_inodes:
                        continue
                    seen_inodes.add(inode_key)
                    if now - st.st_mtime >= max_age_tdlib_sec:
                        file_path.unlink(missing_ok=True)
                        removed_bytes += st.st_size
                except OSError:
                    continue

    # 2. Clean orphaned folders in data/temp_downloads older than 30 minutes
    if settings.temp_downloads_dir.exists():
        for entry in settings.temp_downloads_dir.iterdir():
            try:
                if now - entry.stat().st_mtime >= max_age_temp_sec:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
            except OSError:
                continue

    return removed_bytes


async def storage_cleanup_worker() -> None:
    """Background task running every 5 minutes to prevent disk from filling up."""
    while True:
        try:
            freed = await asyncio.to_thread(_purge_old_files_sync)
            await db.prune_old_requests(hours=6)
            if freed > 0:
                logger.info(
                    "Storage janitor freed {:.1f} MB from local Bot API cache",
                    freed / (1024 * 1024),
                )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("Storage cleanup worker error: {}", exc)
        await asyncio.sleep(300)


async def set_bot_commands(bot: Bot) -> None:
    """Register public slash commands in Telegram client menu."""
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Справка и поддерживаемые платформы"),
    ]
    try:
        await bot.set_my_commands(commands)
    except Exception as exc:
        logger.warning("Could not set bot commands: {}", exc)


async def main() -> None:
    """Initialize database, Local Bot API session, routers, and start polling."""
    setup_logging()
    prepare_directories_and_assets()
    await db.init()

    local_url = settings.local_api_url.strip()
    if local_url:
        api_server = TelegramAPIServer.from_base(local_url, is_local=True)
        session = AiohttpSession(api=api_server, timeout=1800)
        logger.info("Using Local Telegram Bot API server at {} (2 GB limit enabled)", local_url)
    else:
        session = AiohttpSession(timeout=600)
        logger.info("Using standard Telegram Bot API server")

    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=MemoryStorage())

    throttling_mw = ThrottlingMiddleware()
    dp.message.middleware(throttling_mw)
    dp.callback_query.middleware(throttling_mw)

    dp.include_router(admin_router)
    dp.include_router(callbacks_router)
    dp.include_router(user_router)

    await set_bot_commands(bot)
    cleanup_task = asyncio.create_task(storage_cleanup_worker())

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info("LissSave Bot started as @{} (id={})", me.username, me.id)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        await bot.session.close()
        logger.info("LissSave Bot stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process terminated by signal")
