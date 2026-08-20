from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess


ROOT = Path(r"D:\StoryDriver")
QWEN_PYTHON = ROOT / "tts_engines" / "qwen3_tts" / "venv" / "Scripts" / "python.exe"
DRIVER = ROOT / "scripts" / "tests" / "qwen_continuous_browser_driver.cjs"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=int, choices=(6, 12), required=True)
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--custom-voice", action="store_true")
    parser.add_argument("--automatic-direction", action="store_true")
    args = parser.parse_args()
    command = [
        str(QWEN_PYTHON),
        str(ROOT / "scripts" / "qwen_continuous_playback_test.py"),
        "--minutes", str(args.minutes),
        "--port", str(args.port),
        "--backend", "cpu",
        "--dtype", "bfloat16",
        "--attention", "sdpa",
        "--batch-size", "4",
        "--threads", "4",
        "--interop-threads", "4",
        "--no-manage-gemma",
    ]
    if args.custom_voice:
        command.append("--custom-voice")
    if args.automatic_direction:
        command.append("--automatic-direction")
    environment = os.environ.copy()
    environment.update(
        {
            "TMP": str(ROOT / "backend" / "data" / "temp"),
            "TEMP": str(ROOT / "backend" / "data" / "temp"),
            "STORYDRIVER_BROWSER_PATH": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "NODE_PATH": r"C:\Users\wngar\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules\.pnpm\node_modules",
        }
    )
    server = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
    )
    try:
        timeout_ms = (args.minutes * 60 + 900) * 1000
        driver = subprocess.run(
            ["node.exe", str(DRIVER), str(args.port), str(timeout_ms)],
            cwd=ROOT,
            env=environment,
            timeout=(timeout_ms / 1000) + 60,
        )
        server.wait(timeout=720)
        return 0 if driver.returncode == 0 and server.returncode == 0 else 1
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
