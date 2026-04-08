"""Fedora/KScreen display-state helpers."""

from __future__ import annotations

import json
import time
from typing import Any

from .console import log
from .models import DisplaySnapshot
from .process import run_command


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
        timeout: float = 6.0,
        interval: float = 0.25,
    ) -> bool:
        """Poll KScreen until the requested state is visible."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output = load_display_output(self.output_name)
            mode_ok = expected_mode_id is None or str(output["currentModeId"]) == expected_mode_id
            if mode_ok and bool(output["hdr"]) == expected_hdr and bool(output["wcg"]) == expected_wcg:
                return True
            time.sleep(interval)
        return False

    def apply_movie_state(self, *, enable_hdr: bool, force_60hz: bool, dry_run: bool) -> None:
        """Apply the Fedora-side playback state.

        The default path leaves refresh rate alone. ``force_60hz`` remains
        available because it was useful during earlier experiments and is still
        a legitimate fallback.
        """

        output = load_display_output(self.output_name)
        actions: list[str] = []
        target_mode_id: str | None = None

        if force_60hz:
            target_mode_id = select_mode_id(output, 3840, 2160, 60.0)
            if target_mode_id and str(output["currentModeId"]) != target_mode_id:
                actions.append(f"output.{self.output_name}.mode.{target_mode_id}")
            elif target_mode_id is None:
                log("4K60 mode not found; leaving refresh unchanged.")

        actions.append(f"output.{self.output_name}.hdr.{'enable' if enable_hdr else 'disable'}")
        actions.append(f"output.{self.output_name}.wcg.{'enable' if enable_hdr else 'disable'}")

        if not actions:
            return

        log(f"Applying display state: {' '.join(actions)}")
        if dry_run:
            return

        run_command(["kscreen-doctor", *actions])
        expected_mode_id = target_mode_id if force_60hz and target_mode_id else None
        if not self._wait_for_state(expected_mode_id, enable_hdr, enable_hdr):
            raise RuntimeError("Display state did not reach the requested movie preset.")

    def apply_desktop_state(self, *, dry_run: bool) -> None:
        """Apply the normal desktop display state after playback."""

        output = load_display_output(self.output_name)
        actions: list[str] = []
        target_mode_id: str | None = None

        if self.saved_state is not None and str(output["currentModeId"]) != self.saved_state.mode_id:
            target_mode_id = self.saved_state.mode_id
            actions.append(f"output.{self.output_name}.mode.{target_mode_id}")

        actions.append(f"output.{self.output_name}.hdr.disable")
        actions.append(f"output.{self.output_name}.wcg.disable")

        log(f"Applying desktop display state: {' '.join(actions)}")
        if dry_run:
            return

        run_command(["kscreen-doctor", *actions])
        if not self._wait_for_state(target_mode_id, False, False):
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

        log(f"Restoring display state: {' '.join(actions)}")
        if dry_run:
            return

        run_command(["kscreen-doctor", *actions])
        if not self._wait_for_state(
            self.saved_state.mode_id,
            self.saved_state.hdr,
            self.saved_state.wcg,
        ):
            raise RuntimeError("Display state did not restore to the saved state.")
