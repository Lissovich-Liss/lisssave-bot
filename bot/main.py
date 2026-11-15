"""Main entry point for LissSave Telegram Media Downloader Bot."""

from __future__ import annotations

import asyncio
import shutil
import sys

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

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info("LissSave Bot started as @{} (id={})", me.username, me.id)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("LissSave Bot stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process terminated by signal")
