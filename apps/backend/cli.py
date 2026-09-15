from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen
import uuid
import zipfile


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def app_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def read_config(root: Path) -> dict:
    path = root / "storydriver.config.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def data_root(arguments: argparse.Namespace) -> Path:
    root = app_root()
    configured = arguments.data_root or os.getenv("STORYDRIVER_DATA_DIR") or read_config(root).get("dataRoot")
    if configured:
        return Path(configured).expanduser().resolve()
    if (root / "portable.marker").is_file():
        return (root / "data").resolve()
    if not getattr(sys, "frozen", False):
        return (root / "backend" / "data").resolve()
    return (Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "StoryDriver").resolve()


def port_open(port: int = 8001) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False


def database_integrity(path: Path) -> str:
    if not path.is_file():
        return "missing"
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def database_counts(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("sessions", "scenes", "scene_versions", "characters", "relationships", "story_memories")
            if table in tables
        }
    finally:
        connection.close()


def backup_database(root: Path, destination: Path | None = None) -> Path:
    source = root / "app.db"
    if not source.is_file():
        raise FileNotFoundError(f"StoryDriver database not found: {source}")
    destination = destination or root / "backups" / "manual" / f"app-{utc_stamp()}-{uuid.uuid4().hex[:8]}.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp")
    source_connection = sqlite3.connect(str(source))
    target_connection = sqlite3.connect(str(temporary))
    try:
        source_connection.backup(target_connection)
        target_connection.execute("PRAGMA journal_mode=DELETE")
    finally:
        target_connection.close()
        source_connection.close()
    if database_integrity(temporary) != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError("The backup failed SQLite integrity validation.")
    os.replace(temporary, destination)
    return destination


def cmd_status(arguments: argparse.Namespace) -> dict:
    root = data_root(arguments)
    response: dict = {"running": False, "port_in_use": port_open(arguments.port), "url": f"http://127.0.0.1:{arguments.port}", "data_root": str(root)}
    if response["port_in_use"]:
        try:
            with urlopen(f"http://127.0.0.1:{arguments.port}/health", timeout=2) as handle:
                health = json.loads(handle.read().decode("utf-8"))
                response["running"] = health.get("app") == "StoryDriver" and health.get("ok") is True
        except (OSError, URLError, ValueError):
            pass
    if response["running"]:
        try:
            with urlopen(f"http://127.0.0.1:{arguments.port}/system/services", timeout=2) as handle:
                response["services"] = json.loads(handle.read().decode("utf-8"))
        except (OSError, URLError, ValueError) as error:
            response["service_error"] = str(error)
    return response


def cmd_doctor(arguments: argparse.Namespace) -> dict:
    root = data_root(arguments)
    database = root / "app.db"
    base = app_root()
    return {
        "ok": database_integrity(database) in {"ok", "missing"},
        "data_root": str(root),
        "database": str(database),
        "database_integrity": database_integrity(database),
        "counts": database_counts(database),
        "desktop_executable": (base / "StoryDriver.exe").is_file(),
        "backend_executable": (base / "backend" / "StoryDriverBackend.exe").is_file(),
        "llama_cpp": (base / "runtimes" / "llama.cpp" / "llama-server.exe").is_file(),
        "backend_running": port_open(arguments.port),
    }


def cmd_backup(arguments: argparse.Namespace) -> dict:
    root = data_root(arguments)
    path = backup_database(root)
    return {"ok": True, "backup": str(path), "integrity": database_integrity(path), "bytes": path.stat().st_size}


def table_rows(database: Path, table: str) -> list[dict]:
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')] if exists else []
    finally:
        connection.close()


def cmd_export(arguments: argparse.Namespace) -> dict:
    root = data_root(arguments)
    export_path = root / "exports" / f"StoryDriver-export-{utc_stamp()}.zip"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = root / "temp" / f"export-{uuid.uuid4().hex}.db"
    backup_database(root, snapshot)
    metadata = {
        "format": "storydriver-local-export-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": database_counts(snapshot),
        "includes_voice_recordings": False,
        "includes_model_weights": False,
    }
    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.write(snapshot, "app.db")
        archive.writestr("manifest.json", json.dumps(metadata, indent=2))
        for table in ("app_settings", "model_presets", "task_model_profiles"):
            archive.writestr(f"settings/{table}.json", json.dumps(table_rows(snapshot, table), indent=2))
        library = root / "config" / "model-library.json"
        if library.is_file():
            archive.write(library, "settings/model-library.json")
        voices = root / "voices"
        if voices.is_dir():
            for path in voices.rglob("*.json"):
                archive.write(path, f"voice-metadata/{path.relative_to(voices).as_posix()}")
    snapshot.unlink(missing_ok=True)
    return {"ok": True, "export": str(export_path), "bytes": export_path.stat().st_size, **metadata}


def cmd_restore(arguments: argparse.Namespace) -> dict:
    if port_open(arguments.port):
        raise RuntimeError("Quit StoryDriver before restoring a database.")
    root = data_root(arguments)
    selected = arguments.source or arguments.backup
    if not selected:
        raise ValueError("Specify a database or export ZIP to restore.")
    source = Path(selected).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    temporary = root / "temp" / f"restore-{uuid.uuid4().hex}.db"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            with archive.open("app.db") as source_handle, temporary.open("wb") as target:
                shutil.copyfileobj(source_handle, target)
    else:
        origin = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(str(temporary))
            try:
                origin.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
            finally:
                target.close()
        finally:
            origin.close()
    if database_integrity(temporary) != "ok":
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Restore source failed SQLite integrity validation.")
    check = sqlite3.connect(str(temporary))
    try:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"sessions", "scenes", "scene_versions"}.issubset(tables):
            raise ValueError("This is not a StoryDriver database.")
        check.execute("PRAGMA journal_mode=DELETE")
    finally:
        check.close()
    previous = backup_database(root, root / "backups" / "pre-restore" / f"app-{utc_stamp()}.db") if (root / "app.db").is_file() else None
    # A WAL belongs to the old database and must never be replayed onto the restored file.
    if (root / "app.db").is_file():
        existing = sqlite3.connect(str(root / "app.db"), timeout=1)
        try:
            existing.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            existing.execute("PRAGMA journal_mode=DELETE")
        finally:
            existing.close()
    os.replace(temporary, root / "app.db")
    return {"ok": True, "restored": str(source), "pre_restore_backup": str(previous) if previous else None}


def cmd_cleanup(arguments: argparse.Namespace) -> dict:
    root = data_root(arguments)
    temp_root = (root / "temp").resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc).timestamp() - max(arguments.older_than_days, 1) * 86400
    candidates = [path for path in temp_root.rglob("*") if path.is_file() and path.stat().st_mtime < cutoff]
    removed = 0
    removed_bytes = 0
    if arguments.apply:
        for path in candidates:
            resolved = path.resolve()
            if temp_root not in resolved.parents:
                raise RuntimeError(f"Refusing cleanup outside the data temp root: {resolved}")
            removed_bytes += resolved.stat().st_size
            resolved.unlink()
            removed += 1
    return {
        "ok": True,
        "dry_run": not arguments.apply,
        "candidate_files": len(candidates),
        "candidate_bytes": sum(path.stat().st_size for path in candidates) if not arguments.apply else removed_bytes,
        "removed_files": removed,
        "temp_root": str(temp_root),
    }


def cmd_start(arguments: argparse.Namespace) -> dict:
    executable = app_root() / "StoryDriver.exe"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(executable), "--data-root", str(data_root(arguments))], cwd=str(executable.parent), creationflags=flags, close_fds=True)
    return {"ok": True, "started": str(executable)}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="StoryDriverCLI", description="Local StoryDriver maintenance utility")
    value.add_argument("--data-root")
    value.add_argument("--port", type=int, default=None)
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("status", "doctor", "backup", "export", "start"):
        commands.add_parser(name)
    restore = commands.add_parser("restore")
    restore.add_argument("source", nargs="?")
    restore.add_argument("--backup", help="Alias for the restore source")
    cleanup = commands.add_parser("cleanup")
    cleanup_mode = cleanup.add_mutually_exclusive_group()
    cleanup_mode.add_argument("--apply", action="store_true")
    cleanup_mode.add_argument("--dry-run", action="store_true")
    cleanup.add_argument("--older-than-days", type=int, default=7)
    return value


def main() -> int:
    arguments = parser().parse_args()
    arguments.port = arguments.port or read_config(app_root()).get("backendPort", 8001)
    commands = {
        "status": cmd_status,
        "doctor": cmd_doctor,
        "backup": cmd_backup,
        "export": cmd_export,
        "restore": cmd_restore,
        "cleanup": cmd_cleanup,
        "start": cmd_start,
    }
    try:
        result = commands[arguments.command](arguments)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
