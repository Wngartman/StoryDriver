from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.schemas import ImageWorkflowConfig, ImageWorkflowNodeCandidate, ImageWorkflowRead
from app.utils.paths import ensure_runtime_paths


WORKFLOW_DIR = DATA_DIR / "comfy_workflows"
SIDECAR_SUFFIX = ".storydriver.json"
UNSAFE_PREVIEW_TERMS = (
    "nude",
    "naked",
    "sex",
    "sexual",
    "pussy",
    "penis",
    "cum",
    "child",
    "kid",
    "minor",
    "underage",
    "year old",
)


def workflow_id_from_file(path: Path) -> str:
    return path.stem


def config_path_for_workflow_id(workflow_id: str) -> Path:
    return WORKFLOW_DIR / f"{Path(workflow_id).stem}{SIDECAR_SUFFIX}"


def is_sidecar_config(path: Path) -> bool:
    return path.name.endswith(SIDECAR_SUFFIX)


def workflow_files() -> list[Path]:
    ensure_runtime_paths()
    return sorted(
        path
        for path in WORKFLOW_DIR.glob("*.json")
        if path.is_file() and not is_sidecar_config(path)
    )


def read_json_file(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clean_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    if not name.lower().endswith(".json"):
        name = f"{name}.json"
    return name or "workflow.json"


def unique_workflow_path(filename: str) -> Path:
    ensure_runtime_paths()
    base_name = clean_filename(filename)
    stem = Path(base_name).stem
    suffix = Path(base_name).suffix or ".json"
    candidate = WORKFLOW_DIR / base_name
    index = 2
    while candidate.exists():
        candidate = WORKFLOW_DIR / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def safe_text_preview(value: Any, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    if not text:
        return ""
    lower = text.lower()
    if any(term in lower for term in UNSAFE_PREVIEW_TERMS):
        return ""
    return text[:limit]


def workflow_file_from_config_path(path: Path) -> str:
    name = path.name[: -len(SIDECAR_SUFFIX)]
    return f"{name}.json"


def sidecar_configs_by_workflow_file() -> dict[str, tuple[Path, ImageWorkflowConfig | None, str | None]]:
    ensure_runtime_paths()
    configs: dict[str, tuple[Path, ImageWorkflowConfig | None, str | None]] = {}
    for path in sorted(WORKFLOW_DIR.glob(f"*{SIDECAR_SUFFIX}")):
        try:
            data = read_json_file(path)
            workflow_file = str(data.get("workflow_file") or workflow_file_from_config_path(path))
            config = ImageWorkflowConfig(**{**data, "workflow_file": workflow_file})
            configs[Path(workflow_file).name] = (path, config, None)
        except Exception as error:
            configs[workflow_file_from_config_path(path)] = (path, None, str(error))
    return configs


def load_workflow(workflow_file: str) -> dict[str, Any]:
    path = (WORKFLOW_DIR / Path(workflow_file).name).resolve()
    if not path.is_file() or WORKFLOW_DIR.resolve() not in path.parents:
        raise FileNotFoundError(f"Workflow file not found: {workflow_file}")
    data = read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("Workflow JSON must be a ComfyUI API workflow object.")
    return data


def is_api_workflow_shape(workflow: dict[str, Any]) -> bool:
    if not workflow:
        return False
    node_count = 0
    for value in workflow.values():
        if isinstance(value, dict) and isinstance(value.get("inputs"), dict) and "class_type" in value:
            node_count += 1
    return node_count >= max(1, len(workflow) // 2)


TEXT_CLASS_HINTS = (
    "cliptextencode",
    "textencode",
    "text encode",
    "prompt",
    "conditioning",
    "encoder",
    "primitive",
    "string",
    "t5",
    "clip",
)
SAMPLER_CLASS_HINTS = (
    "sampler",
    "ksampler",
    "samplercustom",
    "sampling",
    "guider",
    "basicguider",
    "cfgguider",
    "fluxguidance",
)
OUTPUT_CLASS_HINTS = ("saveimage", "previewimage", "output", "save")
ZIMAGE_HINTS = ("zimage", "z_image", "z-image", "z image", "z-img", "turbo")
PROMPT_INPUT_HINTS = ("text", "prompt", "positive", "caption", "string", "value")
NEGATIVE_INPUT_HINTS = ("negative", "negative_prompt", "negative_text")
SEED_INPUT_HINTS = ("seed", "noise_seed")
MODEL_INPUT_HINTS = (
    "ckpt",
    "checkpoint",
    "model",
    "unet",
    "vae",
    "clip_name",
    "lora",
    "image",
    "filename",
    "file",
)
IMAGE_REFERENCE_CLASS_HINTS = (
    "loadimage",
    "imageinput",
    "reference",
    "ipadapter",
    "instantid",
    "faceid",
    "reactor",
    "controlnet",
    "img2img",
    "image to image",
)
IMAGE_REFERENCE_INPUT_HINTS = (
    "image",
    "reference",
    "reference_image",
    "ref_image",
    "init_image",
    "input_image",
    "source_image",
    "character_reference",
    "face_image",
)
DENOISE_INPUT_HINTS = ("denoise", "denoise_strength", "strength", "noise_strength")
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
WORKFLOW_TYPE_LABELS = {
    "z_image_turbo": "Z-Image Turbo",
    "z_image_base": "Z-Image Base",
    "lonecat_zit": "Lonecat ZIT",
    "flux_klein": "Flux 2D / Klein",
    "lonecat_flux_klein": "Lonecat Flux/Klein",
    "other": "Other",
    "auto": "Auto",
    "unknown": "Unknown",
}
EXPECTED_TIME_RANGES = {
    "z_image_turbo": (10, 30),
    "z_image_base": (60, 120),
    "lonecat_zit": (60, 120),
    "flux_klein": (60, 150),
    "lonecat_flux_klein": (60, 120),
}


def has_hint(value: str, hints: tuple[str, ...]) -> bool:
    lower = value.lower()
    return any(hint in lower for hint in hints)


def confidence_from_score(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 55:
        return "medium"
    return "low"


def link_target(value: Any) -> str | None:
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        if isinstance(first, (str, int)):
            return str(first)
    return None


def workflow_contains_zimage(workflow: dict[str, Any]) -> bool:
    try:
        text = json.dumps(workflow, ensure_ascii=False).lower()
    except Exception:
        text = str(workflow).lower()
    return any(hint in text for hint in ZIMAGE_HINTS)


def detect_workflow_type(workflow: dict[str, Any]) -> str:
    try:
        text = json.dumps(workflow, ensure_ascii=False).lower()
    except Exception:
        text = str(workflow).lower()
    if "lonecat" in text and ("flux" in text and "klein" in text):
        return "lonecat_flux_klein"
    if "lonecat" in text or "zit nsfw" in text or ("zit" in text and "z-image" in text):
        return "lonecat_zit"
    if ("flux 2d" in text and "klein" in text) or "klein_9b" in text or "klein 9b" in text:
        return "flux_klein"
    if any(term in text for term in ("z_image_turbo", "z-image-turbo", "zimage_turbo", "z image turbo", "turbo")):
        return "z_image_turbo"
    if any(term in text for term in ("z_image_base", "z-image-base", "zimage_base", "z image base")):
        return "z_image_base"
    if any(term in text for term in ("z_image", "z-image", "zimage", "z image")):
        return "other"
    return "unknown"


def _first_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def extract_workflow_metadata_from_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    if not is_api_workflow_shape(workflow):
        return {"api_workflow": False, "workflow_type": "unknown", "workflow_type_label": "Unknown"}

    model_loader_nodes: list[dict[str, Any]] = []
    sampler_nodes: list[dict[str, Any]] = []
    output_nodes: list[dict[str, Any]] = []
    model_names: list[str] = []
    diffusion_model_names: list[str] = []
    upscale_model_names: list[str] = []
    auxiliary_model_names: list[str] = []
    text_encoder_names: list[str] = []
    vae_names: list[str] = []
    lora_names: list[str] = []
    upscaler_nodes: list[dict[str, Any]] = []
    detailer_nodes: list[dict[str, Any]] = []
    reference_input_nodes: list[dict[str, Any]] = []
    denoise_nodes: list[dict[str, Any]] = []
    sizes: list[dict[str, Any]] = []
    steps_values: list[int] = []

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        class_lower = class_type.lower()
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        title = ""
        meta = node.get("_meta")
        if isinstance(meta, dict):
            title = str(meta.get("title") or "")

        def input_text(*names: str) -> str | None:
            for name in names:
                value = inputs.get(name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None

        if "upscalemodelloader" in class_lower or "upscale model" in class_lower:
            name = input_text("model_name", "upscale_model", "model")
            if name:
                model_names.append(name)
                upscale_model_names.append(name)
            model_loader_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title, "model": name})

        elif any(term in class_lower for term in ("unetloader", "checkpointloader", "ckpt", "diffusion", "modelpatchloader")):
            name = input_text("unet_name", "ckpt_name", "model_name", "model", "checkpoint")
            if name:
                model_names.append(name)
                diffusion_model_names.append(name)
            model_loader_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title, "model": name})

        elif any(term in class_lower for term in ("florence", "gguf", "llm", "promptgen")):
            name = input_text("model", "model_name", "repo_id", "clip_name")
            if name:
                model_names.append(name)
                auxiliary_model_names.append(name)
            model_loader_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title, "model": name})

        if "cliploader" in class_lower or "dualcliploader" in class_lower or "textencoder" in class_lower:
            name = input_text("clip_name", "clip_name1", "clip_name2", "text_encoder_name", "t5_name")
            if name:
                text_encoder_names.append(name)
            model_loader_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title, "model": name})

        if "vaeloader" in class_lower or class_lower == "vae loader":
            name = input_text("vae_name", "vae")
            if name:
                vae_names.append(name)
            model_loader_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title, "model": name})

        if "lora" in class_lower:
            name = input_text("lora_name", "lora", "name")
            if name:
                lora_names.append(name)
            model_loader_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title, "model": name})

        if "upscale" in class_lower or "upscaler" in class_lower:
            upscaler_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title})

        if any(term in class_lower for term in ("detailer", "impact", "bbox", "segm")):
            detailer_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title})

        reference_inputs = [
            str(input_name)
            for input_name in inputs.keys()
            if has_hint(str(input_name), IMAGE_REFERENCE_INPUT_HINTS)
        ]
        if has_hint(class_lower, IMAGE_REFERENCE_CLASS_HINTS) or reference_inputs:
            reference_input_nodes.append(
                {
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "title": title,
                    "input_names": reference_inputs or [str(name) for name in inputs.keys()][:12],
                }
            )

        denoise_inputs = [
            str(input_name)
            for input_name in inputs.keys()
            if has_hint(str(input_name), DENOISE_INPUT_HINTS)
        ]
        if denoise_inputs:
            denoise_nodes.append(
                {
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "title": title,
                    "input_names": denoise_inputs,
                }
            )

        for step_input in ("steps", "steps_total", "refiner_step", "sampling_steps"):
            steps = _first_number(inputs.get(step_input))
            if isinstance(steps, int) and steps > 0:
                steps_values.append(steps)

        if has_hint(class_lower, SAMPLER_CLASS_HINTS):
            steps = next((_first_number(inputs.get(name)) for name in ("steps", "steps_total", "refiner_step") if _first_number(inputs.get(name))), None)
            if isinstance(steps, int):
                steps_values.append(steps)
            sampler_nodes.append(
                {
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "title": title,
                    "steps": steps,
                    "sampler": inputs.get("sampler_name"),
                    "scheduler": inputs.get("scheduler"),
                    "cfg": inputs.get("cfg"),
                }
            )

        width = _first_number(inputs.get("width"))
        height = _first_number(inputs.get("height"))
        if width and height:
            sizes.append(
                {
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "width": int(width),
                    "height": int(height),
                    "batch_size": inputs.get("batch_size"),
                }
            )

        if has_hint(class_lower, OUTPUT_CLASS_HINTS):
            output_nodes.append({"node_id": str(node_id), "class_type": class_type, "title": title})

    workflow_type = detect_workflow_type(workflow)
    expected = EXPECTED_TIME_RANGES.get(workflow_type)
    complexity = "full" if upscaler_nodes or detailer_nodes or len(model_loader_nodes) > 8 else "standard"
    speed_note = None
    if workflow_type == "lonecat_zit":
        speed_note = (
            "Lonecat ZIT is working as a full/heavy workflow. Current expected first-image time is roughly 2-5 minutes "
            "on this AMD runtime depending on whether the full graph is warm; warm Regenerate may be faster. "
            "This is not treated as broken just because it is slower than lightweight Turbo or Flux/Klein workflows."
        )
    elif workflow_type == "lonecat_flux_klein":
        speed_note = (
            "Lonecat Flux/Klein is a quality-preserving Flux 2D / Klein workflow tuned for this AMD runtime. "
            "The StoryDriver API export keeps the core 1024x1024, 20-step, fp8 Flux/Klein path and leaves "
            "Desktop-only preview/detailer/upscale experimentation in ComfyUI."
        )
    elif workflow_type == "flux_klein":
        speed_note = (
            "Flux 2D / Klein is treated as a full ComfyUI Desktop workflow. Expected first-image time depends on the "
            "exported API graph and current VRAM state; warm Regenerate should be faster when ComfyUI is still warm."
        )
    elif complexity == "full":
        speed_note = "This workflow appears full/heavy. For faster story images, consider a lighter API workflow."
    return {
        "api_workflow": True,
        "workflow_type": workflow_type,
        "workflow_type_label": WORKFLOW_TYPE_LABELS.get(workflow_type, "Unknown"),
        "expected_time_seconds_min": expected[0] if expected else None,
        "expected_time_seconds_max": expected[1] if expected else None,
        "model_names": _unique(model_names),
        "diffusion_model_names": _unique(diffusion_model_names),
        "upscale_model_names": _unique(upscale_model_names),
        "auxiliary_model_names": _unique(auxiliary_model_names),
        "text_encoder_names": _unique(text_encoder_names),
        "vae_names": _unique(vae_names),
        "lora_names": _unique(lora_names),
        "model_loader_nodes": model_loader_nodes[:12],
        "sampler_nodes": sampler_nodes[:8],
        "output_nodes": output_nodes[:8],
        "upscaler_nodes": upscaler_nodes[:8],
        "detailer_nodes": detailer_nodes[:8],
        "reference_input_nodes": reference_input_nodes[:12],
        "denoise_nodes": denoise_nodes[:8],
        "supports_reference_inputs": bool(reference_input_nodes),
        "steps": sorted(set(steps_values)),
        "sizes": sizes[:8],
        "workflow_complexity": complexity,
        "workflow_speed_note": speed_note,
    }


def extract_workflow_metadata(workflow_file: str) -> dict[str, Any]:
    try:
        workflow = load_workflow(workflow_file)
    except Exception:
        return {"api_workflow": False, "workflow_type": "unknown", "workflow_type_label": "Unknown"}
    return extract_workflow_metadata_from_workflow(workflow)


def effective_workflow_type(config: ImageWorkflowConfig | None, metadata: dict[str, Any] | None = None) -> str:
    configured = (config.workflow_type if config else "auto") or "auto"
    if configured != "auto":
        return configured
    detected = (metadata or {}).get("workflow_type")
    return detected if detected in WORKFLOW_TYPE_LABELS else "unknown"


def expected_time_range(config: ImageWorkflowConfig | None, metadata: dict[str, Any] | None = None) -> tuple[int | None, int | None]:
    if config and config.expected_time_seconds_min and config.expected_time_seconds_max:
        return config.expected_time_seconds_min, config.expected_time_seconds_max
    workflow_type = effective_workflow_type(config, metadata)
    expected = EXPECTED_TIME_RANGES.get(workflow_type)
    if expected:
        return expected
    return None, None


def metadata_with_config_overrides(
    metadata: dict[str, Any],
    config: ImageWorkflowConfig | None,
) -> dict[str, Any]:
    """Use the saved StoryDriver sidecar as the display/diagnostic authority."""
    if not config:
        return metadata
    updated = dict(metadata)
    workflow_type = effective_workflow_type(config, metadata)
    expected_min, expected_max = expected_time_range(config, metadata)
    updated["workflow_type"] = workflow_type
    updated["workflow_type_label"] = WORKFLOW_TYPE_LABELS.get(workflow_type, "Unknown")
    updated["expected_time_seconds_min"] = expected_min
    updated["expected_time_seconds_max"] = expected_max
    return updated


def input_names_for_node(node: dict[str, Any]) -> list[str]:
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    return [str(name) for name in inputs.keys()]


def looks_like_replaceable_text_input(input_name: str, value: Any, class_type: str, role: str) -> bool:
    if not isinstance(value, str):
        return False
    input_lower = input_name.lower()
    class_lower = class_type.lower()
    if has_hint(input_lower, MODEL_INPUT_HINTS):
        return False
    if role == "negative" and has_hint(input_lower, NEGATIVE_INPUT_HINTS):
        return True
    if has_hint(input_lower, PROMPT_INPUT_HINTS + NEGATIVE_INPUT_HINTS):
        return True
    return input_lower in {"text", "prompt", "value"} and has_hint(class_lower, TEXT_CLASS_HINTS + ZIMAGE_HINTS)


def replaceable_text_inputs(node: dict[str, Any], role: str) -> list[str]:
    class_type = str(node.get("class_type") or "")
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    names = [
        str(name)
        for name, value in inputs.items()
        if looks_like_replaceable_text_input(str(name), value, class_type, role)
    ]
    preferred = {
        "positive": ["positive", "positive_prompt", "prompt", "text", "caption", "value", "string"],
        "negative": ["negative", "negative_prompt", "negative_text", "prompt", "text", "value", "string"],
        "prompt": ["prompt", "text", "positive", "caption", "value", "string"],
    }.get(role, ["text", "prompt", "value"])
    return sorted(names, key=lambda name: (preferred.index(name.lower()) if name.lower() in preferred else 99, name))


def detect_node_candidates_from_workflow(workflow: dict[str, Any]) -> list[ImageWorkflowNodeCandidate]:
    if not is_api_workflow_shape(workflow):
        return []

    candidate_map: dict[tuple[str, str, str], ImageWorkflowNodeCandidate] = {}
    zimage_workflow = workflow_contains_zimage(workflow)

    def append_candidate(
        node_id: str,
        role: str,
        input_name: str,
        *,
        score: int,
        detected_by: str,
    ) -> None:
        node = workflow.get(str(node_id))
        if not isinstance(node, dict):
            return
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        key = (str(node_id), role, input_name)
        existing = candidate_map.get(key)
        confidence = confidence_from_score(score)
        if existing and existing.confidence_score >= score:
            return
        candidate_map[key] = ImageWorkflowNodeCandidate(
            node_id=str(node_id),
            class_type=class_type,
            input_name=input_name,
            input_names=input_names_for_node(node)[:12],
            role=role,  # type: ignore[arg-type]
            confidence=confidence,  # type: ignore[arg-type]
            confidence_score=score,
            detected_by=detected_by,
            current_text_preview=safe_text_preview(inputs.get(input_name)),
        )

    def trace_prompt_source(
        start_node_id: str,
        role: str,
        *,
        depth: int = 0,
        seen: set[str] | None = None,
    ) -> tuple[str, str, int, str] | None:
        seen = seen or set()
        node_id = str(start_node_id)
        if depth > 8 or node_id in seen:
            return None
        seen.add(node_id)
        node = workflow.get(node_id)
        if not isinstance(node, dict):
            return None
        class_type = str(node.get("class_type") or "")
        class_lower = class_type.lower()
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}

        if role == "negative" and "conditioningzeroout" in class_lower:
            return None

        text_inputs = replaceable_text_inputs(node, role)
        if text_inputs:
            input_name = text_inputs[0]
            score = 93 if depth <= 2 else 84
            if has_hint(class_lower, ("cliptextencode", "textencode", "text encode")):
                score += 4
            if role == "negative" and (
                has_hint(input_name, NEGATIVE_INPUT_HINTS) or has_hint(class_lower, ("negative",))
            ):
                score += 5
            return node_id, input_name, min(score, 99), f"traced from {role} graph input"

        preferred_inputs = {
            "positive": ("positive", "conditioning", "cond", "prompt", "text", "guider"),
            "negative": ("negative", "negative_conditioning", "conditioning", "cond", "guider"),
            "prompt": ("conditioning", "cond", "prompt", "text", "positive", "guider"),
        }.get(role, ("conditioning", "cond", "prompt", "text"))

        linked_inputs: list[tuple[int, str, str]] = []
        for input_name, value in inputs.items():
            target = link_target(value)
            if not target:
                continue
            input_lower = str(input_name).lower()
            priority = next((index for index, hint in enumerate(preferred_inputs) if hint in input_lower), 99)
            linked_inputs.append((priority, str(input_name), target))
        for _, _, target in sorted(linked_inputs, key=lambda item: (item[0], item[1])):
            traced = trace_prompt_source(target, role, depth=depth + 1, seen=set(seen))
            if traced:
                return traced
        return None

    def sampler_input_role(input_name: str) -> str | None:
        lower = input_name.lower()
        if "negative" in lower:
            return "negative"
        if "positive" in lower:
            return "positive"
        if lower in {"conditioning", "cond", "c", "guidance"}:
            return "prompt"
        return None

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        class_lower = class_type.lower()
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        input_names = [str(name) for name in inputs.keys()]

        if has_hint(class_lower, OUTPUT_CLASS_HINTS):
            append_candidate(str(node_id), "output", "", score=90, detected_by="output/save image node")

        for input_name in input_names:
            if has_hint(input_name, SEED_INPUT_HINTS):
                score = 92 if has_hint(class_lower, SAMPLER_CLASS_HINTS + ZIMAGE_HINTS) else 68
                append_candidate(str(node_id), "seed", input_name, score=score, detected_by="seed input")
            if has_hint(input_name, IMAGE_REFERENCE_INPUT_HINTS):
                score = 78
                if has_hint(class_lower, IMAGE_REFERENCE_CLASS_HINTS):
                    score += 10
                if "loadimage" in class_lower:
                    score += 5
                role = "img2img" if "init" in input_name.lower() or "img2img" in class_lower else "reference"
                append_candidate(
                    str(node_id),
                    role,
                    input_name,
                    score=min(score, 95),
                    detected_by="image/reference input",
                )
            if has_hint(input_name, DENOISE_INPUT_HINTS):
                append_candidate(
                    str(node_id),
                    "denoise",
                    input_name,
                    score=72,
                    detected_by="denoise/strength input",
                )

        if has_hint(class_lower, SAMPLER_CLASS_HINTS) or any(sampler_input_role(name) for name in input_names):
            for input_name, value in inputs.items():
                role = sampler_input_role(str(input_name))
                target = link_target(value)
                if not role or not target:
                    continue
                traced = trace_prompt_source(target, "positive" if role == "prompt" else role)
                if traced:
                    traced_id, traced_input, score, detected_by = traced
                    append_candidate(traced_id, "positive" if role == "prompt" else role, traced_input, score=score, detected_by=detected_by)

        for input_name in replaceable_text_inputs(node, "positive"):
            input_lower = input_name.lower()
            haystack = " ".join([str(node_id), class_type, input_name]).lower()
            role = "negative" if "negative" in haystack else "positive" if "positive" in haystack else "prompt"
            score = 45
            if has_hint(class_lower, ("cliptextencode", "textencode", "text encode")):
                score += 22
            if input_lower in {"text", "prompt", "positive", "positive_prompt"}:
                score += 12
            if zimage_workflow and (input_lower in {"text", "prompt"} or has_hint(class_lower, ZIMAGE_HINTS)):
                score += 12
            if role == "negative":
                score += 6
            append_candidate(str(node_id), role, input_name, score=score, detected_by="text input heuristic")

    positive_candidates = [
        candidate
        for candidate in candidate_map.values()
        if candidate.role in {"positive", "prompt"}
    ]
    negative_candidates = [candidate for candidate in candidate_map.values() if candidate.role == "negative"]
    if zimage_workflow and len(positive_candidates) == 1 and not negative_candidates:
        candidate = positive_candidates[0]
        append_candidate(
            candidate.node_id,
            "positive",
            candidate.input_name,
            score=max(candidate.confidence_score, 88),
            detected_by="single Z-Image prompt input",
        )

    role_order = {"positive": 0, "prompt": 1, "negative": 2, "seed": 3, "output": 4, "reference": 5, "img2img": 6, "denoise": 7}
    return sorted(
        candidate_map.values(),
        key=lambda item: (
            role_order.get(item.role, 9),
            -CONFIDENCE_RANK.get(item.confidence, 0),
            -item.confidence_score,
            item.node_id,
            item.input_name,
        ),
    )


def detect_node_candidates(workflow_file: str) -> list[ImageWorkflowNodeCandidate]:
    try:
        workflow = load_workflow(workflow_file)
    except Exception:
        return []
    return detect_node_candidates_from_workflow(workflow)


def best_candidate(
    candidates: list[ImageWorkflowNodeCandidate],
    roles: tuple[str, ...],
    *,
    min_confidence: str | None = None,
) -> ImageWorkflowNodeCandidate | None:
    role_set = set(roles)
    matching = [candidate for candidate in candidates if candidate.role in role_set]
    if min_confidence:
        minimum = CONFIDENCE_RANK[min_confidence]
        matching = [candidate for candidate in matching if CONFIDENCE_RANK.get(candidate.confidence, 0) >= minimum]
    if not matching:
        return None
    return max(
        matching,
        key=lambda item: (
            CONFIDENCE_RANK.get(item.confidence, 0),
            item.confidence_score,
            1 if item.current_text_preview else 0,
            -len(item.input_names),
        ),
    )


def initial_config_for_workflow(workflow_file: str, name: str | None = None) -> ImageWorkflowConfig:
    metadata = extract_workflow_metadata(workflow_file)
    detected_type = metadata.get("workflow_type")
    workflow_type = (
        detected_type
        if detected_type in {"z_image_turbo", "z_image_base", "lonecat_zit", "flux_klein", "lonecat_flux_klein", "other"}
        else "auto"
    )
    expected_min, expected_max = expected_time_range(
        ImageWorkflowConfig(workflow_type=workflow_type, workflow_file=Path(workflow_file).name),
        metadata,
    )
    candidates = detect_node_candidates(workflow_file)
    positive = best_candidate(candidates, ("positive", "prompt"), min_confidence="high")
    negative = best_candidate(candidates, ("negative",), min_confidence="high")
    seed = best_candidate(candidates, ("seed",), min_confidence="high")
    output = best_candidate(candidates, ("output",), min_confidence="high")
    detected = [candidate for candidate in (positive, negative, seed, output) if candidate]
    notes = "Detected automatically by StoryDriver." if positive else "Imported through StoryDriver workflow setup."
    if detected:
        notes += "\n" + "\n".join(
            f"{candidate.role}: node {candidate.node_id}"
            f"{f' / {candidate.input_name}' if candidate.input_name else ''}"
            f" ({candidate.confidence} confidence)"
            for candidate in detected
        )
    return ImageWorkflowConfig(
        name=(name or Path(workflow_file).stem).strip(),
        workflow_file=Path(workflow_file).name,
        workflow_type=workflow_type,
        expected_time_seconds_min=expected_min,
        expected_time_seconds_max=expected_max,
        positive_prompt_node_id=positive.node_id if positive else "",
        positive_prompt_input=positive.input_name or "text" if positive else "text",
        negative_prompt_node_id=negative.node_id if negative else "",
        negative_prompt_input=negative.input_name or "text" if negative else "text",
        seed_node_id=seed.node_id if seed else "",
        seed_input=seed.input_name or "seed" if seed else "seed",
        output_node_id=output.node_id if output else "",
        notes=notes,
    )


def import_workflow_json(filename: str, content: str) -> ImageWorkflowRead:
    path = unique_workflow_path(filename)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"Workflow JSON could not be parsed: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("Workflow JSON must be an object.")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if is_api_workflow_shape(data):
        config = initial_config_for_workflow(path.name)
        if config.positive_prompt_node_id.strip():
            save_workflow_config(path.stem, config)
    return get_workflow(path.stem) or scan_workflows()[0]


def validate_workflow_config(config: ImageWorkflowConfig | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if config is None:
        return ["Workflow config is missing."], warnings

    workflow_file = Path(config.workflow_file).name
    workflow_path = WORKFLOW_DIR / workflow_file
    if not workflow_file:
        errors.append("workflow_file is required.")
        return errors, warnings
    if not workflow_path.exists():
        errors.append(f"Workflow file is missing: {workflow_file}")
        return errors, warnings
    if not config.positive_prompt_node_id.strip():
        errors.append("positive_prompt_node_id is required.")
        return errors, warnings
    if not config.positive_prompt_input.strip():
        errors.append("positive_prompt_input is required.")
        return errors, warnings

    try:
        workflow = load_workflow(workflow_file)
    except Exception as error:
        errors.append(f"Workflow JSON could not be read: {error}")
        return errors, warnings
    if not is_api_workflow_shape(workflow):
        errors.append("Workflow JSON does not look like an exported ComfyUI API workflow.")
        return errors, warnings

    def validate_node_input(node_id: str, input_name: str, label: str, required: bool) -> None:
        clean_node_id = str(node_id or "").strip()
        clean_input = str(input_name or "").strip()
        if not clean_node_id:
            if required:
                errors.append(f"{label} node ID is required.")
            return
        node = workflow.get(clean_node_id)
        if node is None:
            errors.append(f"{label} node does not exist: {clean_node_id}")
            return
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            warnings.append(f"{label} node has no inputs object to validate.")
            return
        if clean_input and clean_input not in inputs:
            errors.append(f"{label} input '{clean_input}' does not exist on node {clean_node_id}.")

    validate_node_input(config.positive_prompt_node_id, config.positive_prompt_input, "Positive prompt", True)
    validate_node_input(config.negative_prompt_node_id, config.negative_prompt_input, "Negative prompt", False)
    validate_node_input(config.seed_node_id, config.seed_input, "Seed", False)
    validate_node_input(config.reference_image_node_id, config.reference_image_input, "Reference image", False)
    validate_node_input(config.img2img_input_node_id, config.img2img_input, "Img2img input", False)
    validate_node_input(config.character_reference_node_id, config.character_reference_input, "Character reference", False)
    validate_node_input(config.denoise_node_id, config.denoise_input, "Denoise/strength", False)
    if config.output_node_id.strip() and config.output_node_id.strip() not in workflow:
        errors.append(f"Output node does not exist: {config.output_node_id.strip()}")
    metadata = extract_workflow_metadata_from_workflow(workflow)
    return errors, warnings


def scan_workflows() -> list[ImageWorkflowRead]:
    ensure_runtime_paths()
    configs = sidecar_configs_by_workflow_file()
    workflows: list[ImageWorkflowRead] = []
    for workflow_path in workflow_files():
        workflow_id = workflow_id_from_file(workflow_path)
        config_path, config, config_error = configs.get(workflow_path.name, (None, None, None))
        errors: list[str] = []
        warnings: list[str] = []
        metadata = extract_workflow_metadata(workflow_path.name)
        node_candidates = detect_node_candidates(workflow_path.name)
        detected_config = None
        api_shape = True
        try:
            api_shape = is_api_workflow_shape(load_workflow(workflow_path.name))
            if api_shape:
                auto_config = initial_config_for_workflow(workflow_path.name, name=workflow_path.stem)
                if auto_config.positive_prompt_node_id.strip():
                    detected_config = auto_config
        except Exception as error:
            api_shape = False
            errors.append(f"Workflow JSON could not be read: {error}")
        if not api_shape and not errors:
            errors.append("This does not look like exported API JSON. Export API workflow from ComfyUI and place it in the workflow folder.")
        if config_error:
            errors.append(f"Config could not be read: {config_error}")
        elif config is None and not errors:
            if detected_config:
                warnings.append("Auto-detected a high-confidence prompt mapping. Review it and save the config.")
            else:
                warnings.append("Needs setup. Add prompt node IDs before generating.")
            if node_candidates:
                warnings.append("Detected prompt/seed/output node candidates below.")
        elif config is not None and not errors:
            errors, warnings = validate_workflow_config(config)

        metadata = metadata_with_config_overrides(metadata, config)
        status = "needs_setup" if config is None and not errors else "invalid" if errors else "ready"
        name = config.name.strip() if config and config.name.strip() else workflow_path.stem
        workflows.append(
            ImageWorkflowRead(
                id=workflow_id,
                name=name,
                workflow_file=workflow_path.name,
                workflow_path=str(workflow_path),
                config_file=str(config_path) if config_path else None,
                has_config=config is not None,
                status=status,
                warnings=warnings,
                errors=errors,
                config=config,
                detected_config=detected_config,
                node_candidates=node_candidates,
                metadata=metadata,
            )
        )
    return workflows


def get_workflow(workflow_id: str) -> ImageWorkflowRead | None:
    return next((workflow for workflow in scan_workflows() if workflow.id == workflow_id), None)


def save_workflow_config(workflow_id: str, config: ImageWorkflowConfig) -> ImageWorkflowConfig:
    ensure_runtime_paths()
    workflow_file = Path(config.workflow_file).name
    if not workflow_file:
        workflow_file = f"{Path(workflow_id).stem}.json"
    data = config.model_dump()
    data["workflow_file"] = workflow_file
    data["name"] = data["name"].strip() or Path(workflow_file).stem
    path = config_path_for_workflow_id(workflow_id)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return ImageWorkflowConfig(**data)


def inject_workflow_values(
    workflow: dict[str, Any],
    config: ImageWorkflowConfig,
    *,
    prompt: str,
    negative_prompt: str = "",
    seed: int | None = None,
    reference_image_path: str | None = None,
    denoise_strength: float | None = None,
) -> dict[str, Any]:
    def set_input(node_id: str, input_name: str, value: Any) -> None:
        node = workflow[str(node_id).strip()]
        node.setdefault("inputs", {})[str(input_name).strip()] = value

    set_input(config.positive_prompt_node_id, config.positive_prompt_input, prompt)
    if config.negative_prompt_node_id.strip() and config.negative_prompt_input.strip():
        set_input(config.negative_prompt_node_id, config.negative_prompt_input, negative_prompt)
    if seed is not None and config.seed_node_id.strip() and config.seed_input.strip():
        set_input(config.seed_node_id, config.seed_input, seed)
    if reference_image_path:
        for node_id, input_name in (
            (config.reference_image_node_id, config.reference_image_input),
            (config.img2img_input_node_id, config.img2img_input),
            (config.character_reference_node_id, config.character_reference_input),
        ):
            if node_id.strip() and input_name.strip():
                set_input(node_id, input_name, reference_image_path)
    if denoise_strength is not None and config.denoise_node_id.strip() and config.denoise_input.strip():
        set_input(config.denoise_node_id, config.denoise_input, denoise_strength)
    return workflow
