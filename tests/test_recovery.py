"""Pairing migration checks never touch the real pairing database."""

from __future__ import annotations

from pathlib import Path
from contextlib import closing
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
            with closing(sqlite3.connect(legacy)) as db, db:
                db.execute("CREATE TABLE pairing (key TEXT)")
                db.execute("INSERT INTO pairing VALUES ('test-only-key')")
            original = legacy.read_bytes()
            connect = sqlite3.connect
            connections = []
            def track(*args, **kwargs):
                connection = connect(*args, **kwargs)
                connections.append(connection)
                return connection
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}), patch("lg_tv_automation.config.__file__", str(root / "src/lg_tv_automation/config.py")):
                with patch("lg_tv_automation.config.sqlite3.connect", side_effect=track):
                    target = pairing_key_path()
                    self.assertEqual(pairing_key_path(), target)
            self.assertEqual(len(connections), 2)
            for connection in connections:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
            self.assertEqual(legacy.read_bytes(), original)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute("SELECT key FROM pairing").fetchone()[0], "test-only-key")

    def test_first_use_ignores_unrelated_calling_directory_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caller, home = root / "caller", root / "home"
            caller.mkdir()
            home.mkdir()
            for folder, marker in ((caller, "unrelated-key"), (home, "home-key")):
                with closing(sqlite3.connect(folder / ".aiopylgtv.sqlite")) as db, db:
                    db.execute("CREATE TABLE pairing (key TEXT)")
                    db.execute("INSERT INTO pairing VALUES (?)", (marker,))
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(root / "config")}), patch.object(Path, "cwd", return_value=caller), patch.object(Path, "home", return_value=home), patch("lg_tv_automation.config.__file__", str(root / "checkout/src/lg_tv_automation/config.py")):
                target = pairing_key_path()
            with closing(sqlite3.connect(target)) as db:
                self.assertEqual(db.execute("SELECT key FROM pairing").fetchone()[0], "home-key")

    def test_existing_pairing_is_preserved_and_kept_private(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "lg-tv-automation"
            config.mkdir()
            target = config / "pairing.sqlite"
            target.write_bytes(b"existing database")
            target.chmod(0o644)
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": directory}):
                self.assertEqual(pairing_key_path(), target)
            self.assertEqual(target.read_bytes(), b"existing database")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
