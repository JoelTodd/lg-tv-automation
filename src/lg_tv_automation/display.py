"""The minimal KScreen changes needed for playback and desktop restoration."""

from __future__ import annotations

import json
import time
from typing import Any

from .models import DisplaySnapshot
from .process import run_command

VRR_POLICY_TO_NAME = {0: "never", 1: "always", 2: "automatic"}


def load_display_output(name: str, retries: int = 20, interval: float = .25, *, timeout: float = 6) -> dict[str, Any]:
    """Retry transient missing/invalid KScreen output during HDMI retraining."""
    deadline = time.monotonic() + timeout
    last_error = None
    for attempt in range(retries):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            data = json.loads(run_command(["kscreen-doctor", "-j"], timeout=remaining).stdout)
            for output in data["outputs"]:
                if output["name"] == name:
                    return output
        except (RuntimeError, ValueError, KeyError) as err:
            last_error = err
        if attempt < retries - 1:
            time.sleep(max(0, min(interval, deadline - time.monotonic())))
    raise RuntimeError(f"Display output {name!r} unavailable after {timeout:g}s. Last error: {last_error}")


class DisplayController:
    def __init__(self, output_name: str):
        self.output_name = output_name
        self.saved_state: DisplaySnapshot | None = None
        self._captured_output: dict[str, Any] | None = None

    def capture(self) -> DisplaySnapshot:
        output = load_display_output(self.output_name)
        self._captured_output = output
        self.saved_state = DisplaySnapshot(
            mode_id=str(output["currentModeId"]), hdr=bool(output["hdr"]),
            wcg=bool(output["wcg"]), vrr_policy=output.get("vrrPolicy"),
        )
        return self.saved_state

    def _wait_for_state(self, mode_id: str | None, hdr: bool, wcg: bool, vrr_policy: Any, *, timeout: float = 6) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                output = load_display_output(self.output_name, retries=1, timeout=max(.001, deadline - time.monotonic()))
                mode_ok = mode_id is None or str(output["currentModeId"]) == mode_id
                vrr_ok = vrr_policy is None or output.get("vrrPolicy") == vrr_policy
                if mode_ok and vrr_ok and bool(output["hdr"]) == hdr and bool(output["wcg"]) == wcg:
                    return True
            except RuntimeError:
                pass
            time.sleep(max(0, min(.25, deadline - time.monotonic())))
        return False

    def _apply_state(self, *, hdr: bool, wcg: bool, vrr_policy: Any, mode_id: str | None = None) -> None:
        output = self._captured_output
        self._captured_output = None
        if output is None:
            output = load_display_output(self.output_name)
        prefix = f"output.{self.output_name}"
        actions = []
        if mode_id is not None and str(output["currentModeId"]) != mode_id:
            actions.append(f"{prefix}.mode.{mode_id}")
        # The installed EDID deliberately removes VRR. Missing policy is not an
        # error and must not generate an unsupported KScreen command.
        if output.get("vrrPolicy") not in VRR_POLICY_TO_NAME:
            vrr_policy = None
        if vrr_policy in VRR_POLICY_TO_NAME and output.get("vrrPolicy") != vrr_policy:
            actions.append(f"{prefix}.vrrpolicy.{VRR_POLICY_TO_NAME[vrr_policy]}")
        if bool(output["hdr"]) != hdr:
            actions.append(f"{prefix}.hdr.{'enable' if hdr else 'disable'}")
        if bool(output["wcg"]) != wcg:
            actions.append(f"{prefix}.wcg.{'enable' if wcg else 'disable'}")
        if not actions:
            return
        run_command(["kscreen-doctor", *actions])
        if not self._wait_for_state(mode_id, hdr, wcg, vrr_policy):
            raise RuntimeError("Display did not reach the requested playback/desktop state.")

    def apply_movie_state(self, *, enable_hdr: bool) -> None:
        """Toggle HDR/WCG and disable VRR; never change playback refresh."""
        self._apply_state(hdr=enable_hdr, wcg=enable_hdr, vrr_policy=0)

    def apply_desktop_state(self) -> None:
        """Restore original mode/VRR policy, with desktop HDR/WCG off."""
        if self.saved_state is None:
            raise RuntimeError("Desktop restoration requires the pre-playback display snapshot.")
        self._apply_state(hdr=False, wcg=False, vrr_policy=self.saved_state.vrr_policy, mode_id=self.saved_state.mode_id)

    def restore(self) -> None:
        """On failed preparation, undo changes to the original display state."""
        if self.saved_state is not None:
            self._apply_state(hdr=self.saved_state.hdr, wcg=self.saved_state.wcg,
                              vrr_policy=self.saved_state.vrr_policy, mode_id=self.saved_state.mode_id)
