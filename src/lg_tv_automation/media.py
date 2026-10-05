"""Resolve one local movie and detect its HDR metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .process import run_command

MEDIA_EXTENSIONS = {".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".mxf", ".ts", ".webm", ".wmv"}


def resolve_movie_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_dir():
        videos = [child for child in path.iterdir() if child.is_file() and child.suffix.lower() in MEDIA_EXTENSIONS]
        if not videos:
            raise RuntimeError(f"No video file found in {path}.")
        path = max(videos, key=lambda child: (child.stat().st_size, child.name.lower()))
    if not path.is_file():
        raise RuntimeError(f"Movie file not found: {path}")
    return path


def normalize_mpv_args(arguments: list[str]) -> list[str]:
    """Do not let Wayland classify movie playback as game content."""
    wayland = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    if arguments and wayland and not any(arg.startswith("--wayland-content-type") for arg in arguments):
        return ["--wayland-content-type=none", *arguments]
    return arguments[:]


def probe_media(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(run_command([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=color_transfer,color_primaries,pix_fmt:stream_side_data=side_data_type",
            "-of", "json", str(path),
        ], timeout=8).stdout)
        streams = data.get("streams") or []
        return streams[0] if streams else {"probe_error": "no video stream found"}
    except Exception as err:
        return {"probe_error": f"ffprobe failed: {err}"}


def detect_hdr_from_file(path: Path, *, stream: dict[str, Any] | None = None) -> tuple[bool, str]:
    stream = probe_media(path) if stream is None else stream
    if "probe_error" in stream:
        return False, stream["probe_error"]
    transfer = (stream.get("color_transfer") or "").lower()
    primaries = (stream.get("color_primaries") or "").lower()
    pix_fmt = (stream.get("pix_fmt") or "").lower()
    side_types = {str(item.get("side_data_type", "")).lower() for item in stream.get("side_data_list") or []}
    if transfer in {"smpte2084", "arib-std-b67"}:
        return True, f"transfer={transfer}"
    if {"mastering display metadata", "content light level metadata"} & side_types:
        return True, "HDR side-data present"
    if primaries == "bt2020" and any(token in pix_fmt for token in ("10", "12", "16")):
        return True, f"bt2020 + {pix_fmt}"
    return False, f"transfer={transfer or 'unknown'} primaries={primaries or 'unknown'} pix_fmt={pix_fmt or 'unknown'}"
