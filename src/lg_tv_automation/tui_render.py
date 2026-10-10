"""Responsive terminal frames and restrained ANSI colour; no external renderer."""
from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tui import PlaybackUI

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def plain(text: str) -> str:
    return "".join(c for c in ANSI.sub("", text) if not unicodedata.category(c).startswith("C"))


def cell_width(char: str) -> int:
    return 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1


def clip(text: str, columns: int) -> str:
    result, width = [], 0
    for char in plain(text):
        size = cell_width(char)
        if width + size > columns:
            break
        result.append(char)
        width += size
    return "".join(result)


def pad(text: str, columns: int) -> str:
    text = clip(text, columns)
    return text + " " * max(0, columns - sum(map(cell_width, text)))


def wrap(text: str, columns: int) -> list[str]:
    remaining, rows = plain(text), []
    while remaining:
        part = clip(remaining, max(1, columns))
        if not part:
            break
        if len(part) < len(remaining) and " " in part:
            part = part[:part.rfind(" ")] or part
        rows.append(part)
        remaining = remaining[len(part):].lstrip()
    return rows


def clock(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def lifecycle_stages(ui: PlaybackUI) -> list[str]:
    return (["Restoring display", "Restoring TV", "Disconnecting TV"]
            if ui.phase.startswith(("Restoring", "Disconnecting")) else
            ["Reading media", "Connecting TV / reading display", "Preparing display", "Preparing TV"])


def spectrum_rows(ui: PlaybackUI, width: int, height: int, now: float) -> list[str]:
    if ui.phase != "Playback":
        stages = lifecycle_stages(ui)
        timings = dict(ui.phase_times)
        rows = []
        for stage in stages:
            active = stage == ui.phase
            marker = "›" if active else "·" if stage in timings else "○"
            duration = f"{now - ui.phase_started:.1f}s" if active else f"{timings[stage]:.2f}s" if stage in timings else ""
            rows.append(pad(f"  {marker} {stage}", width - 9) + pad(duration, 9))
        blank = max(0, (height - len(rows)) // 2)
        return ([""] * blank + rows + [""] * height)[:height]
    levels = ui.audio_levels(now)
    if not levels:
        rows = [""] * height
        rows[height // 2] = pad("", max(0, (width - len(ui.spectrum_status)) // 2)) + ui.spectrum_status
        return rows
    group = max(1, width // len(levels))
    bar_width = max(1, group - 1)
    offset = max(0, (width - group * len(levels)) // 2)
    rows = []
    blocks = " ▁▂▃▄▅▆▇█"
    for row in range(height):
        line = " " * offset
        for value in levels:
            eighths = max(0, min(8, round(value * height * 8) - (height - row - 1) * 8))
            line += blocks[eighths] * bar_width + " " * (group - bar_width)
        rows.append(line)
    return rows


def dashboard(ui: PlaybackUI, width: int, height: int, now: float) -> list[str]:
    width = max(0, width - 1)
    if width < 63 or height < 22:
        return compact(ui, width, height, now)
    inner = width - 4
    def row(text=""):
        return "│ " + pad(text, inner) + " │"
    def rule(label):
        label = clip(label, inner - 2)
        return "├─ " + label + " " + "─" * (inner - sum(map(cell_width, label)) - 1) + "┤"
    def pair(left, right):
        half = (inner - 3) // 2
        return row(pad(left, half) + " │ " + right)
    position, duration = ui.number("time-pos"), ui.number("duration")
    fraction = min(1, max(0, position / duration)) if position is not None and duration and duration > 0 else None
    state = ui.state()
    marker = "Ⅱ" if state == "Paused" else "▶" if state == "Playing" else "›"
    right = f"SESSION {clock(now - ui.started)}"
    heading = pad(f"{marker} {state}", inner - len(right)) + right
    progress = f"{clock(position)} / {clock(duration)}"
    remaining = f"{clock(max(0, duration - position))} left" if position is not None and duration is not None else ""
    step = None
    if ui.phase != "Playback":
        stages = lifecycle_stages(ui)
        index = stages.index(ui.phase) if ui.phase in stages else 0
        step = f"{index + 1}/{len(stages)}"
        fraction = index / len(stages)
        progress, remaining = f"Step {index + 1} of {len(stages)}", f"{now - ui.phase_started:.1f}s"
    bar_width = inner - 8
    filled = int((fraction or 0) * bar_width)
    bar = "━" * filled + ("◆" if fraction is not None and filled < bar_width else "")
    bar += "─" * max(0, bar_width - len(bar))
    percent = step or (f"{fraction * 100:5.1f}%" if fraction is not None else "    —")
    w, h, fps = (ui.number(p) for p in ("video-params/w", "video-params/h", "container-fps"))
    video = f"{w:g} × {h:g}" if w and h else "—"
    video += f"  {ui.format}" if ui.format else ""
    display = ui.number("estimated-display-fps")
    refresh = f"{display:.3f} Hz measured" if display else "—"
    if not display and (display := ui.number("display-fps")):
        refresh = f"{display:.3f} Hz reported"
    setup = f"{ui.setup_seconds:.2f}s" if ui.setup_seconds is not None else "—"
    section = "AUDIO SPECTRUM" if ui.phase == "Playback" else "RESTORING" if ui.phase.startswith(("Restoring", "Disconnecting")) else "PREPARING"
    rows = ["╭" + "─" * (width - 2) + "╮", row(heading), row(ui.movie.name), row(),
            row(pad(progress, inner - len(remaining)) + remaining), row(bar + "  " + percent), row(), rule(section)]
    rows.extend(row(line) for line in spectrum_rows(ui, inner, height - 17, now))
    rows.append(row(pad("50 Hz", inner // 3) + pad("300 Hz", inner // 3) + pad("2 kHz", inner - 2 * (inner // 3) - 6) + "10 kHz") if ui.phase == "Playback" else row())
    rows += [rule("VIDEO"), pair(video, refresh), pair(f"{fps:.3f} fps" if fps else "— fps", f"Setup  {setup}"), row()]
    alerts = ui.alert_lines(now, inner)
    alert = alerts[0] if alerts else ""
    if len(alerts) > 1:
        alert = clip(alert, inner - 1) + "…"
    rows += [row(alert), row(), row("Space pause   ←/→ seek   q close   Ctrl-C stop"), "╰" + "─" * (width - 2) + "╯"]
    return rows[:height]


def compact(ui: PlaybackUI, width: int, height: int, now: float) -> list[str]:
    rows = [ui.movie.name, f"{ui.state()}   {clock(ui.number('time-pos'))} / {clock(ui.number('duration'))}"]
    if ui.phase != "Playback":
        rows[1] = f"{ui.phase}   {now - ui.phase_started:.1f}s"
    alerts = ui.alert_lines(now, width)
    if alerts:
        rows = rows[:max(1, height - 1)] + [alerts[0]]
    return [clip(line, width) for line in rows[:height]]


def shutter(rows: list[str], openness: float) -> list[str]:
    """A short projector-style reveal/collapse, used only at entry and exit."""
    if openness >= 1 or not rows:
        return rows
    middle = (len(rows) - 1) / 2
    radius = max(.5, len(rows) * max(0, openness) / 2)
    width = sum(map(cell_width, rows[0]))
    return [row if abs(index - middle) <= radius else " " * width for index, row in enumerate(rows)]


def colour_frame(rows: list[str]) -> list[str]:
    """Keep the frame uniform; reserve colour changes for the content."""
    result = []
    for index, line in enumerate(rows):
        border = line.startswith(("╭", "╰", "├"))
        framed = line.startswith("│") and line.endswith("│")
        colour = "38;5;252"
        if index == 1:
            colour = "1;38;5;117"
        elif index == 2:
            colour = "1;38;5;255"
        elif "ERROR" in line:
            colour = "1;38;5;203"
        elif "WARN" in line:
            colour = "38;5;221"
        elif index == len(rows) - 2:
            colour = "38;5;103"
        spectrum = framed and 8 <= index < len(rows) - 9
        rendered, previous = [], None
        for column, char in enumerate(line):
            tint = colour
            if border or (framed and column in (0, len(line) - 1)):
                tint = "38;5;61"
            elif framed and index == 5 and char in "━◆─":
                tint = "38;5;81" if char in "━◆" else "38;5;240"
            elif spectrum and char in "▁▂▃▄▅▆▇█":
                tint = "38;5;" + str((81, 117, 111, 141)[min(3, max(0, column - 2) * 4 // max(1, len(line) - 4))])
            if tint != previous:
                # Reset intensity as well as foreground at content boundaries.
                rendered.append(f"\x1b[0;{tint}m")
            rendered.append(char)
            previous = tint
        result.append("".join(rendered) + "\x1b[0m")
    return result
