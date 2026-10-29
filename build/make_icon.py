"""生成应用图标（纯标准库：手写 PNG + ICO 容器）。

图形：蓝色圆角底 + 白色卡片 + 三条待办横线 + 一个勾。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

BG = (26, 115, 232)      # #1a73e8
CARD = (255, 255, 255)
LINE = (150, 180, 220)
TICK = (26, 115, 232)


def _rounded(x, y, w, h, r, px, py) -> bool:
    """点 (px,py) 是否在圆角矩形内（x,y,w,h,r 均为 0-1 归一化）。"""
    if px < x or px > x + w or py < y or py > y + h:
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def _seg_dist(px, py, x1, y1, x2, y2) -> float:
    """点到线段的距离。"""
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    qx, qy = x1 + t * dx, y1 + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


def render(size: int) -> bytes:
    """返回 RGBA 像素（自上而下）。"""
    rows = []
    for j in range(size):
        row = bytearray()
        for i in range(size):
            px, py = (i + 0.5) / size, (j + 0.5) / size
            color, alpha = (0, 0, 0), 0
            if _rounded(0.02, 0.02, 0.96, 0.96, 0.22, px, py):
                color, alpha = BG, 255
            if _rounded(0.15, 0.15, 0.70, 0.70, 0.10, px, py):
                color, alpha = CARD, 255
            # 三条待办横线（左上）
            for cy, x2 in ((0.30, 0.66), (0.44, 0.66), (0.58, 0.52)):
                if abs(py - cy) <= 0.028 and 0.24 <= px <= x2:
                    color, alpha = LINE, 255
            # 右下对勾：两条线段
            if (_seg_dist(px, py, 0.50, 0.66, 0.59, 0.75) <= 0.032
                    or _seg_dist(px, py, 0.59, 0.75, 0.77, 0.50) <= 0.032):
                color, alpha = TICK, 255
            row += bytes((color[0], color[1], color[2], alpha))
        rows.append(bytes(row))
    return b"".join(rows)


def png(size: int) -> bytes:
    raw = b"".join(b"\x00" + render(size)[i * size * 4:(i + 1) * size * 4] for i in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def build_ico(path: Path, sizes=(16, 32, 48, 64, 128, 256)) -> Path:
    images = [png(s) for s in sizes]
    count = len(images)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    entries, blobs = b"", b""
    for size, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)
    return path


if __name__ == "__main__":
    out = build_ico(Path(__file__).resolve().parent / "app.ico")
    print("图标:", out, out.stat().st_size, "bytes")
