#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""示例数据:给截图和演示用的一套假数据(不联网、不含任何真实账号信息)。

用法:
    from demo_data import build_demo
    service, videos, folder = build_demo()     # 临时目录,进程结束即丢

也可以单独运行它,打印出示例数据目录:

    python dev/demo_data.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bilifocus.models import Folder, Video  # noqa: E402
from bilifocus.service import FavoritesService  # noqa: E402
from bilifocus.store import StateStore  # noqa: E402

DEMO_FID = 10001
DEMO_USERNAME = "示例用户"
COVERS_DIR = Path(__file__).resolve().parent / "demo-covers"
COVERS_DIR_SMALL = Path(__file__).resolve().parent / "demo-covers-small"

# (标题, UP 主, 时长秒, 封面序号)
_TITLES = (
    ("示例视频:Python 编程入门 · 第一讲", "示例UP · 编程", 18 * 60 + 56, 1),
    ("示例视频:Python 编程入门 · 第二讲", "示例UP · 编程", 22 * 60 + 10, 2),
    ("示例视频:Python 编程入门 · 第三讲", "示例UP · 编程", 25 * 60 + 4, 3),
    ("示例视频:Git 与命令行 基础", "示例UP · 编程", 14 * 60 + 32, 4),
    ("示例视频:数据结构 栈与队列", "示例UP · 编程", 19 * 60 + 48, 5),
    ("示例视频:高等数学 极限与导数", "示例UP · 数学", 31 * 60 + 5, 6),
    ("示例视频:线性代数 矩阵与秩", "示例UP · 数学", 26 * 60 + 40, 7),
    ("示例视频:概率论 条件概率", "示例UP · 数学", 23 * 60 + 15, 8),
    ("示例视频:PyTorch 深度学习 基础", "示例UP · AI", 40 * 60 + 20, 9),
    ("示例视频:机器学习 入门概述", "示例UP · AI", 12 * 60 + 45, 10),
    ("示例视频:大模型 微调入门", "示例UP · AI", 35 * 60 + 30, 11),
    ("示例视频:外刊精读 · 一篇经济学人", "示例UP · 英语", 8 * 60 + 31, 12),
    ("示例视频:英语写作 高分句型", "示例UP · 英语", 16 * 60 + 12, 13),
    ("示例视频:计算机图形学 渲染管线", "示例UP · 图形学", 44 * 60 + 7, 14),
    ("示例视频:OpenGL 光照模型", "示例UP · 图形学", 33 * 60 + 21, 15),
    ("示例视频:哈佛 CS50 公开课 第一节", "示例UP · 公开课", 60 * 60 + 0, 16),
    ("示例视频:算法导论 公开课 排序", "示例UP · 公开课", 52 * 60 + 30, 17),
)


def make_videos() -> list[Video]:
    videos: list[Video] = []
    now = int(time.time())
    for index, (title, upper, duration, cover) in enumerate(_TITLES, start=1):
        videos.append(
            Video(
                aid=900000 + index,
                bvid=f"BVdemo{index:04d}",
                title=title,
                upper=upper,
                duration=duration,
                fav_time=now - index * 3600,
                intro="这是一条示例收藏,用于截图演示,不包含真实内容。",
                attr=0,
                pic=f"https://example.invalid/cover/{cover:02d}.png",
            )
        )
    return videos


def seed_covers(
    home: Path, videos: list[Video], cover_w: int | None = None, cover_h: int | None = None
) -> int:
    """把 dev/demo-covers/*.png 按封面 URL 的哈希放进封面缓存,免得截图时联网。

    注意:缓存 key 用的是 `Video.cover_url(w, h)`(带 @宽x高 后缀的转码地址),
    不是原始 pic,所以宽高必须和网格实际用的一致。
    """
    from bilifocus import theme

    width = cover_w or theme.CARD_WIDTH
    height = cover_h or theme.COVER_HEIGHT
    cache_dir = home / "cache" / "covers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    copies = 0
    for video in videos:
        url = video.cover_url(width, height)
        if not url:
            continue
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        target = cache_dir / f"{digest}.png"
        if target.exists():
            continue
        name = video.pic.rsplit("/", 1)[-1]
        # 日程面板用小尺寸封面:有专门的小图目录就用它,避免 Tk 里显示成"大字报"
        source = COVERS_DIR_SMALL / name if width <= 300 and COVERS_DIR_SMALL.is_dir() else COVERS_DIR / name
        if not source.is_file():
            source = COVERS_DIR / name
        if source.exists():
            shutil.copy2(source, target)
            copies += 1
    return copies


def build_demo(home: Path | None = None) -> tuple[FavoritesService, list[Video], Folder]:
    """搭一套示例数据,返回 (service, videos, folder)。"""
    if home is None:
        home = Path(tempfile.mkdtemp(prefix="bilifocus-demo-"))
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)

    store = StateStore(home=home)
    store.set_sessdata("demo-sessdata-not-real")
    store.set_pref("width", 780)
    store.set_pref("height", 560)
    store.set_pref("auto_group", True)
    store.set_pref("hide_watched", False)
    store.set_pref("last_uname", DEMO_USERNAME)
    store.set_pref("collapsed_groups", [])

    videos = make_videos()

    # 三条已看完(播放进度 ≥ 90%),两条看了一半
    for video in videos[:3]:
        store.set_progress(
            video.key,
            position=float(video.duration),
            duration=float(video.duration),
            watched_seconds=float(video.duration),
            finished=True,
            when=time.time() - 3600,
        )
        store.mark_watched(video.key)
    for video, ratio in zip(videos[3:5], (0.42, 0.68)):
        store.set_progress(
            video.key,
            position=video.duration * ratio,
            duration=float(video.duration),
            watched_seconds=video.duration * ratio,
            finished=False,
            when=time.time() - 1800,
        )

    store.add_watch_seconds(95 * 60)
    store.add_session()
    today = time.strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    two_days_ago = (date.today() - timedelta(days=2)).isoformat()
    store.set_schedule(
        [
            {
                "id": "demo-1",
                "time": "08:30",
                "title": videos[11].title,
                "bvid": videos[11].bvid,
                "repeat": "daily",
                "done_dates": [today, yesterday, two_days_ago],
            },
            {
                "id": "demo-2",
                "time": "14:00",
                "title": videos[5].title,
                "bvid": videos[5].bvid,
                "repeat": "daily",
                "done_dates": [yesterday],
            },
            {
                "id": "demo-3",
                "time": "20:00",
                "title": videos[0].title,
                "bvid": videos[0].bvid,
                "repeat": "weekly",
                "weekdays": [0, 2, 4],
                "done_dates": [],
            },
            {
                "id": "demo-4",
                "time": "22:30",
                "title": "复习今天的笔记",
                "repeat": "daily",
                "done_dates": [],
            },
        ]
    )

    covers = seed_covers(home, videos)
    service = FavoritesService(store=store)
    service.set_default_fid(DEMO_FID)
    folder = Folder(fid=DEMO_FID, title="示例收藏夹", media_count=len(videos), attr=0)
    return service, videos, folder


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="bilifocus-demo-"))
    service, videos, folder = build_demo(home)
    print(f"示例数据目录:{home}")
    print(f"收藏夹:{folder.title} ({folder.media_count} 条)")
    print(f"已看完:{len(service.finished_keys())} 条" if hasattr(service, "finished_keys") else "已看完:3 条")
    print(f"封面缓存:{home / 'cache' / 'covers'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
