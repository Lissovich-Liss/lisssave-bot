"""Inline keyboards for media quality selection and the administrator panel."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.services.downloader import MediaMetadata


class DownloadCallback(CallbackData, prefix="dl"):
    """Callback data payload for quality selection buttons."""

    req_id: str
    choice: str  # "best", "1080", "720", "480", "360", or "audio"


class AdminCallback(CallbackData, prefix="adm"):
    """Callback data payload for administrator panel actions."""

    action: str


def build_quality_keyboard(req_id: str, metadata: MediaMetadata) -> InlineKeyboardMarkup:
    """Build inline keyboard for selecting video resolution or audio extraction."""
    builder = InlineKeyboardBuilder()

    if metadata.has_multiple_qualities and metadata.qualities:
        for q in metadata.qualities:
            builder.button(
                text=q.label,
                callback_data=DownloadCallback(req_id=req_id, choice=str(q.height)),
            )
        # Arrange video quality buttons in a compact 2-column grid
        row_sizes = [2] * (len(metadata.qualities) // 2)
        if len(metadata.qualities) % 2 == 1:
            row_sizes.append(1)

        builder.button(
            text="🎧 Только аудио (MP3)",
            callback_data=DownloadCallback(req_id=req_id, choice="audio"),
        )
        row_sizes.append(1)
        builder.adjust(*row_sizes)
    else:
        builder.button(
            text="🎬 Скачать видео (HD)",
            callback_data=DownloadCallback(req_id=req_id, choice="best"),
        )
        builder.button(
            text="🎧 Аудио (MP3)",
            callback_data=DownloadCallback(req_id=req_id, choice="audio"),
        )
        builder.adjust(2)

    return builder.as_markup()


def build_admin_keyboard() -> InlineKeyboardMarkup:
    """Build main inline keyboard for /admin dashboard."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📊 Обновить статистику",
        callback_data=AdminCallback(action="stats"),
    )
    builder.button(
        text="📢 Рассылка пользователям",
        callback_data=AdminCallback(action="broadcast"),
    )
    builder.button(
        text="🔄 Обновить yt-dlp",
        callback_data=AdminCallback(action="update_ytdlp"),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    """Build cancel button during FSM broadcast creation."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ Отменить рассылку",
        callback_data=AdminCallback(action="bc_cancel"),
    )
    return builder.as_markup()


def build_broadcast_buttons_step_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard allowing admin to skip adding URL buttons or cancel."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⏭ Без кнопок (пропустить)",
        callback_data=AdminCallback(action="bc_skip_btns"),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=AdminCallback(action="bc_cancel"),
    )
    builder.adjust(1)
    return builder.as_markup()


def build_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Build final confirmation keyboard before launching broadcast."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Запустить рассылку",
        callback_data=AdminCallback(action="bc_confirm"),
    )
    builder.button(
        text="❌ Отмена",
        callback_data=AdminCallback(action="bc_cancel"),
    )
    builder.adjust(2)
    return builder.as_markup()


def parse_url_buttons(raw_text: str) -> list[list[ dict[str, str]]]:
    """Parse multiline 'Label | https://url' input into serializable button rows."""
    rows: list[list[dict[str, str]]] = []
    for line in raw_text.strip().splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        label, url = [part.strip() for part in line.split("|", maxsplit=1)]
        if label and (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
            rows.append([{"text": label, "url": url}])
    return rows


def build_url_markup_from_rows(rows: list[list[dict[str, str]]] | None) -> InlineKeyboardMarkup | None:
    """Construct an InlineKeyboardMarkup from serialized URL button rows."""
    if not rows:
        return None
    inline_keyboard: list[list[InlineKeyboardButton]] = []
    for row in rows:
        btn_row = [InlineKeyboardButton(text=item["text"], url=item["url"]) for item in row]
        if btn_row:
            inline_keyboard.append(btn_row)
    if not inline_keyboard:
        return None
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)
