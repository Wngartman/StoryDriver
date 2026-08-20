import hashlib
import json
import os
import re
import struct
import zlib
from pathlib import Path
from uuid import uuid4

from app.config import DATA_DIR


BACKGROUND_DIR = DATA_DIR / "assets" / "backgrounds"
METADATA_PATH = BACKGROUND_DIR / ".storydriver-backgrounds.json"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 30_000_000
MAX_DIMENSION = 8192


def ensure_background_dir() -> Path:
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    return BACKGROUND_DIR


def _load_metadata() -> dict:
    ensure_background_dir()
    if not METADATA_PATH.exists():
        return {"names": {}}
    try:
        data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"names": {}}
    return data if isinstance(data, dict) else {"names": {}}


def _save_metadata(data: dict) -> None:
    ensure_background_dir()
    temporary = METADATA_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, METADATA_PATH)


def _stable_id(path: Path) -> str:
    relative = path.relative_to(BACKGROUND_DIR).as_posix().lower()
    return hashlib.sha256(relative.encode("utf-8")).hexdigest()[:24]


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return None
    dimensions = struct.unpack(">II", data[16:24])
    offset = 8
    found_end = False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            return None
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            return None
        offset = chunk_end
        if chunk_type == b"IEND":
            found_end = True
            break
    return dimensions if found_end else None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        return None
    offset = 2
    while offset + 9 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            return None
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        if length < 2 or offset + length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
            return width, height
        offset += length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP" or int.from_bytes(data[4:8], "little") + 8 > len(data):
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return struct.unpack("<H", data[26:28])[0] & 0x3FFF, struct.unpack("<H", data[28:30])[0] & 0x3FFF
    return None


def validate_image_bytes(filename: str, data: bytes) -> tuple[str, int, int]:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError("Use a PNG, JPG/JPEG, or WebP image.")
    if not data:
        raise ValueError("The image file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Background images are limited to {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    if extension == ".png":
        dimensions = _png_dimensions(data)
    elif extension in {".jpg", ".jpeg"}:
        dimensions = _jpeg_dimensions(data)
    else:
        dimensions = _webp_dimensions(data)
    if not dimensions:
        raise ValueError("The image is malformed or its file type does not match its extension.")
    width, height = dimensions
    if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ValueError(f"Image dimensions exceed the safe {MAX_DIMENSION}px / {MAX_PIXELS:,}-pixel limit.")
    return extension, width, height


def _item_from_path(path: Path, names: dict) -> dict | None:
    try:
        extension, width, height = validate_image_bytes(path.name, path.read_bytes())
    except (OSError, ValueError):
        return None
    item_id = _stable_id(path)
    return {
        "id": item_id,
        "display_name": str(names.get(item_id) or path.stem.replace("_", " ").replace("-", " ")).strip(),
        "filename": path.name,
        "format": extension.lstrip(".").replace("jpeg", "jpg"),
        "width": width,
        "height": height,
        "bytes": path.stat().st_size,
        "url": f"/backgrounds/{item_id}/file",
        "updated_at": path.stat().st_mtime,
    }


def list_backgrounds() -> dict:
    names = _load_metadata().get("names", {})
    items = []
    for path in sorted(ensure_background_dir().iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        item = _item_from_path(path, names)
        if item:
            items.append(item)
    return {"folder": str(BACKGROUND_DIR), "max_file_bytes": MAX_FILE_BYTES, "items": items}


def resolve_background(item_id: str) -> Path | None:
    for path in ensure_background_dir().iterdir():
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and _stable_id(path) == item_id:
            return path
    return None


def add_background(filename: str, data: bytes) -> dict:
    extension, _, _ = validate_image_bytes(filename, data)
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")[:60] or "background"
    path = ensure_background_dir() / f"{stem}-{uuid4().hex[:12]}{extension}"
    path.write_bytes(data)
    item = _item_from_path(path, _load_metadata().get("names", {}))
    if not item:
        path.unlink(missing_ok=True)
        raise ValueError("The uploaded background could not be validated after saving.")
    return item


def rename_background(item_id: str, display_name: str) -> dict:
    path = resolve_background(item_id)
    if not path:
        raise FileNotFoundError(item_id)
    normalized = re.sub(r"\s+", " ", display_name).strip()[:100]
    if not normalized:
        raise ValueError("Display name cannot be empty.")
    metadata = _load_metadata()
    names = metadata.setdefault("names", {})
    names[item_id] = normalized
    _save_metadata(metadata)
    return _item_from_path(path, names)


def remove_background(item_id: str) -> None:
    path = resolve_background(item_id)
    if not path:
        raise FileNotFoundError(item_id)
    path.unlink()
    metadata = _load_metadata()
    metadata.setdefault("names", {}).pop(item_id, None)
    _save_metadata(metadata)
