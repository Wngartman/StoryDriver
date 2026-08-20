from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
WM_CLOSE = 0x0010


def safe_fresh_directory(path: Path) -> None:
    resolved = path.resolve()
    build = BUILD.resolve()
    if build not in resolved.parents or resolved == build:
        raise RuntimeError(f"Refusing package test outside the build root: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def get_json(url: str, timeout: float = 1.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def windows_for_pid(pid: int) -> list[dict]:
    user32 = ctypes.windll.user32
    values: list[dict] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        values.append({"handle": int(hwnd), "title": title.value})
        return True

    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return values


def close_windows(pid: int) -> None:
    user32 = ctypes.windll.user32
    for window in windows_for_pid(pid):
        user32.PostMessageW(window["handle"], WM_CLOSE, 0, 0)


def database_integrity(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=ROOT / "release" / "StoryDriver-Portable-x64.zip")
    parser.add_argument("--executable", type=Path, help="Test an already installed StoryDriver.exe")
    parser.add_argument("--allow-development-data", action="store_true", help="Allow an installed migration test to reuse the preserved D:\\StoryDriver data root")
    parser.add_argument("--leave-running", action="store_true")
    args = parser.parse_args()

    if args.executable:
        executable = args.executable.resolve()
        package_root = executable.parent
        mode = "installed"
    else:
        target = BUILD / "tests" / "portable"
        safe_fresh_directory(target)
        with zipfile.ZipFile(args.archive.resolve()) as package:
            package.extractall(target)
        package_root = target / "StoryDriver"
        executable = package_root / "StoryDriver.exe"
        mode = "portable"
    required = [
        executable,
        package_root / "backend" / "StoryDriverBackend.exe",
        package_root / "backend" / "frontend_dist" / "index.html",
        package_root / "runtimes" / "llama.cpp" / "llama-server.exe",
    ]
    if missing := [str(path) for path in required if not path.is_file()]:
        raise AssertionError(f"Portable package is incomplete: {missing}")
    if mode == "portable" and not (package_root / "portable.marker").is_file():
        raise AssertionError("Portable marker is missing")

    started = time.perf_counter()
    process = subprocess.Popen(
        [str(executable)],
        cwd=package_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    window_seconds = None
    backend_seconds = None
    health: dict = {}
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Portable StoryDriver exited with code {process.returncode}")
            elapsed = time.perf_counter() - started
            if window_seconds is None and windows_for_pid(process.pid):
                window_seconds = elapsed
            if backend_seconds is None:
                try:
                    health = get_json("http://127.0.0.1:8001/health")
                    backend_seconds = elapsed
                except (OSError, urllib.error.URLError, TimeoutError):
                    pass
            if window_seconds is not None and backend_seconds is not None:
                break
            time.sleep(0.1)
        if window_seconds is None or backend_seconds is None:
            raise TimeoutError("Portable window or backend did not become ready within 45 seconds")

        services = get_json("http://127.0.0.1:8001/system/services", timeout=3)
        if mode == "portable":
            database = package_root / "data" / "app.db"
        else:
            config = json.loads((package_root / "storydriver.config.json").read_text(encoding="utf-8"))
            database = Path(config["dataRoot"]) / "app.db"
        result = {
            "passed": True,
            "mode": mode,
            "package_root": str(package_root),
            "shell_pid": process.pid,
            "visible_windows": windows_for_pid(process.pid),
            "window_visible_seconds": round(window_seconds, 3),
            "backend_ready_seconds": round(backend_seconds, 3),
            "health": health,
            "services": services,
            "database": str(database),
            "database_exists": database.is_file(),
            "database_integrity": database_integrity(database),
            "development_database_used": database.resolve() == (ROOT / "backend" / "data" / "app.db").resolve(),
            "portable_marker": (package_root / "portable.marker").is_file(),
            "left_running": args.leave_running,
        }
        if result["development_database_used"] and not args.allow_development_data:
            raise AssertionError("Package unexpectedly used the development database")
        print(json.dumps(result, indent=2))
        return 0
    finally:
        if not args.leave_running and process.poll() is None:
            close_windows(process.pid)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
