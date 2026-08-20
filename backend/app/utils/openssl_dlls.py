from __future__ import annotations

import os
import sys
from pathlib import Path


def _has_openssl_pair(path: Path) -> bool:
    if not path.exists():
        return False
    names = {item.name.lower() for item in path.glob("*.dll")}
    return any(name.startswith("libssl") for name in names) and any(
        name.startswith("libcrypto") for name in names
    )


def _candidate_dll_dirs() -> list[Path]:
    dirs: list[Path] = []
    configured = os.environ.get("STORYDRIVER_OPENSSL_DLL_DIR")
    if configured:
        dirs.append(Path(configured))

    backend_dir = Path(__file__).resolve().parents[2]
    dirs.append(backend_dir / "runtime_dlls")
    dirs.append(Path(sys.base_prefix) / "DLLs")

    local_python_root = Path.home() / "AppData" / "Local" / "Programs" / "Python"
    if local_python_root.exists():
        dirs.extend(
            sorted(
                (path / "DLLs" for path in local_python_root.glob("Python*")),
                reverse=True,
            )
        )
    return dirs


def add_openssl_dll_directory() -> None:
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    try:
        import _ssl  # noqa: F401

        return
    except ImportError:
        pass

    for dll_dir in _candidate_dll_dirs():
        if not _has_openssl_pair(dll_dir):
            continue
        try:
            os.add_dll_directory(str(dll_dir))
            return
        except OSError:
            continue
