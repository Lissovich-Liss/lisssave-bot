"""Middleware and concurrency limiter for per-user active downloads and anti-spam."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings


class UserConcurrencyLimiter:
    """Tracks the number of active download tasks per user ID."""

    def __init__(self, max_concurrent: int = 2) -> None:
        self.max_concurrent = max_concurrent
        self._active_counts: dict[int, int] = defaultdict(int)

    def get_active(self, user_id: int) -> int:
        """Return current number of active tasks for a user."""
        return self._active_counts.get(user_id, 0)

    def can_start(self, user_id: int) -> bool:
        """Check whether the user is below the concurrency threshold."""
        return self._active_counts.get(user_id, 0) < self.max_concurrent

    @asynccontextmanager
    async def track(self, user_id: int) -> AsyncIterator[None]:
        """Context manager that increments and reliably decrements user task count."""
        self._active_counts[user_id] += 1
        try:
            yield
        finally:
            self._active_counts[user_id] = max(0, self._active_counts[user_id] - 1)
            if self._active_counts[user_id] == 0:
                self._active_counts.pop(user_id, None)


user_limiter = UserConcurrencyLimiter(max_concurrent=settings.max_concurrent_downloads)


class ThrottlingMiddleware(BaseMiddleware):
    """Enforces anti-flood cooldowns and maximum concurrent downloads per user."""

    def __init__(
        self,
        limiter: UserConcurrencyLimiter = user_limiter,
        min_interval_sec: float = 0.7,
    ) -> None:
        self.limiter = limiter
        self.min_interval_sec = min_interval_sec
        self._last_event_ts: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["user_limiter"] = self.limiter

        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)

        user_id = user.id
        now = time.monotonic()

        # Check download callback concurrency limit
        if isinstance(event, CallbackQuery) and (event.data or "").startswith("dl:"):
            if not self.limiter.can_start(user_id):
                await event.answer(
                    f"⏳ У вас уже активны {self.limiter.max_concurrent} загрузки. "
                    "Дождитесь их завершения.",
                    show_alert=True,
                )
                return None

        # Lightweight anti-flood on rapid consecutive messages (excluding commands/admin)
        if isinstance(event, Message) and event.text and not event.text.startswith("/"):
            last_ts = self._last_event_ts.get(user_id, 0.0)
            if now - last_ts < self.min_interval_sec:
                return None
            self._last_event_ts[user_id] = now

            if not self.limiter.can_start(user_id):
                await event.answer(
                    f"⏳ <b>Лимит параллельных задач</b>\n\n"
                    f"Сейчас у вас уже выполняются <code>{self.limiter.max_concurrent}</code> "
                    "загрузки. Дождитесь завершения текущей обработки."
                )
                return None

        return await handler(event, data)
