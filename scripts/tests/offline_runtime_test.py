from __future__ import annotations

import json
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"D:\StoryDriver")
BACKEND = "http://localhost:8001"
METRICS = ROOT / "backend" / "data" / "logs" / "OFFLINE_RUNTIME_LATEST.json"


def get_json(path: str) -> dict:
    with urllib.request.urlopen(BACKEND + path, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def port_open(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def require(condition: bool, message: str, checks: dict[str, bool], key: str) -> None:
    checks[key] = bool(condition)
    if not condition:
        raise AssertionError(message)


def main() -> int:
    result: dict = {"started_at": datetime.now(timezone.utc).isoformat(), "checks": {}, "errors": []}
    checks = result["checks"]
    try:
        diagnostics = get_json("/diagnostics")
        openapi = get_json("/openapi.json")
        paths = set(openapi.get("paths") or {})
        privacy = diagnostics.get("privacy") or {}
        endpoints = privacy.get("endpoints") or {}
        require(privacy.get("mode") == "local_only", "diagnostics privacy mode is not local_only", checks, "local_only_mode")
        require(bool(privacy.get("all_configured_endpoints_local")), "a configured runtime endpoint is external", checks, "all_endpoints_local")
        require(not any(str(item.get("url") or "").startswith(("https://", "http://")) and not item.get("local") for item in endpoints.values()), "external runtime endpoint found", checks, "no_external_runtime_endpoint")
        require(not any(route.startswith(("/images", "/image-workflows", "/resource-status", "/settings/image")) for route in paths), "retired image route remains active", checks, "image_routes_unmounted")

        qwen_launch = (ROOT / "tts_engines" / "qwen3_tts" / "service" / "launch_qwen3_tts.bat").read_text(encoding="utf-8", errors="replace")
        qwen_service = (ROOT / "tts_engines" / "qwen3_tts" / "service" / "app.py").read_text(encoding="utf-8", errors="replace")
        require("HF_HUB_OFFLINE=1" in qwen_launch and "TRANSFORMERS_OFFLINE=1" in qwen_launch, "Qwen launcher lacks offline environment flags", checks, "qwen_offline_flags")
        require("local_files_only" in qwen_service, "Qwen service does not force local model loading", checks, "qwen_local_files_only")
        registry = (diagnostics.get("tts") or {}).get("provider_registry") or {}
        model_path = Path(registry.get("high_quality_local", {}).get("diagnostics", {}).get("model_path") or "")
        require(model_path.is_dir() and str(model_path).lower().startswith(str(ROOT).lower()), "Qwen model is absent or outside D:\\StoryDriver", checks, "qwen_model_on_d")
        require(not port_open("127.0.0.1", 8891), "on-demand Qwen worker was left running", checks, "no_orphan_qwen_worker")
        require(
            registry.get("requested_primary_provider") == "high_quality_local",
            "Qwen is not the configured on-demand primary provider",
            checks,
            "qwen_requested_primary",
        )
        kokoro_provider = registry.get("kokoro") or {}
        require(
            bool(kokoro_provider.get("available")) and kokoro_provider.get("role") == "default_fast_fallback",
            "Kokoro is not available as the fast local fallback",
            checks,
            "kokoro_fallback_available",
        )
        require(bool(registry.get("high_quality_local", {}).get("available")), "accepted Qwen provider is unavailable", checks, "qwen_gate_available")
        require(bool((registry.get("selection_gate") or {}).get("passed")), "Qwen continuity gate is not recorded as passed", checks, "qwen_gate_passed")
        require(not privacy.get("telemetry") and not privacy.get("cloud_llm") and not privacy.get("cloud_tts") and not privacy.get("remote_assets"), "cloud or telemetry flag enabled", checks, "no_cloud_or_telemetry")
        result["runtime_endpoints"] = endpoints
        result["active_routes"] = len(paths)
        result["offline_evidence"] = "Qwen loads on demand with local_files_only while HF_HUB_OFFLINE and TRANSFORMERS_OFFLINE are set; active StoryDriver diagnostics expose local endpoints only."
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["passed"] = not result["errors"] and all(checks.values())
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print("PASS: installed runtime is local-only and the accepted Qwen worker remains on demand with no orphan process." if result["passed"] else "FAIL")
    for error in result["errors"]:
        print(f"  {error}")
    print(f"Metrics: {METRICS}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
