from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.ui_preset_store import (  # noqa: E402
    PresetValidationError,
    delete_custom_preset,
    duplicate_preset,
    export_preset,
    import_custom_preset,
    list_custom_presets,
    normalize_custom_preset,
)


def make_preset(preset_id: str) -> dict:
    return {
        "id": preset_id,
        "displayName": "Smoke Test Preset",
        "shortDescription": "Temporary preset used by the UI preset manager smoke test.",
        "category": "Custom",
        "tags": ["smoke", "local"],
        "version": "1.0.0",
        "author": "StoryDriver smoke test",
        "source": "local smoke test",
        "compatibilityVersion": "1",
        "tokens": {
            "colors": {
                "ink": "#090a0c",
                "panel": "#10131a",
                "panelSoft": "#171b24",
                "line": "#334155",
                "story": "#f3efe7",
                "muted": "#a5adba",
                "moss": "#93c5a1",
                "ember": "#d8a66b",
                "tide": "#88c4dd",
            },
            "typography": {
                "sans": "Inter, ui-sans-serif, system-ui, sans-serif",
                "story": "Georgia, Charter, serif",
            },
            "surfaces": {
                "sunken": "#0b0d12",
                "hover": "#1f2937",
                "warning": "#23170d",
            },
            "texture": {
                "css": "linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px)",
                "size": "36px 36px",
                "opacity": 1,
            },
        },
    }


def main() -> int:
    suffix = datetime.now().strftime("%Y%m%d%H%M%S")
    preset_id = f"smoke-preset-{suffix}"
    duplicate_id = None
    export_path = None
    failures: list[str] = []

    try:
        normalize_custom_preset(make_preset(preset_id))
    except Exception as exc:
        failures.append(f"valid preset failed validation: {exc}")

    try:
        normalize_custom_preset({"id": "bad id", "displayName": "Bad", "tokens": {"colors": {}}})
        failures.append("invalid id was accepted")
    except PresetValidationError:
        pass

    try:
        unsafe = make_preset(f"unsafe-preset-{suffix}")
        unsafe["cssVariables"] = {"--sd-app-texture": "url(https://example.invalid/a.png)"}
        normalize_custom_preset(unsafe)
        failures.append("unsafe remote texture was accepted")
    except PresetValidationError:
        pass

    imported = None
    try:
        imported = import_custom_preset(make_preset(preset_id), conflict_strategy="error")["preset"]
        if imported["id"] != preset_id:
            failures.append("import changed the preset id unexpectedly")
    except Exception as exc:
        failures.append(f"valid import failed: {exc}")

    try:
        listed = list_custom_presets()
        if not any(preset.get("id") == preset_id for preset in listed):
            failures.append("imported preset was not listed")
    except Exception as exc:
        failures.append(f"list custom presets failed: {exc}")

    if imported:
        try:
            exported = export_preset(imported)
            export_path = Path(exported["export_path"])
            if not export_path.exists():
                failures.append("export path was not created")
        except Exception as exc:
            failures.append(f"export failed: {exc}")

        try:
            duplicated = duplicate_preset(imported, display_name=f"Smoke Test Duplicate {suffix}")["preset"]
            duplicate_id = duplicated["id"]
            if duplicate_id == preset_id:
                failures.append("duplicate reused the source preset id")
        except Exception as exc:
            failures.append(f"duplicate failed: {exc}")

    for cleanup_id in [preset_id, duplicate_id]:
        if not cleanup_id:
            continue
        try:
            delete_custom_preset(cleanup_id)
        except FileNotFoundError:
            pass
        except Exception as exc:
            failures.append(f"cleanup failed for {cleanup_id}: {exc}")
    if export_path and export_path.exists():
        try:
            export_path.unlink()
        except OSError as exc:
            failures.append(f"export cleanup failed for {export_path}: {exc}")

    if failures:
        print("UI preset manager smoke test FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("UI preset manager smoke test passed")
    print("- valid custom preset imported, listed, exported, duplicated, and cleaned up")
    print("- invalid slug and remote texture payload rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
