"""Thin subprocess wrapper used by the display and media helpers."""

from __future__ import annotations

import subprocess


def run_command(cmd: list[str], check: bool = True, *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its text output.

    The launcher intentionally centralises subprocess handling here so failures
    have a consistent shape and are easy to stub in future tests.
    """

    try:
        return subprocess.run(cmd, check=check, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout:g}s.") from err
    except FileNotFoundError as err:
        raise RuntimeError(f"Required executable {cmd[0]!r} is not installed or not on PATH.") from err
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or err.stdout or "").strip()
        raise RuntimeError(f"{cmd[0]} failed (exit {err.returncode}): {detail}") from err
