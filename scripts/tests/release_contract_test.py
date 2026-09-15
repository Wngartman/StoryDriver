from __future__ import annotations
import argparse
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TEMP = ROOT / ".tools" / "contract-tests"
TEMP.mkdir(parents=True, exist_ok=True)
os.environ["STORYDRIVER_BASE_DIR"] = str(ROOT)
os.environ["STORYDRIVER_DATA_DIR"] = str(TEMP)
os.environ["STORYDRIVER_DB_PATH"] = str(TEMP / "app.db")
os.environ["STORYDRIVER_AUTO_START_KOKORO"] = "false"
sys.path.insert(0, str(ROOT / "backend"))
spec = importlib.util.spec_from_file_location("maintenance_cli", ROOT / "apps" / "backend" / "cli.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def fixture(path: Path, text: str):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    for table in ("sessions", "scenes", "scene_versions"):
        connection.execute(f"CREATE TABLE {table} (value TEXT)")
        connection.execute(f"INSERT INTO {table} VALUES (?)", (text,))
    connection.commit()
    return connection


class ReleaseContracts(unittest.TestCase):
    def test_cli_live_wal_backup_and_restore(self):
        with tempfile.TemporaryDirectory(dir=TEMP) as directory:
            root = Path(directory)
            connection = fixture(root / "app.db", "original")
            backup = cli.backup_database(root)
            self.assertEqual(cli.database_integrity(backup), "ok")
            connection.execute("UPDATE scenes SET value='newer'")
            connection.commit()
            connection.close()
            arguments = argparse.Namespace(data_root=str(root), source=str(backup), backup=None, port=65534)
            with patch.object(cli, "port_open", return_value=False):
                result = cli.cmd_restore(arguments)
            self.assertTrue(result["ok"])
            check = sqlite3.connect(root / "app.db")
            self.assertEqual(check.execute("SELECT value FROM scenes").fetchone()[0], "original")
            check.close()
            self.assertTrue(Path(result["pre_restore_backup"]).exists())

    def test_cli_flags(self):
        self.assertTrue(cli.parser().parse_args(["cleanup", "--dry-run"]).dry_run)
        self.assertEqual(cli.parser().parse_args(["restore", "--backup", "x.db"]).backup, "x.db")

    def test_default_provider_and_prompt_size(self):
        from app.settings.store import merged_model_settings
        from app.schemas import ModelSettings
        value = merged_model_settings()
        self.assertEqual(value["provider"], "llama_cpp")
        self.assertEqual(value["provider_url"], "http://127.0.0.1:12345/v1")
        self.assertEqual(len(ModelSettings(system_prompt="x" * 300000).system_prompt), 300000)
        with self.assertRaises(ValueError):
            ModelSettings(system_prompt="x", temperature=-1)

    def test_installer_never_recursively_deletes_selected_paths(self):
        installer = (ROOT / "installer" / "StoryDriver.nsi").read_text()
        self.assertNotIn('RMDir /r "$DataRoot"', installer)
        self.assertNotIn('RMDir /r "$INSTDIR"', installer)
        self.assertIn("uninstall-files.nsh", installer)

    def test_runtime_ownership_and_options(self):
        import asyncio
        from app.generation.provider_runtime import ProviderRegistry
        registry = ProviderRegistry()
        self.assertEqual(registry.get("llama_cpp", "http://127.0.0.1:9988/v1").endpoint, "http://127.0.0.1:12345/v1")
        result = asyncio.run(registry.llama.health())
        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "unloaded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
