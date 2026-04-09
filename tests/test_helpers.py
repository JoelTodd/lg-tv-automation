"""Small pure-function tests for the refactored helpers."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lg_tv_automation.cli.play import append_debug_event, run_cleanup_step
from lg_tv_automation.constants import expected_input_app_id
from lg_tv_automation.display import DisplayController, select_mode_id
from lg_tv_automation.media import (
    choose_display_refresh_rate,
    choose_hdr_mode,
    detect_video_frame_rate,
    find_primary_media_path,
    normalize_mpv_args,
    resolve_probe_path,
)
from lg_tv_automation.models import DisplaySnapshot, HdmiFeatureState, TvProfile
from lg_tv_automation.tv import TvController
from lg_tv_automation.tv_ui import LgTvUiAutomation


class HelperTests(unittest.TestCase):
    def test_expected_input_app_id(self) -> None:
        self.assertEqual(expected_input_app_id("HDMI_1"), "com.webos.app.hdmi1")

    def test_normalize_mpv_args_strips_leading_separator(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(normalize_mpv_args(["--", "movie.mkv"]), ["movie.mkv"])

    def test_normalize_mpv_args_injects_wayland_content_type_none_by_default(self) -> None:
        with patch.dict(
            "os.environ",
            {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"},
            clear=False,
        ):
            self.assertEqual(
                normalize_mpv_args(["movie.mkv"]),
                ["--wayland-content-type=none", "movie.mkv"],
            )

    def test_normalize_mpv_args_preserves_explicit_wayland_content_type_override(self) -> None:
        with patch.dict(
            "os.environ",
            {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland"},
            clear=False,
        ):
            self.assertEqual(
                normalize_mpv_args(["--wayland-content-type=video", "movie.mkv"]),
                ["--wayland-content-type=video", "movie.mkv"],
            )

    def test_select_mode_id_picks_closest_refresh(self) -> None:
        output = {
            "modes": [
                {"id": 10, "size": {"width": 3840, "height": 2160}, "refreshRate": 119.88},
                {"id": 7, "size": {"width": 3840, "height": 2160}, "refreshRate": 60.00},
            ]
        }
        self.assertEqual(select_mode_id(output, 3840, 2160, 60.0), "7")
        self.assertEqual(select_mode_id(output, 3840, 2160, 120.0), "10")
        self.assertIsNone(select_mode_id(output, 2560, 1440, 60.0))

    def test_find_primary_media_path_ignores_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            media = Path(tmpdir) / "movie.mkv"
            media.write_text("placeholder")
            result = find_primary_media_path(["--no-audio", str(media)])
            self.assertEqual(result, media)

    def test_resolve_probe_path_prefers_largest_media_file_in_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir) / "feature"
            media_dir.mkdir()
            (media_dir / "poster.jpg").write_text("not video")
            sample = media_dir / "sample.mkv"
            sample.write_bytes(b"1" * 10)
            feature = media_dir / "movie.mkv"
            feature.write_bytes(b"1" * 100)

            self.assertEqual(resolve_probe_path(media_dir), feature)

    def test_choose_hdr_mode_probes_resolved_file_for_directory_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir) / "feature"
            media_dir.mkdir()
            feature = media_dir / "movie.mkv"
            feature.write_text("placeholder")

            with patch("lg_tv_automation.media.detect_hdr_from_file", return_value=(True, "transfer=smpte2084")) as detect:
                want_hdr, reason = choose_hdr_mode(
                    force_hdr=False,
                    force_sdr=False,
                    mpv_args=[str(media_dir)],
                )

            self.assertTrue(want_hdr)
            self.assertIn(str(media_dir), reason)
            self.assertIn(str(feature), reason)
            detect.assert_called_once_with(feature)

    def test_detect_video_frame_rate_parses_fractional_rate(self) -> None:
        payload = {"streams": [{"avg_frame_rate": "24000/1001", "r_frame_rate": "24000/1001"}]}
        with patch("lg_tv_automation.media.run_command", return_value=SimpleNamespace(stdout=json.dumps(payload))):
            frame_rate, reason = detect_video_frame_rate(Path("/tmp/movie.mkv"))

        self.assertAlmostEqual(frame_rate, 24000 / 1001, places=3)
        self.assertIn("23.976", reason)

    def test_choose_display_refresh_rate_uses_resolved_file_for_directory_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir) / "feature"
            media_dir.mkdir()
            feature = media_dir / "movie.mkv"
            feature.write_text("placeholder")

            with patch(
                "lg_tv_automation.media.detect_video_frame_rate",
                return_value=(24000 / 1001, "fps=23.976"),
            ) as detect:
                refresh, reason = choose_display_refresh_rate([str(media_dir)])

        self.assertAlmostEqual(refresh, 24000 / 1001, places=3)
        self.assertIn(str(media_dir), reason)
        self.assertIn(str(feature), reason)
        detect.assert_called_once_with(feature)

    def test_detect_master_on_uses_lowered_threshold(self) -> None:
        ui = object.__new__(LgTvUiAutomation)
        ui._saturated_pixel_count = lambda *_args, **_kwargs: 700
        self.assertTrue(ui.detect_master_on(None))
        ui._saturated_pixel_count = lambda *_args, **_kwargs: 600
        self.assertFalse(ui.detect_master_on(None))

    def test_apply_movie_state_forces_vrr_policy_never(self) -> None:
        controller = DisplayController("HDMI-A-1")
        output = {
            "name": "HDMI-A-1",
            "currentModeId": "10",
            "hdr": False,
            "wcg": False,
            "vrrPolicy": 2,
            "modes": [],
        }

        with (
            patch("lg_tv_automation.display.load_display_output", return_value=output),
            patch("lg_tv_automation.display.run_command") as run_command,
            patch.object(controller, "_wait_for_state", return_value=True) as wait_for_state,
        ):
            controller.apply_movie_state(enable_hdr=True, force_60hz=False, target_refresh=None, dry_run=False)

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.vrrpolicy.never",
                "output.HDMI-A-1.hdr.enable",
                "output.HDMI-A-1.wcg.enable",
            ]
        )
        wait_for_state.assert_called_once_with(None, True, True, 0)

    def test_apply_movie_state_matches_source_refresh_when_requested(self) -> None:
        controller = DisplayController("HDMI-A-1")
        output = {
            "name": "HDMI-A-1",
            "currentModeId": "10",
            "hdr": False,
            "wcg": False,
            "vrrPolicy": 2,
            "modes": [
                {"id": 10, "size": {"width": 3840, "height": 2160}, "refreshRate": 119.88},
                {"id": 16, "size": {"width": 3840, "height": 2160}, "refreshRate": 23.976},
            ],
        }

        with (
            patch("lg_tv_automation.display.load_display_output", return_value=output),
            patch("lg_tv_automation.display.run_command") as run_command,
            patch.object(controller, "_wait_for_state", return_value=True) as wait_for_state,
        ):
            controller.apply_movie_state(
                enable_hdr=True,
                force_60hz=False,
                target_refresh=24000 / 1001,
                dry_run=False,
            )

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.mode.16",
                "output.HDMI-A-1.vrrpolicy.never",
                "output.HDMI-A-1.hdr.enable",
                "output.HDMI-A-1.wcg.enable",
            ]
        )
        wait_for_state.assert_called_once_with("16", True, True, 0)

    def test_apply_desktop_state_restores_saved_vrr_policy(self) -> None:
        controller = DisplayController("HDMI-A-1")
        controller.saved_state = DisplaySnapshot(mode_id="10", hdr=True, wcg=True, vrr_policy=2)
        output = {
            "name": "HDMI-A-1",
            "currentModeId": "10",
            "hdr": True,
            "wcg": True,
            "vrrPolicy": 0,
            "modes": [],
        }

        with (
            patch("lg_tv_automation.display.load_display_output", return_value=output),
            patch("lg_tv_automation.display.run_command") as run_command,
            patch.object(controller, "_wait_for_state", return_value=True) as wait_for_state,
        ):
            controller.apply_desktop_state(dry_run=False)

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.vrrpolicy.automatic",
                "output.HDMI-A-1.hdr.disable",
                "output.HDMI-A-1.wcg.disable",
            ]
        )
        wait_for_state.assert_called_once_with(None, False, False, 2)

    def test_append_debug_event_writes_timestamped_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "debug.jsonl"
            append_debug_event(path, "after_apply", {"display": {"hdr": True}})

            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["event"], "after_apply")
            self.assertEqual(payload["payload"], {"display": {"hdr": True}})
            self.assertIn("timestamp", payload)


class AsyncHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_tv_connect_timeout_mentions_pairing_prompt(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        fake_client = SimpleNamespace(connect=AsyncMock(), disconnect=AsyncMock())

        async def timeout_wait_for(awaitable, timeout):
            _ = timeout
            awaitable.close()
            raise TimeoutError

        with patch("lg_tv_automation.tv.WebOsClient.create", new=AsyncMock(return_value=fake_client)):
            with patch("lg_tv_automation.tv.asyncio.wait_for", new=timeout_wait_for):
                with self.assertRaisesRegex(RuntimeError, "pairing prompt"):
                    await controller.__aenter__()

    async def test_hidden_setting_timeout_raises_readable_error(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.client = SimpleNamespace(request=AsyncMock())

        async def timeout_wait_for(awaitable, timeout):
            _ = timeout
            awaitable.close()
            raise TimeoutError

        with patch("lg_tv_automation.tv.asyncio.wait_for", new=timeout_wait_for):
            with self.assertRaisesRegex(RuntimeError, "create hidden settings alert"):
                await controller._alert_luna("luna://example", {"x": 1})

    async def test_apply_profile_times_out_trumotion_write_cleanly(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.client = SimpleNamespace(
            set_system_picture_mode=AsyncMock(),
            set_current_picture_mode=AsyncMock(),
            set_picture_settings=AsyncMock(),
            set_settings=AsyncMock(),
        )
        controller.ui = SimpleNamespace()
        controller._ensure_input_active = AsyncMock()
        controller._get_input_info = AsyncMock(return_value={"label": "HDMI 1", "icon": "HDMI_1.png"})
        controller._get_picture_mode = AsyncMock(side_effect=["hdrFilmMaker", "hdrFilmMaker"])
        controller._apply_hidden_hdmi_state = AsyncMock()

        async def request_timeout(awaitable, description, *, timeout=5.0):
            _ = timeout
            if "truMotionMode" in description:
                awaitable.close()
                raise RuntimeError("Timed out waiting for TV response while trying to set truMotionMode to cinemaClear.")
            return await awaitable

        profile = TvProfile(
            label="HDMI 1",
            icon="HDMI_1",
            picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            hdmi_features=HdmiFeatureState(
                passthrough_444=False,
                game_optimizer_master=False,
                vrr=False,
                allm=False,
            ),
        )

        with (
            patch.object(controller, "_request_with_timeout", side_effect=request_timeout),
            patch("lg_tv_automation.tv.asyncio.sleep", new=AsyncMock()),
        ):
            await controller.apply_profile(profile, dry_run=False)

        self.assertTrue(any("truMotion change failed" in failure for failure in controller.failures))

    async def test_run_cleanup_step_retries_after_cancellation(self) -> None:
        calls = 0

        async def operation() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                asyncio.current_task().cancel()
            await asyncio.sleep(0)

        interrupted = await run_cleanup_step(operation, "cleanup")

        self.assertTrue(interrupted)
        self.assertEqual(calls, 2)

    async def test_apply_profile_does_not_use_ui_fallback_in_main_flow(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.client = SimpleNamespace(
            set_system_picture_mode=AsyncMock(),
            set_current_picture_mode=AsyncMock(),
            set_picture_settings=AsyncMock(),
        )
        controller._ensure_input_active = AsyncMock()
        controller._get_input_info = AsyncMock(
            return_value={"label": "HDMI 1", "icon": "HDMI_1.png"}
        )
        controller._get_picture_mode = AsyncMock(return_value="hdrFilmMaker")
        controller._apply_hidden_hdmi_state = AsyncMock(side_effect=RuntimeError("hidden write failed"))

        profile = TvProfile(
            label="HDMI 1",
            icon="HDMI_1",
            picture_mode="hdrFilmMaker",
            tru_motion=None,
            hdmi_features=HdmiFeatureState(
                passthrough_444=False,
                game_optimizer_master=False,
                vrr=False,
                allm=False,
            ),
        )

        with patch("lg_tv_automation.tv.asyncio.sleep", new=AsyncMock()):
            await controller.apply_profile(profile, dry_run=False)

    async def test_apply_hidden_hdmi_state_hard_disables_allm_auto_game_path(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller._set_hidden_other_settings = AsyncMock()

        with patch("lg_tv_automation.tv.asyncio.sleep", new=AsyncMock()):
            await controller._apply_hidden_hdmi_state(
                HdmiFeatureState(
                    passthrough_444=False,
                    game_optimizer_master=False,
                    vrr=False,
                    allm=False,
                )
            )

        calls = controller._set_hidden_other_settings.await_args_list
        self.assertEqual(
            calls[0].args[0],
            {"gameOptimization": "off", "gameOptimizationHDMI1": "off"},
        )
        self.assertEqual(
            calls[1].args[0],
            {"enableALLM": "off", "inputOptimization": "off", "enableQuickGame": "off"},
        )
        self.assertEqual(calls[2].args[0], {"gameMode": {"hdmi1": "off"}})
        self.assertEqual(calls[3].args[0], {"444BypassHDMI1": "off"})

    async def test_apply_hidden_hdmi_state_enables_allm_auto_game_path_for_desktop(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller._set_hidden_other_settings = AsyncMock()

        with patch("lg_tv_automation.tv.asyncio.sleep", new=AsyncMock()):
            await controller._apply_hidden_hdmi_state(
                HdmiFeatureState(
                    passthrough_444=True,
                    game_optimizer_master=True,
                    vrr=True,
                    allm=True,
                )
            )

        calls = controller._set_hidden_other_settings.await_args_list
        self.assertEqual(calls[0].args[0], {"gameMode": {"hdmi1": "on"}})
        self.assertEqual(
            calls[1].args[0],
            {"gameOptimization": "on", "gameOptimizationHDMI1": "on"},
        )
        self.assertEqual(
            calls[2].args[0],
            {"enableALLM": "on", "inputOptimization": "on", "enableQuickGame": "on"},
        )
        self.assertEqual(calls[3].args[0], {"444BypassHDMI1": "on"})


if __name__ == "__main__":
    unittest.main()
