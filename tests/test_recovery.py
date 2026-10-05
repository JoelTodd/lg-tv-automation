"""Pairing migration checks never touch the real pairing database."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from lg_tv_automation.config import pairing_key_path


class RecoveryTests(unittest.TestCase):
    def test_pairing_migration_preserves_keys_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / ".aiopylgtv.sqlite"
            with sqlite3.connect(legacy) as db:
                db.execute("CREATE TABLE pairing (key TEXT)")
                db.execute("INSERT INTO pairing VALUES ('test-only-key')")
            original = legacy.read_bytes()
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}), patch.object(Path, "cwd", return_value=root):
                target = pairing_key_path()
                self.assertEqual(pairing_key_path(), target)
            self.assertEqual(legacy.read_bytes(), original)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute("SELECT key FROM pairing").fetchone()[0], "test-only-key")
