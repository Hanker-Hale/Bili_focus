"""界面配色、字体、卡片尺寸、DPI 适配。

单独一个模块,是因为界面(ui_tk)和卡片网格(ui_cards)都要用同一套;
放在任何一边都会造成循环 import。

**关于"字发虚"**:Windows 默认把程序当"96 DPI 不感知"处理,于是系统会把
整个窗口按缩放比例(这台机器是 200%)位图放大——字就糊了。正确做法是在创建
Tk 之前声明每显示器 DPI 感知,让程序直接按物理像素绘制;
代价是所有"像素"尺寸都要跟着缩放比乘一遍,所以这里有 `init_display()`。
"""

from __future__ import annotations

import sys

# ── 底色(米白系)────────────────────────────────────────────────
BG = "#FCFAF6"            # 页面底色:米白(更接近纸白)
BG_ALT = "#F4F0E8"        # 输入框 / 次要按钮
CARD_BG = "#FFFFFF"       # 卡片:纯白,让卡片从米白底上浮起来
CARD_HOVER = "#F7F3EB"
BORDER = "#E8E1D4"
PLACEHOLDER_BG = "#F0EBE2"

# ── 文字(偏黑,不是纯黑,避免刺眼)──────────────────────────────
FG = "#12100C"            # 主文字:近黑
FG_DIM = "#4F4739"        # 次要文字
WATCHED_FG = "#A49B8B"    # 已看过的标题

# ── 强调与语义色 ────────────────────────────────────────────────
ACCENT = "#2A80BE"        # 天蓝:比原来的青蓝更亮、更"天空"
ACCENT_DARK = "#1E6A9F"
WARN = "#8A5B0A"
ERROR = "#A32C22"

# ── 时长角标 ────────────────────────────────────────────────────
BADGE_BG = "#12100C"
BADGE_FG = "#FFFFFF"

# ── 卡片尺寸(基准值;真实值 = 基准 × DPI 缩放)──────────────────
_BASE_CARD_WIDTH = 244
_BASE_COVER_HEIGHT = 137
_BASE_CARD_GAP = 14
_BASE_GRID_PAD = 14

SCALE = 1.0               # 由 init_display() 按真实 DPI 设置
CARD_WIDTH = _BASE_CARD_WIDTH
COVER_WIDTH = _BASE_CARD_WIDTH
COVER_HEIGHT = _BASE_COVER_HEIGHT
CARD_GAP = _BASE_CARD_GAP
GRID_PAD = _BASE_GRID_PAD


def px(value: float) -> int:
    """把"设计稿像素"换算成当前 DPI 下的实际像素。

    界面里所有 padx / pady / 固定宽高都应该走这里,否则在高 DPI 屏上
    间距会显得只有一半。
    """
    return int(round(value * SCALE))


def enable_dpi_awareness() -> bool:
    """在 **创建 Tk 之前** 调用,声明每显示器 DPI 感知。

    不声明的话,Windows 会把 96 DPI 渲染的窗口位图放大到屏幕缩放比例,
    文字发虚——这正是"分辨率很低"的观感来源。
    """
    if sys.platform != "win32":
        return False
    import ctypes

    # PER_MONITOR_AWARE_V2(Win10 1703+)
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:  # noqa: BLE001 - 老系统没有这个 API
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE(Win8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:  # noqa: BLE001
        return False


# ── 字体 ────────────────────────────────────────────────────────
# 幼圆(YouYuan)是 Windows 中文自带字体,圆润手写感,和手绘图标最搭;
# 没装幼圆的系统(英文版 Windows 精简安装)依次退回楷体/雅黑。
FONT_CANDIDATES = ("幼圆", "YouYuan", "You Yuan", "楷体", "华文楷体", "KaiTi", "Microsoft YaHei UI")
FONT_FAMILY = "幼圆"


def init_fonts(root) -> str:
    """挑一个真实存在的字体族(幼圆在 Tk 里叫 `幼圆`,不是 "YouYuan")。"""
    global FONT_FAMILY
    try:
        from tkinter import font as tkfont

        available = set(tkfont.families(root))
    except Exception:  # noqa: BLE001
        return FONT_FAMILY
    for name in FONT_CANDIDATES:
        if name in available:
            FONT_FAMILY = name
            break
    return FONT_FAMILY


def font(size: int, weight: str = "normal") -> tuple:
    """统一字体入口:`font(10)` / `font(11, "bold")`。

    字号单位是"点",Tk 会按 DPI 自动换算成像素,所以这里不用乘 SCALE。
    """
    if weight and weight != "normal":
        return (FONT_FAMILY, size, weight)
    return (FONT_FAMILY, size)


def init_display(root) -> float:
    """创建 root 之后调用一次:设置 Tk 缩放,并更新布局尺寸。"""
    global SCALE, CARD_WIDTH, COVER_WIDTH, COVER_HEIGHT, CARD_GAP, GRID_PAD
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:  # noqa: BLE001
        dpi = 96.0
    SCALE = max(1.0, round(dpi / 96.0, 2))
    try:
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:  # noqa: BLE001
        pass
    CARD_WIDTH = px(_BASE_CARD_WIDTH)
    COVER_WIDTH = CARD_WIDTH
    COVER_HEIGHT = px(_BASE_COVER_HEIGHT)
    CARD_GAP = px(_BASE_CARD_GAP)
    GRID_PAD = px(_BASE_GRID_PAD)
    return SCALE
