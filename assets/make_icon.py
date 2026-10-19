#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 BiliFocus 的图标(assets/bilifocus-icon.ico + 预览图)。

设计思路(与应用内风格严格对齐):

  · 米白纸面底 —— 用界面的 #F6F1E7 系暖白,而不是深色。
    图标在深色任务栏上自然突出,在浅色桌面/资源管理器里靠
    #E0D6C3 的细描边保持边界。
  · 青蓝"取景框"四角 —— 用主题强调色 #0B7BA0,和启动页的 logo 一模一样。
  · 近黑播放三角 —— 用正文色 #12100C,和启动页的三角同色。

小尺寸(16/24)单独画一版:去掉细线取景框,改成青蓝实心底 + 米白三角,
否则 1~2px 的线条会糊成一团。ICO 里每个尺寸可以放不同的图,
所以这里手写 ICO 容器,而不是让 PIL 用一张图缩放出所有尺寸。

用法:
    <带 PIL 的 python> assets/make_icon.py
"""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ICO_PATH = HERE / "bilifocus-icon.ico"
PREVIEW_PATH = HERE / "icon-preview.png"

# 配色(和 bilifocus/theme.py 保持一致)
CREAM = (248, 243, 232, 255)         # #F8F3E8 纸面(略亮于页面底色,更"图标感")
CREAM_EDGE = (216, 204, 182, 255)    # #D8CCB6 细描边(浅色桌面上保边界)
INK = (18, 16, 12, 255)              # #12100C 播放三角
ACCENT = (11, 123, 160, 255)         # #0B7BA0 取景框
ACCENT_DEEP = (9, 100, 131, 255)     # #096483 小尺寸实心底

SUPERSAMPLE = 4


def _rounded(size: int, radius_ratio: float, fill, outline=None, outline_width: int = 0) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(size * radius_ratio)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=fill)
    if outline and outline_width:
        draw.rounded_rectangle(
            [0, 0, size - 1, size - 1], radius=radius, outline=outline, width=outline_width
        )
    return image


def _triangle(draw: ImageDraw.ImageDraw, size: int, color, *, scale: float = 1.0) -> None:
    """播放三角。视觉重心略偏右,不然看起来会往左倒。"""
    cx, cy = size * 0.525, size * 0.5
    half_h = size * 0.215 * scale
    half_w = size * 0.185 * scale
    points = [
        (cx - half_w, cy - half_h),
        (cx - half_w, cy + half_h),
        (cx + half_w, cy),
    ]
    draw.polygon(points, fill=color)


def draw_large(size: int) -> Image.Image:
    """大尺寸:米白纸面 + 青蓝取景框 + 近黑三角(同启动页)。"""
    s = size * SUPERSAMPLE
    image = _rounded(s, 0.235, CREAM, CREAM_EDGE, max(2, s // 90))
    draw = ImageDraw.Draw(image)

    inset = s * 0.195
    arm = s * 0.135
    width = max(3, int(s * 0.044))
    left, top, right, bottom = inset, inset, s - inset, s - inset

    # 四个 L 形角
    draw.line([(left, top + arm), (left, top), (left + arm, top)], fill=ACCENT, width=width, joint="curve")
    draw.line([(right - arm, top), (right, top), (right, top + arm)], fill=ACCENT, width=width, joint="curve")
    draw.line([(left, bottom - arm), (left, bottom), (left + arm, bottom)], fill=ACCENT, width=width, joint="curve")
    draw.line(
        [(right - arm, bottom), (right, bottom), (right, bottom - arm)],
        fill=ACCENT,
        width=width,
        joint="curve",
    )

    _triangle(draw, s, INK, scale=0.8)
    return image.resize((size, size), Image.LANCZOS)


def draw_small(size: int) -> Image.Image:
    """小尺寸:去掉细线,只留青蓝底 + 米白三角,保证 16px 也认得出来。"""
    s = size * SUPERSAMPLE
    image = _rounded(s, 0.26, ACCENT_DEEP)
    draw = ImageDraw.Draw(image)
    _triangle(draw, s, CREAM, scale=0.92)
    return image.resize((size, size), Image.LANCZOS)


def _render(size: int) -> Image.Image:
    return draw_large(size) if size >= 32 else draw_small(size)


def build_ico(sizes: tuple[int, ...]) -> bytes:
    """手写 ICO 容器:每个尺寸放各自渲染的 PNG。"""
    frames: list[tuple[int, bytes]] = []
    for size in sizes:
        image = _render(size)
        buffer = __import__("io").BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        frames.append((size, buffer.getvalue()))

    header = struct.pack("<HHH", 0, 1, len(frames))
    directory = b""
    offset = 6 + 16 * len(frames)
    payload = b""
    for size, data in frames:
        dimension = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)
        payload += data
    return header + directory + payload


def build_preview(sizes: tuple[int, ...] = (256, 64, 32, 24, 16)) -> Image.Image:
    """拼一张对比图:分别放在浅色、纸色、深色背景上看效果。"""
    gap = 24
    zoom = 4  # 小图放大展示像素

    rows_backdrops = (
        ("light", (255, 255, 255, 255)),
        ("paper", (246, 241, 231, 255)),
        ("dark", (30, 31, 35, 255)),
    )

    rendered: list[tuple[int, Image.Image]] = []
    for size in sizes:
        image = _render(size)
        if size < 64:
            image = image.resize((size * zoom, size * zoom), Image.NEAREST)
        rendered.append((size, image))

    content_w = sum(image.width for _size, image in rendered) + gap * (len(rendered) + 1)
    row_h = max(image.height for _size, image in rendered) + gap * 2
    sheet = Image.new("RGBA", (content_w, row_h * len(rows_backdrops)), (0, 0, 0, 255))
    for row, (_name, backdrop) in enumerate(rows_backdrops):
        top = row * row_h
        ImageDraw.Draw(sheet).rectangle([0, top, content_w, top + row_h], fill=backdrop)
        x = gap
        for _size, image in rendered:
            sheet.alpha_composite(image, (x, top + (row_h - image.height) // 2))
            x += image.width + gap
    return sheet


def main() -> int:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    ICO_PATH.write_bytes(build_ico(sizes))
    build_preview().save(PREVIEW_PATH, format="PNG")
    print(f"icon : {ICO_PATH} ({ICO_PATH.stat().st_size} bytes, {len(sizes)} sizes)")
    print(f"sheet: {PREVIEW_PATH} ({PREVIEW_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
