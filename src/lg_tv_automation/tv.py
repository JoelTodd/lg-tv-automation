"""Bounded direct LG webOS operations used for movie playback and cleanup."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from bscpylgtv import WebOsClient

from .console import detail, log
from .config import pairing_key_path
from .constants import expected_input_app_id
from .models import HdmiFeatureState, TvProfile

Fetcher = Callable[[], Awaitable[Any]]
Predicate = Callable[[Any], bool]
TV_CONNECT_TIMEOUT = 8.0
TV_REQUEST_TIMEOUT = 5.0
TV_HIDDEN_SETTINGS_TIMEOUT = 3.0
TV_PICTURE_MODE_VERIFY_TIMEOUT = 8.0
TV_PICTURE_MODE_RETRY_BACKOFFS = (0.75, 1.5)
TV_HDMI_SIGNAL_TIMEOUT = 6.0


class TvController:
    """Apply and restore TV-side state for the Fedora HDMI input."""

    def __init__(self, ip: str, input_id: str):
        self.ip = ip
        self.input_id = input_id
        self.input_app_id = expected_input_app_id(input_id)
        self.client: WebOsClient | None = None
        self.failures: list[str] = []
        self.return_to_input = True
        self.verification: dict[str, str] = {}

    async def __aenter__(self) -> "TvController":
        try:
            async with asyncio.timeout(TV_CONNECT_TIMEOUT):
                key_path = await asyncio.to_thread(pairing_key_path)
                self.client = await WebOsClient.create(self.ip, key_file_path=str(key_path), states=[])
                await self.client.connect()
        except TimeoutError as err:
            raise RuntimeError(
                "TV connection timed out. Check for a pairing prompt on the TV."
            ) from err
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        """Disconnect cleanly and always land back on HDMI 1.

        The user can only observe and assist the session while the TV is on the
        HDMI input driven by the Fedora box, so cleanup cannot treat the current
        app as an implementation detail.
        """

        if self.client is None:
            return
        failures = []
        try:
            if self.return_to_input:
                await self._ensure_input_active()
        except Exception as err:
            failures.append(f"TV input restore on disconnect failed: {err}")
        finally:
            try:
                await self._request_with_timeout(self.client.disconnect(), "disconnect TV session")
            except Exception as err:
                failures.append(f"TV disconnect request failed: {err}")
        if failures:
            raise RuntimeError("; ".join(failures))

    async def _request_with_timeout(
        self,
        awaitable: Awaitable[Any],
        description: str,
        *,
        timeout: float = TV_REQUEST_TIMEOUT,
    ) -> Any:
        """Bound TV round trips so the CLI cannot hang forever on a missing reply."""

        try:
            return await asyncio.wait_for(awaitable, timeout=timeout)
        except TimeoutError as err:
            raise RuntimeError(f"Timed out waiting for TV response while trying to {description}.") from err

    async def _get_input_info(self) -> dict[str, Any]:
        assert self.client is not None
        inputs = await self._request_with_timeout(
            self.client.request("com.webos.service.eim/getAllInputStatus", {}),
            "query HDMI input status",
        )
        for device in inputs.get("devices", []):
            if device.get("id") == self.input_id:
                return device
        raise RuntimeError(f"TV does not expose input {self.input_id!r}.")

    async def _get_picture_setting(self, key: str) -> Any:
        assert self.client is not None
        params: dict[str, Any] = {"category": "picture", "keys": [key]}
        if key != "pictureMode":
            params["current_app"] = True
        result = await self._request_with_timeout(
            self.client.request(
                "settings/getSystemSettings",
                params,
            ),
            f"read picture setting {key}",
        )
        return result["settings"][key]

    async def _get_picture_mode(self) -> str:
        return str(await self._get_picture_setting("pictureMode"))

    def _input_port_number(self) -> str:
        """Extract the HDMI port number used by LG's hidden setting keys."""

        suffix = self.input_id.split("_")[-1]
        if not suffix.isdigit():
            raise RuntimeError(f"Could not derive HDMI port number from input id {self.input_id!r}.")
        return suffix

    def _settings_input_name(self) -> str:
        """Return the input token expected by mode-specific settings APIs."""

        return self.input_id.lower().replace("_", "")

    @staticmethod
    def _picture_mode_dynamic_range(picture_mode: str) -> str:
        """Infer the dynamic-range bucket required by ``set_picture_mode``."""

        lowered = picture_mode.lower()
        if lowered.startswith("dolby"):
            return "dolbyHdr"
        if lowered.startswith("hdr"):
            return "hdr"
        return "sdr"

    async def _alert_luna(self, uri: str, params: dict[str, Any]) -> None:
        """Execute a Luna call through the notifications service.

        LG does not expose some HDMI and Game Optimizer flags through the normal
        documented settings APIs. A reliable workaround is to create an alert
        whose button callback performs the hidden Luna call, then immediately
        close the alert. Closing triggers the same callback again, which is why
        the payload includes ``onclose`` and ``onfail`` handlers as well.
        """

        assert self.client is not None
        payload = {
            "message": "Applying...",
            "buttons": [{"label": "", "onClick": uri, "params": params}],
            "onclose": {"uri": uri, "params": params},
            "onfail": {"uri": uri, "params": params},
        }
        result = await self._request_with_timeout(
            self.client.request("system.notifications/createAlert", payload),
            f"create hidden settings alert for {uri}",
            timeout=TV_HIDDEN_SETTINGS_TIMEOUT,
        )
        if not result.get("returnValue"):
            raise RuntimeError(f"createAlert failed for {uri}: {result}")

        alert_id = result.get("alertId")
        if alert_id:
            close = await self._request_with_timeout(
                self.client.request("system.notifications/closeAlert", {"alertId": alert_id}),
                f"close hidden settings alert for {uri}",
                timeout=TV_HIDDEN_SETTINGS_TIMEOUT,
            )
            if not close.get("returnValue"):
                raise RuntimeError(f"closeAlert failed for {uri}: {close}")

    async def _ensure_input_active(self) -> None:
        """Return to the Fedora HDMI app if a previous step opened another app."""

        assert self.client is not None
        current_app = await self._request_with_timeout(self.client.get_current_app(), "get current app")
        if current_app != self.input_app_id:
            await self._request_with_timeout(
                self.client.launch_app(self.input_app_id),
                f"launch {self.input_app_id}",
            )
            await self._wait_for_value(
                lambda: self._request_with_timeout(self.client.get_current_app(), "verify active input"),
                lambda app: app == self.input_app_id,
                "active HDMI input", timeout=4.0, interval=0.1,
            )

    async def _ensure_hdmi_signal(self) -> bool | None:
        """Require the TV to see HDMI pixels after a profile transition.

        A Fedora 44 / NVIDIA transition can leave KScreen reporting an enabled
        output while webOS remains stuck on ``No Signal``.  Do not claim the
        profile succeeded merely because KScreen's logical state looks intact.
        """

        assert self.client is not None
        input_info = await self._get_input_info()
        if "hdmiSignalExist" not in input_info:
            return
        if input_info["hdmiSignalExist"]:
            return True

        await self._wait_for_value(
            self._get_input_info,
            lambda info: bool(info.get("hdmiSignalExist")),
            f"{self.input_id} signal",
            timeout=TV_HDMI_SIGNAL_TIMEOUT,
            interval=0.5,
        )
        return True

    async def _set_hidden_other_settings(self, settings: dict[str, Any], description: str) -> None:
        detail(f"Setting {description}.")
        await self._alert_luna(
            "luna://com.webos.settingsservice/setSystemSettings",
            {"category": "other", "settings": settings},
        )

    async def _apply_hidden_hdmi_state(self, state: HdmiFeatureState) -> None:
        """Apply HDMI/Game Optimizer flags through hidden setting keys.

        The ordering matters. When disabling movie-hostile features we keep Game
        Optimizer enabled long enough for VRR and ALLM writes to succeed, then
        switch it off last. The reverse is used when enabling desktop mode.
        """

        port = self._input_port_number()

        def on_off(value: bool) -> str:
            return "on" if value else "off"

        input_optimization = "on" if state.allm else "off"
        quick_game = on_off(state.allm)

        if state.game_optimizer_master:
            await self._set_hidden_other_settings(
                {"gameMode": {f"hdmi{port}": "on"}},
                f"Game Optimizer master for HDMI {port} to on",
            )
            await self._set_hidden_other_settings(
                {
                    "gameOptimization": on_off(state.vrr),
                    f"gameOptimizationHDMI{port}": on_off(state.vrr),
                },
                f"VRR & G-Sync to {on_off(state.vrr)}",
            )
            await self._set_hidden_other_settings(
                {
                    "enableALLM": on_off(state.allm),
                    "inputOptimization": input_optimization,
                    "enableQuickGame": quick_game,
                },
                f"ALLM to {on_off(state.allm)}",
            )
        else:
            await self._set_hidden_other_settings(
                {
                    "gameOptimization": on_off(state.vrr),
                    f"gameOptimizationHDMI{port}": on_off(state.vrr),
                },
                f"VRR & G-Sync to {on_off(state.vrr)}",
            )
            await self._set_hidden_other_settings(
                {
                    "enableALLM": on_off(state.allm),
                    "inputOptimization": input_optimization,
                    "enableQuickGame": quick_game,
                },
                f"ALLM to {on_off(state.allm)}",
            )
            await self._set_hidden_other_settings(
                {"gameMode": {f"hdmi{port}": "off"}},
                f"Game Optimizer master for HDMI {port} to off",
            )

        await self._set_hidden_other_settings(
            {f"444BypassHDMI{port}": on_off(state.passthrough_444)},
            f"4:4:4 Pass Through for HDMI {port} to {on_off(state.passthrough_444)}",
        )
        await asyncio.sleep(1.0)

    async def _wait_for_value(
        self,
        fetcher: Fetcher,
        predicate: Predicate,
        description: str,
        timeout: float = 4.0,
        interval: float = 0.25,
    ) -> Any:
        """Poll an async getter until its result matches the desired value."""

        deadline = time.monotonic() + timeout
        last_value: Any = None
        try:
            async with asyncio.timeout(timeout):
                while time.monotonic() < deadline:
                    last_value = await fetcher()
                    if predicate(last_value):
                        return last_value
                    await asyncio.sleep(min(interval, max(0, deadline - time.monotonic())))
        except TimeoutError:
            pass
        raise RuntimeError(f"{description} verification failed; last value was {last_value!r}")

    def critical_failures(self, *, tolerate_picture_mode: bool = False) -> list[str]:
        """Return the subset of apply failures that should abort playback."""

        return [
            failure
            for failure in self.failures
            if not failure.startswith("TV truMotion change failed:")
            and (not tolerate_picture_mode or not failure.startswith("TV picture mode change failed:"))
        ]

    async def _set_picture_mode(self, picture_mode: str) -> None:
        """Apply a picture mode with bounded fallbacks across direct API paths."""

        assert self.client is not None
        current_picture_mode = await self._get_picture_mode()
        if current_picture_mode != picture_mode:
            detail(f"Setting current picture mode to {picture_mode}.")
        else:
            detail(f"Picture mode already at {picture_mode}.")
            return

        async def write_system_mode() -> None:
            await self._request_with_timeout(
                self.client.set_system_picture_mode(picture_mode),
                f"set picture mode to {picture_mode}",
            )

        async def write_mode_specific() -> None:
            await self._request_with_timeout(
                self.client.set_picture_mode(
                    picture_mode,
                    self._settings_input_name(),
                    dynamic_range=self._picture_mode_dynamic_range(picture_mode),
                ),
                f"set mode-specific picture mode to {picture_mode}",
            )

        async def write_current_app_mode() -> None:
            await self._request_with_timeout(
                self.client.set_settings(
                    "picture",
                    {"pictureMode": picture_mode},
                    current_app=True,
                ),
                f"set current-app picture mode to {picture_mode}",
            )

        attempts: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("current-app picture-mode API", write_current_app_mode),
            ("system picture-mode API", write_system_mode),
            ("mode-specific picture-mode API", write_mode_specific),
        ]

        last_error: Exception | None = None
        for index, (label, writer) in enumerate(attempts):
            try:
                await self._ensure_input_active()
                await writer()
                await self._wait_for_value(
                    self._get_picture_mode,
                    lambda mode: mode == picture_mode,
                    "picture mode",
                    timeout=TV_PICTURE_MODE_VERIFY_TIMEOUT,
                )
                return
            except Exception as err:
                last_error = err
                if index < len(attempts) - 1:
                    log(f"{label} did not verify, retrying next path: {err}")
                if index < len(TV_PICTURE_MODE_RETRY_BACKOFFS):
                    await asyncio.sleep(TV_PICTURE_MODE_RETRY_BACKOFFS[index])

        if last_error is None:
            raise RuntimeError(f"picture mode {picture_mode!r} failed without an explicit error")
        raise RuntimeError(str(last_error))

    async def apply_profile(self, profile: TvProfile) -> None:
        """Apply a TV profile and verify each direct setting that can be verified."""

        self.failures.clear()
        self.verification.clear()

        assert self.client is not None

        await self._ensure_input_active()

        input_info = await self._get_input_info()
        current_icon = Path(input_info["icon"]).stem
        if input_info["label"] != profile.label or current_icon != profile.icon:
            try:
                detail(f"Setting {self.input_id} label/icon to {profile.label}/{profile.icon}.")
                await self._request_with_timeout(
                    self.client.set_device_info(self.input_id, profile.icon, profile.label),
                    f"set {self.input_id} label/icon to {profile.label}/{profile.icon}",
                )
                await self._wait_for_value(
                    self._get_input_info,
                    lambda info: info["label"] == profile.label and Path(info["icon"]).stem == profile.icon,
                    f"{self.input_id} label/icon",
                )
            except Exception as err:
                message = f"TV relabel failed: {err}"
                self.failures.append(message)
                log(message)
        else:
            detail(f"{self.input_id} label/icon already at {profile.label}/{profile.icon}.")

        hdmi_features_applied = False
        if profile.hdmi_features is not None:
            try:
                await self._apply_hidden_hdmi_state(profile.hdmi_features)
                hdmi_features_applied = True
            except Exception as err:
                message = f"Direct HDMI/Game Optimizer state change failed: {err}"
                self.failures.append(message)
                log(message)

            await self._ensure_input_active()

        try:
            await self._set_picture_mode(profile.picture_mode)
            self.verification["picture_mode"] = "verified"
        except Exception as err:
            message = f"TV picture mode change failed: {err}"
            self.failures.append(message)
            log(message)

        if profile.tru_motion is not None:
            target_motion = profile.tru_motion
            try:
                current_motion = await self._get_picture_setting("truMotionMode")
                if current_motion != target_motion:
                    detail(f"Setting TruMotion to {target_motion}.")
                    async def current_app_write() -> None:
                        await self._request_with_timeout(
                            self.client.set_settings("picture", {"truMotionMode": target_motion}, current_app=True),
                            f"set truMotionMode to {target_motion}",
                        )

                    async def mode_write() -> None:
                        await self._request_with_timeout(
                            self.client.set_picture_settings(
                                {"truMotionMode": target_motion}, profile.picture_mode,
                                self._settings_input_name(), current_app=True,
                            ), f"retry truMotionMode to {target_motion}",
                        )

                    for index, writer in enumerate((current_app_write, mode_write)):
                        try:
                            await writer()
                            await self._wait_for_value(
                                lambda: self._get_picture_setting("truMotionMode"),
                                lambda value: value == target_motion, "TruMotion", timeout=3.0,
                            )
                            break
                        except Exception:
                            if index == 1:
                                raise
                            detail("Current-app TruMotion write did not verify; retrying mode-specific write.")
                self.verification["tru_motion"] = "verified"
            except Exception as err:
                message = f"TV truMotion change failed: {err}"
                self.failures.append(message)
                log(message)

        if profile.hdmi_features is not None and hdmi_features_applied:
            try:
                detail("Reapplying HDMI/Game Optimizer state after picture-mode writes.")
                await self._apply_hidden_hdmi_state(profile.hdmi_features)
                self.verification["hidden_hdmi_features"] = "write acknowledged; visible state unverified"
            except Exception as err:
                message = f"Final HDMI/Game Optimizer state change failed: {err}"
                self.failures.append(message)
                log(message)

            self.verification["game_optimizer_master"] = "visible state unverified; inspect with Codex"

        try:
            detail(f"Verifying {self.input_id} has a live HDMI signal.")
            await self._ensure_input_active()
            signal = await self._ensure_hdmi_signal()
            self.verification["hdmi_signal"] = "verified" if signal else "unavailable"
        except Exception as err:
            message = f"TV HDMI signal verification failed: {err}"
            self.failures.append(message)
            log(message)
