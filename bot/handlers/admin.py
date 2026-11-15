"""Administrator panel (/admin): statistics, FSM broadcast with FloodWait handling, and yt-dlp updater."""

from __future__ import annotations

import asyncio
import html
import shutil
import sys
import time
from typing import Any

import yt_dlp
from aiogram import Bot, F, Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from loguru import logger

from bot.config import settings
from bot.database.db import db
from bot.keyboards.inline import (
    AdminCallback,
    build_admin_keyboard,
    build_broadcast_buttons_step_keyboard,
    build_broadcast_cancel_keyboard,
    build_broadcast_confirm_keyboard,
    build_url_markup_from_rows,
    parse_url_buttons,
)


class AdminFilter(Filter):
    """Allow access only to user IDs configured in ADMIN_IDS."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        if not event.from_user:
            return False
        return event.from_user.id in settings.admin_ids


class BroadcastState(StatesGroup):
    """FSM states for step-by-step administrator broadcast."""

    waiting_for_content = State()
    waiting_for_buttons = State()
    waiting_for_confirm = State()


admin_router = Router(name="admin_router")
admin_router.message.filter(AdminFilter())
admin_router.callback_query.filter(AdminFilter())


def _format_bytes(num_bytes: int) -> str:
    """Format byte count into human-readable MB or GB."""
    if num_bytes <= 0:
        return "0 МБ"
    gb = num_bytes / (1024**3)
    if gb >= 1.0:
        return f"{gb:.2f} ГБ"
    mb = num_bytes / (1024**2)
    return f"{mb:.1f} МБ"


def _build_progress_bar(current: int, total: int, width: int = 12) -> str:
    """Render a text progress bar for broadcast status updates."""
    if total <= 0:
        return "<code>[" + "█" * width + "] 100%</code>"
    ratio = min(1.0, max(0.0, current / total))
    filled = int(round(ratio * width))
    bar = "█" * filled + "░" * (width - filled)
    pct = int(ratio * 100)
    return f"<code>[{bar}] {pct}%</code>"


async def _render_admin_dashboard_text() -> str:
    """Collect database and system metrics and format the /admin dashboard text."""
    stats = await db.get_statistics()

    try:
        disk = shutil.disk_usage(settings.data_dir if settings.data_dir.exists() else "/")
        free_gb = disk.free / (1024**3)
        total_gb = disk.total / (1024**3)
        used_pct = ((disk.total - disk.free) / max(1, disk.total)) * 100
        disk_str = f"{free_gb:.1f} ГБ свободно из {total_gb:.1f} ГБ (занято {used_pct:.0f}%)"
    except Exception:
        disk_str = "Н/Д"

    if stats.top_platforms:
        platforms_lines = "\n".join(
            f"• <b>{html.escape(platform)}</b>: <code>{count}</code>"
            for platform, count in stats.top_platforms
        )
    else:
        platforms_lines = "• Пока нет загрузок"

    cookies_status = "✅ Активен" if settings.valid_cookies_path else "⚪️ Не задан"
    proxy_status = "✅ Активен" if settings.proxy_url.strip() else "⚪️ Прямое соединение"
    ytdlp_ver = getattr(yt_dlp.version, "__version__", "unknown")

    return (
        "🦊 <b>Панель управления LissSave</b>\n\n"
        "<b>👥 Пользователи:</b>\n"
        f"• Всего в базе: <code>{stats.total_users}</code> (активных: <code>{stats.active_users}</code>)\n"
        f"• Новых за 24 часа: <code>+{stats.new_users_24h}</code>\n"
        f"• Новых за 7 дней: <code>+{stats.new_users_7d}</code>\n\n"
        "<b>📥 Статистика скачиваний:</b>\n"
        f"• Всего загрузок: <code>{stats.total_downloads}</code>\n"
        f"• Загрузок за 24 часа: <code>+{stats.downloads_24h}</code>\n"
        f"• Общий трафик медиа: <code>{_format_bytes(stats.total_bytes)}</code>\n\n"
        "<b>🏆 Топ платформ:</b>\n"
        f"{platforms_lines}\n\n"
        "<b>🖥 Инфраструктура и система:</b>\n"
        f"• Диск сервера: <code>{disk_str}</code>\n"
        f"• Версия yt-dlp: <code>{ytdlp_ver}</code>\n"
        f"• Cookies (18+/Auth): <code>{cookies_status}</code>\n"
        f"• Прокси: <code>{proxy_status}</code>"
    )


async def _send_broadcast_payload(
    bot: Bot,
    chat_id: int,
    content_type: str,
    html_text: str,
    file_id: str | None,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    """Deliver a broadcast message (text, photo, or video) to a target chat."""
    if content_type == "photo" and file_id:
        await bot.send_photo(
            chat_id=chat_id,
            photo=file_id,
            caption=html_text or None,
            reply_markup=reply_markup,
        )
    elif content_type == "video" and file_id:
        await bot.send_video(
            chat_id=chat_id,
            video=file_id,
            caption=html_text or None,
            reply_markup=reply_markup,
            supports_streaming=True,
        )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=html_text,
            reply_markup=reply_markup,
            disable_web_page_preview=False,
        )


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    """Open administrator control panel."""
    await state.clear()
    text = await _render_admin_dashboard_text()
    await message.answer(text, reply_markup=build_admin_keyboard())


@admin_router.callback_query(AdminCallback.filter(F.action == "stats"))
async def cb_admin_stats(callback: CallbackQuery) -> None:
    """Refresh administrator dashboard statistics."""
    await callback.answer("Статистика обновлена")
    if not callback.message:
        return
    text = await _render_admin_dashboard_text()
    try:
        await callback.message.edit_text(text, reply_markup=build_admin_keyboard())
    except TelegramBadRequest as exc:
        logger.debug("Dashboard text unchanged: {}", exc)


@admin_router.callback_query(AdminCallback.filter(F.action == "update_ytdlp"))
async def cb_admin_update_ytdlp(callback: CallbackQuery) -> None:
    """Run hot-upgrade of yt-dlp package via pip directly from the admin panel."""
    await callback.answer("Запускаю обновление yt-dlp...")
    if not callback.message:
        return

    old_version = getattr(yt_dlp.version, "__version__", "unknown")
    status_msg = await callback.message.answer(
        f"⏳ <b>Обновление yt-dlp...</b>\n"
        f"Текущая версия: <code>{old_version}</code>\n"
        f"Выполняется: <code>pip install -U yt-dlp</code>"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--no-cache-dir",
            "yt-dlp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        rc = proc.returncode

        # Query newly installed yt-dlp version in a fresh subprocess
        ver_proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import yt_dlp; print(yt_dlp.version.__version__)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        ver_out, _ = await asyncio.wait_for(ver_proc.communicate(), timeout=15)
        new_version = ver_out.decode("utf-8", errors="replace").strip() or old_version
        yt_dlp.version.__version__ = new_version

        if rc == 0:
            summary = (
                "✅ <b>Пакет yt-dlp успешно проверен / обновлён</b>\n\n"
                f"• Была версия: <code>{old_version}</code>\n"
                f"• Актуальная версия: <code>{html.escape(new_version)}</code>"
            )
            logger.info("yt-dlp updated via admin panel: {} -> {}", old_version, new_version)
        else:
            err_tail = stderr.decode("utf-8", errors="replace")[-350:]
            summary = (
                "⚠️ <b>Ошибка при обновлении yt-dlp</b>\n\n"
                f"Код возврата: <code>{rc}</code>\n"
                f"<blockquote><code>{html.escape(err_tail)}</code></blockquote>"
            )
            logger.error("yt-dlp update failed (rc={}): {}", rc, err_tail)

        await status_msg.edit_text(summary)

    except Exception as exc:
        logger.error("Exception during yt-dlp hot update: {}", exc)
        await status_msg.edit_text(
            f"❌ <b>Не удалось выполнить обновление yt-dlp</b>\n"
            f"<code>{html.escape(str(exc))}</code>"
        )


# ---------------------------------------------------------------------------
# Broadcast FSM Handlers
# ---------------------------------------------------------------------------
@admin_router.callback_query(AdminCallback.filter(F.action == "broadcast"))
async def cb_start_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Step 1: Prompt admin to send broadcast message content."""
    await callback.answer()
    if not callback.message:
        return

    await state.set_state(BroadcastState.waiting_for_content)
    await callback.message.answer(
        "📢 <b>Создание рассылки — Шаг 1 из 3</b>\n\n"
        "Отправьте сообщение, которое нужно разослать пользователям:\n"
        "• Обычный текст (поддерживается форматирование)\n"
        "• Фото с подписью\n"
        "• Видео с подписью",
        reply_markup=build_broadcast_cancel_keyboard(),
    )


@admin_router.callback_query(AdminCallback.filter(F.action == "bc_cancel"))
async def cb_cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Cancel active broadcast FSM flow."""
    await state.clear()
    await callback.answer("Рассылка отменена")
    if callback.message:
        await callback.message.edit_text("❌ Создание рассылки отменено.")


@admin_router.message(BroadcastState.waiting_for_content)
async def fsm_receive_broadcast_content(message: Message, state: FSMContext) -> None:
    """Step 2: Store broadcast content and ask for optional inline URL buttons."""
    content_type = "text"
    file_id: str | None = None
    html_text = message.html_text or ""

    if message.photo:
        content_type = "photo"
        file_id = message.photo[-1].file_id
    elif message.video:
        content_type = "video"
        file_id = message.video.file_id
    elif message.text:
        content_type = "text"
    else:
        await message.answer(
            "⚠️ Поддерживаются только текст, фото или видео. Отправьте корректное сообщение:",
            reply_markup=build_broadcast_cancel_keyboard(),
        )
        return

    await state.update_data(
        content_type=content_type,
        file_id=file_id,
        html_text=html_text,
        button_rows=[],
    )
    await state.set_state(BroadcastState.waiting_for_buttons)

    await message.answer(
        "🔗 <b>Создание рассылки — Шаг 2 из 3 (Кнопки)</b>\n\n"
        "Если нужно прикрепить Inline-кнопки со ссылками, отправьте их списком "
        "(каждая кнопка с новой строки) в формате:\n"
        "<code>Текст кнопки | https://t.me/lissvpnbot</code>\n\n"
        "Или нажмите <b>«Без кнопок (пропустить)»</b>.",
        reply_markup=build_broadcast_buttons_step_keyboard(),
    )


async def _show_broadcast_preview(
    chat_id: int,
    bot: Bot,
    state: FSMContext,
) -> None:
    """Render broadcast preview and confirmation prompt for the administrator."""
    data: dict[str, Any] = await state.get_data()
    content_type = str(data.get("content_type", "text"))
    file_id = data.get("file_id")
    html_text = str(data.get("html_text", ""))
    button_rows = data.get("button_rows") or []

    markup = build_url_markup_from_rows(button_rows)
    active_ids = await db.get_active_user_ids()

    await bot.send_message(chat_id=chat_id, text="👁 <b>Предпросмотр сообщения:</b>")
    await _send_broadcast_payload(
        bot=bot,
        chat_id=chat_id,
        content_type=content_type,
        html_text=html_text,
        file_id=file_id,
        reply_markup=markup,
    )

    await state.set_state(BroadcastState.waiting_for_confirm)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "📢 <b>Создание рассылки — Шаг 3 из 3 (Подтверждение)</b>\n\n"
            f"• Получателей (активных пользователей): <code>{len(active_ids)}</code>\n"
            f"• Тип вложения: <code>{content_type.upper()}</code>\n"
            f"• Кнопок: <code>{len(button_rows)}</code>\n\n"
            "Начать отправку?"
        ),
        reply_markup=build_broadcast_confirm_keyboard(),
    )


@admin_router.message(BroadcastState.waiting_for_buttons, F.text)
async def fsm_receive_broadcast_buttons(message: Message, state: FSMContext) -> None:
    """Parse URL buttons and transition to broadcast confirmation preview."""
    rows = parse_url_buttons(message.text or "")
    if not rows:
        await message.answer(
            "⚠️ Не удалось распознать кнопки. Используйте формат:\n"
            "<code>Название кнопки | https://example.com</code>\n"
            "Либо нажмите кнопку пропуска ниже.",
            reply_markup=build_broadcast_buttons_step_keyboard(),
        )
        return

    await state.update_data(button_rows=rows)
    await _show_broadcast_preview(chat_id=message.chat.id, bot=message.bot, state=state)


@admin_router.callback_query(
    BroadcastState.waiting_for_buttons,
    AdminCallback.filter(F.action == "bc_skip_btns"),
)
async def cb_skip_broadcast_buttons(callback: CallbackQuery, state: FSMContext) -> None:
    """Skip URL buttons step and proceed directly to confirmation preview."""
    await callback.answer()
    if not callback.message:
        return
    await state.update_data(button_rows=[])
    await _show_broadcast_preview(
        chat_id=callback.message.chat.id,
        bot=callback.bot,
        state=state,
    )


@admin_router.callback_query(
    BroadcastState.waiting_for_confirm,
    AdminCallback.filter(F.action == "bc_confirm"),
)
async def cb_execute_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Execute broadcast to all active users with progress bar and FloodWait handling."""
    await callback.answer("Рассылка запущена")
    if not callback.message:
        return

    data: dict[str, Any] = await state.get_data()
    await state.clear()

    content_type = str(data.get("content_type", "text"))
    file_id = data.get("file_id")
    html_text = str(data.get("html_text", ""))
    button_rows = data.get("button_rows") or []
    markup = build_url_markup_from_rows(button_rows)

    user_ids = await db.get_active_user_ids()
    total = len(user_ids)

    progress_msg = await callback.message.edit_text(
        f"⏳ <b>Выполняется рассылка...</b>\n"
        f"{_build_progress_bar(0, total)}\n"
        f"Обработано: <code>0 / {total}</code>"
    )

    delivered = 0
    blocked = 0
    errors = 0
    start_ts = time.monotonic()
    last_ui_update = start_ts

    for idx, uid in enumerate(user_ids, start=1):
        sent = False
        attempts = 0
        while not sent and attempts < 3:
            attempts += 1
            try:
                await _send_broadcast_payload(
                    bot=callback.bot,
                    chat_id=uid,
                    content_type=content_type,
                    html_text=html_text,
                    file_id=file_id,
                    reply_markup=markup,
                )
                delivered += 1
                sent = True
            except TelegramRetryAfter as exc:
                wait_sec = float(exc.retry_after) + 1.0
                logger.warning("FloodWait during broadcast: sleeping {:.1f}s", wait_sec)
                await asyncio.sleep(wait_sec)
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                err_str = str(exc).lower()
                if (
                    isinstance(exc, TelegramForbiddenError)
                    or "chat not found" in err_str
                    or "user is deactivated" in err_str
                    or "bot was blocked" in err_str
                ):
                    blocked += 1
                    await db.set_user_active(uid, False)
                else:
                    errors += 1
                break
            except Exception as exc:
                logger.warning("Unexpected broadcast error for user={}: {}", uid, exc)
                errors += 1
                break

        # Respect Telegram global broadcast rate limits (~25 messages/sec max)
        await asyncio.sleep(0.04)

        now = time.monotonic()
        if now - last_ui_update >= 2.5 or idx == total:
            last_ui_update = now
            try:
                await progress_msg.edit_text(
                    f"⏳ <b>Выполняется рассылка...</b>\n\n"
                    f"{_build_progress_bar(idx, total)}\n"
                    f"• Обработано: <code>{idx} / {total}</code>\n"
                    f"• Доставлено: <code>{delivered}</code>\n"
                    f"• Заблокировали бота: <code>{blocked}</code>"
                )
            except Exception as exc:
                logger.debug("Progress bar update skipped: {}", exc)

    elapsed = time.monotonic() - start_ts
    await progress_msg.edit_text(
        "✅ <b>Рассылка завершена</b>\n\n"
        f"• Всего в очереди: <code>{total}</code>\n"
        f"• Успешно доставлено: <code>{delivered}</code>\n"
        f"• Заблокировали / удалены: <code>{blocked}</code>\n"
        f"• Прочие ошибки: <code>{errors}</code>\n"
        f"• Время выполнения: <code>{elapsed:.1f} сек</code>"
    )
    logger.info(
        "Broadcast finished: total={} delivered={} blocked={} errors={} elapsed={:.1f}s",
        total,
        delivered,
        blocked,
        errors,
        elapsed,
    )
