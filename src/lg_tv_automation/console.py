"""Shared console logging helpers."""

from __future__ import annotations

import sys
import logging


def detail(message: str) -> None:
    logging.getLogger("lg_tv_automation").debug(message)


def log(message: str) -> None:
    """Emit a stable stderr prefix so command output remains easy to scan."""

    print(f"[lg-tv-play] {message}", file=sys.stderr)
