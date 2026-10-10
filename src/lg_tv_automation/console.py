"""Shared console logging helpers."""

from __future__ import annotations

import sys


def log(message: str) -> None:
    """Emit a stable stderr prefix so command output remains easy to scan."""

    print(f"[lg-tv-play] {message}", file=sys.stderr)
