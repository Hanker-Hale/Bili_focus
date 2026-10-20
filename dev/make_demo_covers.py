#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新生成演示用的封面图(配色统一到应用的米白 + 浅蓝天蓝).

产物:
  dev/demo-covers/01.png ... 17.png         488x274(卡片网格用)
  dev/demo-covers-small/01.png ... 17.png   232x132(日程面板用)

设计:
  - 所有封面都在浅蓝/青/米白这一族里,按分类只有很轻微的色相变化,不再出现紫/棕等跳色;
  - 米白取景框角 + 播放三角,和启动页/图标是同一套手绘语言;
  - 不写"示例封面"字样,画面更干净;只在左下角放分类词。

需要 Pillow(仅开发时用,应用本身不依赖):

    <带 PIL 的 python> dev/make_demo_covers.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
BIG_DIR = HERE / "demo-covers"
SMALL_DIR = HERE / "demo-covers-small"
BIG_SIZE = (488, 274)
SMALL_SIZE = (232, 132)

CREAM = (255, 250, 240)
CREAM_DIM = (255, 250, 240, 190)
CREAM_FAINT = (255, 250, 240, 60)

# 分类 → (渐变起色, 渐变止色, 左下角分类词)
PALETTE = {
    "编程": ((143, 208, 255), (95, 184, 245), "编程"),
    "数学": ((159, 216, 232), (103, 190, 214), "数学"),
    "AI": ((169, 200, 255), (110, 159, 240), "AI"),
    "英语": ((191, 224, 255), (136, 188, 217), "英语"),
    "图形学": ((143, 217, 218), (95, 183, 196), "图形学"),
    "公开课": ((200, 214, 235), (140, 170, 205), "公开课"),
}

# 和第 1~17 条示例视频一一对应
ITEMS = (
    ("编程", 1), ("编程", 2), ("编程", 3), ("编程", 4), ("编程", 5),
    ("数学", 6), ("数学", 7), ("数学", 8),
    ("AI", 9), ("AI", 10), ("AI", 11),
    ("英语", 12), ("英语", 13),
    ("图形学", 14), ("图形学", 15),
    ("公开课", 16), ("公开课", 17),
)


def _font(size: int):
    for path in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient(size: tuple[int, int], start, stop) -> Image.Image:
    """对角渐变;横向为主,带一点纵向变化,避免死板。"""
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            ratio = (x / max(1, width - 1)) * 0.75 + (y / max(1, height - 1)) * 0.25
            pixels[x, y] = tuple(
                round(a + (b - a) * ratio) for a, b in zip(start, stop)
            )
    return image


def _draw_cover(size: tuple[int, int], category: str) -> Image.Image:
    start, stop, word = PALETTE[category]
    base = _gradient(size, start, stop).convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    width, height = size
    draw = ImageDraw.Draw(overlay)

    # 中央很淡的圆形高光,让播放三角下面有层次
    cx, cy = width // 2, int(height * 0.48)
    radius = int(height * 0.34)
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=CREAM_FAINT,
    )

    # 米白取景框四角
    inset = int(min(width, height) * 0.075)
    arm = int(min(width, height) * 0.16)
    thickness = max(2, int(min(width, height) * 0.018))
    left, top, right, bottom = inset, inset, width - inset, height - inset
    for (x0, y0, x1, y1) in (
        (left, top + arm, left, top),
        (left, top, left + arm, top),
        (right - arm, top, right, top),
        (right, top, right, top + arm),
        (left, bottom - arm, left, bottom),
        (left, bottom, left + arm, bottom),
        (right - arm, bottom, right, bottom),
        (right, bottom, right, bottom - arm),
    ):
        draw.line((x0, y0, x1, y1), fill=CREAM, width=thickness)

    # 播放三角
    half_h = int(height * 0.19)
    half_w = int(width * 0.095)
    draw.polygon(
        [
            (cx - half_w, cy - half_h),
            (cx - half_w, cy + half_h),
            (cx + half_w, cy),
        ],
        fill=(255, 250, 240, 235),
    )

    # 左下角分类词
    font = _font(max(13, int(height * 0.115)))
    margin = int(width * 0.055)
    draw.text(
        (margin, height - margin),
        word,
        font=font,
        fill=(255, 250, 240, 215),
        anchor="ls",
    )
    return Image.alpha_composite(base, overlay).convert("RGB")


def main() -> int:
    BIG_DIR.mkdir(parents=True, exist_ok=True)
    SMALL_DIR.mkdir(parents=True, exist_ok=True)
    for index, (category, number) in enumerate(ITEMS, start=1):
        big = _draw_cover(BIG_SIZE, category)
        big.save(BIG_DIR / f"{number:02d}.png", format="PNG", optimize=True)
        small = big.resize(SMALL_SIZE, Image.LANCZOS)
        small.save(SMALL_DIR / f"{number:02d}.png", format="PNG", optimize=True)
    print(f"已生成 {len(ITEMS)} 张封面:{BIG_DIR} / {SMALL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
