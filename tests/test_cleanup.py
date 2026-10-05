"""Cleanup-script checks are read-only and need neither sudo nor TV hardware."""

from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/cleanup-legacy-system-links.sh"


class CleanupTests(unittest.TestCase):
    def test_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_options_are_rejected_before_cleanup(self):
        result = subprocess.run(["bash", str(SCRIPT), "--unknown"], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Usage:", result.stderr)
        self.assertNotIn("Removed", result.stdout)

    def test_dry_run_never_changes_system_links(self):
        paths = [Path("/usr/local/bin/lg-tv-edid"), Path("/usr/local/bin/lg-tv-ui-probe")]
        def snapshots():
            return [(path.is_symlink(), str(path.readlink()) if path.is_symlink() else path.exists()) for path in paths]
        before = snapshots()
        result = subprocess.run(["bash", str(SCRIPT), "--dry-run"], capture_output=True, text=True, timeout=5)
        self.assertEqual(snapshots(), before)
        # A foreign file/link may exist on another machine; refusing it is safe.
        self.assertIn(result.returncode, (0, 1))
        self.assertNotIn("Removed symlink:", result.stdout)
