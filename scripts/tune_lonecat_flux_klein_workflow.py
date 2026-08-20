from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_WORKFLOW_DIR = Path(r"C:\Users\wngar\Documents\ComfyUI\user\default\workflows")
ORIGINAL_PATTERN = "Lonecat*Flux*5.0.3.json"
TUNED_NAME = "Lonecat's Flux 2D & Klein_9b Generator ver 5.0.3 - StoryDriver AMD Tuned.json"

WORKFLOW_DIR = ROOT / "backend" / "data" / "comfy_workflows"
SOURCE_API = WORKFLOW_DIR / "flux_2d_klein_9b_v5_0_3_api.json"
TUNED_API = WORKFLOW_DIR / "lonecat_flux_2d_klein_9b_v5_0_3_storydriver_amd_tuned_api.json"
TUNED_SIDECAR = WORKFLOW_DIR / "lonecat_flux_2d_klein_9b_v5_0_3_storydriver_amd_tuned_api.storydriver.json"
REPORT = ROOT / "backend" / "data" / "logs" / "LONECAT_FLUX_KLEIN_TUNE_APPLIED.json"


def find_original() -> Path:
    matches = sorted(DESKTOP_WORKFLOW_DIR.glob(ORIGINAL_PATTERN))
    if not matches:
        raise FileNotFoundError(f"No workflow matching {ORIGINAL_PATTERN!r} in {DESKTOP_WORKFLOW_DIR}")
    return matches[0]


def tune_desktop_workflow(original: Path, tuned: Path) -> dict:
    data = json.loads(original.read_text(encoding="utf-8"))
    changes: list[dict] = []
    for node in data.get("nodes") or []:
        node_type = str(node.get("type") or "")
        node_id = node.get("id")
        if node_type in {"easy clearCacheAll", "easy cleanGpuUsed"}:
            old_mode = node.get("mode", 0)
            node["mode"] = 4
            if old_mode != 4:
                changes.append({
                    "node_id": node_id,
                    "type": node_type,
                    "old_mode": old_mode,
                    "new_mode": 4,
                    "reason": "StoryDriver handles ComfyUI /free after the fast regenerate window; in-graph cleanup hurts warm regenerate.",
                })
        elif node_type == "PlaySound|pysssss":
            old_mode = node.get("mode", 0)
            node["mode"] = 2
            if old_mode != 2:
                changes.append({
                    "node_id": node_id,
                    "type": node_type,
                    "old_mode": old_mode,
                    "new_mode": 2,
                    "reason": "Disable Desktop notification sound in the StoryDriver tuned copy; this does not affect image quality.",
                })

    extra = data.setdefault("extra", {})
    extra["storydriver_amd_tuned"] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_workflow": str(original),
        "tuned_workflow": str(tuned),
        "quality_policy": "Preserve core Lonecat Flux/Klein model, prompt, size, steps, sampler, scheduler, VAE, and CLIP behavior.",
        "resource_policy": "Disable in-graph cleanup/sound in the copy; StoryDriver manages /free after warm regenerate window.",
        "changes": changes,
    }
    tuned.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"changes": changes, "node_count": len(data.get("nodes") or [])}


def sync_api_export(original: Path, tuned_desktop: Path) -> dict:
    api = json.loads(SOURCE_API.read_text(encoding="utf-8"))
    if "9" in api and isinstance(api["9"], dict):
        api["9"].setdefault("inputs", {})["filename_prefix"] = "StoryDriver-Lonecat-FluxKlein"
    TUNED_API.write_text(json.dumps(api, indent=2, ensure_ascii=False), encoding="utf-8")

    sidecar = {
        "name": "Lonecat Flux/Klein v5.0.3 - StoryDriver AMD Tuned",
        "workflow_type": "lonecat_flux_klein",
        "workflow_file": TUNED_API.name,
        "resource_mode": "image_priority",
        "expected_time_seconds_min": 60,
        "expected_time_seconds_max": 120,
        "expected_warm_regenerate_seconds_min": 45,
        "expected_warm_regenerate_seconds_max": 90,
        "positive_prompt_node_id": "75:74",
        "positive_prompt_input": "text",
        "negative_prompt_node_id": "75:67",
        "negative_prompt_input": "text",
        "seed_node_id": "75:73",
        "seed_input": "noise_seed",
        "output_node_id": "9",
        "reference_image_node_id": "",
        "reference_image_input": "image",
        "img2img_input_node_id": "",
        "img2img_input": "image",
        "character_reference_node_id": "",
        "character_reference_input": "image",
        "denoise_node_id": "",
        "denoise_input": "denoise",
        "quality_profile": "StoryDriver AMD Tuned Quality",
        "quality_profile_notes": (
            "Quality-preserving API export for the Lonecat Flux/Klein workflow family. "
            "The export keeps 1024x1024, batch 1, 20 steps, euler sampler, Flux2Scheduler, CFG 5.0, "
            "flux-2-klein-base-9b-fp8, qwen_3_8b_fp8mixed, and flux2-vae."
        ),
        "amd_tuning_notes": (
            "The Desktop tuned copy disables in-graph cleanup and notification sound nodes so warm regenerate can stay fast. "
            "StoryDriver keeps ComfyUI warm during the regenerate window, then calls /free through resource policy."
        ),
        "original_workflow_source_path": str(original),
        "tuned_workflow_source_path": str(tuned_desktop),
        "api_export_source": str(SOURCE_API),
        "api_export_note": (
            "This API export is synced from the proven compact Flux/Klein executable graph recovered from ComfyUI PNG metadata. "
            "ComfyUI Desktop remains the workflow editor/source of truth; if core model, sampler, size, or step settings change in Desktop, re-export API JSON and refresh StoryDriver."
        ),
        "date": datetime.now().date().isoformat(),
        "notes": (
            "StoryDriver injects positive text into 75:74.text, negative text into 75:67.text, randomizes 75:73.noise_seed, "
            "and retrieves output from SaveImage node 9. ComfyUI Desktop owns model files, LoRAs, sampler UI, and future edits."
        ),
    }
    TUNED_SIDECAR.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"api_export": str(TUNED_API), "sidecar": str(TUNED_SIDECAR), "node_count": len(api)}


def main() -> int:
    original = find_original()
    tuned_desktop = DESKTOP_WORKFLOW_DIR / TUNED_NAME
    if not tuned_desktop.exists():
        shutil.copy2(original, tuned_desktop)
    desktop_result = tune_desktop_workflow(original, tuned_desktop)
    api_result = sync_api_export(original, tuned_desktop)
    report = {
        "original_workflow": str(original),
        "tuned_desktop_workflow": str(tuned_desktop),
        "desktop_result": desktop_result,
        "api_result": api_result,
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
