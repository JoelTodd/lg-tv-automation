"""Optional, read-only monitoring of this mpv process's audio stream."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import cmath
import json
import math
import shutil
import struct
import time

from .lifecycle import run_cleanup_step
from .tui import PlaybackUI

RATE = 24000
SIZE = 2048
HOP = 512  # Overlap windows for ~47 updates/s without losing bass resolution.
BANDS = 24
EDGES = [round(50 * (10000 / 50) ** (index / BANDS) * SIZE / RATE) for index in range(BANDS + 1)]
WINDOW = [0.5 - 0.5 * math.cos(2 * math.pi * index / (SIZE - 1)) for index in range(SIZE)]
REVERSED = [int(f"{index:011b}"[::-1], 2) for index in range(SIZE)]
TWIDDLES = {size: [cmath.exp(-2j * math.pi * index / size) for index in range(size // 2)]
            for size in (2 ** power for power in range(1, 12))}


def analyse(pcm: bytes) -> list[float]:
    """Hann-windowed FFT, 24 logarithmic bands, fixed -70..0 dBFS scale."""
    samples = struct.unpack(f"<{SIZE}h", pcm)
    values = [complex(samples[index] / 32768 * WINDOW[index]) for index in REVERSED]
    for size, twiddles in TWIDDLES.items():
        half = size // 2
        for start in range(0, SIZE, size):
            for index, twiddle in enumerate(twiddles):
                even, odd = values[start + index], values[start + index + half] * twiddle
                values[start + index], values[start + index + half] = even + odd, even - odd
    bands = []
    for low, high in zip(EDGES, EDGES[1:]):
        amplitude = max(abs(value) for value in values[max(1, low):max(low + 1, high)]) * 4 / SIZE
        db = 20 * math.log10(max(amplitude, 1e-9))
        bands.append(min(1, max(0, (db + 70) / 70)))
    return bands


@asynccontextmanager
async def child(*args: str):
    # Shield spawn so cancellation cannot strand a recorder or discovery child.
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    ))
    proc = None
    try:
        proc = await asyncio.shield(spawn)
        yield proc
    finally:
        async def stop():
            process = proc if proc is not None else await spawn
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.communicate(), 1)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.communicate()
        await run_cleanup_step(stop, "Stop audio monitor", timeout=3)


async def audio_target(pid: int) -> tuple[int, str] | None:
    async def listing(kind):
        async with child("pactl", "--format=json", "list", kind) as proc:
            data, _ = await asyncio.wait_for(proc.communicate(), 2)
            if proc.returncode:
                return []
            result = json.loads(data)
            return result if isinstance(result, list) else []
    inputs = await listing("sink-inputs")
    stream = next((item for item in inputs if str(item.get("properties", {}).get("application.process.id")) == str(pid)), None)
    if stream is None and inputs:
        # Native PipeWire streams keep the PID on their client, not the node.
        clients = await listing("clients")
        owned = {str(item.get("index")) for item in clients
                 if str(item.get("properties", {}).get("application.process.id")) == str(pid)}
        stream = next((item for item in inputs if str(item.get("client")) in owned), None)
    if stream is None:
        return None
    sinks = await listing("sinks")
    sink = next((item for item in sinks if item.get("index") == stream.get("sink")), None)
    if sink is None:
        return None
    # Explicit sink monitor + stream index: never record a microphone or other apps.
    source = sink.get("monitor_source_name") or sink.get("monitor_source")
    if isinstance(stream.get("index"), int) and isinstance(source, str) and source.endswith(".monitor"):
        return stream["index"], source
    return None


async def capture(target: tuple[int, str], ui: PlaybackUI) -> None:
    index, source = target
    async with child("parec", f"--monitor-stream={index}", f"--device={source}",
                     "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1",
                     "--latency-msec=20", "--process-time-msec=10",
                     "--client-name=lg-tv-spectrum") as proc:
        pcm = await asyncio.wait_for(proc.stdout.readexactly(SIZE * 2), 2)
        while True:
            ui.update_spectrum(analyse(pcm), time.monotonic())
            ui.spectrum_status = "Live"
            following = await asyncio.wait_for(proc.stdout.readexactly(HOP * 2), 2)
            pcm = pcm[HOP * 2:] + following


async def watch_spectrum(pid: int, ui: PlaybackUI) -> None:
    recorder = None
    current = None
    if not shutil.which("pactl") or not shutil.which("parec"):
        ui.spectrum_status = "Audio monitor unavailable"
        return
    try:
        while True:
            try:
                target = await audio_target(pid)
            except (OSError, ValueError, TimeoutError, RuntimeError, AttributeError, TypeError):
                target = None
            if target != current or (recorder is not None and recorder.done()):
                if recorder is not None and recorder.done():
                    ui.spectrum_status = "Audio monitor unavailable"
                if recorder is not None:
                    recorder.cancel()
                    await asyncio.gather(recorder, return_exceptions=True)
                ui.update_spectrum([], time.monotonic())
                current = target
                recorder = asyncio.create_task(capture(target, ui)) if target is not None else None
            if target is None:
                ui.spectrum_status = "No monitorable audio stream"
            await asyncio.sleep(2)
    finally:
        if recorder is not None:
            recorder.cancel()
            await asyncio.gather(recorder, return_exceptions=True)
        ui.update_spectrum([], time.monotonic())
