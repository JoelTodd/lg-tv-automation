"""Fedora/KScreen display-state helpers."""

from __future__ import annotations

import json
import time
from typing import Any

from .console import log
from .constants import DEFAULT_DESKTOP_VRR_POLICY
from .models import DisplaySnapshot
from .process import run_command


VRR_POLICY_TO_NAME = {
    0: "never",
    1: "always",
    2: "automatic",
}


def has_vrr_policy(output: dict[str, Any]) -> bool:
    """Return whether KScreen exposes a configurable VRR policy."""

    return output.get("vrrPolicy") in VRR_POLICY_TO_NAME


def load_display_output(name: str, retries: int = 20, interval: float = 0.25) -> dict[str, Any]:
    """Load one output entry from ``kscreen-doctor -j``.

    The retry loop is deliberate. During HDR changes the NVIDIA/KWin stack can
    briefly return an incomplete output list, and immediate failure would leave
    restore logic in a bad state.
    """

    for attempt in range(retries):
        config = json.loads(run_command(["kscreen-doctor", "-j"]).stdout)
        for output in config["outputs"]:
            if output["name"] == name:
                return output
        if attempt < retries - 1:
            time.sleep(interval)
    raise RuntimeError(f"Display output {name!r} not found in kscreen-doctor output.")


def select_mode_id(output: dict[str, Any], width: int, height: int, refresh: float) -> str | None:
    """Find the closest matching mode id for the requested geometry."""

    best: tuple[float, str] | None = None
    for mode in output["modes"]:
        size = mode["size"]
        if size["width"] != width or size["height"] != height:
            continue
        delta = abs(float(mode["refreshRate"]) - refresh)
        if best is None or delta < best[0]:
            best = (delta, str(mode["id"]))
    if best and best[0] < 1.0:
        return best[1]
    return None


def current_mode_size(output: dict[str, Any]) -> tuple[int, int] | None:
    """Return the size of the currently active output mode."""

    current_mode_id = str(output["currentModeId"])
    for mode in output["modes"]:
        if str(mode["id"]) == current_mode_id:
            size = mode["size"]
            return int(size["width"]), int(size["height"])
    return None


def select_highest_refresh_mode_id(output: dict[str, Any], width: int, height: int) -> str | None:
    """Find the highest-refresh mode id for the requested geometry."""

    best: tuple[float, str] | None = None
    for mode in output["modes"]:
        size = mode["size"]
        if size["width"] != width or size["height"] != height:
            continue
        refresh = float(mode["refreshRate"])
        if best is None or refresh > best[0]:
            best = (refresh, str(mode["id"]))
    return None if best is None else best[1]


class DisplayController:
    """Capture, apply, and restore the Fedora output state used for playback."""

    def __init__(self, output_name: str):
        self.output_name = output_name
        self.saved_state: DisplaySnapshot | None = None

    def capture(self) -> DisplaySnapshot:
        """Capture the current mode and HDR state for later restoration."""

        output = load_display_output(self.output_name)
        self.saved_state = DisplaySnapshot(
            mode_id=str(output["currentModeId"]),
            hdr=bool(output["hdr"]),
            wcg=bool(output["wcg"]),
            vrr_policy=output.get("vrrPolicy"),
        )
        return self.saved_state

    def _wait_for_state(
        self,
        expected_mode_id: str | None,
        expected_hdr: bool,
        expected_wcg: bool,
        expected_vrr_policy: Any = None,
        timeout: float = 6.0,
        interval: float = 0.25,
    ) -> bool:
        """Poll KScreen until the requested state is visible."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = load_display_output(self.output_name)
            mode_ok = expected_mode_id is None or str(output["currentModeId"]) == expected_mode_id
            vrr_ok = expected_vrr_policy is None or output.get("vrrPolicy") == expected_vrr_policy
            if mode_ok and bool(output["hdr"]) == expected_hdr and bool(output["wcg"]) == expected_wcg and vrr_ok:
                return True
            time.sleep(interval)
        return False

    def apply_movie_state(
        self,
        *,
        enable_hdr: bool,
        force_60hz: bool,
        target_refresh: float | None,
        dry_run: bool,
    ) -> None:
        """Apply the Fedora-side playback state.

        The default path leaves refresh rate alone. ``force_60hz`` remains
        available because it was useful during earlier experiments and is still
        a legitimate fallback.
        """

        output = load_display_output(self.output_name)
        actions: list[str] = []
        target_mode_id: str | None = None

        target_refresh_hz = 60.0 if force_60hz else target_refresh
        if target_refresh_hz is not None:
            size = current_mode_size(output)
            if size is None:
                log("Current display mode size could not be determined; leaving refresh unchanged.")
            else:
                width, height = size
                target_mode_id = select_mode_id(output, width, height, target_refresh_hz)
                if target_mode_id and str(output["currentModeId"]) != target_mode_id:
                    actions.append(f"output.{self.output_name}.mode.{target_mode_id}")
                elif target_mode_id is None:
                    log(
                        f"No {width}x{height}@{target_refresh_hz:.3f} mode found; leaving refresh unchanged."
                    )

        expected_vrr_policy = None
        if has_vrr_policy(output):
            actions.append(f"output.{self.output_name}.vrrpolicy.never")
            expected_vrr_policy = 0
        else:
            log("VRR policy is unavailable; treating the sink as VRR-incapable.")
        actions.append(f"output.{self.output_name}.hdr.{'enable' if enable_hdr else 'disable'}")
        actions.append(f"output.{self.output_name}.wcg.{'enable' if enable_hdr else 'disable'}")

        if not actions:
            return

        log(f"Applying display state: {' '.join(actions)}")
        if dry_run:
            return

        run_command(["kscreen-doctor", *actions])
        expected_mode_id = target_mode_id if target_mode_id else None
        if not self._wait_for_state(expected_mode_id, enable_hdr, enable_hdr, expected_vrr_policy):
            raise RuntimeError("Display state did not reach the requested movie preset.")

    def apply_desktop_state(self, *, dry_run: bool) -> None:
        """Apply the normal desktop display state after playback."""

        output = load_display_output(self.output_name)
        actions: list[str] = []
        target_mode_id: str | None = None

        if self.saved_state is not None:
            if str(output["currentModeId"]) != self.saved_state.mode_id:
                target_mode_id = self.saved_state.mode_id
                actions.append(f"output.{self.output_name}.mode.{target_mode_id}")
            target_vrr_policy = self.saved_state.vrr_policy
        else:
            target_vrr_policy = DEFAULT_DESKTOP_VRR_POLICY
            size = current_mode_size(output)
            if size is None:
                log("Current display mode size could not be determined; leaving mode unchanged.")
            else:
                width, height = size
                fallback_mode_id = select_highest_refresh_mode_id(output, width, height)
                if fallback_mode_id is None:
                    log(f"No desktop-class mode found for {width}x{height}; leaving mode unchanged.")
                elif str(output["currentModeId"]) != fallback_mode_id:
                    target_mode_id = fallback_mode_id
                    actions.append(f"output.{self.output_name}.mode.{target_mode_id}")

        if target_vrr_policy in VRR_POLICY_TO_NAME and has_vrr_policy(output):
            actions.append(f"output.{self.output_name}.vrrpolicy.{VRR_POLICY_TO_NAME[target_vrr_policy]}")
        elif target_vrr_policy in VRR_POLICY_TO_NAME:
            log("VRR policy is unavailable; skipping desktop VRR policy restore.")
            target_vrr_policy = None

        actions.append(f"output.{self.output_name}.hdr.disable")
        actions.append(f"output.{self.output_name}.wcg.disable")

        log(f"Applying desktop display state: {' '.join(actions)}")
        if dry_run:
            return

        run_command(["kscreen-doctor", *actions])
        expected_mode_id = target_mode_id if target_mode_id else None
        if not self._wait_for_state(expected_mode_id, False, False, target_vrr_policy):
            raise RuntimeError("Display state did not reach the requested desktop preset.")

    def restore(self, *, dry_run: bool) -> None:
        """Restore the exact captured output state."""

        if self.saved_state is None:
            return

        actions = [
            f"output.{self.output_name}.mode.{self.saved_state.mode_id}",
            f"output.{self.output_name}.hdr.{'enable' if self.saved_state.hdr else 'disable'}",
            f"output.{self.output_name}.wcg.{'enable' if self.saved_state.wcg else 'disable'}",
        ]
        output = load_display_output(self.output_name)
        expected_vrr_policy = self.saved_state.vrr_policy
        if self.saved_state.vrr_policy in VRR_POLICY_TO_NAME and has_vrr_policy(output):
            actions.insert(1, f"output.{self.output_name}.vrrpolicy.{VRR_POLICY_TO_NAME[self.saved_state.vrr_policy]}")
        elif self.saved_state.vrr_policy in VRR_POLICY_TO_NAME:
            log("VRR policy is unavailable; skipping exact VRR policy restore.")
            expected_vrr_policy = None

        log(f"Restoring display state: {' '.join(actions)}")
        if dry_run:
            return

        run_command(["kscreen-doctor", *actions])
        if not self._wait_for_state(
            self.saved_state.mode_id,
            self.saved_state.hdr,
            self.saved_state.wcg,
            expected_vrr_policy,
        ):
            raise RuntimeError("Display state did not restore to the saved state.")
