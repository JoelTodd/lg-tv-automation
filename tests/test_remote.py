"""Hardware-free tests for the agent remote transport, not UI heuristics."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from lg_tv_automation import remote, session
from lg_tv_automation.tv import TvController


class RequestTests(unittest.TestCase):
    def test_rejects_unsafe_or_malformed_requests(self):
        invalid = [
            None, [], {}, {"op": []}, {"op": "request", "uri": "settings/set"},
            {"op": "button", "key": "POWER"}, {"op": "button", "key": []},
            {"op": "button", "key": "ENTER", "count": 5},
            {"op": "launch", "app": ""}, {"op": "launch", "app": "has spaces"},
            {"op": "launch", "app": []}, {"op": "observe", "settle": -1},
            {"op": "observe", "settle": 4}, {"op": "observe", "settle": True},
            {"op": "observe", "settle": float("nan")},
            {"op": "observe", "settle": float("inf")},
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                remote.validate_request(request)

    def test_default_delays_and_single_actions(self):
        self.assertEqual(remote.validate_request({"op": "observe"})["settle"], 0)
        self.assertEqual(remote.validate_request({"op": "button", "key": "DOWN"})["settle"], .35)
        self.assertEqual(remote.validate_request({"op": "launch", "app": "com.webos.app.gameoptimizer"})["settle"], .8)

    def test_untrusted_screenshot_urls_are_not_fetched(self):
        for uri in ["file:///etc/passwd", "http://other-host/shot", "https://user:secret@tv/shot"]:
            with self.subTest(uri=uri), patch.object(remote.urllib.request, "build_opener") as opener:
                with self.assertRaises(RuntimeError):
                    remote.download_image(uri, Path("unused.png"), "tv")
                opener.assert_not_called()

    def test_preserves_native_screenshot_resolution_and_private_permissions(self):
        import tempfile
        buffer = io.BytesIO()
        Image.new("RGB", (1920, 1080)).save(buffer, format="JPEG")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            opener = Mock()
            opener.open.return_value = contextlib.closing(io.BytesIO(buffer.getvalue()))
            with patch.object(remote.urllib.request, "build_opener", return_value=opener):
                self.assertEqual(remote.download_image("https://tv/screen", path, "tv"), (1920, 1080))
            with Image.open(path) as shot:
                self.assertEqual(shot.size, (1920, 1080))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_screenshot_size_limit(self):
        opener = Mock()
        opener.open.return_value = contextlib.closing(io.BytesIO(b"x" * 33))
        with patch.object(remote.urllib.request, "build_opener", return_value=opener), patch.object(remote, "MAX_IMAGE_BYTES", 32):
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                remote.download_image("http://tv/screen", Path("unused.png"), "tv")


    def test_trickling_screenshot_has_a_total_deadline(self):
        opener = Mock()
        opener.open.return_value = contextlib.closing(io.BytesIO(b"x" * 32))
        with patch.object(remote.urllib.request, "build_opener", return_value=opener), patch.object(remote.time, "monotonic", side_effect=[0, 6]):
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                remote.download_image("http://tv/screen", Path("unused.png"), "tv")


class RemoteTests(unittest.IsolatedAsyncioTestCase):
    def make_tv(self):
        tv = TvController("tv", "HDMI_1")
        tv.client = SimpleNamespace(
            button=AsyncMock(return_value=True),
            launch_app=AsyncMock(return_value=True),
            get_current_app=AsyncMock(return_value=tv.input_app_id),
            request=AsyncMock(return_value={"imageUri": "http://tv/screen"}),
        )
        tv.__aenter__ = AsyncMock(return_value=tv)
        tv.__aexit__ = AsyncMock()
        return tv

    async def test_action_then_observation_and_no_heuristics(self):
        tv = self.make_tv()
        session = remote.RemoteSession(tv, Path("/private/artifacts"))
        session.observe = AsyncMock(return_value={"screenshot": "/private/artifacts/1.png"})
        result = await session.execute(remote.validate_request({"op": "button", "key": "DOWN", "settle": 0}))
        self.assertTrue(result["ok"])
        self.assertTrue(result["action_applied"])
        tv.client.button.assert_awaited_once_with("DOWN")
        session.observe.assert_awaited_once()
        self.assertFalse(hasattr(tv, "ui"))

    async def test_observe_never_sends_a_button_or_launch(self):
        tv = self.make_tv()
        session = remote.RemoteSession(tv, Path("/private/artifacts"))
        with patch.object(remote, "download_image", return_value=(1920, 1080)) as download:
            result = await session.execute(remote.validate_request({"op": "observe"}))
        self.assertFalse(result["action_applied"])
        self.assertEqual(result["width"], 1920)
        self.assertEqual(result["sequence"], 1)
        tv.client.button.assert_not_awaited()
        tv.client.launch_app.assert_not_awaited()
        download.assert_called_once_with("http://tv/screen", Path("/private/artifacts/0001.png"), "tv")

    async def test_failed_capture_does_not_repeat_successful_action(self):
        tv = self.make_tv()
        session = remote.RemoteSession(tv, Path("/unused"))
        session.observe = AsyncMock(side_effect=RuntimeError("capture unavailable"))
        result = await session.execute(remote.validate_request({"op": "button", "key": "ENTER", "settle": 0}))
        self.assertFalse(result["ok"])
        self.assertIs(result["action_applied"], True)
        tv.client.button.assert_awaited_once_with("ENTER")

    async def test_failed_transport_marks_action_unknown_not_safe_to_repeat(self):
        tv = self.make_tv()
        tv.client.button.side_effect = RuntimeError("response lost")
        session = remote.RemoteSession(tv, Path("/unused"))
        session.observe = AsyncMock()
        result = await session.execute(remote.validate_request({"op": "button", "key": "ENTER", "settle": 0}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["action_applied"], "unknown")
        tv.client.button.assert_awaited_once()
        session.observe.assert_not_awaited()

    async def run_mock_session(self, reader, tv):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch.dict(remote.os.environ, {"XDG_STATE_HOME": directory}), patch.object(remote, "require_tv_ip", return_value="tv"), patch.object(remote, "TvController", return_value=tv), patch.object(remote.RemoteSession, "observe", new=AsyncMock(return_value={"screenshot": "/unused.png"})):
            return await remote.run_session(argparse.Namespace(input="HDMI_1", idle_timeout=.01), reader)

    async def test_quit_and_eof_close_overlay_disconnect_and_report_return(self):
        for lines in [b'{"op":"quit"}\n', b'']:
            with self.subTest(lines=lines):
                reader = asyncio.StreamReader()
                reader.feed_data(lines)
                reader.feed_eof()
                tv = self.make_tv()
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(await self.run_mock_session(reader, tv), 0)
                tv.client.button.assert_awaited_once_with("EXIT")
                tv.__aexit__.assert_awaited_once()
                events = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(events[-1]["event"], "closed")

    async def test_idle_timeout_cleans_up(self):
        reader = asyncio.StreamReader()
        tv = self.make_tv()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            await self.run_mock_session(reader, tv)
        self.assertIn('"event": "idle_timeout"', output.getvalue())
        tv.__aexit__.assert_awaited_once()

    async def test_invalid_json_and_requests_are_rejected_before_actions(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'not json\n{"op":"button","key":"POWER"}\n{"op":"quit"}\n')
        reader.feed_eof()
        tv = self.make_tv()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            await self.run_mock_session(reader, tv)
        # Only cleanup sends a button; no malformed input reaches the TV.
        tv.client.button.assert_awaited_once_with("EXIT")
        self.assertEqual(sum(not json.loads(line)["ok"] for line in output.getvalue().splitlines()), 2)

    async def test_disconnect_even_if_exit_button_fails(self):
        reader = asyncio.StreamReader()
        reader.feed_eof()
        tv = self.make_tv()
        tv.client.button.side_effect = RuntimeError("exit failed")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "exit failed"):
            await self.run_mock_session(reader, tv)
        tv.__aexit__.assert_awaited_once()

    async def test_cancellation_during_action_still_cleans_up(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"op":"button","key":"DOWN","settle":0}\n')
        tv = self.make_tv()
        started = asyncio.Event()
        async def button(key):
            if key == "DOWN":
                started.set()
                await asyncio.Future()
        tv.client.button.side_effect = button
        with contextlib.redirect_stdout(io.StringIO()):
            task = asyncio.create_task(self.run_mock_session(reader, tv))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(tv.client.button.await_args_list[-1].args, ("EXIT",))
        tv.__aexit__.assert_awaited_once()

    async def test_exclusive_session_lock_is_the_playback_lock(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory, patch.dict(remote.os.environ, {"XDG_RUNTIME_DIR": directory}), patch.object(remote.sys, "stdin", SimpleNamespace(buffer=Mock())):
            loop = asyncio.get_running_loop()
            transport = Mock()
            with session.session_lock(), patch.object(loop, "connect_read_pipe", new=AsyncMock(return_value=(transport, Mock()))), patch.object(remote, "run_session", new=AsyncMock()) as run:
                with self.assertRaisesRegex(RuntimeError, "already controlling"):
                    await remote.async_main(argparse.Namespace())
            run.assert_not_awaited()
            transport.close.assert_called_once()
