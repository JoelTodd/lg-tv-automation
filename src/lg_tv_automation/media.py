"""Helpers for choosing SDR vs HDR playback paths."""

from __future__ import annotations

import json
from pathlib import Path

from .process import run_command


def normalize_mpv_args(raw_args: list[str]) -> list[str]:
    """Strip the leading ``--`` used to terminate launcher argument parsing."""

    if raw_args and raw_args[0] == "--":
        return raw_args[1:]
    return raw_args


def find_primary_media_path(mpv_args: list[str]) -> Path | None:
    """Pick the first existing non-option path from the mpv argument list."""

    for arg in mpv_args:
        if arg == "--" or arg.startswith("-"):
            continue
        candidate = Path(arg).expanduser()
        if candidate.exists():
            return candidate
    return None


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


def choose_hdr_mode(*, force_hdr: bool, force_sdr: bool, mpv_args: list[str]) -> tuple[bool, str]:
    """Decide whether the playback path should use HDR behavior."""

    if force_hdr:
        return True, "forced by --hdr"
    if force_sdr:
        return False, "forced by --sdr"

    media_path = find_primary_media_path(mpv_args)
    if media_path is None:
        return False, "no local media file found, defaulting to SDR"

    is_hdr, reason = detect_hdr_from_file(media_path)
    return is_hdr, f"{media_path}: {reason}"
