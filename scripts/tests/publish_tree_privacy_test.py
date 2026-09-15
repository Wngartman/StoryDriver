from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PARTS = {
    ".env",
    ".codex",
    "app.db",
    "backups",
    "cache",
    "generated_audio",
    "generated_images",
    "logs",
    "node_modules",
    "release",
    "voices",
    "webview2",
}
FORBIDDEN_SUFFIXES = {".db", ".gguf", ".onnx", ".safetensors", ".wav", ".mp3", ".flac", ".key", ".pfx"}
ALLOWED_MEDIA = {
    "assets/desktop/storydriver-loading.png",
    "assets/desktop/storydriver.ico",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / value.decode("utf-8") for value in result.stdout.split(b"\0") if value]


def main() -> int:
    errors: list[str] = []
    files = tracked_files()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if path.name == ".gitkeep":
            continue
        if relative == "backend/data/ui_presets/custom_presets.json":
            errors.append(f"forbidden runtime preset file: {relative}")
        parts = {part.lower() for part in path.relative_to(ROOT).parts}
        if parts & FORBIDDEN_PARTS:
            errors.append(f"forbidden private/runtime path: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES and relative not in ALLOWED_MEDIA:
            errors.append(f"forbidden private/model/media extension: {relative}")
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                errors.append(f"possible secret in tracked file: {relative}")
                break
    if errors:
        raise AssertionError("Publish tree privacy gate failed:\n" + "\n".join(sorted(set(errors))))
    print(f"PASS: {len(files)} tracked files contain no blocked databases, private media, model weights, or obvious secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
