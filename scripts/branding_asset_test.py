import json
import struct
from pathlib import Path


root = Path(__file__).resolve().parents[1]
brand = root / "frontend/src/assets/brand"
public = root / "frontend/public"
for filename in ("storydriver-mark.svg", "storydriver-monochrome.svg", "storydriver-wordmark.svg"):
    text = (brand / filename).read_text(encoding="utf-8")
    if "<svg" not in text or "sparkle" in text.lower():
        raise AssertionError(f"invalid canonical branding asset: {filename}")
if (root / "backend/data/assets/storydriver-emblem-source.png").exists():
    raise AssertionError("obsolete raster-first emblem source remains")

expected = {
    "favicon-16x16.png": 16,
    "favicon-32x32.png": 32,
    "apple-touch-icon.png": 180,
    "android-chrome-192x192.png": 192,
    "android-chrome-512x512.png": 512,
    "brand/storydriver-mark-24.png": 24,
    "brand/storydriver-mark-64.png": 64,
    "brand/storydriver-brand-mark.png": 256,
}
for relative, size in expected.items():
    data = (public / relative).read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError(f"not PNG: {relative}")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) != (size, size):
        raise AssertionError(f"wrong dimensions for {relative}: {width}x{height}")
manifest = json.loads((public / "site.webmanifest").read_text(encoding="utf-8"))
if not all("maskable" in icon["purpose"] for icon in manifest["icons"]):
    raise AssertionError("PWA icons are not maskable")
if 'href="/favicon.svg"' not in (root / "frontend/index.html").read_text(encoding="utf-8"):
    raise AssertionError("SVG favicon is not primary")
print("branding asset contract: PASS")
