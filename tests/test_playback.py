"""Playback-only orchestration, media, deadline and cancellation regressions."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from lg_tv_automation.cli import play
from lg_tv_automation.display import DisplayController, load_display_output
from lg_tv_automation.media import detect_hdr_from_file, normalize_mpv_args, resolve_movie_path
from lg_tv_automation.models import DisplaySnapshot
from lg_tv_automation.process import run_command
from lg_tv_automation.tv import TvController


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.movie = Path(self.directory.name) / "movie with spaces.mkv"
        self.movie.write_bytes(b"test-only")
        self.tv = TvController("tv", "HDMI_1")
        self.tv.__aenter__ = AsyncMock(return_value=self.tv)
        self.tv.__aexit__ = AsyncMock()
        self.profiles = []
        async def apply(profile):
            self.profiles.append(profile)
            self.tv.failures = []
        self.tv.apply_profile = AsyncMock(side_effect=apply)
        self.display = Mock()
        self.player = AsyncMock(return_value=0)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in [
            ("require_tv_ip", Mock(return_value="tv")),
            ("TvController", Mock(return_value=self.tv)),
            ("DisplayController", Mock(return_value=self.display)),
            ("probe_hdr", Mock(return_value=True)),
            ("run_mpv", self.player),
        ]:
            self.stack.enter_context(patch.object(play, name, value))
        self.stack.enter_context(patch.object(play.shutil, "which", return_value="/usr/bin/mpv"))
        self.stack.enter_context(patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.directory.name}))

    async def test_default_playback_and_desktop_restoration(self):
        self.assertEqual(await play.run_playback(self.movie), 0)
        self.display.capture.assert_called_once()
        self.display.apply_movie_state.assert_called_once_with(enable_hdr=True)
        self.display.apply_desktop_state.assert_called_once_with()
        self.assertEqual([p.picture_mode for p in self.profiles], ["hdrFilmMaker", "expert1"])
        self.assertEqual(self.profiles[0].tru_motion, "cinemaClear")
        self.assertEqual(self.profiles[0].label, "Blu-ray Player")
        self.assertEqual(self.profiles[-1].label, "HDMI 1")
        self.tv.__aexit__.assert_awaited_once()
        self.assertEqual(self.player.await_args.args[0][-2:], ["--", str(self.movie)])

    async def test_sdr_uses_sdr_profile(self):
        play.probe_hdr.return_value = False
        self.assertEqual(await play.run_playback(self.movie), 0)
        self.display.apply_movie_state.assert_called_once_with(enable_hdr=False)
        self.assertEqual(self.profiles[0].picture_mode, "filmMaker")

    async def test_invalid_path_rejected_before_connect_or_capture(self):
        self.assertEqual(await play.run_playback(self.movie.with_name("missing.mkv")), 1)
        self.tv.__aenter__.assert_not_awaited()
        self.display.capture.assert_not_called()
        self.player.assert_not_awaited()

    async def test_missing_mpv_rejected_before_hardware(self):
        play.shutil.which.return_value = None
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.tv.__aenter__.assert_not_awaited()
        self.display.capture.assert_not_called()

    async def test_probe_failure_disconnects_without_changes(self):
        play.probe_hdr.side_effect = RuntimeError("no video stream found")
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.display.apply_movie_state.assert_not_called()
        self.tv.apply_profile.assert_not_awaited()
        self.tv.__aexit__.assert_awaited_once()

    async def test_failed_connection_disconnects_partial_client(self):
        self.tv.__aenter__.side_effect = RuntimeError("connection failed")
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.display.apply_movie_state.assert_not_called()
        self.tv.__aexit__.assert_awaited_once()
        self.assertFalse(self.tv.return_to_input)

    async def test_partial_display_failure_rolls_back_and_never_applies_tv(self):
        self.display.apply_movie_state.side_effect = RuntimeError("failed after write")
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.display.restore.assert_called_once_with()
        self.display.apply_desktop_state.assert_not_called()
        self.tv.apply_profile.assert_not_awaited()
        self.tv.__aexit__.assert_awaited_once()
        self.player.assert_not_awaited()

    async def test_partial_tv_failure_restores_both_sides(self):
        async def apply(profile):
            self.profiles.append(profile)
            self.tv.failures = ["TV relabel failed: bad input"] if len(self.profiles) == 1 else []
        self.tv.apply_profile.side_effect = apply
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.display.restore.assert_called_once()
        self.assertEqual(self.profiles[-1].picture_mode, "expert1")
        self.tv.__aexit__.assert_awaited_once()
        self.player.assert_not_awaited()

    async def test_picture_failure_is_visible_degradation_not_unusable_player(self):
        async def apply(profile):
            self.profiles.append(profile)
            self.tv.failures = ["TV picture mode change failed: firmware refused"] if len(self.profiles) == 1 else []
        self.tv.apply_profile.side_effect = apply
        with patch.object(play, "log") as log:
            self.assertEqual(await play.run_playback(self.movie), 0)
        self.player.assert_awaited_once()
        self.assertTrue(any("degraded" in call.args[0] for call in log.call_args_list))

    async def test_cleanup_failure_changes_success_exit_and_still_disconnects(self):
        self.display.apply_desktop_state.side_effect = RuntimeError("restore unavailable")
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.assertEqual(self.profiles[-1].picture_mode, "expert1")
        self.tv.__aexit__.assert_awaited_once()

    async def test_tv_cleanup_reports_picture_failures_and_still_disconnects(self):
        async def apply(profile):
            self.profiles.append(profile)
            self.tv.failures = ["TV picture mode change failed: restore refused"] if len(self.profiles) == 2 else []
        self.tv.apply_profile.side_effect = apply
        self.assertEqual(await play.run_playback(self.movie), 1)
        self.tv.__aexit__.assert_awaited_once()

    async def test_player_exit_status_survives_cleanup(self):
        self.player.return_value = 7
        self.assertEqual(await play.run_playback(self.movie), 7)
        self.display.apply_desktop_state.assert_called_once()
        self.tv.__aexit__.assert_awaited_once()

    async def test_read_only_preparations_overlap(self):
        probe_started, capture_started, release = threading.Event(), threading.Event(), threading.Event()
        def probe(*_):
            probe_started.set()
            if not release.wait(2):
                raise RuntimeError("TV connect did not overlap probe")
            return True
        def capture():
            capture_started.set()
            if not release.wait(2):
                raise RuntimeError("TV connect did not overlap display capture")
        async def connect():
            async with asyncio.timeout(1):
                while not (probe_started.is_set() and capture_started.is_set()):
                    await asyncio.sleep(.001)
            release.set()
        play.probe_hdr.side_effect = probe
        self.display.capture.side_effect = capture
        self.tv.__aenter__.side_effect = connect
        self.assertEqual(await play.run_playback(self.movie), 0)

    async def test_cancel_waits_for_display_write_before_rollback(self):
        entered, release = threading.Event(), threading.Event()
        def apply(**_):
            entered.set()
            release.wait(2)
        self.display.apply_movie_state.side_effect = apply
        task = asyncio.create_task(play.run_playback(self.movie))
        try:
            async with asyncio.timeout(1):
                while not entered.is_set():
                    await asyncio.sleep(.001)
            task.cancel()
            await asyncio.sleep(.01)
            self.display.restore.assert_not_called()
            release.set()
            self.assertEqual(await task, 130)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.display.restore.assert_called_once()
        self.tv.__aexit__.assert_awaited_once()

    async def test_real_child_is_reaped_on_cancellation(self):
        create = asyncio.create_subprocess_exec
        launched = asyncio.Event()
        children = []
        async def spawn(*_, **kwargs):
            proc = await create(sys.executable, "-c", "import time; time.sleep(30)")
            children.append(proc)
            launched.set()
            return proc
        # setUp mocks playback launch; explicitly restore the real runner here.
        self.stack.close()
        with patch.object(play.asyncio, "create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(play.run_mpv([str(self.movie)]))
            await launched.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(children[0].returncode)


class CoreTests(unittest.TestCase):
    def test_cli_has_one_required_path_and_no_legacy_modes(self):
        with patch.object(sys, "argv", ["lg-tv-play", "a movie.mkv"]):
            self.assertEqual(play.parse_args().movie, Path("a movie.mkv"))
        for args in [[], ["--status"], ["--movie-mode"], ["--verify-tv-ui", "movie.mkv"], ["a.mkv", "b.mkv"]]:
            with self.subTest(args=args), contextlib.redirect_stderr(__import__("io").StringIO()), patch.object(sys, "argv", ["lg-tv-play", *args]), self.assertRaises(SystemExit):
                play.parse_args()

    def test_directory_selects_largest_video_not_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.mkv").write_bytes(b"x")
            movie = root / "main film.MKV"
            movie.write_bytes(b"xxx")
            (root / "cover.jpg").write_bytes(b"x" * 20)
            self.assertEqual(resolve_movie_path(root), movie)

    def test_empty_directory_and_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "No video"):
                resolve_movie_path(Path(directory))
            with self.assertRaisesRegex(RuntimeError, "not found"):
                resolve_movie_path(Path(directory) / "missing.mkv")

    def test_tilde_path_is_expanded_before_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            movie = Path(directory) / "-movie.mkv"
            movie.write_bytes(b"x")
            with patch.object(Path, "expanduser", return_value=movie) as expand:
                self.assertEqual(resolve_movie_path(Path("~/-movie.mkv")), movie)
            expand.assert_called_once()

    def test_hdr_detection_handles_pq_hlg_side_data_and_sdr(self):
        for stream, hdr in [
            ({"color_transfer": "smpte2084"}, True),
            ({"color_transfer": "arib-std-b67"}, True),
            ({"side_data_list": [{"side_data_type": "Mastering display metadata"}]}, True),
            ({"color_primaries": "bt2020", "pix_fmt": "yuv420p10le"}, True),
            ({"color_transfer": "bt709", "pix_fmt": "yuv420p"}, False),
        ]:
            with self.subTest(stream=stream):
                self.assertEqual(detect_hdr_from_file(Path("unused"), stream=stream)[0], hdr)

    def test_probe_errors_are_fatal_instead_of_silent_sdr(self):
        for reason in ["ffprobe failed: tool missing", "no video stream found"]:
            with patch.object(play, "detect_hdr_from_file", return_value=(False, reason)), self.assertRaisesRegex(RuntimeError, "Cannot determine"):
                play.probe_hdr(Path("unused"))

    def test_wayland_hint_keeps_player_option_delimiter(self):
        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}):
            self.assertEqual(normalize_mpv_args(["--", "/movie.mkv"]), ["--wayland-content-type=none", "--", "/movie.mkv"])

    def test_display_noop_performs_no_writes_or_waits(self):
        output = {"name": "HDMI-A-1", "currentModeId": "14", "hdr": False, "wcg": False}
        display = DisplayController("HDMI-A-1")
        with patch("lg_tv_automation.display.load_display_output", return_value=output), patch("lg_tv_automation.display.run_command") as command, patch.object(display, "_wait_for_state") as wait:
            display.apply_movie_state(enable_hdr=False)
        command.assert_not_called()
        wait.assert_not_called()

    def test_display_capture_is_reused_for_immediate_apply(self):
        display = DisplayController("HDMI-A-1")
        output = {"currentModeId": "14", "hdr": False, "wcg": False}
        with patch("lg_tv_automation.display.load_display_output", return_value=output) as load:
            display.capture()
            display.apply_movie_state(enable_hdr=False)
        load.assert_called_once()

    def test_failed_setup_restores_original_hdr_not_desktop_default(self):
        display = DisplayController("HDMI-A-1")
        display.saved_state = DisplaySnapshot("14", True, True)
        with patch.object(display, "_apply_state") as apply:
            display.restore()
        apply.assert_called_once_with(hdr=True, wcg=True, vrr_policy=None, mode_id="14")

    def test_corrupt_display_json_is_retried(self):
        output = {"name": "HDMI-A-1"}
        with patch("lg_tv_automation.display.run_command", side_effect=[SimpleNamespace(stdout="bad"), SimpleNamespace(stdout=json.dumps({"outputs": [output]}))]):
            self.assertEqual(load_display_output("HDMI-A-1", interval=0), output)

    def test_subprocess_timeout_is_actionable(self):
        import subprocess
        with patch("lg_tv_automation.process.subprocess.run", side_effect=subprocess.TimeoutExpired("test", 1)):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                run_command(["test"], timeout=1)


class TvTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_is_api_only_and_checks_live_signal(self):
        tv = TvController("tv", "HDMI_1")
        tv.client = SimpleNamespace(request=AsyncMock(side_effect=AssertionError("Unexpected UI request")))
        tv._ensure_input_active = AsyncMock()
        tv._get_input_info = AsyncMock(return_value={"label": "Blu-ray Player", "icon": "bluray.png"})
        tv._set_picture_mode = AsyncMock()
        tv._get_picture_setting = AsyncMock(return_value="cinemaClear")
        tv._apply_hidden_hdmi_state = AsyncMock()
        tv._ensure_hdmi_signal = AsyncMock(return_value=True)
        await tv.apply_profile(play.build_movie_profile(want_hdr=True))
        self.assertFalse(tv.failures)
        tv.client.request.assert_not_awaited()
        tv._ensure_hdmi_signal.assert_awaited_once()
        self.assertEqual(tv.verification["tru_motion"], "verified")
        self.assertEqual(tv.verification["hdmi_signal"], "verified")
        self.assertIn("unverified", tv.verification["game_optimizer_master"])

    async def test_short_deadline_bounds_slow_fetch(self):
        tv = TvController("tv", "HDMI_1")
        async def fetch():
            await asyncio.sleep(1)
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            await tv._wait_for_value(fetch, lambda _: False, "test", timeout=.01)

    async def test_already_active_input_needs_no_launch(self):
        tv = TvController("tv", "HDMI_1")
        tv.client = SimpleNamespace(get_current_app=AsyncMock(return_value=tv.input_app_id), launch_app=AsyncMock())
        await tv._ensure_input_active()
        tv.client.launch_app.assert_not_awaited()

    async def test_disconnect_runs_even_if_input_restore_fails(self):
        tv = TvController("tv", "HDMI_1")
        tv.client = SimpleNamespace(disconnect=AsyncMock())
        tv._ensure_input_active = AsyncMock(side_effect=RuntimeError("missing input"))
        with self.assertRaisesRegex(RuntimeError, "missing input"):
            await tv.__aexit__(None, None, None)
        tv.client.disconnect.assert_awaited_once()


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_after_fork_before_handle_return_still_reaps_child(self):
        create = asyncio.create_subprocess_exec
        started, release = asyncio.Event(), asyncio.Event()
        children = []
        async def spawn(*_, **kwargs):
            child = await create(sys.executable, "-c", "import time; time.sleep(30)")
            children.append(child)
            started.set()
            await release.wait()
            return child
        with patch.object(play.asyncio, "create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(play.run_mpv(["unused.mkv"]))
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                task.cancel()
                await asyncio.sleep(.01)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIsNotNone(children[0].returncode)

    async def test_spawn_failure_preserves_useful_error(self):
        with patch.object(play.asyncio, "create_subprocess_exec", new=AsyncMock(side_effect=FileNotFoundError("mpv missing"))), self.assertRaisesRegex(FileNotFoundError, "mpv missing"):
            await play.run_mpv(["unused.mkv"])
