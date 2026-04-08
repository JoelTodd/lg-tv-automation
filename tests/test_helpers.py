"""Small pure-function tests for the refactored helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lg_tv_automation.constants import expected_input_app_id
from lg_tv_automation.display import select_mode_id
from lg_tv_automation.media import find_primary_media_path, normalize_mpv_args


class HelperTests(unittest.TestCase):
    def test_expected_input_app_id(self) -> None:
        self.assertEqual(expected_input_app_id("HDMI_1"), "com.webos.app.hdmi1")

    def test_normalize_mpv_args_strips_leading_separator(self) -> None:
        self.assertEqual(normalize_mpv_args(["--", "movie.mkv"]), ["movie.mkv"])

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


if __name__ == "__main__":
    unittest.main()
