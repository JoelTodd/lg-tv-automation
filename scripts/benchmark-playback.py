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
from typing import Any



COUNTERS = ("frame-drop-count", "decoder-frame-drop-count", "mistimed-frame-count")
VIDEO_PROPERTIES = ("hwdec-current", "video-params/w", "video-params/h", "video-params/gamma", "video-params/primaries")


def record_event(measurements: dict[str, Any], event: dict, elapsed: float) -> None:
    if event.get("event") == "playback-restart":
        measurements.setdefault("playback_ready_seconds", elapsed)
    elif event.get("event") == "property-change":
        name, value = event.get("name"), event.get("data")
        if name in COUNTERS and isinstance(value, (int, float)) and not isinstance(value, bool):
            key = name.replace("-", "_")
            measurements[key] = max(measurements.get(key, 0), value)
        elif name in VIDEO_PROPERTIES and value is not None:
            measurements[name.replace("-", "_").replace("/", "_")] = value


def measurement_error(measurements: dict[str, Any]) -> str | None:
    if "playback_ready_seconds" not in measurements:
        return "No playback readiness event was observed."
    missing = [name for name in COUNTERS[:2] if name.replace("-", "_") not in measurements]
    if missing:
        return "Playback drop counters were unavailable: " + ", ".join(missing)
    return None


async def benchmark(media: str, seconds: float = 4.0) -> int:
    with tempfile.TemporaryDirectory(prefix="lg-tv-benchmark-") as temp_dir:
        socket = Path(temp_dir) / "mpv.sock"
        player_log = Path(temp_dir) / "mpv.log"
        started = time.monotonic()
        measurements: dict[str, Any] = {}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys; from lg_tv_automation.cli.play import main; "
            "options = sys.argv[2:]; sys.argv = sys.argv[:2]; main(mpv_options=options)",
            media, f"--length={seconds}", "--mute=yes",
            "--input-ipc-server=" + str(socket),
            "--log-file=" + str(player_log),
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
                for index, name in enumerate((*COUNTERS, *VIDEO_PROPERTIES)):
                    writer.write((json.dumps({"command": ["observe_property", index, name]}) + "\n").encode())
                await writer.drain()
                while proc.returncode is None:
                    line = await reader.readline()
                    if not line:
                        break
                    event = json.loads(line)
                    record_event(measurements, event, time.monotonic() - started)
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
        error = measurement_error(measurements)
        result = proc.returncode or (1 if error else 0)
        if error:
            measurements["measurement_error"] = error
        if result and player_log.exists():
            print("Last mpv diagnostics:", file=sys.stderr)
            print("\n".join(player_log.read_text(errors="replace").splitlines()[-30:]), file=sys.stderr)
        print(json.dumps({"exit_code": result, "wrapper_exit_code": proc.returncode, **measurements}, indent=2))
        return result


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
