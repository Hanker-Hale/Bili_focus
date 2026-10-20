#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示模式:用示例数据启动完整界面,专门给录屏 / 截图用。

特点:

- **不碰真实数据**:演示数据放在临时目录,退出即消失,不会读你的收藏夹和登录态;
- 收藏夹、分类、进度、日程、退出小结都是示例内容,录视频不用打码;
- 关闭窗口退出;ESC 只退出全屏(和正式版一致)。

用法(仓库根目录):

    python dev/demo_app.py

录播放器那几秒时,示例视频的 bvid 是假的,打不开真实页面;
那段建议用你自己收藏里的**公开视频**(播放时画面里只有 B 站视频页,不暴露收藏夹)。
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_data  # noqa: E402
from favfocus import theme, ui_tk  # noqa: E402
from favfocus.models import FavoritesView  # noqa: E402


def pump(root: tk.Tk, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        root.update_idletasks()
        root.update()
        time.sleep(0.01)


def main() -> int:
    # 高 DPI 下也清晰:必须在 tk.Tk() 之前调用
    theme.enable_dpi_awareness()
    # 录屏时别弹"发现新版本"横幅,画面干净一点
    os.environ["FAVFOCUS_NO_UPDATE_CHECK"] = "1"

    service, videos, folder = demo_data.build_demo()

    root = tk.Tk()
    app = ui_tk.FocusApp(
        root,
        fid_override=demo_data.DEMO_FID,
        mid=0,
        bootstrap=False,
        service=service,
        fullscreen=True,
    )
    app.folders = [folder]
    app.folder_box.configure(values=[f"{folder.title} ({folder.media_count})"])
    app.folder_box.current(0)

    # 等全屏生效、网格按最终宽度算好列数,再补种示例封面
    pump(root, 1.4)
    grid = app.grid_view
    demo_data.seed_covers(service.store.home, videos, grid._cover_w, grid._cover_h)
    app.videos = list(videos)
    app._render()
    app._hide_splash()

    view = FavoritesView(
        fid=demo_data.DEMO_FID,
        videos=tuple(videos),
        total=len(videos),
        fetched_at=time.time(),
        from_cache=True,
        checked_at=time.time(),
        new_count=0,
    )
    stats = service.stats(view)
    digest = service.digest(videos)
    app._set_status(
        f"共 {stats['count']} 条,已看完 {digest['finished']},"
        f"未看完 {digest['remaining']}(演示模式)。双击播放。"
    )
    pump(root, 1.0)

    print(f"演示模式已启动(数据目录:{service.store.home})")
    print("关闭窗口即可退出;不会读写真实数据。")
    root.mainloop()
    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
