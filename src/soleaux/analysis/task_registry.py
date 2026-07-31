"""Package-wide keyed awaitable task sharing (D018)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")


class TaskRegistry:
    """Share equivalent work while retaining explicit cancellation ownership."""

    def __init__(self) -> None:
        self._entries: dict[Hashable, tuple[asyncio.Task[Any], int]] = {}

    @property
    def in_flight(self) -> int:
        """Return the number of live shared-work keys."""
        return len(self._entries)

    async def share(self, key: Hashable, factory: Callable[[], Awaitable[T]]) -> T:
        """Await one task per key; failures are observed but never cached."""
        entry = self._entries.get(key)
        if entry is None:
            task: asyncio.Task[Any] = asyncio.ensure_future(factory())
            entry = (task, 0)
            self._entries[key] = entry
        task, waiters = entry
        self._entries[key] = (task, waiters + 1)
        cancelled_work: asyncio.Task[Any] | None = None
        try:
            return await asyncio.shield(task)
        finally:
            current = self._entries.get(key)
            if current is not None and current[0] is task:
                remaining = current[1] - 1
                if task.done() or remaining <= 0:
                    self._entries.pop(key, None)
                    if remaining <= 0 and not task.done():
                        task.cancel()
                        cancelled_work = task
                else:
                    self._entries[key] = (task, remaining)
            if cancelled_work is not None:
                await asyncio.gather(cancelled_work, return_exceptions=True)

    async def cancel_all(self) -> None:
        """Cancel and drain all task-owned work during restart or shutdown."""
        entries = tuple(self._entries.values())
        self._entries.clear()
        for task, _waiters in entries:
            if not task.done():
                task.cancel()
        if entries:
            await asyncio.gather(
                *(task for task, _waiters in entries),
                return_exceptions=True,
            )
