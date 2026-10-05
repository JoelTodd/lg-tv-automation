"""Finish hardware work before cancellation hands control to restoration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .console import log

T = TypeVar("T")


async def finish_on_cancel(awaitable: Awaitable[T]) -> T:
    """Let an in-flight command settle before restoring the hardware it uses."""
    operation = asyncio.ensure_future(awaitable)
    interrupted = False
    while True:
        try:
            result = await asyncio.shield(operation)
            break
        except asyncio.CancelledError:
            if operation.cancelled():
                raise
            interrupted = True
            task = asyncio.current_task()
            if task is not None:
                while task.cancelling():
                    task.uncancel()
    if interrupted:
        raise asyncio.CancelledError
    return result


async def run_cleanup_step(
    operation: Callable[[], Awaitable[None]], description: str, *, timeout: float = 90.0,
) -> bool:
    """Shield a single cleanup task, with a bounded retry for self-cancellation."""
    saw_cancellation = False
    deadline = asyncio.get_running_loop().time() + timeout
    for attempt in range(2):
        async def bounded() -> None:
            async with asyncio.timeout(max(0, deadline - asyncio.get_running_loop().time())):
                await operation()

        pending = asyncio.create_task(bounded())
        while True:
            task = asyncio.current_task()
            if task is not None:
                while task.cancelling():
                    task.uncancel()
                    saw_cancellation = True
            try:
                await asyncio.shield(pending)
                return saw_cancellation
            except asyncio.CancelledError:
                saw_cancellation = True
                if pending.cancelled():
                    break
            except TimeoutError as err:
                raise RuntimeError(f"{description} exceeded its {timeout:g}s cleanup deadline.") from err
        log(f"{description} cancelled internally; retrying cleanup.")
    raise RuntimeError(f"{description} repeatedly cancelled before completing.")
