"""Small pure-function tests for the refactored helpers."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lg_tv_automation.cli import edid as edid_cli
from lg_tv_automation.cli.play import append_debug_event, async_main, print_status, run_cleanup_step
from lg_tv_automation.constants import expected_input_app_id
from lg_tv_automation.display import DisplayController, select_mode_id
from lg_tv_automation.media import (
    choose_display_refresh_rate,
    choose_hdr_mode,
    detect_video_frame_rate,
    find_primary_media_path,
    normalize_mpv_args,
    resolve_primary_media_arg,
    resolve_probe_path,
)
from lg_tv_automation.models import DisplaySnapshot, HdmiFeatureState, TvProfile
from lg_tv_automation.tv import TvController
from lg_tv_automation.tv_ui import LgTvUiAutomation


class HelperTests(unittest.TestCase):
    def test_expected_input_app_id(self) -> None:
        self.assertEqual(expected_input_app_id("HDMI_1"), "com.webos.app.hdmi1")

    def test_edid_packaged_asset_matches_expected_checksum(self) -> None:
        data = edid_cli.package_edid_bytes()

        self.assertEqual(edid_cli.sha256_bytes(data), edid_cli.PATCHED_SHA256)

    def test_edid_status_verifies_active_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            firmware_root = root / "firmware"
            installed = firmware_root / edid_cli.firmware_relative_path()
            installed.parent.mkdir(parents=True)
            installed.write_bytes(edid_cli.package_edid_bytes())

            proc_cmdline = root / "cmdline"
            proc_cmdline.write_text(f"quiet {edid_cli.kernel_arg()} rhgb", encoding="utf-8")

            sysfs_drm = root / "drm"
            live = sysfs_drm / "card1-HDMI-A-1"
            live.mkdir(parents=True)
            (live / "edid").write_bytes(edid_cli.package_edid_bytes())

            with patch(
                "lg_tv_automation.cli.edid.kscreen_output",
                return_value={"currentModeId": "14", "hdr": True, "wcg": True, "vrrPolicy": None},
            ):
                status = edid_cli.collect_status(
                    firmware_root=firmware_root,
                    proc_cmdline=proc_cmdline,
                    sysfs_drm=sysfs_drm,
                )

        self.assertEqual(status["installed"]["sha256"], edid_cli.PATCHED_SHA256)
        self.assertEqual(status["runtime"]["live_edid_sha256"], edid_cli.PATCHED_SHA256)
        self.assertTrue(status["runtime"]["cmdline_has_kernel_arg"])
        self.assertEqual(status["kscreen"]["vrrPolicy"], None)
        self.assertEqual(edid_cli.verification_failures(status), [])

    def test_edid_status_reports_live_edid_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            firmware_root = root / "firmware"
            installed = firmware_root / edid_cli.firmware_relative_path()
            installed.parent.mkdir(parents=True)
            installed.write_bytes(edid_cli.package_edid_bytes())

            proc_cmdline = root / "cmdline"
            proc_cmdline.write_text(edid_cli.kernel_arg(), encoding="utf-8")

            sysfs_drm = root / "drm"
            live = sysfs_drm / "card1-HDMI-A-1"
            live.mkdir(parents=True)
            (live / "edid").write_bytes(edid_cli.package_edid_bytes(edid_cli.ORIGINAL_EDID))

            status = edid_cli.collect_status(
                firmware_root=firmware_root,
                proc_cmdline=proc_cmdline,
                sysfs_drm=sysfs_drm,
                include_kscreen=False,
            )

        self.assertIn("live DRM EDID does not match", "\n".join(edid_cli.verification_failures(status)))

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

    def test_resolve_primary_media_arg_replaces_directory_with_primary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir) / "feature"
            media_dir.mkdir()
            sample = media_dir / "sample.mkv"
            sample.write_bytes(b"1" * 10)
            feature = media_dir / "movie.mkv"
            feature.write_bytes(b"1" * 100)

            resolved_args, reason = resolve_primary_media_arg(["--length=10", str(media_dir)])

        self.assertEqual(resolved_args, ["--length=10", str(feature)])
        self.assertIn(str(media_dir), reason)
        self.assertIn(str(feature), reason)

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

    def test_detect_game_optimizer_page_uses_header_threshold(self) -> None:
        ui = object.__new__(LgTvUiAutomation)
        ui._saturated_pixel_count = lambda *_args, **_kwargs: 6000
        self.assertTrue(ui.detect_game_optimizer_page(None))
        ui._saturated_pixel_count = lambda *_args, **_kwargs: 4000
        self.assertFalse(ui.detect_game_optimizer_page(None))

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
            controller.apply_movie_state(
                enable_hdr=True,
                force_60hz=False,
                target_refresh=None,
                dry_run=False,
            )

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.vrrpolicy.never",
                "output.HDMI-A-1.hdr.enable",
                "output.HDMI-A-1.wcg.enable",
            ]
        )
        wait_for_state.assert_called_once_with(None, True, True, 0)

    def test_apply_movie_state_accepts_vrr_incapable_sink(self) -> None:
        controller = DisplayController("HDMI-A-1")
        output = {
            "name": "HDMI-A-1",
            "currentModeId": "10",
            "hdr": False,
            "wcg": False,
            "vrrPolicy": None,
            "modes": [],
        }

        with (
            patch("lg_tv_automation.display.load_display_output", return_value=output),
            patch("lg_tv_automation.display.run_command") as run_command,
            patch.object(controller, "_wait_for_state", return_value=True) as wait_for_state,
        ):
            controller.apply_movie_state(
                enable_hdr=True,
                force_60hz=False,
                target_refresh=None,
                dry_run=False,
            )

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.hdr.enable",
                "output.HDMI-A-1.wcg.enable",
            ]
        )
        wait_for_state.assert_called_once_with(None, True, True, None)

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

    def test_apply_desktop_state_skips_vrr_policy_for_vrr_incapable_sink(self) -> None:
        controller = DisplayController("HDMI-A-1")
        output = {
            "name": "HDMI-A-1",
            "currentModeId": "10",
            "hdr": True,
            "wcg": True,
            "vrrPolicy": None,
            "modes": [
                {"id": 10, "size": {"width": 3840, "height": 2160}, "refreshRate": 119.88},
            ],
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
                "output.HDMI-A-1.hdr.disable",
                "output.HDMI-A-1.wcg.disable",
            ]
        )
        wait_for_state.assert_called_once_with(None, False, False, None)

    def test_apply_desktop_state_falls_back_to_highest_refresh_and_automatic_vrr(self) -> None:
        controller = DisplayController("HDMI-A-1")
        output = {
            "name": "HDMI-A-1",
            "currentModeId": "16",
            "hdr": True,
            "wcg": True,
            "vrrPolicy": 0,
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
            controller.apply_desktop_state(dry_run=False)

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.mode.10",
                "output.HDMI-A-1.vrrpolicy.automatic",
                "output.HDMI-A-1.hdr.disable",
                "output.HDMI-A-1.wcg.disable",
            ]
        )
        wait_for_state.assert_called_once_with("10", False, False, 2)

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
    async def test_print_status_emits_single_valid_json_document(self) -> None:
        args = SimpleNamespace(
            display_output="HDMI-A-1",
            no_tv=False,
            no_tv_ui=True,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
        )
        class FakeTvContext:
            def __init__(self) -> None:
                self.capture = AsyncMock(return_value={"picture_mode": "expert1"})

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

        fake_tv = FakeTvContext()
        stdout = io.StringIO()

        with (
            patch(
                "lg_tv_automation.cli.play.load_display_output",
                return_value={
                    "name": "HDMI-A-1",
                    "currentModeId": "10",
                    "hdr": False,
                    "wcg": False,
                    "vrrPolicy": 0,
                },
            ),
            patch("lg_tv_automation.cli.play.TvController", return_value=fake_tv),
            contextlib.redirect_stdout(stdout),
        ):
            result = await print_status(args)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["display"]["currentModeId"], "10")
        self.assertEqual(payload["tv"]["picture_mode"], "expert1")

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

    async def test_apply_profile_reapplies_hdmi_state_after_picture_writes(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.client = SimpleNamespace(
            set_picture_settings=AsyncMock(),
        )
        events: list[str] = []

        async def apply_hidden_state(_state: HdmiFeatureState) -> None:
            events.append("hdmi")

        async def set_picture_mode(_picture_mode: str) -> None:
            events.append("picture")

        async def request_with_timeout(awaitable, description, *, timeout=5.0):
            _ = timeout
            if "truMotionMode" in description:
                events.append("trumotion")
            return await awaitable

        controller._ensure_input_active = AsyncMock()
        controller._get_input_info = AsyncMock(return_value={"label": "HDMI 1", "icon": "HDMI_1.png"})
        controller._apply_hidden_hdmi_state = AsyncMock(side_effect=apply_hidden_state)
        controller._set_picture_mode = AsyncMock(side_effect=set_picture_mode)

        profile = TvProfile(
            label="HDMI 1",
            icon="HDMI_1",
            picture_mode="hdrFilmMaker",
            tru_motion="off",
            hdmi_features=HdmiFeatureState(
                passthrough_444=False,
                game_optimizer_master=False,
                vrr=False,
                allm=False,
            ),
        )

        with (
            patch.object(controller, "_request_with_timeout", side_effect=request_with_timeout),
            patch("lg_tv_automation.tv.asyncio.sleep", new=AsyncMock()),
        ):
            await controller.apply_profile(profile, dry_run=False)

        self.assertEqual(events, ["hdmi", "picture", "trumotion", "hdmi"])
        self.assertEqual(controller._apply_hidden_hdmi_state.await_count, 2)
        self.assertFalse(controller.failures)

    async def test_status_falls_back_to_ui_error_when_ui_capture_times_out(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.ui = SimpleNamespace(
            capture_hdmi_settings_state=AsyncMock(),
            capture_game_optimizer_state=AsyncMock(
                return_value={"game_optimizer_master": True, "vrr": True, "allm": True}
            ),
        )
        controller._get_input_info = AsyncMock(return_value={"label": "HDMI 1", "icon": "HDMI_1.png"})
        controller._get_picture_mode = AsyncMock(return_value="expert1")

        async def fake_request_with_timeout(awaitable, description, *, timeout=5.0):
            _ = timeout
            if "capture HDMI settings UI state" in description:
                awaitable.close()
                raise RuntimeError("ui timeout")
            return await awaitable

        controller.client = SimpleNamespace(get_current_app=AsyncMock(return_value="com.webos.app.hdmi1"))

        with patch.object(controller, "_request_with_timeout", side_effect=fake_request_with_timeout):
            payload = await controller.status()

        self.assertEqual(payload["picture_mode"], "expert1")
        self.assertEqual(payload["ui_error"], "HDMI settings: ui timeout")

    async def test_open_game_optimizer_master_uses_double_right_after_overlay_appears(self) -> None:
        client = SimpleNamespace(launch_app=AsyncMock())
        ui = LgTvUiAutomation(client, "com.webos.app.hdmi1")
        ui.ensure_input_active = AsyncMock()
        ui._wait_for_game_optimizer_page = AsyncMock(side_effect=["launch", "master"])
        ui._press = AsyncMock()

        result = await ui.open_game_optimizer_master()

        self.assertEqual(result, "master")
        client.launch_app.assert_awaited_once_with("com.webos.app.gameoptimizer")
        self.assertEqual(
            ui._press.await_args_list,
            [
                unittest.mock.call("RIGHT", 0.4),
                unittest.mock.call("RIGHT", 0.4),
            ],
        )

    async def test_capture_game_optimizer_state_uses_expected_row_offsets(self) -> None:
        client = SimpleNamespace()
        ui = LgTvUiAutomation(client, "com.webos.app.hdmi1")
        ui.open_game_optimizer_master = AsyncMock(return_value="master-image")
        ui.detect_master_on = lambda _image: True
        ui._press_many = AsyncMock()
        ui._capture_image = AsyncMock(side_effect=["vrr-image", "allm-image"])
        ui.detect_row_toggle_on = lambda image: image == "vrr-image"
        ui.close_overlay = AsyncMock()

        result = await ui.capture_game_optimizer_state()

        self.assertEqual(
            ui._press_many.await_args_list,
            [
                unittest.mock.call("DOWN", 6, 0.2),
                unittest.mock.call("DOWN", 3, 0.2),
            ],
        )
        self.assertEqual(
            result,
            {"game_optimizer_master": True, "vrr": True, "allm": False},
        )

    async def test_capture_hdmi_settings_state_saves_final_artifact(self) -> None:
        client = SimpleNamespace()
        ui = LgTvUiAutomation(client, "com.webos.app.hdmi1")
        image = Image.new("RGB", (8, 8), "white")
        ui.open_hdmi_settings = AsyncMock(return_value=image)
        ui.detect_hdmi_settings_page = lambda _image: True
        ui.detect_444_on = lambda _image: True
        ui.close_overlay = AsyncMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = await ui.capture_hdmi_settings_state(artifact_dir=Path(tmpdir))

            self.assertTrue((Path(tmpdir) / "hdmi-settings-final.png").exists())

        self.assertEqual(result, {"passthrough_444": True})

    async def test_capture_raises_when_exact_ui_state_is_required(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.client = SimpleNamespace(get_current_app=AsyncMock(return_value="com.webos.app.hdmi1"))
        controller._get_input_info = AsyncMock(
            return_value={"label": "HDMI 1", "icon": "HDMI_1.png", "appId": "com.webos.app.hdmi1"}
        )
        controller._get_picture_mode = AsyncMock(return_value="expert1")
        controller._capture_ui_hdmi_feature_state = AsyncMock(side_effect=RuntimeError("ui timeout"))

        with self.assertRaisesRegex(RuntimeError, "exact restore is unavailable"):
            await controller.capture(include_ui_state=True, require_ui_state=True)

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

    async def test_apply_profile_retries_picture_mode_with_legacy_path(self) -> None:
        controller = TvController("192.168.1.134", "HDMI_1")
        controller.client = SimpleNamespace(
            set_system_picture_mode=AsyncMock(return_value=None),
            set_current_picture_mode=AsyncMock(return_value=None),
            set_picture_settings=AsyncMock(return_value=None),
        )
        controller._ensure_input_active = AsyncMock()
        controller._get_input_info = AsyncMock(return_value={"label": "HDMI 1", "icon": "HDMI_1.png"})
        controller._get_picture_mode = AsyncMock(side_effect=["expert1", "expert1", "expert1", "hdrFilmMaker"])
        controller._apply_hidden_hdmi_state = AsyncMock()

        profile = TvProfile(
            label="HDMI 1",
            icon="HDMI_1",
            picture_mode="hdrFilmMaker",
            tru_motion=None,
            hdmi_features=None,
        )

        async def fake_wait_for_value(fetcher, predicate, description, timeout=4.0, interval=0.25):
            _ = description
            _ = timeout
            _ = interval
            value = await fetcher()
            if predicate(value):
                return value
            value = await fetcher()
            if predicate(value):
                return value
            raise RuntimeError(f"picture mode verification failed; last value was {value!r}")

        with (
            patch.object(controller, "_wait_for_value", side_effect=fake_wait_for_value),
            patch("lg_tv_automation.tv.asyncio.sleep", new=AsyncMock()),
        ):
            await controller.apply_profile(profile, dry_run=False)

        controller.client.set_system_picture_mode.assert_awaited_once_with("hdrFilmMaker")
        controller.client.set_current_picture_mode.assert_awaited_once_with("hdrFilmMaker")
        self.assertFalse(controller.failures)

    async def test_async_main_aborts_when_display_setup_fails(self) -> None:
        args = SimpleNamespace(
            status=False,
            movie_mode=False,
            desktop_mode=False,
            mpv_args=["/tmp/movie.mkv"],
            dry_run=False,
            no_tv=True,
            no_tv_ui=True,
            no_display=False,
            force_60hz=False,
            restore_saved_state=False,
            debug_log=None,
            debug_cue_seconds=8.0,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
            display_output="HDMI-A-1",
            movie_label="HDMI 1",
            movie_icon="HDMI_1",
            sdr_picture_mode="filmMaker",
            hdr_picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            desktop_label="HDMI 1",
            desktop_icon="HDMI_1",
            desktop_picture_mode="expert1",
            hdr=False,
            sdr=False,
        )
        display = SimpleNamespace(capture=SimpleNamespace(side_effect=RuntimeError("broken")))

        with (
            patch("lg_tv_automation.cli.play.parse_args", return_value=args),
            patch("lg_tv_automation.cli.play.choose_hdr_mode", return_value=(True, "forced")),
            patch("lg_tv_automation.cli.play.choose_display_refresh_rate", return_value=(24.0, "fps=24")),
            patch("lg_tv_automation.cli.play.DisplayController") as display_controller,
            patch("lg_tv_automation.cli.play.snapshot_runtime_state", new=AsyncMock(return_value={})),
            patch("lg_tv_automation.cli.play.run_mpv_with_debug", new=AsyncMock(return_value=0)) as run_mpv,
            patch("lg_tv_automation.cli.play.session_lock"),
        ):
            display_controller.return_value.capture.side_effect = RuntimeError("broken")
            result = await async_main()

        self.assertEqual(result, 1)
        run_mpv.assert_not_called()

    async def test_async_main_aborts_when_tv_setup_has_critical_failures(self) -> None:
        args = SimpleNamespace(
            status=False,
            movie_mode=False,
            desktop_mode=False,
            mpv_args=["/tmp/movie.mkv"],
            dry_run=False,
            no_tv=False,
            no_tv_ui=True,
            no_display=True,
            force_60hz=False,
            restore_saved_state=False,
            debug_log=None,
            debug_cue_seconds=8.0,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
            display_output="HDMI-A-1",
            movie_label="HDMI 1",
            movie_icon="HDMI_1",
            sdr_picture_mode="filmMaker",
            hdr_picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            desktop_label="HDMI 1",
            desktop_icon="HDMI_1",
            desktop_picture_mode="expert1",
            hdr=False,
            sdr=False,
        )
        fake_tv = SimpleNamespace(
            __aenter__=AsyncMock(),
            __aexit__=AsyncMock(return_value=None),
            apply_profile=AsyncMock(return_value=None),
            critical_failures=lambda tolerate_picture_mode=False: [
                "Direct HDMI/Game Optimizer state change failed: hidden write failed"
            ],
        )
        fake_tv.__aenter__.return_value = fake_tv

        with (
            patch("lg_tv_automation.cli.play.parse_args", return_value=args),
            patch("lg_tv_automation.cli.play.choose_hdr_mode", return_value=(True, "forced")),
            patch("lg_tv_automation.cli.play.choose_display_refresh_rate", return_value=(24.0, "fps=24")),
            patch("lg_tv_automation.cli.play.TvController", return_value=fake_tv),
            patch("lg_tv_automation.cli.play.snapshot_runtime_state", new=AsyncMock(return_value={})),
            patch("lg_tv_automation.cli.play.run_mpv_with_debug", new=AsyncMock(return_value=0)) as run_mpv,
            patch("lg_tv_automation.cli.play.session_lock"),
        ):
            result = await async_main()

        self.assertEqual(result, 1)
        run_mpv.assert_not_called()

    async def test_async_main_tolerates_picture_mode_failure_during_playback(self) -> None:
        args = SimpleNamespace(
            status=False,
            movie_mode=False,
            desktop_mode=False,
            mpv_args=["/tmp/movie.mkv"],
            dry_run=False,
            no_tv=False,
            no_tv_ui=True,
            no_display=True,
            force_60hz=False,
            restore_saved_state=False,
            debug_log=None,
            debug_cue_seconds=8.0,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
            display_output="HDMI-A-1",
            movie_label="HDMI 1",
            movie_icon="HDMI_1",
            sdr_picture_mode="filmMaker",
            hdr_picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            desktop_label="HDMI 1",
            desktop_icon="HDMI_1",
            desktop_picture_mode="expert1",
            hdr=False,
            sdr=False,
        )
        fake_tv = SimpleNamespace(
            __aenter__=AsyncMock(),
            __aexit__=AsyncMock(return_value=None),
            apply_profile=AsyncMock(return_value=None),
            critical_failures=lambda tolerate_picture_mode=False: (
                [] if tolerate_picture_mode else ["TV picture mode change failed: still in SDR"]
            ),
        )
        fake_tv.__aenter__.return_value = fake_tv

        with (
            patch("lg_tv_automation.cli.play.parse_args", return_value=args),
            patch("lg_tv_automation.cli.play.choose_hdr_mode", return_value=(True, "forced")),
            patch("lg_tv_automation.cli.play.choose_display_refresh_rate", return_value=(24.0, "fps=24")),
            patch("lg_tv_automation.cli.play.TvController", return_value=fake_tv),
            patch("lg_tv_automation.cli.play.apply_desktop_preset", new=AsyncMock(return_value=None)),
            patch("lg_tv_automation.cli.play.snapshot_runtime_state", new=AsyncMock(return_value={})),
            patch("lg_tv_automation.cli.play.run_mpv_with_debug", new=AsyncMock(return_value=0)) as run_mpv,
            patch("lg_tv_automation.cli.play.session_lock"),
        ):
            result = await async_main()

        self.assertEqual(result, 0)
        run_mpv.assert_awaited_once()

    async def test_async_main_movie_mode_leaves_refresh_unchanged(self) -> None:
        args = SimpleNamespace(
            status=False,
            movie_mode=True,
            desktop_mode=False,
            mpv_args=["/tmp/movie.mkv"],
            dry_run=False,
            no_tv=True,
            no_tv_ui=True,
            no_display=False,
            force_60hz=False,
            restore_saved_state=False,
            debug_log=None,
            debug_cue_seconds=8.0,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
            display_output="HDMI-A-1",
            movie_label="HDMI 1",
            movie_icon="HDMI_1",
            sdr_picture_mode="filmMaker",
            hdr_picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            desktop_label="HDMI 1",
            desktop_icon="HDMI_1",
            desktop_picture_mode="expert1",
            hdr=False,
            sdr=False,
        )

        with (
            patch("lg_tv_automation.cli.play.parse_args", return_value=args),
            patch("lg_tv_automation.cli.play.choose_hdr_mode", return_value=(True, "forced")),
            patch("lg_tv_automation.cli.play.choose_display_refresh_rate") as choose_refresh,
            patch("lg_tv_automation.cli.play.DisplayController") as display_controller,
            patch("lg_tv_automation.cli.play.snapshot_runtime_state", new=AsyncMock(return_value={})),
            patch("lg_tv_automation.cli.play.session_lock"),
        ):
            result = await async_main()

        self.assertEqual(result, 0)
        choose_refresh.assert_not_called()
        display_controller.return_value.apply_movie_state.assert_called_once_with(
            enable_hdr=True,
            force_60hz=False,
            target_refresh=None,
            dry_run=False,
        )

    async def test_async_main_rejects_restore_saved_state_without_tv_ui(self) -> None:
        args = SimpleNamespace(
            status=False,
            movie_mode=False,
            desktop_mode=False,
            mpv_args=["/tmp/movie.mkv"],
            dry_run=False,
            no_tv=False,
            no_tv_ui=True,
            no_display=True,
            force_60hz=False,
            restore_saved_state=True,
            debug_log=None,
            debug_cue_seconds=8.0,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
            display_output="HDMI-A-1",
            movie_label="HDMI 1",
            movie_icon="HDMI_1",
            sdr_picture_mode="filmMaker",
            hdr_picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            desktop_label="HDMI 1",
            desktop_icon="HDMI_1",
            desktop_picture_mode="expert1",
            hdr=False,
            sdr=False,
        )

        with (
            patch("lg_tv_automation.cli.play.parse_args", return_value=args),
            patch("lg_tv_automation.cli.play.session_lock"),
        ):
            with self.assertRaisesRegex(SystemExit, "requires TV UI capture"):
                await async_main()

    async def test_async_main_returns_non_zero_when_lock_is_contended(self) -> None:
        args = SimpleNamespace(
            status=False,
            movie_mode=True,
            desktop_mode=False,
            mpv_args=["/tmp/movie.mkv"],
            dry_run=False,
            no_tv=True,
            no_tv_ui=True,
            no_display=True,
            force_60hz=False,
            restore_saved_state=False,
            debug_log=None,
            debug_cue_seconds=8.0,
            tv_ip="192.168.1.134",
            tv_input="HDMI_1",
            display_output="HDMI-A-1",
            movie_label="HDMI 1",
            movie_icon="HDMI_1",
            sdr_picture_mode="filmMaker",
            hdr_picture_mode="hdrFilmMaker",
            tru_motion="cinemaClear",
            desktop_label="HDMI 1",
            desktop_icon="HDMI_1",
            desktop_picture_mode="expert1",
            hdr=False,
            sdr=False,
        )

        with (
            patch("lg_tv_automation.cli.play.parse_args", return_value=args),
            patch("lg_tv_automation.cli.play.session_lock", side_effect=RuntimeError("locked")),
        ):
            result = await async_main()

        self.assertEqual(result, 1)

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
