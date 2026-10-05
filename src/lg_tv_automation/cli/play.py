"""Prepare one local movie, run mpv, and restore the desktop."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import shutil
import signal
import time

from ..config import require_tv_ip
from ..console import log
from ..constants import DEFAULT_DISPLAY_OUTPUT, DEFAULT_TV_INPUT
from ..display import DisplayController
from ..lifecycle import finish_on_cancel, run_cleanup_step
from ..media import detect_hdr_from_file, normalize_mpv_args, resolve_movie_path
from ..profiles import build_desktop_profile, build_movie_profile
from ..models import TvProfile
from ..session import session_lock
from ..tv import TvController


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play a local movie with the workstation's LG TV movie settings.")
    parser.add_argument("movie", type=Path, help="Video file or directory containing the main video.")
    return parser.parse_args()


async def run_mpv(mpv_args: list[str]) -> int:
    """Wait for playback; stop and reap the child before restoring hardware."""
    # Shield process creation too: cancellation can arrive after fork but
    # before the process handle is returned to this coroutine.
    spawn = asyncio.create_task(asyncio.create_subprocess_exec("mpv", *mpv_args))
    proc = None
    try:
        proc = await asyncio.shield(spawn)
        return await proc.wait()
    finally:
        async def stop_child() -> None:
            child = proc
            if child is None:
                try:
                    child = await spawn
                except Exception:
                    return  # The original spawn exception still propagates.
            if child.returncode is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(child.wait(), timeout=3)
                except TimeoutError:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    await asyncio.wait_for(child.wait(), timeout=3)
        await run_cleanup_step(stop_child, "Stop mpv", timeout=8)


def probe_hdr(movie: Path) -> bool:
    want_hdr, reason = detect_hdr_from_file(movie)
    if reason.startswith("ffprobe failed:") or reason == "no video stream found":
        raise RuntimeError(f"Cannot determine video format: {reason}")
    return want_hdr


async def apply_tv_profile(tv: TvController, profile: TvProfile, *, cleanup: bool = False) -> None:
    async with asyncio.timeout(90):
        await tv.apply_profile(profile)
    # Input classification and live signal are mandatory. Picture/motion API
    # failures remain playback warnings, but cleanup must report every failure.
    failures = list(tv.failures) if cleanup else tv.critical_failures(tolerate_picture_mode=True)
    if failures:
        raise RuntimeError("; ".join(failures))
    if tv.failures:
        log("Playback profile is degraded: " + "; ".join(tv.failures))


async def run_playback(movie: Path, *, mpv_options: list[str] | None = None) -> int:
    """The sole workflow; optional mpv options are for internal timing tests."""
    started = time.monotonic()
    try:
        with session_lock():
            movie = await asyncio.to_thread(resolve_movie_path, movie)
            if shutil.which("mpv") is None:
                raise RuntimeError("mpv is not installed or not on PATH.")
            tv = TvController(require_tv_ip(), DEFAULT_TV_INPUT)
            display = DisplayController(DEFAULT_DISPLAY_OUTPUT)
            display_changed = tv_changed = connected = setup_complete = interrupted = False
            result = 0
            cleanup_failures: list[str] = []
            log("Preparing playback.")

            async def connect() -> None:
                nonlocal connected
                await tv.__aenter__()
                connected = True

            try:
                # Settle every read/worker before writes or rollback, including
                # on cancellation. Independent reads overlap for latency.
                prepared = await finish_on_cancel(asyncio.gather(
                    asyncio.to_thread(probe_hdr, movie),
                    asyncio.to_thread(display.capture),
                    connect(),
                    return_exceptions=True,
                ))
                errors = [str(value) for value in prepared if isinstance(value, BaseException)]
                if errors:
                    raise RuntimeError("Preparation failed: " + "; ".join(errors))

                display_changed = True
                await finish_on_cancel(asyncio.to_thread(display.apply_movie_state, enable_hdr=prepared[0]))
                tv_changed = True
                await finish_on_cancel(apply_tv_profile(tv, build_movie_profile(want_hdr=prepared[0])))
                setup_complete = True
                log(f"Ready in {time.monotonic() - started:.2f}s; starting mpv.")
                result = await run_mpv(normalize_mpv_args([*(mpv_options or []), "--", str(movie)]))
            except asyncio.CancelledError:
                interrupted = True
                result = 130
            except Exception as err:
                log(f"Playback failed: {err}")
                result = 1
            finally:
                if display_changed or tv_changed:
                    log("Restoring desktop.")
                if display_changed:
                    try:
                        restore_display = display.apply_desktop_state if setup_complete else display.restore
                        interrupted = await run_cleanup_step(
                            lambda: asyncio.to_thread(restore_display), "Display restore",
                        ) or interrupted
                    except Exception as err:
                        cleanup_failures.append(f"Display restore failed: {err}")
                if connected and tv_changed:
                    try:
                        interrupted = await run_cleanup_step(
                            lambda: apply_tv_profile(tv, build_desktop_profile(), cleanup=True), "TV restore",
                        ) or interrupted
                    except Exception as err:
                        cleanup_failures.append(f"TV restore failed: {err}")
                try:
                    if not connected:
                        tv.return_to_input = False
                    interrupted = await run_cleanup_step(
                        lambda: tv.__aexit__(None, None, None), "TV disconnect", timeout=15,
                    ) or interrupted
                except Exception as err:
                    cleanup_failures.append(f"TV disconnect failed: {err}")
                for failure in cleanup_failures:
                    log(failure)

            if interrupted:
                return 130
            return 1 if cleanup_failures and result == 0 else result
    except (OSError, RuntimeError) as err:
        log(str(err))
        return 1


def main(*, mpv_options: list[str] | None = None) -> None:
    args = parse_args()
    async def run() -> int:
        task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
        try:
            return await run_playback(args.movie, mpv_options=mpv_options)
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
    try:
        raise SystemExit(asyncio.run(run()))
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise SystemExit(130)
