"""Thin subprocess wrapper used by the display and media helpers."""

from __future__ import annotations

import subprocess


def run_command(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its text output.

    The launcher intentionally centralises subprocess handling here so failures
    have a consistent shape and are easy to stub in future tests.
    """

    return subprocess.run(cmd, check=check, text=True, capture_output=True)
