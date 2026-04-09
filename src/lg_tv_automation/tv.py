"""LG webOS control layer.

This module is the core of the project. It owns three important ideas:

1. The direct, reliable API paths such as picture mode changes and input relabels.
2. The hidden settings-write trick used for HDMI/Game Optimizer flags.
3. The screenshot-driven UI reads used for status output and exact-state capture.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from bscpylgtv import WebOsClient

from .console import log
from .constants import expected_input_app_id
from .models import HdmiFeatureState, SavedTvState, TvProfile
from .tv_ui import LgTvUiAutomation

Fetcher = Callable[[], Awaitable[Any]]
Predicate = Callable[[Any], bool]
TV_CONNECT_TIMEOUT = 8.0
TV_REQUEST_TIMEOUT = 5.0
TV_HIDDEN_SETTINGS_TIMEOUT = 3.0


class TvController:
    """Apply and restore TV-side state for the Fedora HDMI input."""

    def __init__(self, ip: str, input_id: str):
        self.ip = ip
        self.input_id = input_id
        self.input_app_id = expected_input_app_id(input_id)
        self.client: WebOsClient | None = None
        self.saved_state: SavedTvState | None = None
        self.ui: LgTvUiAutomation | None = None
        self.failures: list[str] = []

    async def __aenter__(self) -> "TvController":
        self.client = await WebOsClient.create(self.ip)
        try:
            await asyncio.wait_for(self.client.connect(), timeout=TV_CONNECT_TIMEOUT)
        except TimeoutError as err:
            raise RuntimeError(
                "TV connection timed out. Check for a pairing prompt on the TV."
            ) from err
        self.ui = LgTvUiAutomation(self.client, self.input_app_id)
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        """Disconnect cleanly and always land back on HDMI 1.

        The user can only observe and assist the session while the TV is on the
        HDMI input driven by the Fedora box, so cleanup cannot treat the current
        app as an implementation detail.
        """

        if self.client is None:
            return
        try:
            current_app = await self._request_with_timeout(self.client.get_current_app(), "get current app")
            if current_app != self.input_app_id:
                await self._request_with_timeout(
                    self.client.launch_app(self.input_app_id),
                    f"launch {self.input_app_id}",
                )
                await asyncio.sleep(1.5)
        except Exception as err:
            log(f"TV input restore on disconnect failed: {err}")
        try:
            await self._request_with_timeout(self.client.disconnect(), "disconnect TV session")
        except Exception as err:
            log(f"TV disconnect request failed: {err}")

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
        return next(device for device in inputs["devices"] if device["id"] == self.input_id)

    async def _get_picture_setting(self, key: str) -> Any:
        assert self.client is not None
        result = await self._request_with_timeout(
            self.client.request(
                "settings/getSystemSettings",
                {"category": "picture", "keys": [key]},
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

    async def _ensure_input_active(self, delay: float = 1.5) -> None:
        """Return to the Fedora HDMI app if a previous step opened another app."""

        assert self.client is not None
        current_app = await self._request_with_timeout(self.client.get_current_app(), "get current app")
        if current_app != self.input_app_id:
            await self._request_with_timeout(
                self.client.launch_app(self.input_app_id),
                f"launch {self.input_app_id}",
            )
            await asyncio.sleep(delay)

    async def _set_hidden_other_settings(self, settings: dict[str, Any], description: str) -> None:
        log(f"Setting {description}.")
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

    async def _capture_ui_hdmi_feature_state(self) -> HdmiFeatureState:
        """Read the visible HDMI/Game Optimizer state through the TV UI."""

        assert self.ui is not None
        ui_state = await self.ui.capture_hdmi_settings_state()
        ui_state.update(await self.ui.capture_game_optimizer_state())
        return HdmiFeatureState(
            passthrough_444=bool(ui_state["passthrough_444"]),
            game_optimizer_master=bool(ui_state["game_optimizer_master"]),
            vrr=bool(ui_state["vrr"]),
            allm=bool(ui_state["allm"]),
        )

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
        while time.monotonic() < deadline:
            last_value = await fetcher()
            if predicate(last_value):
                return last_value
            await asyncio.sleep(interval)
        raise RuntimeError(f"{description} verification failed; last value was {last_value!r}")

    async def capture(self, include_ui_state: bool = True) -> dict[str, Any]:
        """Capture the current TV state for later restore or for status output."""

        input_info = await self._get_input_info()
        picture_mode = await self._get_picture_mode()
        icon = Path(input_info["icon"]).stem
        hdmi_features: HdmiFeatureState | None = None

        if include_ui_state:
            try:
                hdmi_features = await self._capture_ui_hdmi_feature_state()
            except Exception as err:
                log(f"TV UI state capture failed, continuing without it: {err}")

        self.saved_state = SavedTvState(
            current_app=await self._request_with_timeout(self.client.get_current_app(), "get current app"),
            label=input_info["label"],
            icon=icon,
            picture_mode=picture_mode,
            app_id=input_info["appId"],
            hdmi_features=hdmi_features,
        )
        return self.saved_state.as_dict()

    async def status(self) -> dict[str, Any]:
        """Return a detailed live TV status payload."""

        assert self.ui is not None
        input_info = await self._get_input_info()
        payload = {
            "current_app": await self._request_with_timeout(self.client.get_current_app(), "get current app"),
            "input": input_info,
            "picture_mode": await self._get_picture_mode(),
        }
        try:
            payload.update(await self.ui.capture_hdmi_settings_state())
            payload.update(await self.ui.capture_game_optimizer_state())
        except Exception as err:
            payload["ui_error"] = str(err)
        return payload

    async def apply_profile(self, profile: TvProfile, *, dry_run: bool) -> None:
        """Apply a TV profile and verify each direct setting that can be verified."""

        assert self.client is not None
        self.failures.clear()

        if dry_run:
            hdmi_clause = ""
            if profile.hdmi_features is not None:
                features = profile.hdmi_features
                hdmi_clause = (
                    ", set 4:4:4 Pass Through "
                    f"{'on' if features.passthrough_444 else 'off'}, "
                    f"Game Optimizer master {'on' if features.game_optimizer_master else 'off'}, "
                    f"VRR {'on' if features.vrr else 'off'}, and ALLM {'on' if features.allm else 'off'}"
                )
            tru_motion_clause = f", set truMotion {profile.tru_motion}" if profile.tru_motion is not None else ""
            log(
                f"Would relabel {self.input_id} as {profile.label}/{profile.icon}, "
                f"set picture mode {profile.picture_mode}{tru_motion_clause}{hdmi_clause}."
            )
            return

        await self._ensure_input_active()

        input_info = await self._get_input_info()
        current_icon = Path(input_info["icon"]).stem
        if input_info["label"] != profile.label or current_icon != profile.icon:
            try:
                log(f"Setting {self.input_id} label/icon to {profile.label}/{profile.icon}.")
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
            log(f"{self.input_id} label/icon already at {profile.label}/{profile.icon}.")

        if profile.hdmi_features is not None:
            try:
                await self._apply_hidden_hdmi_state(profile.hdmi_features)
            except Exception as err:
                message = f"Direct HDMI/Game Optimizer state change failed: {err}"
                self.failures.append(message)
                log(message)

            await self._ensure_input_active()

        current_picture_mode = await self._get_picture_mode()
        try:
            if current_picture_mode != profile.picture_mode:
                log(f"Setting current picture mode to {profile.picture_mode}.")
            else:
                log(f"Reapplying current picture mode {profile.picture_mode}.")
            try:
                await self._request_with_timeout(
                    self.client.set_system_picture_mode(profile.picture_mode),
                    f"set picture mode to {profile.picture_mode}",
                )
            except Exception as system_err:
                log(f"set_system_picture_mode is not supported here, retrying legacy picture-mode call: {system_err}")
                await self._request_with_timeout(
                    self.client.set_current_picture_mode(profile.picture_mode),
                    f"set legacy picture mode to {profile.picture_mode}",
                )
            try:
                await self._wait_for_value(
                    self._get_picture_mode,
                    lambda mode: mode == profile.picture_mode,
                    "picture mode",
                )
            except Exception as verify_err:
                log(f"Primary picture-mode write did not verify, retrying legacy call: {verify_err}")
                await self._request_with_timeout(
                    self.client.set_current_picture_mode(profile.picture_mode),
                    f"retry legacy picture mode to {profile.picture_mode}",
                )
                await self._wait_for_value(
                    self._get_picture_mode,
                    lambda mode: mode == profile.picture_mode,
                    "picture mode",
                )
            await asyncio.sleep(0.5)
        except Exception as err:
            message = f"TV picture mode change failed: {err}"
            self.failures.append(message)
            log(message)

        if profile.tru_motion is not None:
            try:
                log(f"Setting truMotionMode to {profile.tru_motion} for {self.input_id.lower()} / {profile.picture_mode}.")
                await self._request_with_timeout(
                    self.client.set_picture_settings(
                        {"truMotionMode": profile.tru_motion},
                        profile.picture_mode,
                        self.input_id.lower(),
                        current_app=True,
                    ),
                    f"set truMotionMode to {profile.tru_motion}",
                )
                await asyncio.sleep(0.5)
            except Exception as err:
                try:
                    log(f"Mode-specific truMotion write failed, retrying current-app write: {err}")
                    await self._request_with_timeout(
                        self.client.set_settings("picture", {"truMotionMode": profile.tru_motion}, current_app=True),
                        f"retry truMotionMode to {profile.tru_motion}",
                    )
                    await asyncio.sleep(0.5)
                except Exception as retry_err:
                    message = f"TV truMotion change failed: {retry_err}"
                    self.failures.append(message)
                    log(message)

    async def restore(self, *, dry_run: bool) -> None:
        """Restore the exact TV state captured before playback."""

        if self.saved_state is None:
            return
        log("Restoring captured TV state.")
        await self.apply_profile(self.saved_state.as_profile(), dry_run=dry_run)
