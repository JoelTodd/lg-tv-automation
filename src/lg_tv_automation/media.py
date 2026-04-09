"""Helpers for choosing SDR vs HDR playback paths."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .process import run_command

MEDIA_EXTENSIONS = {
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".mxf",
    ".ts",
    ".webm",
    ".wmv",
}


def normalize_mpv_args(raw_args: list[str]) -> list[str]:
    """Normalize launcher-passed mpv arguments.

    On Wayland we default mpv's content-type hint to ``none``. During local
    validation on KDE Plasma + NVIDIA this prevented the TV from re-enabling
    Game Optimizer as soon as playback started, while still preserving the
    intended HDR/picture-mode/movie-processing state.
    """

    mpv_args = raw_args[:]
    if raw_args and raw_args[0] == "--":
        mpv_args = raw_args[1:]

    on_wayland = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    has_content_type_override = any(arg.startswith("--wayland-content-type") for arg in mpv_args)
    if on_wayland and not has_content_type_override:
        mpv_args = ["--wayland-content-type=none", *mpv_args]

    return mpv_args


def find_primary_media_path(mpv_args: list[str]) -> Path | None:
    """Pick the first existing non-option path from the mpv argument list."""

    for arg in mpv_args:
        if arg == "--" or arg.startswith("-"):
            continue
        candidate = Path(arg).expanduser()
        if candidate.exists():
            return candidate
    return None


def resolve_probe_path(path: Path) -> Path:
    """Resolve a directory input to the most likely primary media file for probing."""

    if not path.is_dir():
        return path

    media_files = [
        child
        for child in path.iterdir()
        if child.is_file() and child.suffix.lower() in MEDIA_EXTENSIONS
    ]
    if not media_files:
        return path

    return max(media_files, key=lambda candidate: (candidate.stat().st_size, candidate.name.lower()))


def detect_hdr_from_file(path: Path) -> tuple[bool, str]:
    """Best-effort local HDR detection using ``ffprobe`` metadata."""

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=color_transfer,color_primaries,color_space,pix_fmt,bits_per_raw_sample:stream_side_data=side_data_type",
        "-of",
        "json",
        str(path),
    ]
    try:
        data = json.loads(run_command(cmd).stdout)
    except Exception as exc:
        return False, f"ffprobe failed: {exc}"

    streams = data.get("streams") or []
    if not streams:
        return False, "no video stream found"

    stream = streams[0]
    transfer = (stream.get("color_transfer") or "").lower()
    primaries = (stream.get("color_primaries") or "").lower()
    pix_fmt = (stream.get("pix_fmt") or "").lower()
    side_data = stream.get("side_data_list") or []
    side_types = {str(item.get("side_data_type", "")).lower() for item in side_data}

    if transfer in {"smpte2084", "arib-std-b67"}:
        return True, f"transfer={transfer}"
    if "mastering display metadata" in side_types or "content light level metadata" in side_types:
        return True, "HDR side-data present"
    if primaries == "bt2020" and any(token in pix_fmt for token in ("10", "12", "16")):
        return True, f"bt2020 + {pix_fmt}"
    return False, (
        f"transfer={transfer or 'unknown'} "
        f"primaries={primaries or 'unknown'} "
        f"pix_fmt={pix_fmt or 'unknown'}"
    )


def _parse_frame_rate(value: str | None) -> float | None:
    """Parse an ffprobe frame-rate string such as ``24000/1001``."""

    if not value:
        return None

    try:
        numerator_text, denominator_text = value.split("/", 1)
        numerator = float(numerator_text)
        denominator = float(denominator_text)
        if denominator == 0:
            return None
        frame_rate = numerator / denominator
    except Exception:
        return None

    if frame_rate <= 0:
        return None
    return frame_rate


def detect_video_frame_rate(path: Path) -> tuple[float | None, str]:
    """Best-effort local frame-rate detection using ``ffprobe`` metadata."""

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    try:
        data = json.loads(run_command(cmd).stdout)
    except Exception as exc:
        return None, f"ffprobe failed: {exc}"

    streams = data.get("streams") or []
    if not streams:
        return None, "no video stream found"

    stream = streams[0]
    average = _parse_frame_rate(stream.get("avg_frame_rate"))
    real = _parse_frame_rate(stream.get("r_frame_rate"))
    frame_rate = average or real
    if frame_rate is None:
        return None, "no usable frame-rate metadata found"

    return frame_rate, f"fps={frame_rate:.3f}"


def choose_display_refresh_rate(mpv_args: list[str]) -> tuple[float | None, str]:
    """Choose a playback-friendly display refresh based on the source video."""

    media_path = find_primary_media_path(mpv_args)
    if media_path is None:
        return None, "no local media file found, leaving refresh unchanged"

    probe_path = resolve_probe_path(media_path)
    frame_rate, reason = detect_video_frame_rate(probe_path)
    if frame_rate is None:
        if probe_path != media_path:
            return None, f"{media_path} -> {probe_path}: {reason}"
        return None, f"{probe_path}: {reason}"

    # Keep the desktop refresh when the source rate is unusually high.
    if frame_rate > 61.0:
        return None, f"{probe_path}: source fps {frame_rate:.3f} is too high to auto-match safely"

    if probe_path != media_path:
        return frame_rate, f"{media_path} -> {probe_path}: {reason}"
    return frame_rate, f"{probe_path}: {reason}"


def choose_hdr_mode(*, force_hdr: bool, force_sdr: bool, mpv_args: list[str]) -> tuple[bool, str]:
    """Decide whether the playback path should use HDR behavior."""

    if force_hdr:
        return True, "forced by --hdr"
    if force_sdr:
        return False, "forced by --sdr"

    media_path = find_primary_media_path(mpv_args)
    if media_path is None:
        return False, "no local media file found, defaulting to SDR"

    probe_path = resolve_probe_path(media_path)
    is_hdr, reason = detect_hdr_from_file(probe_path)
    if probe_path != media_path:
        return is_hdr, f"{media_path} -> {probe_path}: {reason}"
    return is_hdr, f"{probe_path}: {reason}"
