#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片实验室 —— 给零基础用的练习场。

**注意:主界面已经有卡片网格了**(`bilifocus/ui_cards.py`)。这个文件是"沙盒":
改它不会影响正式程序,适合放心乱试新样式,试出满意的再搬进 ui_cards.py。

════════════════════════════════════════════════════════════════
 怎么用(30 秒)
════════════════════════════════════════════════════════════════

  1. 双击 `edit-lab.cmd`   → 用记事本打开这个文件
  2. 改下面「★ 旋钮」区里的数字或颜色
  3. Ctrl+S 保存,关掉记事本
  4. 双击 `run-lab.cmd`    → 看效果

  改错了不用怕:双击 run-lab.cmd 会停住显示报错,
  出错时会停住,方便复制报错信息。

════════════════════════════════════════════════════════════════
 唯一的规矩:不要删行首的空格
════════════════════════════════════════════════════════════════

  Python 靠"行首缩进"判断谁属于谁。删掉空格会报
  IndentationError。备注里那些行首空格,保持原样就行。

────────────────────────────────────────────────────────────────
 这个文件在干什么
────────────────────────────────────────────────────────────────

  它从你的收藏夹取 3 条视频,把每条画成一张"卡片",横着摆一排。
  画卡片的逻辑在 build_card() 里 —— 已经写好了,你可以改它的样子。

  这个文件只用于练习,不影响正式程序。
"""

from __future__ import annotations

import base64
import tkinter as tk

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilifocus.covers import CoverFetcher
from bilifocus.models import Video
from bilifocus.service import FavoritesService
from bilifocus.theme import enable_dpi_awareness, font, init_display, init_fonts

# ════════════════════════════════════════════════════════════════
#   ★ 旋钮区 —— 只改等号右边的值,别动左边的名字
# ════════════════════════════════════════════════════════════════

CARD_WIDTH = 260
"""卡片宽度(像素)。改成 320 卡片就变宽,封面也会跟着变大。试试 180 和 400。"""

COVER_HEIGHT = 146
"""封面高度(像素)。宽度会自动跟 CARD_WIDTH 一致。改大=封面更高。"""

TITLE_MAX_CHARS = 16
"""标题最多显示几个字,超出用 … 代替。改成 30 可以看到更长的标题。"""

CARD_BG = "#FFFCF5"
"""卡片底色。主界面现在是米白纸面风;想试鲜艳一点的,比如 "#F3E4D7"(暖橙)、"#E7EFE4"(浅绿)。"""

WATCHED_FG = "#A79D8C"
"""已看过的卡片,标题颜色。"""

TITLE_FG = "#2A2620"
"""没看过的卡片,标题颜色。"""

# 下面这些是配色/尺寸的固定值,一般不用改
BG = "#F6F1E7"
FG_DIM = "#7B7062"
PLACEHOLDER_BG = "#E8E0CF"
CARD_PAD = 10
COVER_WIDTH = CARD_WIDTH  # 封面宽度永远跟卡片一样宽

# ════════════════════════════════════════════════════════════════
#   下面是我写好的部分,你暂时不用改
# ════════════════════════════════════════════════════════════════


def shorten(text: str, limit: int) -> str:
    """太长就截断,末尾加一个省略号。

    len() 对中文是按"字"数,不是按字节,所以 16 就是 16 个汉字。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def build_card(
    parent: tk.Misc,
    video: Video,
    cover: tk.PhotoImage | None,
    *,
    watched: bool = False,
) -> tk.Frame:
    """把一条视频画成一张卡片,返回这张卡片。

    parent  卡片要放进去的"容器"(可以理解成一块面板)
    video   视频数据 —— 标题、UP主、时长都在上面
    cover   封面图片对象;没下载到时是 None
    watched 这条看过没有
    """
    # ── 第 1 步:一个矩形框,当作卡片本体 ──────────────────────
    card = tk.Frame(parent, bg=CARD_BG)

    # ── 第 2 步:封面(顶部,占满卡片宽度)────────────────────
    if cover is not None:
        # image=cover 表示"显示这张图";bd=0 去掉标签自带的边框
        cover_box = tk.Label(card, image=cover, bd=0, bg=CARD_BG)
    else:
        # 没有封面时,用一个同尺寸的画布画个灰块 + 文字
        cover_box = tk.Canvas(
            card,
            width=COVER_WIDTH,
            height=COVER_HEIGHT,
            bg=PLACEHOLDER_BG,
            highlightthickness=0,
        )
        cover_box.create_text(
            COVER_WIDTH // 2,
            COVER_HEIGHT // 2,
            text="无封面",
            fill=FG_DIM,
            font=font(10),
        )
    cover_box.pack()

    # ── 第 3 步:文字区(标题 + UP主/时长)────────────────────
    # pack(fill="x") = 横向撑满;padx/pady = 四周留白
    body = tk.Frame(card, bg=CARD_BG)
    body.pack(fill="x", padx=CARD_PAD, pady=(8, CARD_PAD))

    title_color = WATCHED_FG if watched else TITLE_FG
    title = tk.Label(
        body,
        text=shorten(video.title, TITLE_MAX_CHARS),
        bg=CARD_BG,
        fg=title_color,
        anchor="w",  # 文字靠左
        font=font(10),
    )
    title.pack(fill="x")

    meta = tk.Label(
        body,
        text=f"{video.upper} · {video.duration_text}",
        bg=CARD_BG,
        fg=FG_DIM,
        anchor="w",
        font=font(8),
    )
    meta.pack(fill="x", pady=(4, 0))

    return card


# ---------------------------------------------------------------------------
# 以下是脚手架:取数据、开窗口。都不用改。
# ---------------------------------------------------------------------------
def load_videos(service: FavoritesService, count: int = 3) -> list[Video]:
    """取几条真实收藏。老缓存里没有封面地址时,自动刷新一次。"""
    fid = service.default_fid
    if not fid:
        raise SystemExit("还没有默认收藏夹。先运行: python -m bilifocus list --fid <你的fid>")
    view = service.videos(fid)
    if not any(video.pic for video in view.videos):
        print("缓存里没有封面地址,刷新一次…")
        view = service.videos(fid, force=True)
    return list(view.videos[:count])


def main() -> int:
    service = FavoritesService()
    videos = load_videos(service)
    if not videos:
        print("收藏夹是空的,没东西可画。")
        return 1

    fetcher = CoverFetcher(service.store.home / "cache" / "covers")
    watched_keys = {video.key for video in videos if service.is_watched(video)}

    enable_dpi_awareness()   # 放在建窗口之前,否则字会发虚
    root = tk.Tk()
    init_fonts(root)
    init_display(root)
    root.title("卡片实验室 — 改 ★ 旋钮区,保存后重开")
    root.configure(bg=BG)
    root.geometry("900x420+420+200")

    tk.Label(
        root,
        text="改 lab_card.py 顶部 ★ 旋钮区的数字,保存 → 关掉 → 双击 run-lab.cmd",
        bg=BG,
        fg=FG_DIM,
        font=font(9),
    ).pack(pady=(10, 6))

    row = tk.Frame(root, bg=BG)
    row.pack(padx=20, pady=10, anchor="n")

    # 图片对象必须被引用住,否则会被 Python 回收,界面上的图就消失了。
    # 这是 Tk 最经典的坑,这里先帮你兜住。
    photos: list[tk.PhotoImage] = []
    for video in videos:
        data = fetcher.fetch(video.cover_url(COVER_WIDTH, COVER_HEIGHT))
        photo = tk.PhotoImage(data=base64.b64encode(data)) if data else None
        if photo is not None:
            photos.append(photo)
        card = build_card(row, video, photo, watched=video.key in watched_keys)
        card.pack(side="left", padx=8)

    root._photos = photos  # 挂住引用,防止被回收
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
