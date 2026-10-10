"""Live playback dashboard and a durable terminal session report."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime
import math
import os
from pathlib import Path
import sys
import termios
import time

from .console import log_sink
from .tui_render import clip, clock, colour_frame, dashboard, plain, shutter, wrap

COUNTERS = ("frame-drop-count", "decoder-frame-drop-count", "mistimed-frame-count")
PROPERTIES = (
    "time-pos", "duration", "pause", "speed", "video-params/w", "video-params/h",
    "container-fps", "display-fps", "estimated-display-fps", "avsync",
    "paused-for-cache", "seeking", *COUNTERS,
)


@dataclass
class Diagnostic:
    elapsed: float
    level: str
    message: str
    repeats: int = 1


def numeric(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


class PlaybackUI:
    def __init__(self, movie: Path):
        self.stream = sys.stderr
        self.enabled = self.stream.isatty() and os.environ.get("TERM", "dumb") != "dumb"
        self.colour = "NO_COLOR" not in os.environ
        self.movie = movie
        self.phase = "Reading media"
        self.started = self.phase_started = time.monotonic()
        self.changed_at = self.started
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.setup_seconds: float | None = None
        self.format: str | None = None
        self.values: dict[str, object] = {}
        self.last_values: dict[str, object] = {}
        self.telemetry = "Connecting"
        self.messages: deque[str] = deque(maxlen=8)
        self.events: deque[Diagnostic] = deque(maxlen=200)
        self.latest_error: Diagnostic | None = None
        self.omitted_events = 0
        self.phase_times: list[tuple[str, float]] = []
        self.counter_totals: dict[str, float] = {}
        self._previous_counters: dict[str, float] = {}
        self.spectrum: list[float] = []
        self.spectrum_updated = 0.0
        self.spectrum_status = "Waiting for audio"
        self.peak_av: float | None = None
        self._last_sample = 0.0
        self._bad_sync_since: float | None = None
        self._last_drop: float | None = None
        self.result: int | None = None
        self.player_exit: int | None = None
        self.end_reason: str | None = None
        self._task: asyncio.Task | None = None
        self._token = None
        self._terminal_state = None
        self._last_frame: list[str] = []
        self._last_size = None

    def set_phase(self, phase: str) -> None:
        now = time.monotonic()
        self.phase_times.append((self.phase, now - self.phase_started))
        self.phase, self.phase_started = phase, now
        self.changed_at = now

    def notice(self, message: str) -> None:
        message = plain(message).strip()[:2000]
        if not message:
            return
        if not self.messages or self.messages[-1] != message:
            self.messages.append(message)
        if self.events and self.events[-1].message == message:
            self.events[-1].repeats += 1
            return
        level = "ERROR" if any(word in message.lower() for word in ("failed", "error", "fatal", "exited with status")) else "WARN"
        if len(self.events) == self.events.maxlen:
            self.omitted_events += 1
        event = Diagnostic(time.monotonic() - self.started, level, message)
        self.events.append(event)
        if level == "ERROR":
            self.latest_error = event

    def property_change(self, event: dict) -> None:
        name = event.get("name")
        if name not in PROPERTIES:
            return
        value = event.get("data")
        if name in ("pause", "seeking", "paused-for-cache") and value != self.values.get(name):
            self.changed_at = time.monotonic()
        self.values[name] = value
        number = numeric(value)
        if number is not None or (name in ("pause", "seeking", "paused-for-cache") and isinstance(value, bool)):
            self.last_values[name] = value
        if name in COUNTERS and number is not None and number >= 0:
            previous = self._previous_counters.get(name, 0)
            delta = number - previous if number >= previous else number
            self.counter_totals[name] = self.counter_totals.get(name, 0) + delta
            self._previous_counters[name] = number
            if delta and name != "mistimed-frame-count":
                self._last_drop = time.monotonic()
        if name == "avsync" and number is not None:
            self.peak_av = max(self.peak_av or 0, abs(number))

    def number(self, name: str) -> float | None:
        return numeric(self.values.get(name))

    def state(self) -> str:
        if self.phase != "Playback":
            return self.phase
        if self.values.get("paused-for-cache"):
            return "Buffering"
        if self.values.get("seeking"):
            return "Seeking"
        pause = self.values.get("pause")
        return "Paused" if pause is True else "Playing" if pause is False else "Playback"

    def sample(self, now: float) -> None:
        if now - self._last_sample < .5:
            return
        self._last_sample = now
        av = self.number("avsync")
        if self.state() == "Playing" and av is not None and abs(av) > .08:
            if self._bad_sync_since is None:
                self._bad_sync_since = now
        else:
            self._bad_sync_since = None

    def audio_levels(self, now: float) -> list[float]:
        if not self.spectrum:
            return []
        if self.state() in ("Paused", "Seeking", "Buffering"):
            decay = max(0, 1 - (now - self.changed_at) / .25)
        else:
            age = now - self.spectrum_updated
            if age > 1:
                self.spectrum_status = "Audio monitor unavailable"
                return []
            decay = min(1, max(0, 1 - (age - .2) / .3))
        return [value * decay for value in self.spectrum]

    def alert_lines(self, now: float, width: int) -> list[str]:
        alerts = []
        if self.phase == "Playback":
            if self.telemetry != "Live":
                alerts.append(f"WARN  Player telemetry {self.telemetry.lower()}")
            if self.values.get("paused-for-cache"):
                alerts.append("WARN  Playback is waiting for media data")
            if self._bad_sync_since is not None and now - self._bad_sync_since >= 2:
                alerts.append("WARN  A/V offset above 80 ms for at least 2s")
            if self._last_drop is not None and now - self._last_drop < 5:
                alerts.append("WARN  Dropped frames in the last 5s")
        def event_lines(event: Diagnostic) -> list[str]:
            repeat = f" (×{event.repeats})" if event.repeats > 1 else ""
            lines = wrap(f"{event.level}  +{clock(event.elapsed)} {event.message}{repeat}", width)
            if len(lines) > 3:
                lines = lines[:3]
                lines[-1] = clip(lines[-1], max(0, width - 1)) + "…"
            return lines
        # Pin the latest error, then live conditions, before older diagnostics.
        lines = event_lines(self.latest_error) if self.latest_error is not None else []
        lines.extend(line for alert in alerts for line in wrap(alert, width))
        for event in reversed(list(self.events)[-4:]):
            if event is not self.latest_error:
                lines.extend(event_lines(event))
        return lines

    def lines(self, width: int, height: int) -> list[str]:
        return dashboard(self, width, height, time.monotonic())

    def draw(self, *, closing: float | None = None) -> None:
        try:
            size = os.get_terminal_size(self.stream.fileno())
            now = time.monotonic()
            self.sample(now)
            rows = dashboard(self, size.columns, max(1, size.lines), now)
            openness = min(1, (now - self.started) / .5) if closing is None else 1 - closing
            if size.columns >= 64 and size.lines >= 22:
                rows = shutter(rows, openness)
            if self.colour and size.columns >= 64 and size.lines >= 22:
                rows = colour_frame(rows)
            # Row diffs avoid flicker and keep idle refreshes inexpensive.
            output = []
            if size != self._last_size:
                output.append("\x1b[2J\x1b[H")
                self._last_frame = []
            for index, row in enumerate(rows):
                if index >= len(self._last_frame) or row != self._last_frame[index]:
                    output.append(f"\x1b[{index + 1};1H\x1b[2K{row}")
            for index in range(len(rows), len(self._last_frame)):
                output.append(f"\x1b[{index + 1};1H\x1b[2K")
            if output:
                self.stream.write("".join(output))
                self.stream.flush()
            self._last_frame, self._last_size = rows, size
        except (OSError, ValueError):
            pass  # Losing the terminal must not interrupt hardware restoration.

    def summary(self, now: float) -> list[str]:
        def number(name):
            return numeric(self.last_values.get(name))
        def duration(seconds):
            return clock(seconds) if seconds >= 60 else f"{seconds:.2f}s"
        result = self.result if self.result is not None else self.player_exit
        outcome = "Interrupted" if result == 130 else "Failed" if result else "Closed"
        rows = ["", *("  " + line for line in wrap(f"{outcome} · {self.movie.name}", 72)), "  " + "─" * 72, ""]
        if number("time-pos") is not None:
            rows.append(f"  Last position: {clock(number('time-pos'))} / {clock(number('duration'))}")
        reason = f" · {plain(self.end_reason)}" if self.end_reason else ""
        player = f" · mpv {self.player_exit}" if self.player_exit is not None and self.player_exit != result else ""
        rows += [f"  Exit: {result if result is not None else 'unknown'}{player}{reason}",
                 f"  Started: {self.started_at.replace('T', ' ')}"]
        phases = [*self.phase_times, (self.phase, now - self.phase_started)]
        restoring = sum(seconds for phase, seconds in phases if phase.startswith(("Restoring", "Disconnecting")))
        playback = sum(seconds for phase, seconds in phases if phase == "Playback")
        setup = sum(seconds for phase, seconds in phases if phase != "Playback" and not phase.startswith(("Restoring", "Disconnecting")))
        rows += ["", "  TIMING", f"    Setup       {duration(setup):>10}",
                 f"    Playback    {duration(playback):>10}", f"    Restore     {duration(restoring):>10}",
                 f"    Total       {duration(now - self.started):>10}"]
        w, h, fps = (number(name) for name in ("video-params/w", "video-params/h", "container-fps"))
        media = [f"{w:g} × {h:g}"] if w and h else []
        if fps:
            media.append(f"{fps:.3f} fps")
        if self.format:
            media.append(self.format)
        rows += ["", "  MEDIA"]
        if media:
            rows.append("    " + " · ".join(media))
        rows.extend("    " + line for line in wrap(f"File: {self.movie}", 70))
        diagnostics = []
        if any(self.counter_totals.values()):
            diagnostics.append("Frame totals: " + "; ".join(
                f"{label} {self.counter_totals[name]:g}" if name in self.counter_totals else f"{label} unavailable"
                for label, name in zip(("render drops", "decoder drops", "mistimed"), COUNTERS)))
        if self.peak_av is not None and self.peak_av > .08:
            diagnostics.append(f"Peak |A/V offset|: {self.peak_av * 1000:.1f} ms")
        events = list(self.events)
        if self.latest_error is not None and self.latest_error not in events:
            events.insert(0, self.latest_error)
        if diagnostics or events or self.omitted_events:
            rows += ["", "  DIAGNOSTICS"]
            for detail in diagnostics:
                rows.extend("    " + line for line in wrap(detail, 70))
            if self.omitted_events:
                rows += [f"    {self.omitted_events} earlier entries omitted; latest 200 follow"]
            for event in events:
                repeats = f" (repeated {event.repeats} times)" if event.repeats > 1 else ""
                rows += ["", f"    +{clock(event.elapsed)}  {event.level}"]
                rows.extend("      " + line for line in wrap(event.message + repeats, 68))
        if result not in (0, None):
            rows += ["", "  STEP TIMINGS"]
            rows.extend(f"    {phase:<38} {duration(seconds):>10}" for phase, seconds in phases)
        rows.append("")
        return rows

    async def _refresh(self) -> None:
        while True:
            started = time.monotonic()
            self.draw()
            await asyncio.sleep(max(0, 1 / 30 - (time.monotonic() - started)))

    async def __aenter__(self) -> PlaybackUI:
        if self.enabled:
            try:
                self._terminal_state = termios.tcgetattr(self.stream.fileno())
            except (OSError, ValueError, termios.error):
                pass
            try:
                self.stream.write("\x1b[?1049h\x1b[?25l")
                self.stream.flush()
            except OSError:
                self.enabled = False
            else:
                self._token = log_sink.set(self.notice)
                self._task = asyncio.create_task(self._refresh())
        return self

    async def __aexit__(self, exc_type, exc, _tb) -> None:
        ended = time.monotonic()
        if self.result is None and exc is not None:
            self.result = 130 if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else 1
            if self.result != 130:
                self.notice(f"Session failed: {exc}")
        try:
            if self._task is not None:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                # Restoration has already finished. The brief closing shutter
                # is skipped on interruption and never delays hardware cleanup.
                if self.result in (0, None) and exc is None:
                    try:
                        for step in range(1, 7):
                            self.draw(closing=step / 6)
                            await asyncio.sleep(.03)
                    except asyncio.CancelledError:
                        self.result = 130
                        raise
        finally:
            if self._token is not None:
                log_sink.reset(self._token)
            if self._terminal_state is not None:
                try:
                    termios.tcsetattr(self.stream.fileno(), termios.TCSANOW, self._terminal_state)
                except (OSError, ValueError, termios.error):
                    pass
            try:
                if self._task is not None:
                    self.stream.write("\x1b[0m\x1b[?25h\x1b[?1049l")
                if self.enabled or self.result is not None:
                    self.stream.write("\n".join(self.summary(ended)) + "\n")
                self.stream.flush()
            except (OSError, ValueError):
                pass
