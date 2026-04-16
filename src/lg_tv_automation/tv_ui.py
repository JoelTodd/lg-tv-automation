"""Screenshot-driven TV UI automation used for status checks and exploration.

This module remains in the repo because it is still useful for exploratory work
when LG exposes no documented API, and for reading UI-visible state that the
main control path cannot fetch directly.
"""

from __future__ import annotations

import asyncio
import colorsys
import io
from pathlib import Path
import ssl
import urllib.request

from bscpylgtv import WebOsClient
from PIL import Image

class LgTvUiAutomation:
    """Send remote keys and inspect screenshots to read TV UI state."""

    MASTER_SWITCH_BOX = (449, 40, 492, 72)
    ROW_SWITCH_BOX = (455, 455, 494, 484)
    HDMI_444_SWITCH_BOX = (350, 114, 384, 144)
    HDMI_QMS_SWITCH_BOX = (350, 160, 384, 190)
    HDMI_CEC_SWITCH_BOX = (350, 252, 384, 282)
    GAME_OPTIMIZER_HEADER_BOX = (180, 20, 780, 130)

    def __init__(self, client: WebOsClient, input_app_id: str):
        self.client = client
        self.input_app_id = input_app_id
        self._ssl_context = ssl._create_unverified_context()

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _press(self, button: str, delay: float = 0.4) -> None:
        await self.client.button(button)
        await self._sleep(delay)

    async def _press_many(self, button: str, count: int, delay: float = 0.35) -> None:
        for _ in range(count):
            await self._press(button, delay)

    async def _capture_image(self) -> Image.Image:
        """Capture a screenshot with light retrying.

        One-shot capture is reliable enough for automation, but it still fails
        sporadically during UI transitions. Retrying here keeps the caller logic
        much simpler.
        """

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                shot = await self.client.request("tv/executeOneShot", {})
                with urllib.request.urlopen(shot["imageUri"], context=self._ssl_context, timeout=15) as response:
                    return Image.open(io.BytesIO(response.read())).convert("RGB")
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    await self._sleep(1.0)
                    continue
        raise RuntimeError(f"TV screenshot capture failed after retries: {last_error}")

    @staticmethod
    def _saturated_pixel_count(
        image: Image.Image,
        box: tuple[int, int, int, int],
        hue_min: float,
        hue_max: float,
    ) -> int:
        """Count pixels in a simple HSV band.

        The UI toggles use high-saturation green or magenta accents. This cheap
        heuristic is brittle in theory but proved good enough in practice for
        the specific screens we care about.
        """

        count = 0
        for r, g, b in image.crop(box).getdata():
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if hue_min < h < hue_max and s > 0.2 and v > 0.2:
                count += 1
        return count

    def detect_master_on(self, image: Image.Image) -> bool:
        return self._saturated_pixel_count(image, self.MASTER_SWITCH_BOX, 0.72, 0.92) > 650

    def detect_row_toggle_on(self, image: Image.Image) -> bool:
        return self._saturated_pixel_count(image, self.ROW_SWITCH_BOX, 0.72, 0.92) > 150

    def detect_hdmi_settings_page(self, image: Image.Image) -> bool:
        quick_media_green = self._saturated_pixel_count(image, self.HDMI_QMS_SWITCH_BOX, 0.25, 0.45)
        cec_green = self._saturated_pixel_count(image, self.HDMI_CEC_SWITCH_BOX, 0.25, 0.45)
        return quick_media_green > 150 and cec_green > 150

    def detect_game_optimizer_page(self, image: Image.Image) -> bool:
        return self._saturated_pixel_count(image, self.GAME_OPTIMIZER_HEADER_BOX, 0.72, 0.92) > 5000

    def detect_444_on(self, image: Image.Image) -> bool:
        return self._saturated_pixel_count(image, self.HDMI_444_SWITCH_BOX, 0.25, 0.45) > 120

    @staticmethod
    def _save_artifact(image: Image.Image, artifact_dir: Path | None, filename: str) -> None:
        if artifact_dir is None:
            return
        artifact_dir.mkdir(parents=True, exist_ok=True)
        image.save(artifact_dir / filename)

    async def ensure_input_active(self) -> None:
        await self.client.launch_app(self.input_app_id)
        await self._sleep(2.0)

    async def close_overlay(self) -> None:
        """Always return to the HDMI input that keeps terminal-side comms alive."""

        await self._press("EXIT", 1.0)
        current_app = await self.client.get_current_app()
        if current_app != self.input_app_id:
            await self.client.launch_app(self.input_app_id)
            await self._sleep(1.5)

    async def _navigate_hdmi_settings(self) -> Image.Image:
        await self.ensure_input_active()
        await self.client.launch_app_with_params("com.palm.app.settings", {"target": "PictureMode"})
        await self._sleep(1.4)

        await self._press_many("LEFT", 4, 0.45)
        await self._capture_image()
        await self._press("DOWN", 0.35)
        await self._press("DOWN", 0.35)
        await self._capture_image()

        await self._press("RIGHT", 0.6)
        await self._capture_image()

        await self._press_many("DOWN", 8, 0.35)
        await self._capture_image()

        await self._press("RIGHT", 0.9)
        await self._capture_image()

        await self._press("DOWN", 0.5)
        await self._capture_image()

        await self._press("RIGHT", 0.9)
        await self._sleep(0.9)
        return await self._capture_image()

    async def open_hdmi_settings(self) -> Image.Image:
        """Reach the HDMI Settings page, using a second more patient pass if needed."""

        for _attempt in range(2):
            image = await self._navigate_hdmi_settings()
            if self.detect_hdmi_settings_page(image):
                return image
            try:
                await self.close_overlay()
            except Exception:
                pass
        raise RuntimeError("Failed to reach HDMI Settings reliably.")

    async def capture_hdmi_settings_state(self, *, artifact_dir: Path | None = None) -> dict[str, bool]:
        """Read 4:4:4 Pass Through from the HDMI settings page."""

        try:
            image = await self.open_hdmi_settings()
            if not self.detect_hdmi_settings_page(image):
                raise RuntimeError("HDMI Settings page verification failed.")
            self._save_artifact(image, artifact_dir, "hdmi-settings-final.png")
            return {"passthrough_444": self.detect_444_on(image)}
        finally:
            try:
                await self.close_overlay()
            except Exception:
                pass

    async def _wait_for_game_optimizer_page(self, *, timeout: float = 5.0, interval: float = 0.5) -> Image.Image:
        deadline = asyncio.get_running_loop().time() + timeout
        last_image: Image.Image | None = None
        while asyncio.get_running_loop().time() < deadline:
            image = await self._capture_image()
            last_image = image
            if self.detect_game_optimizer_page(image):
                return image
            await self._sleep(interval)
        if last_image is None:
            raise RuntimeError("Game Optimizer overlay never produced a screenshot.")
        raise RuntimeError("Game Optimizer overlay did not appear reliably.")

    async def open_game_optimizer_master(self, *, artifact_dir: Path | None = None) -> Image.Image:
        await self.ensure_input_active()
        await self.client.launch_app("com.webos.app.gameoptimizer")
        await self._sleep(1.8)
        launch_image = await self._wait_for_game_optimizer_page()
        self._save_artifact(launch_image, artifact_dir, "game-optimizer-launch.png")
        await self._press("RIGHT", 0.4)
        await self._press("RIGHT", 0.4)
        master_image = await self._wait_for_game_optimizer_page()
        self._save_artifact(master_image, artifact_dir, "game-optimizer-master.png")
        return master_image

    async def capture_game_optimizer_state(self, *, artifact_dir: Path | None = None) -> dict[str, bool]:
        """Read Game Optimizer master, VRR, and ALLM from the visible UI."""

        try:
            master_image = await self.open_game_optimizer_master(artifact_dir=artifact_dir)
            master_on = self.detect_master_on(master_image)
            if not master_on:
                await self._press("ENTER", 0.9)

            await self._press_many("DOWN", 6, 0.2)
            vrr_image = await self._capture_image()
            self._save_artifact(vrr_image, artifact_dir, "game-optimizer-vrr.png")
            vrr_on = self.detect_row_toggle_on(vrr_image)

            await self._press_many("DOWN", 3, 0.2)
            allm_image = await self._capture_image()
            self._save_artifact(allm_image, artifact_dir, "game-optimizer-allm.png")
            allm_on = self.detect_row_toggle_on(allm_image)

            if not master_on:
                await self._press_many("UP", 10, 0.2)
                await self._press("ENTER", 0.9)

            return {"game_optimizer_master": master_on, "vrr": vrr_on, "allm": allm_on}
        finally:
            try:
                await self.close_overlay()
            except Exception:
                pass
