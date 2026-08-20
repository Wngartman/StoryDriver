from app.config import DATA_DIR


def ensure_runtime_paths() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "generated_images").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "generated_audio").mkdir(parents=True, exist_ok=True)
    comfy_workflows = DATA_DIR / "comfy_workflows"
    comfy_workflows.mkdir(parents=True, exist_ok=True)
    readme = comfy_workflows / "README.md"
    if not readme.exists():
        readme.write_text(
            "# ComfyUI API workflows\n\n"
            "Place exported ComfyUI API workflow JSON files in this folder.\n\n"
            "StoryDriver scans this folder for the Image Settings workflow dropdown. Export workflows in API JSON format, "
            "then add or edit the StoryDriver sidecar config so it knows the positive prompt, negative prompt, seed, "
            "and optional output node IDs.\n\n"
            "ComfyUI owns model, LoRAs, sampler, size, style, and workflow complexity. StoryDriver only injects the scene prompt.\n\n"
            "Real image generation requires ComfyUI to be reachable at the configured COMFYUI_BASE_URL.\n\n"
            "Expected location: D:\\StoryDriver\\backend\\data\\comfy_workflows\n",
            encoding="utf-8",
        )
    (DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
