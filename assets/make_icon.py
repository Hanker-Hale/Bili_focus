#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从手绘原画生成 BiliFocus 的应用图标。

原画:`assets/icon-art.jpg`(1080x1080,浅蓝底 + 手写 bilifocus + 灯泡小人)。

小尺寸(16/24/32/40)单独裁"灯泡 + 小人"区域:原画上的文字在 16px 下会糊成一片,
只保留图形主体,任务栏/开始菜单里才认得出来。ICO 里每个尺寸放各自渲染的 PNG,
所以这里手写 ICO 容器,而不是让 PIL 用一张图缩放出所有尺寸。

产物:
  assets/bilifocus-icon.ico   应用图标(16~256 七个尺寸,exe/安装器/窗口都用它)
  assets/icon-512.png         512 透明圆角 PNG(官网 logo / favicon / 文档)
  assets/icon-preview.png     各尺寸在白/纸/深色背景下的预览

用法:
    <带 PIL 的 python> assets/make_icon.py
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ART_PATH = HERE / "icon-art.jpg"
ICO_PATH = HERE / "bilifocus-icon.ico"
PNG_PATH = HERE / "icon-512.png"
PNG128_PATH = HERE / "icon-128.png"
PREVIEW_PATH = HERE / "icon-preview.png"

SIZES = (16, 24, 32, 40, 48, 64, 128, 256)
SMALL_CUTOFF = 40          # <= 这个尺寸裁灯泡特写
CORNER_RADIUS = 0.18       # 圆角比例(0 = 方角)
SUPERSAMPLE = 4

# 灯泡特写区域(相对原画比例):避开顶部文字,保留灯泡和小人
CROP_BOX = (0.18, 0.28, 0.86, 0.96)


def _rounded(image: Image.Image, radius_ratio: float = CORNER_RADIUS) -> Image.Image:
    """给方形图加透明圆角。"""
    size = image.width
    big = size * SUPERSAMPLE
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * radius_ratio), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    result = image.convert("RGBA")
    result.putalpha(mask)
    return result


def _art() -> Image.Image:
    if not ART_PATH.is_file():
        raise SystemExit(f"缺少原画:{ART_PATH}")
    return Image.open(ART_PATH).convert("RGB")


def _render(size: int) -> Image.Image:
    art = _art()
    width, height = art.size
    if size <= SMALL_CUTOFF:
        left, top, right, bottom = CROP_BOX
        art = art.crop(
            (int(width * left), int(height * top), int(width * right), int(height * bottom))
        )
    return _rounded(art.resize((size, size), Image.LANCZOS))


def build_ico(sizes: tuple[int, ...]) -> bytes:
    """手写 ICO 容器:每个尺寸放各自渲染的 PNG。"""
    frames: list[tuple[int, bytes]] = []
    for size in sizes:
        buffer = io.BytesIO()
        _render(size).save(buffer, format="PNG", optimize=True)
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
    """拼一张对比图:分别放在白色、纸色、深色背景上看效果。"""
    gap = 24
    zoom = 4

    backdrops = (
        ("light", (255, 255, 255, 255)),
        ("paper", (251, 247, 238, 255)),
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
    sheet = Image.new("RGBA", (content_w, row_h * len(backdrops)), (0, 0, 0, 255))
    for row, (_name, backdrop) in enumerate(backdrops):
        top = row * row_h
        ImageDraw.Draw(sheet).rectangle([0, top, content_w, top + row_h], fill=backdrop)
        x = gap
        for _size, image in rendered:
            sheet.alpha_composite(image, (x, top + (row_h - image.height) // 2))
            x += image.width + gap
    return sheet


def main() -> int:
    ICO_PATH.write_bytes(build_ico(SIZES))
    _render(512).save(PNG_PATH, format="PNG", optimize=True)
    _render(128).save(PNG128_PATH, format="PNG", optimize=True)
    build_preview().save(PREVIEW_PATH, format="PNG")
    print(f"icon : {ICO_PATH.name} ({ICO_PATH.stat().st_size} bytes, {len(SIZES)} sizes)")
    print(f"png  : {PNG_PATH.name} ({PNG_PATH.stat().st_size} bytes)")
    print(f"png  : {PNG128_PATH.name} ({PNG128_PATH.stat().st_size} bytes)")
    print(f"sheet: {PREVIEW_PATH.name} ({PREVIEW_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
