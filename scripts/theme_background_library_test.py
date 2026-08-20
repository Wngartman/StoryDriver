import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.background_library import (  # noqa: E402
    add_background,
    list_backgrounds,
    remove_background,
    rename_background,
    validate_image_bytes,
)


def chunk(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)


png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00\x00\x00" * 2)) + chunk(b"IEND", b"")
assert validate_image_bytes("fixture.png", png)[1:] == (2, 2)
try:
    validate_image_bytes("broken.png", png[:24])
except ValueError:
    pass
else:
    raise AssertionError("truncated PNG was accepted")

item = add_background("product-polish-fixture.png", png)
try:
    assert any(entry["id"] == item["id"] for entry in list_backgrounds()["items"])
    renamed = rename_background(item["id"], "Product polish fixture")
    assert renamed["display_name"] == "Product polish fixture"
finally:
    remove_background(item["id"])

for preset in ("defaultPreset.js", "emberfallPreset.js", "midnightAtelierPreset.js", "chronicleHallPreset.js", "writersWorkshopPreset.js"):
    if not (ROOT / "frontend" / "src" / "ui-presets" / preset).exists():
        raise AssertionError(f"retained theme missing: {preset}")
print("theme/background library contract: PASS")
