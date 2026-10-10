"""Observe mpv without making playback depend on telemetry availability."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import pty

from .tui import PROPERTIES, PlaybackUI


class PlayerOutput:
    """Capture output through a PTY so mpv retains its terminal key bindings."""

    async def __aenter__(self) -> PlayerOutput:
        master, self.fd = pty.openpty()
        self.pipe = os.fdopen(master, "rb", buffering=0)
        self.reader = asyncio.StreamReader()
        try:
            self.transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(self.reader), self.pipe,
            )
        except BaseException:
            self.close_writer()
            self.pipe.close()
            raise
        return self

    def close_writer(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    async def __aexit__(self, *_exc) -> None:
        self.close_writer()
        self.transport.close()
        self.pipe.close()


async def observe_player(socket: str, ui: PlaybackUI) -> None:
    writer = None
    try:
        async with asyncio.timeout(5):
            while True:
                try:
                    reader, writer = await asyncio.open_unix_connection(socket)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    await asyncio.sleep(.05)
            for index, name in enumerate(PROPERTIES):
                writer.write((json.dumps({"command": ["observe_property", index, name]}) + "\n").encode())
            await writer.drain()
        ui.telemetry = "Live"
        while line := await reader.readline():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("event") == "property-change":
                ui.property_change(event)
            elif event.get("event") == "end-file":
                ui.end_reason = str(event.get("reason", "unknown"))
                if event.get("error"):
                    ui.notice(f"Player error: {event['error']}")
    except (OSError, TimeoutError, ValueError) as err:
        ui.notice(f"Player telemetry unavailable: {err or type(err).__name__}")
    finally:
        ui.telemetry = "Unavailable"
        ui.values.clear()
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (OSError, TimeoutError):
                pass


async def read_diagnostics(reader: asyncio.StreamReader, ui: PlaybackUI) -> None:
    # Read bounded chunks so a malformed/long player line cannot block mpv.
    pending = b""
    while True:
        try:
            chunk = await reader.read(4096)
        except OSError as err:
            if err.errno != errno.EIO:
                ui.notice(f"Player diagnostics unavailable: {err}")
            break  # PTY EOF is reported as EIO on Linux.
        if not chunk:
            break
        pending += chunk
        lines = pending.replace(b"\r", b"\n").split(b"\n")
        pending = lines.pop()
        for line in lines:
            ui.notice(line.decode(errors="replace"))
        if len(pending) >= 4096:
            ui.notice(pending.decode(errors="replace"))
            pending = b""
    if pending:
        ui.notice(pending.decode(errors="replace"))
