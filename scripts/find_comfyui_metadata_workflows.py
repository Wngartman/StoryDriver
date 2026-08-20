from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path


ROOTS = [
    Path(r"C:\Users\wngar\Documents\ComfyUI\output"),
    Path(r"D:\StoryDriver\backend\data\generated_images"),
]
TERMS = ("Lonecat", "Flux 2D", "Klein_9b", "Flux2-Klein", "flux-2-klein", "Flux/Klein")
REPORT_PATH = Path(r"D:\StoryDriver\backend\data\logs\COMFYUI_METADATA_WORKFLOW_SCAN.json")


def png_text_chunks(path: Path) -> dict[str, str]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return {}
    pos = 8
    chunks: dict[str, str] = {}
    while pos + 8 <= len(data):
        size = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + size]
        pos += 12 + size
        if chunk_type == b"tEXt":
            key, _, value = chunk.partition(b"\x00")
            if key:
                chunks[key.decode("latin1", errors="replace")] = value.decode("utf-8", errors="replace")
        elif chunk_type == b"iTXt":
            parts = chunk.split(b"\x00", 5)
            if len(parts) < 6:
                continue
            key = parts[0].decode("latin1", errors="replace")
            value = parts[5]
            if parts[1] == b"\x01":
                value = zlib.decompress(value)
            chunks[key] = value.decode("utf-8", errors="replace")
    return chunks


def summarize_json(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except Exception as error:  # noqa: BLE001
        return {"json_error": str(error), "preview": value[:500]}
    summary: dict[str, object] = {"json_type": type(parsed).__name__}
    if isinstance(parsed, dict):
        summary["top_keys"] = list(parsed)[:20]
        if "nodes" in parsed:
            summary["node_count"] = len(parsed.get("nodes") or [])
            summary["node_types"] = [
                node.get("type")
                for node in (parsed.get("nodes") or [])[:30]
                if isinstance(node, dict)
            ]
        else:
            summary["node_count"] = len(parsed)
            summary["class_types"] = sorted({
                node.get("class_type")
                for node in parsed.values()
                if isinstance(node, dict) and node.get("class_type")
            })[:80]
    return summary


def main() -> int:
    matches: list[dict] = []
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.png"):
            try:
                chunks = png_text_chunks(path)
            except Exception:
                continue
            if not chunks:
                continue
            combined = "\n".join(chunks.values())
            if not any(term.lower() in combined.lower() for term in TERMS):
                continue
            entry = {
                "path": str(path),
                "size": path.stat().st_size,
                "modified": path.stat().st_mtime,
                "chunks": {key: len(value) for key, value in chunks.items()},
            }
            for key in ("prompt", "workflow"):
                if key in chunks:
                    entry[key] = summarize_json(chunks[key])
            matches.append(entry)
    matches.sort(key=lambda item: item["modified"], reverse=True)
    report = {"roots": [str(root) for root in ROOTS], "matches": matches}
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "matches": len(matches)}, indent=2))
    for entry in matches[:20]:
        print(entry["path"])
        print("  prompt", entry.get("prompt", {}))
        print("  workflow", entry.get("workflow", {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
