"""Persistent, screenshot-first remote transport for an interactive agent.

This module deliberately makes no decisions about menus, focus or toggle state.
One JSON request performs at most one action, then returns a fresh screenshot.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
from pathlib import Path
import signal
import ssl
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from PIL import Image

from .config import require_tv_ip
from .lifecycle import run_cleanup_step
from .tv import TvController
from .session import session_lock

BUTTONS = frozenset({"UP", "DOWN", "LEFT", "RIGHT", "ENTER", "BACK", "EXIT", "HOME", "MENU", "QMENU", "ADVANCE_SETTING"})
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def validate_request(request: object) -> dict:
    if not isinstance(request, dict):
        raise ValueError("Request must be a JSON object.")
    op = request.get("op")
    fields = {
        "observe": {"op", "settle"},
        "button": {"op", "key", "settle"},
        "launch": {"op", "app", "settle"},
        "quit": {"op"},
    }
    if not isinstance(op, str) or op not in fields:
        raise ValueError("op must be observe, button, launch or quit.")
    if set(request) - fields[op]:
        raise ValueError("Unknown request fields.")
    if op == "button" and (not isinstance(request.get("key"), str) or request["key"] not in BUTTONS):
        raise ValueError(f"key must be one of {', '.join(sorted(BUTTONS))}.")
    if op == "launch":
        app = request.get("app")
        if not isinstance(app, str) or not app or len(app) > 160 or any(c.isspace() for c in app):
            raise ValueError("app must be a non-empty app id with no whitespace.")
    settle = request.get("settle", 0.35 if op == "button" else 0.8 if op == "launch" else 0)
    if isinstance(settle, bool) or not isinstance(settle, (int, float)) or not math.isfinite(settle) or not 0 <= settle <= 3:
        raise ValueError("settle must be between 0 and 3 seconds.")
    return {**request, "settle": settle}


def download_image(uri: str, destination: Path, tv_ip: str) -> tuple[int, int]:
    """Only fetch the paired TV's image, preserving its original resolution."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme not in {"http", "https"} or parsed.hostname != tv_ip or parsed.username or parsed.password:
        raise RuntimeError("Screenshot URL is not hosted by the configured TV.")
    # The TV uses a self-signed certificate. Reject redirects to other hosts too.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError("Unexpected screenshot redirect.")

    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
    deadline = time.monotonic() + 5
    with opener.open(uri, timeout=3) as response:
        chunks = []
        total = 0
        read_chunk = getattr(response, "read1", response.read)
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError("TV screenshot download exceeded its deadline.")
            chunk = read_chunk(min(65536, MAX_IMAGE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise RuntimeError("TV screenshot exceeds the size limit.")
        data = b"".join(chunks)
    with Image.open(io.BytesIO(data)) as shot:
        size = shot.size
        if min(size) < 1 or max(size) > 8192:
            raise RuntimeError("Invalid TV screenshot dimensions.")
        shot.load()
        shot.save(destination, format="PNG")
    destination.chmod(0o600)
    return size


class RemoteSession:
    def __init__(self, tv: TvController, directory: Path):
        self.tv = tv
        self.directory = directory
        self.sequence = 0

    async def observe(self) -> dict:
        assert self.tv.client is not None
        self.sequence += 1
        path = self.directory / f"{self.sequence:04d}.png"
        shot = await self.tv._request_with_timeout(self.tv.client.request("tv/executeOneShot", {}), "capture TV screen")
        width, height = await asyncio.to_thread(download_image, shot["imageUri"], path, self.tv.ip)
        app = await self.tv._request_with_timeout(self.tv.client.get_current_app(), "read active TV app")
        return {"screenshot": str(path), "width": width, "height": height, "current_app": app, "sequence": self.sequence}

    async def execute(self, request: dict) -> dict:
        assert self.tv.client is not None
        applied: bool | str = False
        try:
            if request["op"] in {"button", "launch"}:
                # A transport error does not prove that the TV ignored the action.
                applied = "unknown"
                action = self.tv.client.button(request["key"]) if request["op"] == "button" else self.tv.client.launch_app(request["app"])
                await self.tv._request_with_timeout(action, "perform remote action")
                applied = True
            await asyncio.sleep(request["settle"])
            return {"ok": True, "action_applied": applied, **await self.observe()}
        except Exception as err:
            return {"ok": False, "action_applied": applied, "error": str(err), "next": "Observe before deciding whether to repeat any action."}


async def run_session(args: argparse.Namespace, reader: asyncio.StreamReader) -> int:
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state").expanduser() / "lg-tv-automation/remote"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix="session-", dir=base))
    tv = TvController(require_tv_ip(), args.input)
    try:
        await tv.__aenter__()
        session = RemoteSession(tv, directory)
        emit({"event": "ready", "ok": True, "artifact_dir": str(directory), "idle_timeout": args.idle_timeout})
        emit(await session.execute(validate_request({"op": "observe"})))
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=args.idle_timeout)
            except TimeoutError:
                emit({"event": "idle_timeout", "ok": False})
                break
            if not line:
                break
            try:
                request = validate_request(json.loads(line))
            except (ValueError, TypeError) as err:
                emit({"ok": False, "action_applied": False, "error": str(err)})
                continue
            if request["op"] == "quit":
                break
            emit(await session.execute(request))
    finally:
        async def cleanup() -> None:
            try:
                if tv.client is not None:
                    await tv._request_with_timeout(tv.client.button("EXIT"), "close TV overlay")
            finally:
                await tv.__aexit__(None, None, None)
        await run_cleanup_step(cleanup, "remote session cleanup", timeout=20)
    emit({"event": "closed", "ok": True, "returned_to": tv.input_app_id})
    return 0


async def async_main(args: argparse.Namespace) -> int:
    reader = asyncio.StreamReader(limit=8192)
    protocol = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    transport = None
    try:
        transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)
        with session_lock():
            return await run_session(args, reader)
    finally:
        if transport is not None:
            transport.close()
        loop.remove_signal_handler(signal.SIGTERM)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="HDMI_1", choices=[f"HDMI_{i}" for i in range(1, 5)], help="Input to return to on exit.")
    parser.add_argument("--idle-timeout", type=float, default=120, help="Idle seconds before cleanup (10–600).")
    args = parser.parse_args()
    if not math.isfinite(args.idle_timeout) or not 10 <= args.idle_timeout <= 600:
        parser.error("--idle-timeout must be between 10 and 600 seconds")
    try:
        raise SystemExit(asyncio.run(async_main(args)))
    except (KeyboardInterrupt, asyncio.CancelledError):
        raise SystemExit(130)
    except Exception as err:
        emit({"event": "fatal", "ok": False, "error": str(err)})
        raise SystemExit(1) from err


if __name__ == "__main__":
    main()
