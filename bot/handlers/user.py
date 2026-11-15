"""User-facing handlers for /start, /help, and incoming media links."""

from __future__ import annotations

import html
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import FSInputFile, Message, ReactionTypeEmoji
from loguru import logger

from assets.generate_banner import main as generate_banner_main
from bot.config import settings
from bot.database.db import db
from bot.keyboards.inline import build_quality_keyboard
from bot.services.downloader import (
    UserFriendlyDownloadError,
    extract_first_url,
    extract_media_info,
    translate_download_error,
)

user_router = Router(name="user_router")

WELCOME_TEXT = (
    "🦊 <b>LissSave — быстрая загрузка видео и аудио</b>\n\n"
    "Отправьте ссылку на публикацию, и бот подготовит медиафайл "
    "в оригинальном качестве без водяных знаков.\n\n"
    "<b>Поддерживаемые площадки:</b>\n"
    "• <b>TikTok</b> — видео в HD без водяных знаков и аудио\n"
    "• <b>Instagram</b> — Reels и видео-посты\n"
    "• <b>YouTube & Shorts</b> — выбор разрешения от <code>360p</code> до <code>1080p</code>\n"
    "• <b>X (Twitter), VK Видео, Reddit, Pinterest, Twitch</b> и ещё 1000+ платформ\n\n"
    "<blockquote>⚡️ Максимальный размер файла — <b>до 2 ГБ</b>.\n"
    "Форматы: <code>MP4 (H.264 / AAC)</code> и <code>MP3 (192 kbps)</code>.</blockquote>\n\n"
    "👇 <b>Отправьте ссылку в чат, чтобы начать:</b>"
)

HELP_TEXT = (
    "⚡️ <b>Справка по использованию LissSave</b>\n\n"
    "1. Скопируйте ссылку на видео или ролик из любой поддерживаемой соцсети.\n"
    "2. Отправьте ссылку боту в этот чат.\n"
    "3. Выберите нужное разрешение (например, <code>🎬 1080p</code>, <code>🎬 720p</code>) "
    "или извлечение дорожки <code>🎧 Аудио (MP3)</code>.\n\n"
    "<b>Технические параметры:</b>\n"
    "• Лимит объема файла: <code>до 2000 МБ (2 ГБ)</code>\n"
    "• Кодеки видео: <code>MP4 (H.264 + AAC + faststart)</code> — полная совместимость с iOS и Android\n"
    "• Одновременных загрузок на пользователя: <code>до 2</code>"
)


def _format_duration(seconds: int) -> str:
    """Format duration in seconds into MM:SS or HH:MM:SS."""
    if seconds <= 0:
        return "—"
    hrs, rem = divmod(seconds, 3600)
    mins, secs = divmod(rem, 60)
    if hrs > 0:
        return f"{hrs:d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


@user_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Handle /start command: register user in SQLite and send welcome banner."""
    if message.from_user:
        is_new = await db.upsert_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            language_code=message.from_user.language_code,
        )
        if is_new:
            logger.info(
                "New user registered: id={} (@{})",
                message.from_user.id,
                message.from_user.username or "no_username",
            )

    banner_path = settings.welcome_banner_path
    if not banner_path.exists():
        try:
            generate_banner_main()
        except Exception as exc:
            logger.warning("Could not auto-generate welcome banner: {}", exc)

    if banner_path.exists():
        await message.answer_photo(
            photo=FSInputFile(banner_path),
            caption=WELCOME_TEXT,
        )
    else:
        await message.answer(WELCOME_TEXT)


@user_router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help command."""
    if message.from_user:
        await db.upsert_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            language_code=message.from_user.language_code,
        )
    await message.answer(HELP_TEXT)


@user_router.message(StateFilter(None), F.text)
async def handle_link_message(message: Message) -> None:
    """Analyze incoming URLs via yt-dlp and display inline quality selection buttons."""
    if not message.from_user or not message.text:
        return

    # Ignore unknown slash commands so they don't trigger link prompts
    if message.text.strip().startswith("/"):
        return

    await db.upsert_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        language_code=message.from_user.language_code,
    )

    url = extract_first_url(message.text)
    if not url:
        await message.answer(
            "⚡️ Отправьте прямую ссылку на видео или аудио "
            "(TikTok, Instagram, YouTube, X, VK и др.)."
        )
        return

    # Set lightning reaction if supported by chat
    try:
        await message.react([ReactionTypeEmoji(emoji="⚡")])
    except Exception as exc:
        logger.debug("Reaction skipped: {}", exc)

    status_msg = await message.reply("⚡️ Анализирую ссылку...")

    try:
        metadata = await extract_media_info(url)
    except Exception as exc:
        friendly = translate_download_error(exc)
        logger.warning(
            "Link analysis failed for user={} url={}: {}",
            message.from_user.id,
            url,
            exc,
        )
        await status_msg.edit_text(friendly.user_message)
        return

    req_id = uuid.uuid4().hex[:10]
    await db.save_media_request(
        req_id=req_id,
        user_id=message.from_user.id,
        url=metadata.url,
        platform=metadata.platform,
        title=metadata.title,
        duration=metadata.duration,
        metadata=metadata.to_dict(),
    )

    safe_title = html.escape(metadata.title)
    safe_platform = html.escape(metadata.platform)
    safe_uploader = html.escape(metadata.uploader)
    dur_str = _format_duration(metadata.duration)

    info_lines = [
        f"🎬 <b>{safe_title}</b>",
        f"<blockquote>Площадка: <code>{safe_platform}</code>\n"
        f"Автор: <code>{safe_uploader}</code>\n"
        f"Длительность: <code>{dur_str}</code></blockquote>",
        "Выберите формат загрузки:",
    ]

    keyboard = build_quality_keyboard(req_id=req_id, metadata=metadata)
    await status_msg.edit_text(
        text="\n\n".join(info_lines),
        reply_markup=keyboard,
    )
