"""Prepare one local movie, run mpv, and restore the desktop."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import shutil
import signal
import tempfile
import time

from ..config import require_tv_ip
from ..console import log
from ..constants import DEFAULT_DISPLAY_OUTPUT, DEFAULT_TV_INPUT
from ..display import DisplayController
from ..lifecycle import finish_on_cancel, run_cleanup_step
from ..media import detect_hdr_from_file, resolve_movie_path
from ..profiles import build_desktop_profile, build_movie_profile
from ..models import TvProfile
from ..session import session_lock
from ..tv import TvController
from ..telemetry import PlayerOutput, observe_player, read_diagnostics
from ..tui import PlaybackUI
from ..spectrum import watch_spectrum


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play a local movie with the workstation's LG TV movie settings.")
    parser.add_argument("movie", type=Path, help="Video file or directory containing the main video.")
    return parser.parse_args()


async def run_mpv(mpv_args: list[str], *, ui: PlaybackUI | None = None) -> int:
    if ui is None or not ui.enabled:
        result = await _run_mpv(mpv_args)
        if ui is not None:
            ui.player_exit = result
        return result
    ui.setup_seconds = time.monotonic() - ui.started
    with tempfile.TemporaryDirectory(prefix="lg-tv-play-") as directory:
        socket = str(Path(directory) / "mpv.sock")
        split = mpv_args.index("--") if "--" in mpv_args else len(mpv_args)
        args = [*mpv_args[:split], f"--input-ipc-server={socket}",
                "--terminal=yes", "--msg-level=all=warn", "--msg-color=no", "--term-status-msg=",
                *mpv_args[split:]]
        async with PlayerOutput() as output:
            result = await _run_mpv(args, ui=ui, socket=socket, output=output)
            ui.player_exit = result
            return result


async def _run_mpv(mpv_args: list[str], *, ui: PlaybackUI | None = None, socket: str = "", output: PlayerOutput | None = None) -> int:
    """Wait for playback; stop and reap the child before restoring hardware."""
    # Shield process creation too: cancellation can arrive after fork but
    # before the process handle is returned to this coroutine.
    redirection = {"stdout": output.fd, "stderr": asyncio.subprocess.STDOUT} if output else {}
    spawn = asyncio.create_task(asyncio.create_subprocess_exec("mpv", *mpv_args, **redirection))
    proc = None
    observer = diagnostics = spectrum = None
    try:
        proc = await asyncio.shield(spawn)
        if ui is not None:
            observer = asyncio.create_task(observe_player(socket, ui))
            diagnostics = asyncio.create_task(read_diagnostics(output.reader, ui))
            spectrum = asyncio.create_task(watch_spectrum(proc.pid, ui))
        return await proc.wait()
    finally:
        async def stop_child() -> None:
            nonlocal diagnostics
            child = proc
            if child is None:
                try:
                    child = await spawn
                except Exception:
                    return  # The original spawn exception still propagates.
            if ui is not None and diagnostics is None:
                diagnostics = asyncio.create_task(read_diagnostics(output.reader, ui))
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
            if ui is not None:
                ui.player_exit = child.returncode
        try:
            interrupted = await run_cleanup_step(stop_child, "Stop mpv", timeout=8)
        finally:
            async def stop_telemetry() -> None:
                if output is not None:
                    output.close_writer()
                if observer is not None:
                    observer.cancel()
                if spectrum is not None:
                    spectrum.cancel()
                if diagnostics is not None:
                    try:
                        await asyncio.wait_for(asyncio.shield(diagnostics), timeout=1)
                    except TimeoutError:
                        diagnostics.cancel()
                await asyncio.gather(*(task for task in (observer, diagnostics, spectrum) if task is not None), return_exceptions=True)
            telemetry_interrupted = await run_cleanup_step(stop_telemetry, "Stop telemetry", timeout=5)
        if interrupted or telemetry_interrupted:
            raise asyncio.CancelledError


async def apply_tv_profile(tv: TvController, profile: TvProfile, *, cleanup: bool = False) -> None:
    async with asyncio.timeout(90):
        await tv.apply_profile(profile)
    # Input classification, live signal and final profile verification are
    # mandatory. Earlier picture/motion API failures remain warnings, but
    # cleanup must report every failure.
    failures = list(tv.failures) if cleanup else tv.critical_failures()
    if failures:
        raise RuntimeError("; ".join(failures))
    if tv.failures:
        log("Playback profile is degraded: " + "; ".join(tv.failures))


async def run_playback(movie: Path, *, mpv_options: list[str] | None = None) -> int:
    """The sole workflow; optional mpv options are for internal timing tests."""
    async with PlaybackUI(movie) as ui:
        ui.result = await _run_playback(movie, mpv_options=mpv_options, ui=ui)
        return ui.result


async def _run_playback(movie: Path, *, mpv_options: list[str] | None, ui: PlaybackUI) -> int:
    started = time.monotonic()
    try:
        with session_lock():
            movie = await asyncio.to_thread(resolve_movie_path, movie)
            ui.movie = movie
            if shutil.which("mpv") is None:
                raise RuntimeError("mpv is not installed or not on PATH.")
            # Reject unreadable/non-video files before connecting (and possibly
            # prompting for pairing) or touching the display at all.
            want_hdr = await asyncio.to_thread(detect_hdr_from_file, movie)
            ui.format = "HDR" if want_hdr else "SDR"
            tv = TvController(require_tv_ip(), DEFAULT_TV_INPUT)
            display = DisplayController(DEFAULT_DISPLAY_OUTPUT)
            display_changed = tv_changed = setup_complete = interrupted = False
            result = 0
            cleanup_failures: list[str] = []
            ui.set_phase("Connecting TV / reading display")
            if not ui.enabled:
                log("Preparing playback.")

            try:
                # Settle every read/worker before writes or rollback, including
                # on cancellation. Independent reads overlap for latency.
                prepared = await finish_on_cancel(asyncio.gather(
                    asyncio.to_thread(display.capture),
                    tv.__aenter__(),
                    return_exceptions=True,
                ))
                errors = [str(value) for value in prepared if isinstance(value, BaseException)]
                if errors:
                    raise RuntimeError("Preparation failed: " + "; ".join(errors))

                display_changed = True
                ui.set_phase("Preparing display")
                await finish_on_cancel(asyncio.to_thread(display.apply_movie_state, enable_hdr=want_hdr))
                tv_changed = True
                ui.set_phase("Preparing TV")
                await finish_on_cancel(apply_tv_profile(tv, build_movie_profile(want_hdr=want_hdr)))
                setup_complete = True
                ui.set_phase("Playback")
                if not ui.enabled:
                    log(f"Ready in {time.monotonic() - started:.2f}s; starting mpv.")
                # Decode pre-target seek frames so skipped Dolby Vision metadata
                # cannot leave FFmpeg's RPU buffer pending and trigger warnings.
                # Keep Wayland from classifying movie playback as game content.
                # Prefer NVIDIA decoding: Vulkan Video stalled during shutdown
                # on this workstation even after reaching the end of playback.
                result = await run_mpv([
                    "--hr-seek-framedrop=no", "--wayland-content-type=none", "--hwdec=nvdec",
                    *(mpv_options or []), "--", str(movie),
                ], ui=ui)
                if result:
                    log(f"mpv exited with status {result}.")
            except asyncio.CancelledError:
                interrupted = True
                result = 130
            except Exception as err:
                log(f"Playback failed: {err}")
                result = 1
            finally:
                if (display_changed or tv_changed) and not ui.enabled:
                    log("Restoring desktop.")
                if display_changed:
                    ui.set_phase("Restoring display")
                    try:
                        restore_display = display.apply_desktop_state if setup_complete else display.restore
                        interrupted = await run_cleanup_step(
                            lambda: asyncio.to_thread(restore_display), "Display restore",
                        ) or interrupted
                    except Exception as err:
                        cleanup_failures.append(f"Display restore failed: {err}")
                if tv_changed:
                    ui.set_phase("Restoring TV")
                    try:
                        interrupted = await run_cleanup_step(
                            lambda: apply_tv_profile(tv, build_desktop_profile(), cleanup=True), "TV restore",
                        ) or interrupted
                    except Exception as err:
                        cleanup_failures.append(f"TV restore failed: {err}")
                try:
                    ui.set_phase("Disconnecting TV")
                    tv.return_to_input = tv_changed
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
