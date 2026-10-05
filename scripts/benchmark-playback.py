#!/usr/bin/env python3
"""Measure wrapper startup and mpv playback readiness with a short muted run.

Run with the project interpreter. The normal TV/display cleanup still runs.
All per-run IPC/log artifacts live in an automatically removed temp directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import tempfile
import time
from pathlib import Path


async def benchmark(media: str, seconds: float = 4.0) -> int:
    with tempfile.TemporaryDirectory(prefix="lg-tv-benchmark-") as temp_dir:
        socket = Path(temp_dir) / "mpv.sock"
        started = time.monotonic()
        measurements: dict[str, float | int] = {}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys; from lg_tv_automation.cli.play import main; "
            "options = sys.argv[2:]; sys.argv = sys.argv[:2]; main(mpv_options=options)",
            media, f"--length={seconds}", "--mute=yes",
            "--input-ipc-server=" + str(socket),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )

        async def watch_stderr() -> None:
            assert proc.stderr is not None
            async for line in proc.stderr:
                message = line.decode(errors="replace").rstrip()
                elapsed = time.monotonic() - started
                if "Launching mpv:" in message or "starting mpv." in message:
                    measurements["mpv_launch_seconds"] = elapsed
                print(f"{elapsed:6.2f}s {message}", file=sys.stderr)

        async def watch_playback() -> None:
            while proc.returncode is None:
                try:
                    reader, writer = await asyncio.open_unix_connection(str(socket))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    await asyncio.sleep(0.01)
            else:
                return
            try:
                for index, name in enumerate(("frame-drop-count", "decoder-frame-drop-count", "mistimed-frame-count")):
                    writer.write((json.dumps({"command": ["observe_property", index, name]}) + "\n").encode())
                await writer.drain()
                while proc.returncode is None:
                    line = await reader.readline()
                    if not line:
                        break
                    event = json.loads(line)
                    if event.get("event") == "playback-restart":
                        measurements.setdefault("playback_ready_seconds", time.monotonic() - started)
                    elif event.get("event") == "property-change" and isinstance(event.get("data"), (int, float)):
                        name = event["name"].replace("-", "_")
                        measurements[name] = max(measurements.get(name, 0), event["data"])
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

        stderr_task = asyncio.create_task(watch_stderr())
        playback_task = asyncio.create_task(watch_playback())
        try:
            await proc.wait()
            await stderr_task
            await playback_task
        finally:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
            for task in (stderr_task, playback_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stderr_task, playback_task, return_exceptions=True)
        measurements["total_seconds"] = time.monotonic() - started
        print(json.dumps({"exit_code": proc.returncode, **measurements}, indent=2))
        return proc.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=4.0, help="Playback duration; default 4 seconds.")
    parser.add_argument("media")
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be positive")
    raise SystemExit(asyncio.run(benchmark(args.media, args.seconds)))


if __name__ == "__main__":
    main()
