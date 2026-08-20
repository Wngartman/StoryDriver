from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "backend" / "data" / "logs" / "FLUX_KLEIN_METADATA_INSPECTION.json"
OUTPUT_FILES = [
    Path(r"C:\Users\wngar\Documents\ComfyUI\output\Flux2-Klein_00001_.png"),
    Path(r"C:\Users\wngar\Documents\ComfyUI\output\Flux2-Klein_00002_.png"),
]


def png_text_chunks(path: Path) -> dict[str, str]:
    data = path.read_bytes()
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


def main() -> int:
    report: dict[str, object] = {"files": []}
    for path in OUTPUT_FILES:
        entry: dict[str, object] = {"path": str(path), "exists": path.exists()}
        if path.exists():
            chunks = png_text_chunks(path)
            entry["chunks"] = {key: len(value) for key, value in chunks.items()}
            for key in ("prompt", "workflow"):
                if key in chunks:
                    try:
                        parsed = json.loads(chunks[key])
                        entry[f"{key}_json_type"] = type(parsed).__name__
                        entry[f"{key}_top_keys"] = list(parsed)[:20] if isinstance(parsed, dict) else None
                        entry[f"{key}_value"] = parsed
                    except Exception as error:  # noqa: BLE001
                        entry[f"{key}_json_error"] = str(error)
                        entry[f"{key}_preview"] = chunks[key][:500]
        report["files"].append(entry)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"report": str(REPORT_PATH), "files": len(report["files"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
