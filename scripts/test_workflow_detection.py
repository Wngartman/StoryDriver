from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.image_workflows import detect_node_candidates_from_workflow, initial_config_for_workflow  # noqa: E402


def candidate(candidates, role: str, node_id: str, input_name: str):
    return next(
        (
            item
            for item in candidates
            if item.role == role and item.node_id == node_id and item.input_name == input_name
        ),
        None,
    )


def test_clip_positive_negative() -> None:
    workflow = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "moonlit chapel", "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blur, low quality", "clip": ["1", 1]}},
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "seed": 123,
            },
        },
        "5": {"class_type": "VAEDecode", "inputs": {"samples": ["4", 0], "vae": ["1", 2]}},
        "6": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "test"}},
    }
    candidates = detect_node_candidates_from_workflow(workflow)
    assert candidate(candidates, "positive", "2", "text").confidence == "high"
    assert candidate(candidates, "negative", "3", "text").confidence == "high"
    assert candidate(candidates, "seed", "4", "seed").confidence == "high"
    assert candidate(candidates, "output", "6", "").confidence == "high"


def test_positive_only_zimage() -> None:
    workflow = {
        "10": {
            "class_type": "ZImageTurboPrompt",
            "inputs": {
                "text": "traveler at a ruined chapel",
                "width": 1024,
                "height": 1024,
                "seed": 321,
                "unet_name": "z_image_turbo_bf16.safetensors",
            },
        },
        "11": {"class_type": "SaveImage", "inputs": {"images": ["10", 0], "filename_prefix": "z-image-turbo"}},
    }
    candidates = detect_node_candidates_from_workflow(workflow)
    positive = candidate(candidates, "positive", "10", "text")
    assert positive is not None
    assert positive.confidence == "high"
    assert candidate(candidates, "negative", "10", "text") is None
    assert candidate(candidates, "seed", "10", "seed") is not None


def test_multiple_text_nodes_are_not_silently_high() -> None:
    workflow = {
        "1": {"class_type": "PrimitiveStringMultiline", "inputs": {"value": "style note"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "actual prompt", "clip": ["4", 0]}},
        "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0]}},
        "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b.safetensors"}},
    }
    candidates = detect_node_candidates_from_workflow(workflow)
    assert candidate(candidates, "prompt", "1", "value").confidence in {"low", "medium"}
    assert candidate(candidates, "prompt", "2", "text").confidence in {"medium", "high"}


def test_non_api_workflow() -> None:
    workflow = {
        "nodes": [
            {"id": 1, "type": "CLIPTextEncode", "widgets_values": ["prompt"]},
            {"id": 2, "type": "SaveImage", "widgets_values": ["prefix"]},
        ]
    }
    assert detect_node_candidates_from_workflow(workflow) == []


def main() -> int:
    tests = [
        test_clip_positive_negative,
        test_positive_only_zimage,
        test_multiple_text_nodes_are_not_silently_high,
        test_non_api_workflow,
    ]
    for test in tests:
        test()
        print(f"OK {test.__name__}")
    print("Workflow detection tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
