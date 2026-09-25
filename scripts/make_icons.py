"""Draw the app icons (PNG) from the same shapes as the favicon, with no image library.

    python scripts/make_icons.py

Writes market_tracker/static/icon-192.png, icon-512.png, icon-maskable-512.png and
apple-touch-icon.png (180 px). The maskable one fills the square and keeps the mark inside the
middle 70%, so Android can crop it to any shape.
"""

from __future__ import annotations

import os
import struct
import zlib

BG = (0x1E, 0x23, 0x28)
INK = (0xEE, 0xF1, 0xF4)
ACCENT = (0x6F, 0x86, 0xF4)
OUT = os.path.join(os.path.dirname(__file__), "..", "market_tracker", "static")


def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _in_round_rect(x, y, size, r):
    cx = min(max(x, r), size - r)
    cy = min(max(y, r), size - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _color(u, v, maskable):
    """Colour at (u, v) in the favicon's 32-unit box, or None for transparent."""
    if not maskable and not _in_round_rect(u, v, 32, 7):
        return None
    # Maskable: scale the mark to 70% around the centre.
    if maskable:
        u, v = 16 + (u - 16) / 0.7, 16 + (v - 16) / 0.7
    if abs(u - 16) / 4 + abs(v - 23) / 5 <= 1:
        return ACCENT
    if _seg_dist(u, v, 9, 7, 23, 7) <= 1.1 or _seg_dist(u, v, 16, 7, 16, 19) <= 1.1:
        return INK
    return BG


def draw(size: int, maskable: bool = False, ss: int = 4) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    u = (x + (sx + 0.5) / ss) * 32 / size
                    v = (y + (sy + 0.5) / ss) * 32 / size
                    c = _color(u, v, maskable)
                    if c:
                        acc[0] += c[0]
                        acc[1] += c[1]
                        acc[2] += c[2]
                        acc[3] += 255
            n = ss * ss
            a = acc[3] // n
            row += bytes([acc[0] * 255 // max(acc[3], 1), acc[1] * 255 // max(acc[3], 1), acc[2] * 255 // max(acc[3], 1), a]) \
                if a else bytes(4)
        rows.append(bytes(row))
    return png(size, size, b"".join(rows))


def png(w: int, h: int, raw: bytes) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> None:
    for name, size, mask in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                             ("icon-maskable-512.png", 512, True), ("apple-touch-icon.png", 180, True)):
        with open(os.path.join(OUT, name), "wb") as fh:
            fh.write(draw(size, mask))
        print("wrote", name)


if __name__ == "__main__":
    main()
