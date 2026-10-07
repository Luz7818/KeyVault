"""生成应用图标：把矢量盾徽光栅化为 PNG（运行时 iconphoto）与 ICO（exe 资源）。

纯 stdlib：zlib 手写 PNG、struct 手写 ICO 容器（Vista+ 支持 PNG-in-ICO）。
产物 packaging/icon.png（256px）与 packaging/icon.ico，入库随仓走。
运行：python packaging/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 256
SS = 3  # 超采样倍数（抗锯齿）

# 盾形轮廓（与 kv/gui/icons.draw_logo 同源，-1..1 网格）
SHIELD = [
    (0.0, -0.92), (0.82, -0.57), (0.82, 0.17), (0.42, 0.72),
    (0.0, 0.92), (-0.42, 0.72), (-0.82, 0.17), (-0.82, -0.57),
]

TOP_COLOR = (0x5B, 0x7C, 0xFF)
BOTTOM_COLOR = (0x32, 0x50, 0xC8)


def _smooth_ring(points: list[tuple[float, float]], per_seg: int = 16) -> list[tuple[float, float]]:
    """Catmull-Rom 闭合插值：8 顶点 → 平滑密集轮廓（对应 tk smooth=True 的观感）。"""
    n = len(points)
    out: list[tuple[float, float]] = []
    for i in range(n):
        p0, p1, p2, p3 = points[(i - 1) % n], points[i], points[(i + 1) % n], points[(i + 2) % n]
        for t in range(per_seg):
            u = t / per_seg
            u2, u3 = u * u, u * u * u
            out.append((
                0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * u
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * u2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * u3),
                0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * u
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * u2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * u3),
            ))
    return out


RING = _smooth_ring(SHIELD)


def _inside_shield(x: float, y: float) -> bool:
    """射线法：点是否在盾形轮廓内。"""
    inside = False
    n = len(RING)
    j = n - 1
    for i in range(n):
        xi, yi = RING[i]
        xj, yj = RING[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _inside_hole(x: float, y: float) -> bool:
    """钥匙孔：圆 + 底部圆头细长竖条的并集。"""
    if -0.17 <= x <= 0.17 and -0.37 <= y <= 0.42:
        if (x * x + (y + 0.15) * (y + 0.15)) <= 0.155 * 0.155:
            return True
        if -0.065 <= x <= 0.065 and -0.08 <= y <= 0.38:
            return True
        if (x * x + (y - 0.38) * (y - 0.38)) <= 0.065 * 0.065:
            return True
    return False


def _pixel(px: int, py: int) -> tuple[int, int, int, int]:
    """一个输出像素的 RGBA：盾内纵向渐变，钥匙孔白色，边缘靠超采样软化。"""
    hit = 0
    hole = 0
    for sy in range(SS):
        for sx in range(SS):
            ux = (px + (sx + 0.5) / SS) / SIZE * 2 - 1
            uy = (py + (sy + 0.5) / SS) / SIZE * 2 - 1
            if _inside_shield(ux, uy):
                hit += 1
                if _inside_hole(ux, uy):
                    hole += 1
    if hit == 0:
        return (0, 0, 0, 0)
    alpha = round(hit / (SS * SS) * 255)
    t = min(max(py / SIZE, 0.0), 1.0)
    r = round(TOP_COLOR[0] + (BOTTOM_COLOR[0] - TOP_COLOR[0]) * t)
    g = round(TOP_COLOR[1] + (BOTTOM_COLOR[1] - TOP_COLOR[1]) * t)
    b = round(TOP_COLOR[2] + (BOTTOM_COLOR[2] - TOP_COLOR[2]) * t)
    if hole * 2 >= hit:  # 钥匙孔占比过半就当白色
        r = g = b = 0xFF
    return (r, g, b, alpha)


def render_png_bytes() -> bytes:
    raw = bytearray()
    for py in range(SIZE):
        raw.append(0)  # filter: None
        for px in range(SIZE):
            raw.extend(_pixel(px, py))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def wrap_ico(png: bytes) -> bytes:
    """PNG-in-ICO 容器（256px 档，width/height 字节 0 表示 256）。"""
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def main() -> None:
    base = Path(__file__).resolve().parent
    png = render_png_bytes()
    (base / "icon.png").write_bytes(png)
    (base / "icon.ico").write_bytes(wrap_ico(png))
    print(f"icon.png ({len(png)} B) / icon.ico 已生成 -> {base}")


if __name__ == "__main__":
    main()
