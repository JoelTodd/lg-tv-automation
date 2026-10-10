"""Shared console logging helpers."""

from __future__ import annotations

import sys
from contextvars import ContextVar
from collections.abc import Callable


log_sink: ContextVar[Callable[[str], None] | None] = ContextVar("log_sink", default=None)


def log(message: str) -> None:
    """Route diagnostics to the dashboard or emit a stable stderr prefix."""

    sink = log_sink.get()
    if sink is not None:
        sink(message)
    else:
        print(f"[lg-tv-play] {message}", file=sys.stderr)
