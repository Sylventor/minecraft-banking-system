"""Confirmation state holds a single stable transaction key across repeated interactions."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass
class Confirmation:
    user_id: int
    timeout: int = 120
    idempotency_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.monotonic)
    completed_operation_id: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def execute(self, user_id: int, send: Callable[[str], Awaitable[str]]) -> str:
        if user_id != self.user_id:
            raise PermissionError("Это подтверждение принадлежит другому пользователю")
        async with self.lock:
            if self.completed_operation_id:
                return self.completed_operation_id
            if time.monotonic() - self.created_at >= self.timeout:
                raise TimeoutError(
                    "Срок кнопки истёк. Откройте /menu и продолжите незавершённый перевод."
                )
            result = await send(self.idempotency_key)
            self.completed_operation_id = result
            return result
