from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class GPUTaskLimiter:
    def __init__(self, max_concurrency: int):
        self._max_concurrency = max(1, max_concurrency)
        self._semaphore = asyncio.Semaphore(self._max_concurrency)

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    async def run(self, task_factory: Callable[[], Awaitable[T]]) -> T:
        async with self._semaphore:
            return await task_factory()
