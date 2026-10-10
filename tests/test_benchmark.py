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
