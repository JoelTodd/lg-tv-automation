"""Real signal analysis and scoped/cancellable audio monitoring."""
import asyncio
from contextlib import asynccontextmanager
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from lg_tv_automation import spectrum
from lg_tv_automation.tui import PlaybackUI


def tone(frequency, amplitude=.5):
    return struct.pack(f"<{spectrum.SIZE}h", *[
        round(32767 * amplitude * math.sin(2 * math.pi * frequency * index / spectrum.RATE))
        for index in range(spectrum.SIZE)])


class SignalTests(unittest.TestCase):
    def test_silence_has_no_fake_activity(self):
        self.assertEqual(spectrum.analyse(bytes(spectrum.SIZE * 2)), [0] * spectrum.BANDS)

    def test_frequency_and_level_are_measured(self):
        for frequency in (100, 1000, 5000):
            bands = spectrum.analyse(tone(frequency))
            peak = max(range(len(bands)), key=bands.__getitem__)
            expected = int(math.log(frequency / 50) / math.log(10000 / 50) * spectrum.BANDS)
            self.assertLessEqual(abs(peak - expected), 1)
            quieter = spectrum.analyse(tone(frequency, .05))
            self.assertGreater(bands[peak], quieter[peak] + .2)

    def test_pause_and_stale_stream_settle_to_zero_without_fake_bars(self):
        ui = PlaybackUI(Path("movie"))
        ui.phase = "Playback"
        ui.values["pause"] = False
        ui.spectrum = [.5] * spectrum.BANDS
        ui.spectrum_updated = 10
        self.assertEqual(ui.audio_levels(10), [.5] * spectrum.BANDS)
        self.assertEqual(ui.audio_levels(10.7), [0] * spectrum.BANDS)
        self.assertEqual(ui.audio_levels(12), [])
        ui.values["pause"] = True
        ui.changed_at = 10
        self.assertEqual(ui.audio_levels(11), [0] * spectrum.BANDS)


class EnvelopeTests(unittest.TestCase):
    def make_ui(self):
        ui = PlaybackUI(Path("movie"))
        ui.phase = "Playback"
        ui.values["pause"] = False
        ui.update_spectrum([0] * spectrum.BANDS, 10)
        ui.audio_levels(10)
        return ui

    def test_short_transient_between_frames_reaches_full_height_immediately(self):
        ui = self.make_ui()
        ui.update_spectrum([.9] + [0] * (spectrum.BANDS - 1), 10.01)
        ui.update_spectrum([.1] + [0] * (spectrum.BANDS - 1), 10.02)
        shown = ui.audio_levels(10 + 1 / 30)
        self.assertEqual(shown, [.9] + [0] * (spectrum.BANDS - 1))
        previous = shown[0]
        for frame in range(2, 16):
            now = 10 + frame / 30
            ui.update_spectrum([0] * spectrum.BANDS, now)
            shown = ui.audio_levels(now)
            self.assertLess(shown[0], previous)
            self.assertGreater(shown[0], previous * .75)
            previous = shown[0]
        self.assertLess(shown[0], .04)

    def test_small_level_flutter_is_reduced_without_merging_frequency_bands(self):
        ui = self.make_ui()
        displayed = []
        for frame in range(1, 61):
            now = 10 + frame / 30
            level = .55 if frame % 2 else .65
            ui.update_spectrum([level] + [0] * (spectrum.BANDS - 1), now)
            shown = ui.audio_levels(now)
            self.assertEqual(shown[1:], [0] * (spectrum.BANDS - 1))
            if frame > 10:
                displayed.append(shown[0])
        self.assertLess(max(displayed) - min(displayed), .025)

    def test_release_depends_on_elapsed_time_not_frame_count(self):
        results = []
        for fps in (30, 60):
            ui = self.make_ui()
            ui.update_spectrum([1] * spectrum.BANDS, 10)
            ui.audio_levels(10)
            for frame in range(1, int(.4 * fps) + 1):
                now = 10 + frame / fps
                ui.update_spectrum([0] * spectrum.BANDS, now)
                shown = ui.audio_levels(now)
            results.append(shown)
        for slow, fast in zip(*results):
            self.assertAlmostEqual(slow, fast)
        self.assertLess(results[0][0], .06)

    def test_reconnect_and_delayed_redraw_do_not_replay_old_peaks(self):
        for reconnect in (False, True):
            ui = self.make_ui()
            ui.update_spectrum([1] * spectrum.BANDS, 10.01)
            if reconnect:
                ui.update_spectrum([], 10.02)
            ui.update_spectrum([0] * spectrum.BANDS, 11)
            self.assertEqual(ui.audio_levels(11), [0] * spectrum.BANDS)


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_matches_player_pid_and_explicit_sink_monitor(self):
        inputs = [
            {"index": 4, "sink": 7, "properties": {"application.process.id": "999"}},
            {"index": 5, "sink": 8, "properties": {"application.process.id": "123"}},
        ]
        sinks = [{"index": 7, "monitor_source": "other.monitor"},
                 {"index": 8, "monitor_source_name": "movie.monitor"}]
        @asynccontextmanager
        async def fake_child(*args):
            data = inputs if args[-1] == "sink-inputs" else sinks
            yield SimpleNamespace(communicate=AsyncMock(return_value=(json.dumps(data).encode(), None)), returncode=0)
        with patch.object(spectrum, "child", fake_child):
            self.assertEqual(await spectrum.audio_target(123), (5, "movie.monitor"))
            self.assertIsNone(await spectrum.audio_target(456))
            sinks[1] = {"index": 8}
            self.assertIsNone(await spectrum.audio_target(123))

    async def test_missing_tools_leave_playback_usable(self):
        ui = PlaybackUI(Path("movie"))
        with patch.object(spectrum.shutil, "which", return_value=None):
            await spectrum.watch_spectrum(123, ui)
        self.assertEqual(ui.spectrum, [])
        self.assertEqual(ui.spectrum_status, "Audio monitor unavailable")

    async def test_native_pipewire_pid_is_resolved_through_owning_client(self):
        listings = {
            "sink-inputs": [{"index": 12, "sink": 8, "client": "44", "properties": {"application.name": "mpv"}},
                            {"index": 13, "sink": 8, "client": "45", "properties": {"application.name": "mpv"}}],
            "clients": [{"index": 44, "properties": {"application.process.id": "999"}},
                        {"index": 45, "properties": {"application.process.id": "123"}}],
            "sinks": [{"index": 8, "monitor_source": "movie.monitor"}],
        }
        @asynccontextmanager
        async def fake_child(*args):
            yield SimpleNamespace(communicate=AsyncMock(return_value=(json.dumps(listings[args[-1]]).encode(), None)), returncode=0)
        with patch.object(spectrum, "child", fake_child):
            self.assertEqual(await spectrum.audio_target(123), (13, "movie.monitor"))

    async def test_capture_uses_only_selected_stream_and_monitor(self):
        ui = PlaybackUI(Path("movie"))
        reader = asyncio.StreamReader()
        reader.feed_data(tone(1000))
        reader.feed_eof()
        commands = []
        @asynccontextmanager
        async def fake_child(*args):
            commands.append(args)
            yield SimpleNamespace(stdout=reader)
        with patch.object(spectrum, "child", fake_child), self.assertRaises(asyncio.IncompleteReadError):
            await spectrum.capture((42, "movie.monitor"), ui)
        self.assertIn("--monitor-stream=42", commands[0])
        self.assertIn("--device=movie.monitor", commands[0])
        self.assertTrue(any(ui.spectrum))

    async def test_capture_analyses_overlapping_windows_of_real_samples(self):
        ui = PlaybackUI(Path("movie"))
        samples = struct.pack(f"<{spectrum.SIZE + 2 * spectrum.HOP}h",
                              *range(spectrum.SIZE + 2 * spectrum.HOP))
        reader = asyncio.StreamReader()
        reader.feed_data(samples)
        reader.feed_eof()
        @asynccontextmanager
        async def fake_child(*args):
            yield SimpleNamespace(stdout=reader)
        with patch.object(spectrum, "child", fake_child), patch.object(spectrum, "analyse", wraps=spectrum.analyse) as analyse:
            with self.assertRaises(asyncio.IncompleteReadError):
                await spectrum.capture((42, "movie.monitor"), ui)
        self.assertEqual(analyse.call_count, 3)
        for index, call in enumerate(analyse.call_args_list):
            offset = index * spectrum.HOP * 2
            self.assertEqual(call.args[0], samples[offset:offset + spectrum.SIZE * 2])

    async def test_cancel_during_spawn_reaps_late_monitor(self):
        create = asyncio.create_subprocess_exec
        started, release = asyncio.Event(), asyncio.Event()
        children = []
        async def spawn(*args, **kwargs):
            proc = await create(*args, **kwargs)
            children.append(proc)
            started.set()
            await release.wait()
            return proc
        async def run():
            async with spectrum.child(sys.executable, "-c", "import time; time.sleep(30)"):
                await asyncio.Event().wait()
        with patch.object(spectrum.asyncio, "create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(run())
            try:
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 4)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIsNotNone(children[0].returncode)
