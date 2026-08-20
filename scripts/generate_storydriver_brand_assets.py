from __future__ import annotations

import json
import math
import struct
import zlib
from binascii import crc32
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DESIGN_SIZE = 64.0
PAGE = [(12, 6), (41, 6), (52, 17), (52, 58), (12, 58)]
PAGE_OUTLINE = [(12, 6), (41, 6), (52, 17), (52, 58), (12, 58), (12, 6)]
FOLD = [(41, 6), (41, 18), (52, 18)]
ROUTE = [(24, 50), (24, 40), (40, 31), (31, 23), (31, 14)]


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def segment_distance(x: float, y: float, start: tuple[float, float], end: tuple[float, float]) -> float:
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    position = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - (x1 + position * dx), y - (y1 + position * dy))


def polyline_distance(x: float, y: float, points: list[tuple[float, float]]) -> float:
    return min(segment_distance(x, y, points[index], points[index + 1]) for index in range(len(points) - 1))


def sample_mark(x: float, y: float, background: bool) -> tuple[int, int, int, int]:
    color = (9, 10, 12, 255) if background else (0, 0, 0, 0)
    if point_in_polygon(x, y, PAGE):
        color = (17, 19, 24, 255)
    if polyline_distance(x, y, PAGE_OUTLINE) <= 2.0 or polyline_distance(x, y, FOLD) <= 2.0:
        color = (232, 228, 218, 255)
    if polyline_distance(x, y, ROUTE) <= 2.5:
        color = (217, 167, 96, 255)
    return color


def render(size: int, *, background: bool, samples: int = 4) -> bytearray:
    output = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            totals = [0, 0, 0, 0]
            for sy in range(samples):
                for sx in range(samples):
                    design_x = (x + (sx + 0.5) / samples) * DESIGN_SIZE / size
                    design_y = (y + (sy + 0.5) / samples) * DESIGN_SIZE / size
                    red, green, blue, alpha = sample_mark(design_x, design_y, background)
                    totals[0] += red * alpha
                    totals[1] += green * alpha
                    totals[2] += blue * alpha
                    totals[3] += alpha
            count = samples * samples
            alpha = round(totals[3] / count)
            index = (y * size + x) * 4
            if totals[3]:
                output[index : index + 4] = bytes((
                    round(totals[0] / totals[3]),
                    round(totals[1] / totals[3]),
                    round(totals[2] / totals[3]),
                    alpha,
                ))
    return output


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc32(kind + payload) & 0xFFFFFFFF)


def png_bytes(size: int, rgba: bytearray) -> bytes:
    rows = bytearray()
    stride = size * 4
    for y in range(size):
        rows.append(0)
        rows.extend(rgba[y * stride : (y + 1) * stride])
    return (
        PNG_SIGNATURE
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + png_chunk(b"IEND", b"")
    )


def write_png(path: Path, size: int, *, background: bool) -> bytes:
    data = png_bytes(size, render(size, background=background))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def write_ico(path: Path, entries: list[tuple[int, bytes]]) -> None:
    header = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    payload = bytearray()
    for size, data in entries:
        header.extend(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(data), offset))
        payload.extend(data)
        offset += len(data)
    path.write_bytes(header + payload)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "frontend" / "src" / "assets" / "brand" / "storydriver-mark.svg"
    if not source.exists():
        raise FileNotFoundError(f"Canonical brand source missing: {source}")
    public = root / "frontend" / "public"
    brand = public / "brand"
    outputs = [
        (public / "favicon-16x16.png", 16, True),
        (public / "favicon-32x32.png", 32, True),
        (public / "apple-touch-icon.png", 180, True),
        (public / "android-chrome-192x192.png", 192, True),
        (public / "android-chrome-512x512.png", 512, True),
        (brand / "storydriver-mark-24.png", 24, False),
        (brand / "storydriver-mark-64.png", 64, False),
        (brand / "storydriver-brand-mark.png", 256, False),
    ]
    rendered = {}
    for path, size, background in outputs:
        rendered[size, background] = write_png(path, size, background=background)
    write_ico(public / "favicon.ico", [
        (16, rendered[16, True]),
        (32, rendered[32, True]),
        (48, png_bytes(48, render(48, background=True))),
    ])
    manifest = {
        "name": "StoryDriver",
        "short_name": "StoryDriver",
        "description": "Local directed fiction workspace for writing, story state, and narration.",
        "id": "/storydriver",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#090a0c",
        "theme_color": "#090a0c",
        "icons": [
            {"src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    (public / "site.webmanifest").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Generated local StoryDriver brand assets from {source}")


if __name__ == "__main__":
    main()
