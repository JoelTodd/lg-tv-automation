"""Ensure failed or incomplete measurements cannot look like a passing run."""

import importlib.util
import asyncio
import sys
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

spec = importlib.util.spec_from_file_location("benchmark_playback", Path(__file__).resolve().parents[1] / "scripts/benchmark-playback.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkTests(unittest.TestCase):
    def test_ctrl_c_exits_130_without_a_traceback(self):
        def interrupted_run(coroutine):
            coroutine.close()
            raise KeyboardInterrupt
        with patch.object(sys, "argv", ["benchmark-playback.py", "unused.mkv"]), patch.object(benchmark.asyncio, "run", side_effect=interrupted_run), self.assertRaises(SystemExit) as error:
            benchmark.main()
        self.assertEqual(error.exception.code, 130)

    def test_missing_readiness_is_not_success(self):
        self.assertIn("readiness", benchmark.measurement_error({"frame_drop_count": 0, "decoder_frame_drop_count": 0}))

    def test_unavailable_drop_counters_are_not_zero(self):
        measurements = {}
        benchmark.record_event(measurements, {"event": "playback-restart"}, 2)
        benchmark.record_event(measurements, {"event": "property-change", "name": "frame-drop-count", "data": None}, 3)
        self.assertIn("unavailable", benchmark.measurement_error(measurements))

    def test_restart_and_counter_resets_keep_first_ready_and_maximum_drops(self):
        measurements = {}
        for elapsed in (2, 4):
            benchmark.record_event(measurements, {"event": "playback-restart"}, elapsed)
        for value in (0, 3, 1):
            benchmark.record_event(measurements, {"event": "property-change", "name": "frame-drop-count", "data": value}, 5)
        benchmark.record_event(measurements, {"event": "property-change", "name": "decoder-frame-drop-count", "data": 0}, 5)
        self.assertEqual(measurements["playback_ready_seconds"], 2)
        self.assertEqual(measurements["frame_drop_count"], 3)
        self.assertIsNone(benchmark.measurement_error(measurements))

    def test_records_actual_decoder_and_color_parameters(self):
        measurements = {}
        for name, value in (("hwdec-current", "nvdec"), ("video-params/gamma", "pq"), ("video-params/w", 3840)):
            benchmark.record_event(measurements, {"event": "property-change", "name": name, "data": value}, 2)
        self.assertEqual(measurements, {"hwdec_current": "nvdec", "video_params_gamma": "pq", "video_params_w": 3840})


class BenchmarkCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_during_spawn_does_not_leave_wrapper_running(self):
        create = asyncio.create_subprocess_exec
        started, release = asyncio.Event(), asyncio.Event()
        children = []
        async def spawn(*args, **kwargs):
            child = await create(sys.executable, "-c", "import time; time.sleep(30)", start_new_session=True)
            children.append(child)
            started.set()
            await release.wait()
            return child
        with patch.object(benchmark.asyncio, "create_subprocess_exec", side_effect=spawn):
            task = asyncio.create_task(benchmark.benchmark("unused.mkv"))
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=1)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
        self.assertIsNotNone(children[0].returncode)

    async def test_finished_wrapper_is_not_signalled(self):
        proc = SimpleNamespace(returncode=0, terminate=Mock())
        await benchmark.stop_wrapper(proc)
        proc.terminate.assert_not_called()

    async def test_graceful_stop_waits_for_restoration(self):
        proc = SimpleNamespace(returncode=None, terminate=Mock(), wait=AsyncMock(return_value=0), pid=123)
        with patch.object(benchmark.os, "killpg") as kill:
            await benchmark.stop_wrapper(proc)
        proc.terminate.assert_called_once()
        proc.wait.assert_awaited_once()
        kill.assert_not_called()

    async def test_stuck_wrapper_group_is_killed_and_reaped(self):
        proc = SimpleNamespace(returncode=None, terminate=Mock(), wait=AsyncMock(side_effect=[TimeoutError, -9]), pid=123)
        with patch.object(benchmark.os, "killpg") as kill:
            await benchmark.stop_wrapper(proc, timeout=.01)
        kill.assert_called_once_with(123, benchmark.signal.SIGKILL)
        self.assertEqual(proc.wait.await_count, 2)
