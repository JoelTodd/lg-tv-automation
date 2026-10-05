"""Exclusive ownership shared by playback and Codex's maintenance transport."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


def lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    directory = Path(runtime) if runtime else Path("/tmp") / f"lg-tv-automation-{os.getuid()}"
    return directory / "lg-tv-play.lock"


@contextmanager
def session_lock():
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as err:
            raise RuntimeError("Another session is already controlling the TV/display.") from err
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
