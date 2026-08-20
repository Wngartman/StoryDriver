from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "backend" / "data" / "logs"
LOG_PATH = LOG_DIR / "comfyui_custom_node_dependency_repair.log"

ALLOWLIST = [
    ("matplotlib", "matplotlib"),
    ("cv2", "opencv-python"),
    ("piexif", "piexif"),
    ("platformdirs", "platformdirs"),
    ("numba", "numba"),
]

BLOCKED_PACKAGE_TERMS = ("torch", "cuda", "nvidia", "rocm")


def log(lines: list[str]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")


def missing_packages() -> list[tuple[str, str]]:
    missing: list[tuple[str, str]] = []
    for import_name, package_name in ALLOWLIST:
        if importlib.util.find_spec(import_name) is None:
            missing.append((import_name, package_name))
    return missing


def validate_package_names(packages: list[str]) -> None:
    for package in packages:
        lower = package.lower()
        if any(term in lower for term in BLOCKED_PACKAGE_TERMS):
            raise SystemExit(f"Refusing to install blocked package name: {package}")
        if package not in {allowed[1] for allowed in ALLOWLIST}:
            raise SystemExit(f"Refusing non-allowlisted package: {package}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair only allowlisted ComfyUI custom-node Python dependencies.")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install missing allowlisted packages into the Python environment running this script.",
    )
    args = parser.parse_args()

    timestamp = datetime.now().isoformat(timespec="seconds")
    python = Path(sys.executable)
    lines = [
        f"===== {timestamp} =====",
        f"Python: {python}",
        f"Version: {sys.version}",
        "Mode: install" if args.install else "Mode: dry-run",
    ]

    if "Documents\\ComfyUI\\.venv\\Scripts\\python.exe".lower() not in str(python).lower():
        lines.append("WARNING: This script is not running under C:\\Users\\wngar\\Documents\\ComfyUI\\.venv\\Scripts\\python.exe.")
        lines.append("Refusing install mode outside the target ComfyUI venv.")
        print("\n".join(lines))
        log(lines)
        return 2 if args.install else 0

    missing = missing_packages()
    if not missing:
        lines.append("All allowlisted dependency imports are already present.")
        print("\n".join(lines))
        log(lines)
        return 0

    packages = [package for _, package in missing]
    validate_package_names(packages)
    lines.append("Missing imports:")
    for import_name, package in missing:
        lines.append(f"  {import_name} -> {package}")

    command = [
        str(python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade-strategy",
        "only-if-needed",
        *packages,
    ]
    lines.append("Install command:")
    lines.append("  " + " ".join(f'"{part}"' if " " in part else part for part in command))

    if not args.install:
        lines.append("Dry run only. Re-run with: D:\\StoryDriver\\scripts\\repair_comfyui_custom_node_deps.bat install")
        print("\n".join(lines))
        log(lines)
        return 0

    lines.append("Installing allowlisted packages into the ComfyUI venv...")
    print("\n".join(lines))
    result = subprocess.run(command, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace")
    final_lines = [
        f"pip exit code: {result.returncode}",
        f"Log: {LOG_PATH}",
        "Restart ComfyUI after install so custom nodes can import the new packages.",
    ]
    print("\n".join(final_lines))
    log([*lines, *final_lines])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
