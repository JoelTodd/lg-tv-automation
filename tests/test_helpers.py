"""Tests for configuration, playback defaults, display updates and direct TV APIs."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from lg_tv_automation.config import require_tv_ip, tv_ip_config_path
from lg_tv_automation.constants import DEFAULT_MOVIE_ICON, DEFAULT_MOVIE_LABEL, DEFAULT_TRUMOTION, expected_input_app_id
from lg_tv_automation.display import DisplayController
from lg_tv_automation.media import normalize_mpv_args
from lg_tv_automation.models import DisplaySnapshot, HdmiFeatureState, TvProfile
from lg_tv_automation.profiles import build_movie_profile
from lg_tv_automation.tv import TvController
from lg_tv_automation.lifecycle import run_cleanup_step


class HelperTests(unittest.TestCase):
    def test_require_tv_ip_loads_persistent_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_home = Path(tmpdir)
            config_path = config_home / "lg-tv-automation" / "tv-ip"
            config_path.parent.mkdir()
            config_path.write_text("203.0.113.20\n", encoding="utf-8")

            with patch.dict(os.environ, {"XDG_CONFIG_HOME": tmpdir}, clear=True):
                self.assertEqual(tv_ip_config_path(), config_path)
                self.assertEqual(require_tv_ip(), "203.0.113.20")

    def test_require_tv_ip_environment_overrides_persistent_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "lg-tv-automation" / "tv-ip"
            config_path.parent.mkdir()
            config_path.write_text("203.0.113.20\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": tmpdir, "LG_TV_IP": "203.0.113.21"},
                clear=True,
            ):
                self.assertEqual(require_tv_ip(), "203.0.113.21")

    def test_require_tv_ip_explains_persistent_config_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(os.environ, {"XDG_CONFIG_HOME": tmpdir}, clear=True),
                self.assertRaisesRegex(RuntimeError, r"lg-tv-automation/tv-ip"),
            ):
                require_tv_ip()

    def test_expected_input_app_id(self) -> None:
        self.assertEqual(expected_input_app_id("HDMI_1"), "com.webos.app.hdmi1")

    def test_default_trumotion_for_video_playback_uses_cinematic_movement(self) -> None:
        self.assertEqual(DEFAULT_TRUMOTION, "cinemaClear")

    def test_movie_profiles_apply_cinematic_movement_by_default(self) -> None:
        sdr_profile = build_movie_profile(
            want_hdr=False,
        )
        hdr_profile = build_movie_profile(
            want_hdr=True,
        )

        self.assertEqual(sdr_profile.picture_mode, "filmMaker")
        self.assertEqual(sdr_profile.tru_motion, "cinemaClear")
        self.assertEqual(hdr_profile.picture_mode, "hdrFilmMaker")
        self.assertEqual(hdr_profile.tru_motion, "cinemaClear")

    def test_default_movie_input_profile_forces_non_pc_classification(self) -> None:
        self.assertEqual(DEFAULT_MOVIE_LABEL, "Blu-ray Player")
        self.assertEqual(DEFAULT_MOVIE_ICON, "bluray")

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
                )

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.hdr.enable",
                "output.HDMI-A-1.wcg.enable",
            ]
        )
        wait_for_state.assert_called_once_with(None, True, True, None)

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
            controller.apply_desktop_state()

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.vrrpolicy.automatic",
                "output.HDMI-A-1.hdr.disable",
                "output.HDMI-A-1.wcg.disable",
            ]
        )
        wait_for_state.assert_called_once_with("10", False, False, 2)

    def test_apply_desktop_state_skips_vrr_policy_for_vrr_incapable_sink(self) -> None:
        controller = DisplayController("HDMI-A-1")
        controller.saved_state = DisplaySnapshot(mode_id="10", hdr=False, wcg=False)
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
            controller.apply_desktop_state()

        run_command.assert_called_once_with(
            [
                "kscreen-doctor",
                "output.HDMI-A-1.hdr.disable",
                "output.HDMI-A-1.wcg.disable",
            ]
        )
        wait_for_state.assert_called_once_with("10", False, False, None)


class AsyncHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_tv_connect_timeout_mentions_pairing_prompt(self) -> None:
        controller = TvController("203.0.113.10", "HDMI_1")
        fake_client = SimpleNamespace(connect=AsyncMock(side_effect=TimeoutError), disconnect=AsyncMock())

        with patch("lg_tv_automation.tv.WebOsClient.create", new=AsyncMock(return_value=fake_client)):
            with patch("lg_tv_automation.tv.pairing_key_path", return_value=Path("/tmp/test-pairing.sqlite")):
                with self.assertRaisesRegex(RuntimeError, "pairing prompt"):
                    await controller.__aenter__()

    async def test_hidden_setting_timeout_raises_readable_error(self) -> None:
        controller = TvController("203.0.113.10", "HDMI_1")
        controller.client = SimpleNamespace(request=AsyncMock())

        async def timeout_wait_for(awaitable, timeout):
            _ = timeout
            awaitable.close()
            raise TimeoutError

        with patch("lg_tv_automation.tv.asyncio.wait_for", new=timeout_wait_for):
            with self.assertRaisesRegex(RuntimeError, "create hidden settings alert"):
                await controller._alert_luna("luna://example", {"x": 1})

    async def test_hdmi_signal_verification_requires_signal_to_return(self) -> None:
        controller = TvController("203.0.113.10", "HDMI_1")
        controller.client = SimpleNamespace()
        controller._get_input_info = AsyncMock(return_value={"hdmiSignalExist": False})
        controller._wait_for_value = AsyncMock(return_value={"hdmiSignalExist": True})

        await controller._ensure_hdmi_signal()

        controller._wait_for_value.assert_awaited_once_with(
            controller._get_input_info,
            unittest.mock.ANY,
            "HDMI_1 signal",
            timeout=6.0,
            interval=0.5,
        )

    async def test_apply_profile_times_out_trumotion_write_cleanly(self) -> None:
        controller = TvController("203.0.113.10", "HDMI_1")
        controller.client = SimpleNamespace(
            set_system_picture_mode=AsyncMock(),
            set_current_picture_mode=AsyncMock(),
            set_picture_settings=AsyncMock(),
            set_settings=AsyncMock(),
        )
        controller._ensure_input_active = AsyncMock()
        controller._get_input_info = AsyncMock(return_value={"label": "HDMI 1", "icon": "HDMI_1.png"})
        controller._get_picture_mode = AsyncMock(side_effect=["hdrFilmMaker", "hdrFilmMaker"])
        controller._get_picture_setting = AsyncMock(return_value="off")
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
            await controller.apply_profile(profile)

        self.assertTrue(any("truMotion change failed" in failure for failure in controller.failures))

    async def test_apply_profile_reapplies_hdmi_state_after_picture_writes(self) -> None:
        controller = TvController("203.0.113.10", "HDMI_1")
        controller.client = SimpleNamespace(
            set_picture_settings=AsyncMock(),
            set_settings=AsyncMock(),
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
        controller._get_picture_setting = AsyncMock(side_effect=["off", "on"])

        profile = TvProfile(
            label="HDMI 1",
            icon="HDMI_1",
            picture_mode="hdrFilmMaker",
            tru_motion="on",
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
            await controller.apply_profile(profile)

        self.assertEqual(events, ["hdmi", "picture", "trumotion", "hdmi"])
        self.assertEqual(controller._apply_hidden_hdmi_state.await_count, 2)
        self.assertFalse(controller.failures)

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

    async def test_apply_hidden_hdmi_state_hard_disables_allm_auto_game_path(self) -> None:
        controller = TvController("203.0.113.10", "HDMI_1")
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
        controller = TvController("203.0.113.10", "HDMI_1")
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
