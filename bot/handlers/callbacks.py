"""Callback query handlers for quality selection, media downloading, and delivery."""

from __future__ import annotations

import re
import shutil
import uuid

from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.types import CallbackQuery, FSInputFile
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from bot.config import settings
from bot.database.db import db
from bot.keyboards.inline import DownloadCallback
from bot.middlewares.throttling import UserConcurrencyLimiter, user_limiter
from bot.services.downloader import (
    DownloadedMedia,
    download_media,
    translate_download_error,
)

callbacks_router = Router(name="callbacks_router")


def _sanitize_filename(title: str, default: str = "LissSave_Media") -> str:
    """Create a safe filesystem/Telegram attachment filename from media title."""
    cleaned = re.sub(r'[\\/*?:"<>|\r\n\t]+', " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return default
    return cleaned[:60]


@callbacks_router.callback_query(DownloadCallback.filter())
async def handle_download_callback(
    callback: CallbackQuery,
    callback_data: DownloadCallback,
    user_limiter: UserConcurrencyLimiter = user_limiter,
) -> None:
    """Process quality button click: download media, send to user, and clean up temp files."""
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    user_id = callback.from_user.id
    req_data = await db.get_media_request(callback_data.req_id)
    if not req_data:
        await callback.answer(
            "⚠️ Сессия ссылки устарела. Отправьте ссылку ещё раз.",
            show_alert=True,
        )
        return

    await callback.answer()

    url = str(req_data["url"])
    platform = str(req_data["platform"])
    title = str(req_data["title"])
    duration = int(req_data.get("duration") or 0)
    choice = callback_data.choice

    # Unique subfolder per download execution to prevent collisions
    task_id = f"{callback_data.req_id}_{uuid.uuid4().hex[:6]}"
    downloaded: DownloadedMedia | None = None

    async with user_limiter.track(user_id):
        try:
            await callback.message.edit_text("⏳ Загружаю и обрабатываю...")
        except Exception as exc:
            logger.debug("Could not edit status message: {}", exc)

        action = ChatAction.UPLOAD_VOICE if choice == "audio" else ChatAction.UPLOAD_VIDEO

        try:
            async with ChatActionSender(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                action=action,
            ):
                downloaded = await download_media(
                    url=url,
                    choice=choice,
                    req_id=task_id,
                    fallback_title=title,
                    fallback_duration=duration,
                )

                safe_name = _sanitize_filename(downloaded.title)

                if downloaded.media_type == "audio":
                    input_file = FSInputFile(
                        path=downloaded.file_path,
                        filename=f"{safe_name}.mp3",
                    )
                    await callback.message.answer_audio(
                        audio=input_file,
                        title=downloaded.title,
                        performer=downloaded.uploader,
                        duration=downloaded.duration,
                        caption=settings.ad_caption,
                    )
                else:
                    input_file = FSInputFile(
                        path=downloaded.file_path,
                        filename=f"{safe_name}.mp4",
                    )
                    await callback.message.answer_video(
                        video=input_file,
                        width=downloaded.width,
                        height=downloaded.height,
                        duration=downloaded.duration,
                        supports_streaming=True,
                        caption=settings.ad_caption,
                    )

            await db.log_download(
                user_id=user_id,
                platform=platform,
                url=url,
                media_type=downloaded.media_type,
                quality=downloaded.quality_label,
                file_size=downloaded.file_size,
            )

            try:
                await callback.message.delete()
            except Exception as exc:
                logger.debug("Could not delete status message after send: {}", exc)

            logger.info(
                "Delivered {} ({}, {:.1f} MB) from {} to user={}",
                downloaded.media_type,
                downloaded.quality_label,
                downloaded.file_size / (1024 * 1024),
                platform,
                user_id,
            )

        except Exception as exc:
            friendly = translate_download_error(exc)
            logger.error(
                "Failed to process download for user={} url={} choice={}: {}",
                user_id,
                url,
                choice,
                exc,
            )
            try:
                await callback.message.edit_text(friendly.user_message)
            except Exception:
                await callback.message.answer(friendly.user_message)

        finally:
            if downloaded is not None:
                try:
                    downloaded.file_path.unlink(missing_ok=True)
                except Exception as exc:
                    logger.warning("Failed to unlink temp file {}: {}", downloaded.file_path, exc)
                shutil.rmtree(downloaded.temp_dir, ignore_errors=True)
            else:
                shutil.rmtree(settings.temp_downloads_dir / task_id, ignore_errors=True)
