"""Primary CLI for playback and preset switching."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..console import log
from ..constants import (
    DEFAULT_DESKTOP_ICON,
    DEFAULT_DESKTOP_LABEL,
    DEFAULT_DESKTOP_PICTURE_MODE,
    DEFAULT_DISPLAY_OUTPUT,
    DEFAULT_HDR_PICTURE_MODE,
    DEFAULT_MOVIE_ICON,
    DEFAULT_MOVIE_LABEL,
    DEFAULT_SDR_PICTURE_MODE,
    DEFAULT_TRUMOTION,
    DEFAULT_TV_INPUT,
    DEFAULT_TV_IP,
)
from ..display import DisplayController, load_display_output
from ..media import choose_display_refresh_rate, choose_hdr_mode, normalize_mpv_args
from ..profiles import build_desktop_profile, build_movie_profile
from ..tv import TvController


def snapshot_display_state(output_name: str) -> dict[str, Any]:
    """Capture the small display-state subset that matters for playback debugging."""

    output = load_display_output(output_name)
    return {
        "name": output["name"],
        "currentModeId": output["currentModeId"],
        "hdr": output["hdr"],
        "wcg": output["wcg"],
        "vrrPolicy": output.get("vrrPolicy"),
    }


async def snapshot_runtime_state(args: argparse.Namespace, tv: TvController | None) -> dict[str, Any]:
    """Collect a non-invasive runtime snapshot for debug logging."""

    payload: dict[str, Any] = {}
    if not args.no_display:
        try:
            payload["display"] = snapshot_display_state(args.display_output)
        except Exception as err:
            payload["display_error"] = str(err)
    if tv is not None:
        try:
            payload["tv"] = await tv.capture(include_ui_state=False)
        except Exception as err:
            payload["tv_error"] = str(err)
    return payload


def append_debug_event(debug_log_path: Path | None, event: str, payload: dict[str, Any]) -> None:
    """Append one timestamped JSONL event to the optional debug log."""

    if debug_log_path is None:
        return

    debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event": event,
        "payload": payload,
    }
    with debug_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


async def run_mpv_with_debug(
    mpv_args: list[str],
    *,
    debug_log_path: Path | None,
    debug_cue_seconds: float,
    args: argparse.Namespace,
    tv: TvController | None,
) -> int:
    """Run mpv and optionally capture one in-playback debug snapshot."""

    log(f"Launching mpv: {' '.join(mpv_args)}")
    proc = subprocess.Popen(["mpv", *mpv_args])
    started_at = time.monotonic()
    cue_emitted = False

    while True:
        returncode = proc.poll()
        if returncode is not None:
            return returncode

        if debug_log_path is not None and not cue_emitted and time.monotonic() - started_at >= debug_cue_seconds:
            append_debug_event(
                debug_log_path,
                "in_playback",
                await snapshot_runtime_state(args, tv),
            )
            log("DEBUG CUE: Take the TV picture now.")
            cue_emitted = True

        await asyncio.sleep(0.25)


def parse_args() -> argparse.Namespace:
    """Define the CLI contract used by both humans and automation wrappers."""

    parser = argparse.ArgumentParser(
        description="Launch mpv or apply explicit Fedora/LG movie and desktop presets."
    )
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--status", action="store_true", help="Print current TV/display state and exit.")
    action_group.add_argument(
        "--movie-mode",
        action="store_true",
        help="Apply the movie preset and exit without launching mpv.",
    )
    action_group.add_argument(
        "--desktop-mode",
        action="store_true",
        help="Apply the desktop preset and exit without launching mpv.",
    )

    hdr_group = parser.add_mutually_exclusive_group()
    hdr_group.add_argument("--hdr", action="store_true", help="Force HDR movie mode.")
    hdr_group.add_argument("--sdr", action="store_true", help="Force SDR movie mode.")

    parser.add_argument("--dry-run", action="store_true", help="Print intended actions without changing anything.")
    parser.add_argument("--no-tv", action="store_true", help="Skip all TV-side automation.")
    parser.add_argument(
        "--no-tv-ui",
        action="store_true",
        help="Skip screenshot-driven TV UI reads used for status output and exact-state capture.",
    )
    parser.add_argument("--no-display", action="store_true", help="Skip Fedora display changes.")
    parser.add_argument(
        "--force-60hz",
        action="store_true",
        help="Temporarily switch the Fedora output to 4K60 for movie mode or playback.",
    )
    parser.add_argument(
        "--restore-saved-state",
        action="store_true",
        help="After playback, restore the exact pre-playback state instead of the configured desktop preset.",
    )
    parser.add_argument(
        "--debug-log",
        help="Write timestamped JSONL playback snapshots to this file.",
    )
    parser.add_argument(
        "--debug-cue-seconds",
        type=float,
        default=8.0,
        help="Seconds after mpv launch to emit the debug picture cue. Default: 8.0",
    )

    parser.add_argument("--tv-ip", default=DEFAULT_TV_IP, help=f"TV IP address. Default: {DEFAULT_TV_IP}")
    parser.add_argument(
        "--tv-input",
        default=DEFAULT_TV_INPUT,
        help=f"TV input id for Fedora. Default: {DEFAULT_TV_INPUT}",
    )
    parser.add_argument(
        "--display-output",
        default=DEFAULT_DISPLAY_OUTPUT,
        help=f"KScreen output name. Default: {DEFAULT_DISPLAY_OUTPUT}",
    )
    parser.add_argument(
        "--movie-label",
        default=DEFAULT_MOVIE_LABEL,
        help=f"Non-PC input label to use during playback. Default: {DEFAULT_MOVIE_LABEL}",
    )
    parser.add_argument(
        "--movie-icon",
        default=DEFAULT_MOVIE_ICON,
        help=f"Non-PC input icon to use during playback. Default: {DEFAULT_MOVIE_ICON}",
    )
    parser.add_argument(
        "--sdr-picture-mode",
        default=DEFAULT_SDR_PICTURE_MODE,
        help=f"TV picture mode for SDR playback. Default: {DEFAULT_SDR_PICTURE_MODE}",
    )
    parser.add_argument(
        "--hdr-picture-mode",
        default=DEFAULT_HDR_PICTURE_MODE,
        help=f"TV picture mode for HDR playback. Default: {DEFAULT_HDR_PICTURE_MODE}",
    )
    parser.add_argument(
        "--tru-motion",
        default=DEFAULT_TRUMOTION,
        help=f"TV TruMotion mode for playback. Default: {DEFAULT_TRUMOTION}",
    )
    parser.add_argument(
        "--desktop-label",
        default=DEFAULT_DESKTOP_LABEL,
        help=f"Input label for desktop mode. Default: {DEFAULT_DESKTOP_LABEL}",
    )
    parser.add_argument(
        "--desktop-icon",
        default=DEFAULT_DESKTOP_ICON,
        help=f"Input icon for desktop mode. Default: {DEFAULT_DESKTOP_ICON}",
    )
    parser.add_argument(
        "--desktop-picture-mode",
        default=DEFAULT_DESKTOP_PICTURE_MODE,
        help=(
            "TV picture mode for desktop mode. "
            f"Default: {DEFAULT_DESKTOP_PICTURE_MODE} (isf Expert Bright space/daytime)"
        ),
    )
    parser.add_argument(
        "mpv_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed through to mpv. Use -- before the file path if needed.",
    )
    return parser.parse_args()


async def print_status(args: argparse.Namespace) -> int:
    """Print current display state and optional TV state as JSON."""

    output = load_display_output(args.display_output)
    print(
        json.dumps(
            {
                "display": {
                    "name": output["name"],
                    "currentModeId": output["currentModeId"],
                    "hdr": output["hdr"],
                    "wcg": output["wcg"],
                    "vrrPolicy": output.get("vrrPolicy"),
                }
            },
            indent=2,
        )
    )
    if args.no_tv:
        return 0

    async with TvController(args.tv_ip, args.tv_input) as tv:
        if args.no_tv_ui:
            print(json.dumps({"tv": await tv.capture(include_ui_state=False)}, indent=2))
        else:
            print(json.dumps({"tv": await tv.status()}, indent=2))
    return 0


async def apply_desktop_preset(
    args: argparse.Namespace,
    display: DisplayController | None,
    tv: TvController | None,
) -> None:
    """Apply the configured desktop preset to whichever sides are enabled."""

    if display is not None:
        display.apply_desktop_state(dry_run=args.dry_run)
    if tv is not None:
        await tv.apply_profile(
            build_desktop_profile(
                desktop_label=args.desktop_label,
                desktop_icon=args.desktop_icon,
                desktop_picture_mode=args.desktop_picture_mode,
            ),
            dry_run=args.dry_run,
        )


async def run_cleanup_step(operation: Callable[[], Awaitable[None]], description: str) -> bool:
    """Finish cleanup work even if the surrounding task was cancelled.

    Python 3.14 surfaces ``asyncio.CancelledError`` as a ``BaseException``.
    During ``asyncio.run()`` shutdown that means the task can enter ``finally``
    with pending cancellation and every awaited cleanup step will abort
    immediately unless the cancellation is deferred first.
    """

    task = asyncio.current_task()
    saw_cancellation = False

    while True:
        if task is not None:
            while task.cancelling():
                task.uncancel()
                saw_cancellation = True

        try:
            await operation()
            return saw_cancellation
        except asyncio.CancelledError:
            saw_cancellation = True
            log(f"{description} interrupted by cancellation, retrying cleanup.")


async def async_main() -> int:
    """Run the main playback or preset-switching flow."""

    args = parse_args()
    mpv_args = normalize_mpv_args(args.mpv_args)
    debug_log_path = Path(args.debug_log).expanduser() if args.debug_log else None

    if debug_log_path is not None:
        log(f"Debug log: {debug_log_path}")
        append_debug_event(
            debug_log_path,
            "start",
            {
                "argv": mpv_args,
                "tv_ip": args.tv_ip,
                "tv_input": args.tv_input,
                "display_output": args.display_output,
            },
        )

    if args.status:
        return await print_status(args)

    apply_only_mode = args.movie_mode or args.desktop_mode
    if not apply_only_mode and not mpv_args:
        raise SystemExit("No mpv arguments provided.")

    display: DisplayController | None = None
    tv: TvController | None = None
    mpv_returncode = 0
    should_cleanup_after_run = not apply_only_mode
    include_tv_ui_state = not args.no_tv_ui and not args.no_tv
    restore_saved_state = args.restore_saved_state
    cleanup_interrupted = False

    want_hdr = False
    target_refresh: float | None = None
    if args.desktop_mode:
        log(
            "Applying desktop preset: non-HDR display, "
            f"{args.desktop_picture_mode} on the TV, 4:4:4 on, Game Optimizer on, VRR on, ALLM on."
        )
    else:
        want_hdr, hdr_reason = choose_hdr_mode(
            force_hdr=args.hdr,
            force_sdr=args.sdr,
            mpv_args=mpv_args,
        )
        log(f"HDR decision: {'HDR' if want_hdr else 'SDR'} ({hdr_reason})")
        if not args.force_60hz:
            target_refresh, refresh_reason = choose_display_refresh_rate(mpv_args)
            if target_refresh is not None:
                log(f"Refresh decision: {target_refresh:.3f} Hz ({refresh_reason})")
            else:
                log(f"Refresh decision: unchanged ({refresh_reason})")

    try:
        if not args.no_display:
            try:
                display = DisplayController(args.display_output)
                if not args.desktop_mode:
                    display.capture()
                if args.desktop_mode:
                    display.apply_desktop_state(dry_run=args.dry_run)
                else:
                    display.apply_movie_state(
                        enable_hdr=want_hdr,
                        force_60hz=args.force_60hz,
                        target_refresh=target_refresh,
                        dry_run=args.dry_run,
                    )
            except Exception as err:
                log(f"Display automation failed, continuing without it: {err}")
                display = None

        if not args.no_tv:
            try:
                tv = TvController(args.tv_ip, args.tv_input)
                await tv.__aenter__()
                if restore_saved_state:
                    await tv.capture(include_ui_state=include_tv_ui_state and not args.dry_run)

                if args.desktop_mode:
                    await apply_desktop_preset(
                        args,
                        None,
                        tv,
                    )
                else:
                    await tv.apply_profile(
                        build_movie_profile(
                            want_hdr=want_hdr,
                            movie_label=args.movie_label,
                            movie_icon=args.movie_icon,
                            sdr_picture_mode=args.sdr_picture_mode,
                            hdr_picture_mode=args.hdr_picture_mode,
                            tru_motion=args.tru_motion,
                        ),
                        dry_run=args.dry_run,
                    )
            except Exception as err:
                log(f"TV automation failed, continuing without it: {err}")
                if tv is not None:
                    try:
                        await tv.__aexit__(None, None, None)
                    except Exception:
                        pass
                tv = None

        append_debug_event(
            debug_log_path,
            "after_apply",
            await snapshot_runtime_state(args, tv),
        )

        if args.movie_mode or args.desktop_mode:
            return 0

        if args.dry_run:
            log(f"Would run: mpv {' '.join(mpv_args)}")
            return 0

        mpv_returncode = await run_mpv_with_debug(
            mpv_args,
            debug_log_path=debug_log_path,
            debug_cue_seconds=args.debug_cue_seconds,
            args=args,
            tv=tv,
        )
    finally:
        if tv is not None:
            try:
                if should_cleanup_after_run:
                    if restore_saved_state:
                        cleanup_interrupted = await run_cleanup_step(
                            lambda: tv.restore(dry_run=args.dry_run),
                            "TV restore",
                        ) or cleanup_interrupted
                    else:
                        cleanup_interrupted = await run_cleanup_step(
                            lambda: apply_desktop_preset(args, None, tv),
                            "TV desktop-preset restore",
                        ) or cleanup_interrupted
            except Exception as err:
                log(f"TV restore failed: {err}")
            append_debug_event(
                debug_log_path,
                "after_tv_restore",
                await snapshot_runtime_state(args, tv),
            )
            try:
                cleanup_interrupted = await run_cleanup_step(
                    lambda: tv.__aexit__(None, None, None),
                    "TV disconnect",
                ) or cleanup_interrupted
            except Exception as err:
                log(f"TV disconnect failed: {err}")

        if display is not None:
            try:
                if should_cleanup_after_run:
                    if restore_saved_state:
                        display.restore(dry_run=args.dry_run)
                    else:
                        display.apply_desktop_state(dry_run=args.dry_run)
            except Exception as err:
                log(f"Display restore failed: {err}")

        append_debug_event(
            debug_log_path,
            "post_restore",
            await snapshot_runtime_state(args, None),
        )

    if cleanup_interrupted:
        return 130
    return mpv_returncode


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
