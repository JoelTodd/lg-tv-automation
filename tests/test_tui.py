"""Terminal lifecycle, truthful telemetry, and real mpv IPC without hardware."""

import asyncio
import fcntl
import io
import json
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

from lg_tv_automation.cli.play import run_mpv
from lg_tv_automation.console import log, log_sink
from lg_tv_automation.telemetry import observe_player, read_diagnostics
from lg_tv_automation.tui import PlaybackUI, PROPERTIES, clip
from lg_tv_automation.tui_render import cell_width, colour_frame, plain, shutter


class Terminal(io.StringIO):
    def isatty(self):
        return True

    def fileno(self):
        return 2


def make_ui():
    with patch("sys.stderr", Terminal()), patch.dict(os.environ, {"TERM": "xterm-256color"}):
        return PlaybackUI(Path("Movie.mkv"))


class RenderTests(unittest.TestCase):
    def test_metadata_fallback_warnings_clear_after_playback_advances(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        warnings = (
            "[ffmpeg/demuxer] mov,mp4,m4a,3gp,3g2,mj2: UDTA parsing failed retrying raw",
            "[ffmpeg/demuxer] mov,mp4,m4a,3gp,3g2,mj2: stream 0, timescale not set",
        )
        for message in warnings:
            ui.notice(message)
            ui.notice(message)
        self.assertIsNone(ui.latest_error)
        self.assertIn("UDTA", "\n".join(ui.alert_lines(time.monotonic(), 75)))
        ui.property_change({"name": "pause", "data": False})
        ui.property_change({"name": "time-pos", "data": 0})
        self.assertIn("UDTA", "\n".join(ui.alert_lines(time.monotonic(), 75)))
        ui.property_change({"name": "time-pos", "data": .5})
        self.assertEqual(ui.alert_lines(time.monotonic(), 75), [])
        ui.values.clear()  # Observer disconnects before the exit report.
        ui.result = ui.player_exit = 0
        self.assertNotIn("DIAGNOSTICS", "\n".join(ui.summary(time.monotonic())))
        # Retain the original context when the session ultimately fails.
        ui.result = 1
        self.assertIn("UDTA", "\n".join(ui.summary(time.monotonic())))

    def test_metadata_warnings_do_not_clear_for_pause_seek_or_buffering(self):
        for condition in ("pause", "seeking", "paused-for-cache"):
            with self.subTest(condition=condition):
                ui = make_ui()
                ui.phase, ui.telemetry = "Playback", "Live"
                ui.notice("[ffmpeg/demuxer] mov: UDTA parsing failed retrying raw")
                ui.values = {"pause": False, condition: True}
                for position in (0, 10):
                    ui.property_change({"name": "time-pos", "data": position})
                self.assertIn("UDTA", "\n".join(ui.alert_lines(time.monotonic(), 75)))
                ui.result = 0
                self.assertIn("UDTA", "\n".join(ui.summary(time.monotonic())))

    def test_advancing_playback_does_not_hide_real_failures(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        ui.notice("Audio output failed: device disconnected")
        ui.notice("[ffmpeg/demuxer] mov: error reading packet")
        ui.notice("Display restore failed")
        ui.property_change({"name": "pause", "data": False})
        for position in (0, 1):
            ui.property_change({"name": "time-pos", "data": position})
        ui.result = 0
        for message in ("Audio output failed", "error reading packet", "Display restore failed"):
            self.assertIn(message, "\n".join(ui.alert_lines(time.monotonic(), 75)))
            self.assertIn(message, "\n".join(ui.summary(time.monotonic())))

    def test_full_terminal_layout_and_effects_preserve_measurements(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        ui.values = {"pause": False, "time-pos": 120, "duration": 600}
        rows = ui.lines(80, 24)
        self.assertEqual(len(rows), 24)
        self.assertTrue(all(sum(map(cell_width, row)) == 79 for row in rows))
        self.assertIn("AUDIO SPECTRUM", rows[7])
        self.assertIn("20.0%", rows[5])
        first = colour_frame(rows)
        self.assertNotEqual(shutter(rows, .1), shutter(rows, 1))
        self.assertEqual(shutter(rows, 1), rows)
        self.assertEqual([plain(row) for row in first], rows)
        for width, height in ((64, 22), (100, 30), (160, 48)):
            rows = ui.lines(width, height)
            self.assertEqual(len(rows), height)
            self.assertTrue(all(sum(map(cell_width, row)) == width - 1 for row in rows))

    def test_border_is_uniform_and_timeline_colour_tracks_playhead(self):
        def styled_characters(line):
            style = ""
            for part in re.split(r"(\x1b\[[0-9;]*m)", line):
                if part.startswith("\x1b["):
                    style = part[2:-1]
                else:
                    yield from ((char, style) for char in part)

        ui = make_ui()
        ui.phase = "Playback"
        ui.spectrum = [.5] * 24
        ui.spectrum_updated = time.monotonic()
        ui.notice("Audio error: disconnected")
        for position in (0, 120, 450, 600):
            ui.values = {"pause": False, "time-pos": position, "duration": 600}
            rows = colour_frame(ui.lines(80, 24))
            for row in rows:
                cells = list(styled_characters(row))
                self.assertEqual(cells[0][1], "0;38;5;61")
                self.assertEqual(cells[-1][1], "0;38;5;61")
                if cells[0][0] in "╭╰├":
                    self.assertTrue(all(style == "0;38;5;61" for _, style in cells))
            timeline = [(char, style) for char, style in styled_characters(rows[5]) if char in "━◆─"]
            self.assertEqual(sum(char == "━" for char, _ in timeline), int(position / 600 * 67))
            for char, style in timeline:
                self.assertEqual(style, "0;38;5;240" if char == "─" else "0;38;5;81")

    def test_error_remains_visible_under_warning_flood_and_wraps(self):
        ui = make_ui()
        message = "Audio output failed: device disconnected. Playback may continue without sound."
        ui.notice(message)
        for index in range(220):
            ui.notice(f"Decoder warning {index}")
        rows = ui.lines(80, 24)
        self.assertIn("ERROR", rows[20])
        self.assertEqual(sum("ERROR" in row for row in rows), 1)
        self.assertTrue(rows[20].rstrip(" │").endswith("…"))
        self.assertEqual(len(ui.events), 200)
        summary = "\n".join(ui.summary(time.monotonic()))
        self.assertIn(message, " ".join(summary.split()))
        self.assertIn("21 earlier entries omitted", summary)

    def test_long_diagnostic_leaves_room_for_current_playback_alerts(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        ui.notice("Audio error: " + "detail " * 100)
        ui.values["paused-for-cache"] = True
        alerts = ui.alert_lines(time.monotonic(), 75)
        self.assertTrue(alerts[2].endswith("…"))
        self.assertIn("waiting for media", alerts[3])

    def test_buffering_and_sustained_sync_warnings_not_transient_spikes(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        ui.values = {"pause": False, "avsync": .1}
        ui.sample(10)
        self.assertNotIn("offset above", "\n".join(ui.alert_lines(11, 75)))
        ui.sample(12)
        self.assertIn("offset above", "\n".join(ui.alert_lines(12, 75)))
        ui.values["avsync"] = .002
        ui.sample(13)
        self.assertNotIn("offset above", "\n".join(ui.alert_lines(13, 75)))
        ui.values["paused-for-cache"] = True
        self.assertEqual(ui.state(), "Buffering")
        self.assertIn("waiting for media", "\n".join(ui.alert_lines(13, 75)))
        ui.telemetry = "Unavailable"
        ui.sample(14)
        self.assertNotIn("A/V HISTORY", "\n".join(ui.lines(80, 24)))

    def test_summary_preserves_last_values_and_totals_across_resets_and_disconnect(self):
        ui = make_ui()
        for name, values in {
            "time-pos": [21, 47, None], "duration": [100, None],
            "frame-drop-count": [0, 4, 1, 3, None],
            "decoder-frame-drop-count": [0, None], "avsync": [.004, -.020, None],
        }.items():
            for value in values:
                ui.property_change({"name": name, "data": value})
        ui.values.clear()
        ui.result, ui.player_exit, ui.end_reason = 0, 0, "stop"
        ui.notice("decoder warning")
        ui.notice("decoder warning")
        summary = "\n".join(ui.summary(time.monotonic()))
        self.assertIn("Last position: 00:47 / 01:40", summary)
        self.assertIn("render drops 7; decoder drops 0; mistimed unavailable", summary)
        self.assertNotIn("20.0 ms", summary)
        self.assertIn("Exit: 0 · stop", summary)
        self.assertIn("repeated 2 times", summary)

    def test_live_measurements_and_no_fixed_success_checks(self):
        ui = make_ui()
        ui.phase, ui.telemetry, ui.format = "Playback", "Live", "HDR"
        ui.setup_seconds = 2.35
        ui.values = {
            "time-pos": 90, "duration": 3600, "pause": False,
            "video-params/w": 3840, "video-params/h": 2160,
            "container-fps": 23.976, "estimated-display-fps": 119.879,
            "avsync": -.002, "frame-drop-count": 3,
            "decoder-frame-drop-count": 0, "mistimed-frame-count": 1,
        }
        rendered = "\n".join(ui.lines(100, 24))
        for text in ("Playing", "01:30 / 1:00:00", "2.5%", "3840 × 2160", "23.976 fps", "119.879 Hz measured", "2.35s"):
            self.assertIn(text, rendered)
        for text in ("HDMI", "Filmmaker", "nvdec", "Connected", "verified", "A/V HISTORY", "decoder 0", "-2.0 ms"):
            self.assertNotIn(text, rendered)
        ui.values["pause"] = True
        self.assertIn("Paused", "\n".join(ui.lines(80, 24)))

    def test_unavailable_invalid_and_reset_properties_are_not_invented(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        for value in (None, True, float("nan"), float("inf"), "0"):
            ui.property_change({"name": "frame-drop-count", "data": value})
            rendered = "\n".join(ui.lines(80, 24))
            self.assertIsNone(ui.number("frame-drop-count"))
            self.assertIn("--:-- / --:--", rendered)
        for value in (5, 0):
            ui.property_change({"name": "frame-drop-count", "data": value})
            self.assertEqual(ui.number("frame-drop-count"), value)
        ui.property_change({"name": "frame-drop-count"})
        self.assertIsNone(ui.number("frame-drop-count"))

    def test_compact_terminals_keep_diagnostics_and_clip_wide_filenames(self):
        ui = make_ui()
        ui.movie = Path("映" * 100 + ".mkv")
        ui.notice("Display restore failed")
        for width, height in ((20, 4), (1, 1), (80, 24)):
            rows = ui.lines(width, height)
            self.assertLessEqual(len(rows), height)
            self.assertTrue(all(clip(row, width - 1) == row for row in rows))
        self.assertIn("ERROR", "\n".join(ui.lines(30, 3)))
        self.assertEqual(clip("映画abc", 5), "映画a")

    def test_control_characters_are_removed_from_filenames_and_diagnostics(self):
        ui = make_ui()
        ui.movie = Path("a\x1b[2J\n\r\tb.mkv")
        ui.notice("\x1b]0;bad title\x07\x1b[31mwarning\x1b[0m")
        rendered = "\n".join(ui.lines(80, 24))
        self.assertIn("ab.mkv", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertEqual(list(ui.messages), ["warning"])


class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_during_closing_animation_restores_terminal_and_propagates(self):
        ui, closing = make_ui(), asyncio.Event()
        def draw(**kwargs):
            if kwargs.get("closing") is not None:
                closing.set()
        ui.draw = draw
        async def run():
            async with ui:
                ui.result = 0
        task = asyncio.create_task(run())
        await asyncio.wait_for(closing.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        output = ui.stream.getvalue()
        self.assertIn("\x1b[?25h\x1b[?1049l", output)
        self.assertIn("Interrupted", output)
        self.assertIn("Exit: 130", output)

    async def test_compact_expired_alert_is_erased(self):
        ui = make_ui()
        ui.phase, ui.telemetry = "Playback", "Live"
        ui.values["paused-for-cache"] = True
        with patch("os.get_terminal_size", return_value=os.terminal_size((50, 10))):
            ui.draw()
            ui.stream.seek(0)
            ui.stream.truncate()
            ui.values["paused-for-cache"] = False
            ui.draw()
        self.assertIn("\x1b[3;1H\x1b[2K", ui.stream.getvalue())

    async def test_no_colour_mode_and_summary_after_screen_restoration(self):
        ui = make_ui()
        ui.colour = False
        with patch("os.get_terminal_size", return_value=os.terminal_size((80, 24))):
            async with ui:
                ui.result = 0
                await asyncio.sleep(0)
        output = ui.stream.getvalue()
        self.assertNotIn("38;5", output)
        self.assertLess(output.index("\x1b[?1049l"), output.index("Closed ·"))
        self.assertIn("Exit: 0", output)

    async def test_redirected_and_dumb_terminals_have_no_escape_sequences(self):
        for stream, term in ((io.StringIO(), "xterm"), (Terminal(), "dumb")):
            with patch("sys.stderr", stream), patch.dict(os.environ, {"TERM": term}):
                async with PlaybackUI(Path("movie")) as ui:
                    self.assertFalse(ui.enabled)
                    log("Preparing playback.")
            self.assertEqual(stream.getvalue(), "[lg-tv-play] Preparing playback.\n")

    async def test_terminal_and_log_routing_restored_after_exception(self):
        ui = make_ui()
        with patch("os.get_terminal_size", return_value=os.terminal_size((80, 24))):
            with self.assertRaisesRegex(RuntimeError, "test"):
                async with ui:
                    log("TV restore failed")
                    await asyncio.sleep(0)
                    raise RuntimeError("test")
        output = ui.stream.getvalue()
        self.assertTrue(output.startswith("\x1b[?1049h\x1b[?25l"))
        summary = output.split("\x1b[?25h\x1b[?1049l", 1)[1]
        self.assertIn("Failed ·", summary)
        self.assertIn("ERROR", summary)
        self.assertIn("TV restore failed", summary)
        self.assertNotIn("\x1b", summary)
        self.assertIsNone(log_sink.get())
        self.assertTrue(ui._task.done())

    async def test_cancel_restores_terminal(self):
        ui, entered = make_ui(), asyncio.Event()
        async def run():
            async with ui:
                entered.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(run())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        summary = ui.stream.getvalue().split("\x1b[?25h\x1b[?1049l", 1)[1]
        self.assertIn("Interrupted", summary)
        self.assertIn("Exit: 130", summary)
        self.assertTrue(ui._task.done())

    async def test_diagnostics_drain_long_lines_with_bounded_memory(self):
        ui, reader = make_ui(), asyncio.StreamReader()
        reader.feed_data(b"x" * 100_000 + b"\nlast error\n")
        reader.feed_eof()
        await read_diagnostics(reader, ui)
        self.assertLessEqual(len(ui.messages), 8)
        self.assertTrue(all(len(message) <= 2000 for message in ui.messages))
        self.assertEqual(ui.messages[-1], "last error")

    async def test_ipc_updates_and_disconnect_clears_stale_values(self):
        ui = make_ui()
        received, release = asyncio.Event(), asyncio.Event()
        commands = []
        async def serve(reader, writer):
            try:
                for _ in PROPERTIES:
                    commands.append(json.loads(await reader.readline())["command"])
                writer.write(b'invalid\n[]\n{"event":"property-change","name":"time-pos","data":12}\n')
                writer.write(b'{"event":"playback-restart"}\n')
                await writer.drain()
                received.set()
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()
        with tempfile.TemporaryDirectory() as directory:
            socket = str(Path(directory) / "mpv.sock")
            server = await asyncio.start_unix_server(serve, path=socket)
            async with server:
                task = asyncio.create_task(observe_player(socket, ui))
                try:
                    await asyncio.wait_for(received.wait(), 1)
                    async with asyncio.timeout(1):
                        while ui.number("time-pos") != 12:
                            await asyncio.sleep(.001)
                    self.assertEqual(ui.telemetry, "Live")
                    self.assertEqual({command[2] for command in commands}, set(PROPERTIES))
                finally:
                    release.set()
                    await asyncio.wait_for(task, 1)
            self.assertEqual(ui.telemetry, "Unavailable")
            self.assertEqual(ui.values, {})

    async def test_ipc_failure_does_not_fail_playback(self):
        ui = make_ui()
        with patch("asyncio.open_unix_connection", side_effect=PermissionError):
            await observe_player("unused", ui)
        self.assertEqual(ui.telemetry, "Unavailable")


@unittest.skipUnless(shutil.which("mpv"), "mpv is not installed")
class PlayerTests(unittest.IsolatedAsyncioTestCase):
    def test_real_terminal_keys_resize_and_restoration_on_quit_and_sigterm(self):
        code = '''
import asyncio, signal
from pathlib import Path
from lg_tv_automation.cli.play import run_mpv
from lg_tv_automation.tui import PlaybackUI
async def run():
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
    async with PlaybackUI(Path("Terminal check.mkv")) as ui:
        ui.set_phase("Playback")
        await run_mpv(["--no-config", "--vo=null", "--ao=null", "--length=30", "--",
                       "av://lavfi:testsrc=size=640x360:rate=24"], ui=ui)
try:
    asyncio.run(run())
except (KeyboardInterrupt, asyncio.CancelledError):
    raise SystemExit(130)
'''
        for stop in ("q", "SIGTERM"):
            with self.subTest(stop=stop):
                master, slave = pty.openpty()
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
                before = termios.tcgetattr(slave)
                def setup():
                    os.setsid()
                    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
                proc = subprocess.Popen(
                    [sys.executable, "-c", code], stdin=slave, stdout=slave, stderr=slave,
                    env={**{key: value for key, value in os.environ.items() if key != "NO_COLOR"}, "TERM": "xterm-256color"}, preexec_fn=setup,
                )
                def read_until(needle):
                    output = bytearray()
                    deadline = time.monotonic() + 5
                    while needle not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], .05)[0]:
                            output.extend(os.read(master, 65536))
                    self.assertIn(needle, output)
                    return output.decode(errors="replace")
                try:
                    read_until(b"Playing")
                    os.write(master, b" ")
                    read_until(b"Paused")
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 6, 25, 0, 0))
                    frame = read_until(b"\x1b[2;1H").split("\x1b[H")[-1]
                    rows = [plain(row) for row in re.split(r"\x1b\[\d+;1H", frame)]
                    self.assertTrue(all(len(row) <= 24 for row in rows))
                    if stop == "q":
                        os.write(master, b"q")
                    else:
                        proc.send_signal(signal.SIGTERM)
                    report = read_until(b"  Exit:")
                    self.assertIn("\x1b[?25h\x1b[?1049l", report)
                    self.assertIn("TIMING", report)
                    self.assertIn("Last position:", report)
                    self.assertEqual(proc.wait(timeout=5), 0 if stop == "q" else 130)
                    self.assertEqual(termios.tcgetattr(slave), before)
                finally:
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    os.close(master)
                    os.close(slave)

    async def test_real_headless_mpv_reports_live_position_and_releases_socket(self):
        ui, seen, sockets = make_ui(), {}, []
        original = ui.property_change
        def update(event):
            original(event)
            if event.get("data") is not None:
                seen[event["name"]] = event["data"]
        ui.property_change = update
        create = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            sockets.extend(arg.split("=", 1)[1] for arg in args if arg.startswith("--input-ipc-server="))
            return await create(*args, **kwargs)
        with patch("asyncio.create_subprocess_exec", side_effect=spawn):
            result = await asyncio.wait_for(run_mpv([
                "--no-config", "--vo=null", "--ao=null", "--input-terminal=no", "--length=0.5",
                "--", "av://lavfi:testsrc=size=64x64:rate=24",
            ], ui=ui), timeout=5)
        self.assertEqual(result, 0)
        self.assertGreater(seen["time-pos"], 0)
        self.assertEqual(seen["video-params/w"], 64)
        self.assertIsNotNone(ui.setup_seconds)
        self.assertTrue(sockets)
        self.assertFalse(Path(sockets[0]).parent.exists())

    async def test_cancel_with_dashboard_reaps_real_player_and_removes_socket(self):
        ui, spawned, children, sockets = make_ui(), asyncio.Event(), [], []
        create = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            sockets.extend(arg.split("=", 1)[1] for arg in args if arg.startswith("--input-ipc-server="))
            child = await create(*args, **kwargs)
            children.append(child)
            spawned.set()
            return child
        with patch("asyncio.create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(run_mpv([
                "--no-config", "--vo=null", "--ao=null", "--input-terminal=no", "--",
                "av://lavfi:testsrc=size=64x64:rate=24",
            ], ui=ui))
            await asyncio.wait_for(spawned.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertIsNotNone(children[0].returncode)
        self.assertFalse(Path(sockets[0]).parent.exists())
