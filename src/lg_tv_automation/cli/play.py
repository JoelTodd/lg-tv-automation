"""Primary CLI for playback and preset switching."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess

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
from ..media import choose_hdr_mode, normalize_mpv_args
from ..profiles import build_desktop_profile, build_movie_profile
from ..tv import TvController


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
        help="Skip the slower screenshot-driven fallback for HDMI and Game Optimizer state.",
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
    *,
    allow_tv_ui_fallback: bool,
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
            allow_ui_fallback=allow_tv_ui_fallback,
            dry_run=args.dry_run,
        )


async def async_main() -> int:
    """Run the main playback or preset-switching flow."""

    args = parse_args()
    mpv_args = normalize_mpv_args(args.mpv_args)

    if args.status:
        return await print_status(args)

    apply_only_mode = args.movie_mode or args.desktop_mode
    if not apply_only_mode and not mpv_args:
        raise SystemExit("No mpv arguments provided.")

    display: DisplayController | None = None
    tv: TvController | None = None
    mpv_returncode = 0
    should_cleanup_after_run = not apply_only_mode
    allow_tv_ui_fallback = not args.no_tv_ui and not args.no_tv
    restore_saved_state = args.restore_saved_state

    want_hdr = False
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

    try:
        if not args.no_display:
            try:
                display = DisplayController(args.display_output)
                if args.force_60hz or restore_saved_state:
                    display.capture()
                if args.desktop_mode:
                    display.apply_desktop_state(dry_run=args.dry_run)
                else:
                    display.apply_movie_state(
                        enable_hdr=want_hdr,
                        force_60hz=args.force_60hz,
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
                    captured_state = await tv.capture(include_ui_state=allow_tv_ui_fallback and not args.dry_run)
                    allow_tv_ui_fallback = allow_tv_ui_fallback and not args.dry_run and {
                        "passthrough_444",
                        "game_optimizer_master",
                        "vrr",
                        "allm",
                    }.issubset(captured_state)

                if args.desktop_mode:
                    await apply_desktop_preset(
                        args,
                        None,
                        tv,
                        allow_tv_ui_fallback=allow_tv_ui_fallback,
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
                        allow_ui_fallback=allow_tv_ui_fallback,
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

        if args.movie_mode or args.desktop_mode:
            return 0

        if args.dry_run:
            log(f"Would run: mpv {' '.join(mpv_args)}")
            return 0

        log(f"Launching mpv: {' '.join(mpv_args)}")
        proc = subprocess.run(["mpv", *mpv_args], text=True)
        mpv_returncode = proc.returncode
    finally:
        if tv is not None:
            try:
                if should_cleanup_after_run:
                    if restore_saved_state:
                        await tv.restore(dry_run=args.dry_run, allow_ui_fallback=allow_tv_ui_fallback)
                    else:
                        await apply_desktop_preset(
                            args,
                            None,
                            tv,
                            allow_tv_ui_fallback=allow_tv_ui_fallback,
                        )
            except Exception as err:
                log(f"TV restore failed: {err}")
            try:
                await tv.__aexit__(None, None, None)
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

    return mpv_returncode


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))
