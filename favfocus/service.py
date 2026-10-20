"""服务层:把"取数据"和"记住状态"编排在一起,给前端一个稳定的入口。

前端(现在的 Tk 界面、以后你换的任何东西)只应该依赖这个类,不直接碰
`BiliClient` 或 `StateStore`。这样缓存策略、已看去重、导出格式都能在底层
单独演进。

缓存语义:
- `videos(fid)` 默认走缓存(TTL `cache_ttl`),过期或不存在才联网。
- `videos(fid, force=True)` 强制联网并覆盖缓存。
- 返回的 `FavoritesView.from_cache` 告诉前端这份数据是不是旧的。
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable

from .client import BiliClient, ProgressCallback
from .models import FavoritesView, Folder, LoginState, Page, Video
from .store import CacheStore, StateStore
from . import schedule as schedule_mod
from . import topics

# 多久做一次"增量核对"。每次只请求第 1 页,很便宜。
DEFAULT_SYNC_INTERVAL = 180.0
# 增量时最多往前翻几页(一页 20 条)。超过就干脆全量重拉,逻辑更简单也更稳。
INCREMENTAL_MAX_PAGES = 5

# 看完的判定:播放位置 >= 95% 时长,或离结尾不足 90 秒。
# 用"或"是因为长视频结尾常有片尾/广告,人不会真看到最后一秒。
# 看完的判定只看比例:播放位置 >= 90% 时长。
# (旧版还有「离结尾不足 90 秒也算」的规则,对短视频太宽松,已去掉)
FINISH_RATIO = 0.90

# 从浏览器复制时常见的几种"整行"形态:SESSDATA=xxx、SESSDATA<TAB>xxx、SESSDATA: xxx
_NAME = "sessdata"
_NAME_SEPARATORS = " \t=:：\u00a0"


def _record_finished(record: dict | None, duration: float = 0.0) -> bool:
    """这条进度算不算看完:有 finished 标记,或比例达到阈值。"""
    if not record:
        return False
    if record.get("finished"):
        return True
    duration = float(duration or record.get("duration") or 0)
    if duration <= 0:
        return False
    return float(record.get("position") or 0) / duration >= FINISH_RATIO


def normalize_sessdata(value: str) -> str:
    """把用户粘贴的内容收敛成裸的 SESSDATA 值。

    容忍:前后空白、引号、换行、以及连名字一起复制的整行。
    """
    text = (value or "").strip()
    if text.lower().startswith(_NAME):
        rest = text[len(_NAME):]
        # 只有后面紧跟分隔符或已经结束时才当作"名字前缀",避免误伤正常值
        if not rest or rest[0] in _NAME_SEPARATORS:
            text = rest.lstrip(_NAME_SEPARATORS)
    # 引号和空白可能在处理前缀之后才露出来
    return text.strip().strip('"').strip("'").strip()


class FavoritesService:
    """收藏夹的唯一门面。"""

    def __init__(
        self,
        *,
        store: StateStore | None = None,
        cache: CacheStore | None = None,
        client: BiliClient | None = None,
        sync_interval: float = DEFAULT_SYNC_INTERVAL,
        cache_ttl: float | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store or StateStore()
        self.cache = cache or CacheStore(self.store.home)
        self._client = client
        # cache_ttl 是旧名字,保留兼容;现在它的含义是"多久核对一次"
        self.sync_interval = float(cache_ttl if cache_ttl is not None else sync_interval)
        self.cache_ttl = self.sync_interval
        self._clock = clock

    # -- 客户端 -----------------------------------------------------------
    @property
    def client(self) -> BiliClient:
        if self._client is None:
            self._client = BiliClient(cookies=self.store.cookies)
        return self._client

    @property
    def default_fid(self) -> int | None:
        value = self.store.get_pref("fid")
        try:
            return int(value) if value else None
        except (TypeError, ValueError):
            return None

    @property
    def default_mid(self) -> int | None:
        """收藏夹所属用户 mid:优先环境变量,其次本机记住的(登录时写入)。"""
        candidates = (
            os.environ.get("FAVFOCUS_MID"),
            os.environ.get("BILI_FOCUS_MID"),
            self.store.get_pref("mid"),
        )
        for candidate in candidates:
            try:
                value = int(candidate) if candidate else 0
            except (TypeError, ValueError):
                value = 0
            if value:
                return value
        return None

    def set_default_mid(self, mid: int | None) -> None:
        self.store.set_pref("mid", int(mid) if mid else None)

    def set_default_fid(self, fid: int | None) -> None:
        self.store.set_pref("fid", int(fid) if fid else None)

    # -- 登录 -------------------------------------------------------------
    def login(self, sessdata: str) -> LoginState:
        """保存并验证 SESSDATA。验证失败会抛出 `AuthRequiredError`。"""
        value = normalize_sessdata(sessdata)
        if not value:
            raise ValueError("SESSDATA 不能为空")
        self.store.set_sessdata(value)
        self.client.set_cookies(self.store.cookies)
        state = self.login_state()
        if not state.is_login:
            # 保存了无效凭证,回滚,避免界面显示"已登录"却什么都取不到。
            self.store.set_sessdata(None)
            self.client.set_cookies(self.store.cookies)
            from .errors import AuthRequiredError

            raise AuthRequiredError("SESSDATA 无效或已过期,请重新从浏览器复制")
        # 记住这个账号的 mid:下次启动不用再猜,也不用把 mid 写进源码
        self.set_default_mid(state.mid)
        return state

    def logout(self) -> None:
        self.store.clear_cookies()
        self.client.set_cookies({})

    @property
    def browser_profile_dir(self) -> Path:
        """本软件自己的浏览器配置目录(和 state.json 同级,不放进项目目录)。"""
        return self.store.home / "browser-profile"

    def login_via_browser(
        self,
        *,
        progress: Callable[[str], None] | None = None,
        timeout: float = 240.0,
    ) -> LoginState:
        """开一个独立浏览器让你登录,登录完成后自动取走 SESSDATA。

        打开的不是你日常的浏览器:它用独立的 `user-data-dir`,登录态只属于本软件。
        取到之后会关掉浏览器并立即验证登录。
        """
        from .browser_login import BrowserLogin

        login = BrowserLogin(self.browser_profile_dir, timeout=timeout)
        captured = login.capture(progress=progress)
        return self.login(captured)

    def forget_browser_login(self) -> bool:
        """删除浏览器配置目录(等于退出浏览器的那个登录态)。"""
        from .browser_login import BrowserLogin

        login = BrowserLogin(self.browser_profile_dir)
        if not self.browser_profile_dir.exists():
            return False
        login.forget()
        return True

    def login_state(self) -> LoginState:
        return self.client.check_login()

    @property
    def is_logged_in(self) -> bool:
        return bool(self.store.sessdata)

    # -- 收藏夹 -----------------------------------------------------------
    def folders(self, mid: int) -> tuple[Folder, ...]:
        return self.client.folders(mid)

    def page(self, fid: int, page: int = 1, *, order: str = "mtime") -> Page:
        """单页查询,不走缓存。适合前端做"懒加载更多"。"""
        return self.client.fav_page(fid, page, order=order)

    def videos(
        self,
        fid: int,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> FavoritesView:
        """取收藏夹。

        - `force=True`:全量重拉(会翻完所有页)。
        - 否则:缓存不存在就全量;刚核对过就直接返回缓存;
          过了 `sync_interval` 就做一次**增量核对**(只拉第 1 页)。
        """
        fid = int(fid)
        if force:
            return self._full_sync(fid, progress=progress, previous=self.cache.get(fid))

        cached = self.cache.get(fid)
        if cached is None:
            return self._full_sync(fid, progress=progress)

        if self._clock() - self._last_check(fid) < self.sync_interval:
            return self._cached_view(fid, cached)

        return self._incremental_sync(fid, cached, progress=progress)

    def sync(
        self,
        fid: int,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> FavoritesView:
        """`videos()` 的别名,语义更明确:同步一次(默认增量)。"""
        return self.videos(fid, force=force, progress=progress)

    def refresh(self, fid: int, *, progress: ProgressCallback | None = None) -> FavoritesView:
        """强制全量刷新。"""
        return self.videos(fid, force=True, progress=progress)

    # -- 同步内部实现 -----------------------------------------------------
    def _sync_pref(self, fid: int) -> str:
        return f"sync_at_{int(fid)}"

    def _last_check(self, fid: int) -> float:
        try:
            return float(self.store.get_pref(self._sync_pref(fid), 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _stamp_check(self, fid: int) -> None:
        self.store.set_pref(self._sync_pref(fid), float(self._clock()))

    def _cached_view(self, fid: int, cached) -> FavoritesView:
        return FavoritesView(
            fid=fid,
            videos=cached.videos,
            total=cached.total or len(cached.videos),
            fetched_at=cached.fetched_at,
            from_cache=True,
            checked_at=self._last_check(fid),
            new_count=0,
        )

    def _full_sync(self, fid: int, *, progress=None, previous=None) -> FavoritesView:
        """翻完所有页。`previous` 只用来算"新增了几条"。"""
        videos, total = self.client.all_favorites(fid, progress=progress)
        old_keys = {video.key for video in previous.videos} if previous is not None else set()
        new_count = sum(1 for video in videos if video.key not in old_keys) if old_keys else 0
        stored = self.cache.put(fid, videos, total=total, now=self._clock())
        self._stamp_check(fid)
        self.set_default_fid(fid)
        return FavoritesView(
            fid=fid,
            videos=stored.videos,
            total=stored.total or len(stored.videos),
            fetched_at=stored.fetched_at,
            from_cache=False,
            checked_at=self._clock(),
            new_count=new_count,
        )

    def _incremental_sync(self, fid: int, cached, *, progress=None) -> FavoritesView:
        """只拉第 1 页,找出比缓存更新的条目。

        收藏夹按收藏时间倒序,新内容一定在最前面,所以只要从第一页往下走,
        遇到一条"已经认识"的就说明后面全是旧的,可以停。
        """
        known = {video.key for video in cached.videos}
        cached_total = cached.total or len(cached.videos)

        page = self.client.fav_page(fid, 1)
        total = page.total or len(page.videos)

        # 总数变少 = 有取消收藏;中段被删也算,直接全量最省心
        if total < cached_total:
            return self._full_sync(fid, progress=progress, previous=cached)

        fresh: list[Video] = []
        page_number = 1
        reached_known = False
        while True:
            for video in page.videos:
                if video.key in known:
                    reached_known = True
                    break
                fresh.append(video)
            if reached_known or not page.has_more:
                break
            page_number += 1
            if page_number > INCREMENTAL_MAX_PAGES:
                return self._full_sync(fid, progress=progress, previous=cached)
            page = self.client.fav_page(fid, page_number)

        if not fresh:
            # 没有新内容:只更新时间戳,不重写缓存文件
            self._stamp_check(fid)
            view = self._cached_view(fid, cached)
            return FavoritesView(
                fid=view.fid,
                videos=view.videos,
                total=view.total,
                fetched_at=view.fetched_at,
                from_cache=True,
                checked_at=self._clock(),
                new_count=0,
            )

        expected_total = cached_total + len(fresh)
        if total != expected_total:
            # 数字对不上(可能同时有增有删),交给全量
            return self._full_sync(fid, progress=progress, previous=cached)

        fresh_keys = {video.key for video in fresh}
        merged = fresh + [video for video in cached.videos if video.key not in fresh_keys]
        stored = self.cache.put(fid, merged, total=total, now=self._clock())
        self._stamp_check(fid)
        return FavoritesView(
            fid=fid,
            videos=stored.videos,
            total=stored.total or len(stored.videos),
            fetched_at=stored.fetched_at,
            from_cache=False,
            checked_at=self._clock(),
            new_count=len(fresh),
        )

    # -- 已看标记 ---------------------------------------------------------
    def mark_watched(self, video: Video | str) -> bool:
        return self.store.mark_watched(_key_of(video), when=self._clock())

    def unmark_watched(self, video: Video | str) -> bool:
        return self.store.unmark_watched(_key_of(video))

    def is_watched(self, video: Video | str) -> bool:
        return self.store.is_watched(_key_of(video))

    def toggle_watched(self, video: Video | str) -> bool:
        """切换并返回切换后的状态。"""
        if self.is_watched(video):
            self.unmark_watched(video)
            return False
        self.mark_watched(video)
        return True

    def watched_count(self, view: FavoritesView | Iterable[Video]) -> int:
        videos = view.videos if isinstance(view, FavoritesView) else view
        return sum(1 for video in videos if self.store.is_watched(video.key))

    # -- 观看进度 ---------------------------------------------------------
    def resume_position(self, video: Video | str) -> int:
        """这条视频该从第几秒开始播。

        没有进度、或者进度已经到结尾(再看等于重看一遍)都返回 0;
        离结尾只剩 FINISH_TAIL_SECONDS 秒以内也不续播。
        """
        key = _key_of(video)
        record = self.store.get_progress(key)
        if not record:
            return 0
        position = float(record.get("position") or 0)
        duration = float(record.get("duration") or 0)
        if position <= 0:
            return 0
        if _record_finished(record, duration):
            return 0  # 已经看完了,下次从头看
        return int(position)

    def record_progress(
        self,
        video: Video | str,
        *,
        position: float,
        duration: float,
        watched_seconds: float = 0.0,
    ) -> dict:
        """保存观看进度,并判断"算不算看完"。

        判定只看比例:播放位置 >= 90% 时长即视为看完。

        和旧版的关键区别:看完**不再删进度**,而是把记录标成 finished。
        因为退出小结要报「已看完多少条」,统计必须落在进度记录上;
        下次续播靠 finished 标记跳过,不影响「看完从头看」。
        返回一条带 `finished` / `ratio` 的摘要,界面直接用它刷新。
        """
        key = _key_of(video)
        position = max(0.0, float(position or 0))
        duration = max(0.0, float(duration or 0))
        if duration <= 0 and isinstance(video, Video):
            duration = float(video.duration or 0)
        ratio = min(1.0, position / duration) if duration > 0 else 0.0
        finished = bool(duration > 0 and ratio >= FINISH_RATIO)

        self.store.add_watch_seconds(watched_seconds)
        self.store.add_session(
            watched_seconds=watched_seconds, plays=1, finished=1 if finished else 0
        )
        if finished:
            self.store.mark_watched(key, when=self._clock())
        record = self.store.set_progress(
            key,
            position=duration if finished else position,
            duration=duration,
            watched_seconds=watched_seconds,
            finished=finished,
            when=self._clock(),
        )
        record = dict(record)
        record.update({"key": key, "ratio": 1.0 if finished else round(ratio, 4)})
        return record

    def progress_view(self, videos: Iterable[Video]) -> dict[str, dict]:
        """给界面用:key -> {ratio, position, duration}。"""
        result: dict[str, dict] = {}
        for video in videos:
            record = self.store.get_progress(video.key)
            if not record:
                continue
            position = float(record.get("position") or 0)
            duration = float(record.get("duration") or 0)
            if position <= 0:
                continue
            finished = _record_finished(record, duration)
            result[video.key] = {
                "ratio": 1.0 if finished else (min(1.0, position / duration) if duration > 0 else 0.0),
                "position": int(duration if finished else position),
                "duration": int(duration),
                "finished": finished,
            }
        return result

    def clear_progress(self, video: Video | str) -> bool:
        return self.store.clear_progress(_key_of(video))

    def total_watch_seconds(self) -> float:
        """历史累计的有效观看时长(秒),包含已经看完的。"""
        return round(self.store.total_watch_seconds, 1)

    def digest(self, videos: Iterable[Video]) -> dict:
        """收藏夹「已看完」统计 —— 只看播放进度,不看手动标记。

        判定与播放器一致:进度 >= 90% 时长算看完。手动按空格标的"已看"
        不算数,因为它不代表真的看完了。
        """
        items = list(videos)
        total = len(items)
        finished = 0
        in_progress = 0
        for video in items:
            record = self.store.get_progress(video.key)
            if _record_finished(record):
                finished += 1
            elif record and float(record.get("position") or 0) > 0:
                in_progress += 1
        rate = (finished / total) if total else 0.0
        return {
            "total": total,
            "finished": finished,
            "in_progress": in_progress,
            "remaining": max(0, total - finished),
            "rate": rate,
            "rate_percent": round(rate * 100, 1),
        }

    def random_unwatched(self, view: FavoritesView | Iterable[Video], *, rng=None) -> Video | None:
        """从未看过的里随机取一条;全都看过则从全部里取。"""
        videos = list(view.videos if isinstance(view, FavoritesView) else view)
        if not videos:
            return None
        pool = [video for video in videos if not self.store.is_watched(video.key) and video.is_openable]
        if not pool:
            pool = [video for video in videos if video.is_openable] or videos
        chooser = rng or __import__("random").choice
        return chooser(pool)

    # -- 日程 -------------------------------------------------------------
    def schedule_items(self) -> list[schedule_mod.ScheduleItem]:
        """读出全部日程(丢弃损坏条目),按时间排序。"""
        items: list[schedule_mod.ScheduleItem] = []
        for raw in self.store.schedule:
            item = schedule_mod.ScheduleItem.from_dict(raw)
            if item is not None:
                items.append(item)
        return schedule_mod.sort_items(items)

    # -- 手动分类 ---------------------------------------------------------
    MAX_TOPIC_OVERRIDES = 500

    def topic_overrides(self) -> dict[str, str]:
        """用户手动纠正过的分类({视频 key: 分类 id}),坏数据直接忽略。"""
        raw = self.store.get_pref("topic_overrides", {}) or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in raw.items()
            if isinstance(value, str) and topics.is_valid_topic(value)
        }

    def set_topic_override(self, video, topic_id: str | None) -> None:
        """把某条视频固定到某个分类;传 None 表示恢复自动分类。"""
        overrides = dict(self.topic_overrides())
        key = video.key
        if not topic_id:
            overrides.pop(key, None)
        else:
            if not topics.is_valid_topic(topic_id):
                raise ValueError(f"未知分类:{topic_id}")
            overrides[key] = topic_id
        if len(overrides) > self.MAX_TOPIC_OVERRIDES:
            # 超出上限就丢最早的几条(字典保持插入顺序)
            for stale in list(overrides)[: len(overrides) - self.MAX_TOPIC_OVERRIDES]:
                overrides.pop(stale, None)
        self.store.set_pref("topic_overrides", overrides)

    def topic_name(self, video) -> str:
        """这条视频现在归在哪一类(有手动纠正就用纠正后的)。"""
        forced = self.topic_overrides().get(video.key)
        return topics.name_of(forced or topics.classify(video))

    def _save_schedule(self, items: list[schedule_mod.ScheduleItem]) -> None:
        self.store.set_schedule([item.to_dict() for item in items])

    def _find_schedule(self, item_id: str):
        items = self.schedule_items()
        for item in items:
            if item.id == item_id:
                return items, item
        raise KeyError(item_id)

    def add_schedule(
        self,
        time_text: str,
        title: str | None = None,
        *,
        video=None,
        bvid: str = "",
        repeat: str = schedule_mod.REPEAT_DAILY,
        date: str | None = None,
        weekdays=(),
    ) -> schedule_mod.ScheduleItem:
        """新增"几点看哪条视频"的日程。时间写法很宽松(20:00 / 2000 / 8点30)。"""
        normalized = schedule_mod.normalize_time(time_text)
        if not normalized:
            raise ValueError("时间格式看不懂,试试 20:00 或 2030")

        if video is not None:
            bvid = getattr(video, "bvid", "") or bvid
            name = schedule_mod.normalize_title(getattr(video, "title", "") or (title or ""))
        else:
            name = schedule_mod.normalize_title(title or "")
        if not name:
            raise ValueError("先选一条收藏视频,或者写一句要看什么")

        if repeat not in schedule_mod.REPEAT_CHOICES:
            repeat = schedule_mod.REPEAT_DAILY
        chosen_days = schedule_mod.normalize_weekdays(weekdays)
        if repeat == schedule_mod.REPEAT_WEEKLY and not chosen_days:
            raise ValueError("每周重复要选至少一天")
        target_date = ""
        if repeat == schedule_mod.REPEAT_ONCE:
            target_date = date or schedule_mod.today(self._clock())

        items = self.schedule_items()
        item = schedule_mod.ScheduleItem(
            id=schedule_mod.new_id(),
            time=normalized,
            title=name,
            bvid=bvid,
            repeat=repeat,
            date=target_date,
            weekdays=chosen_days if repeat == schedule_mod.REPEAT_WEEKLY else (),
        )
        items.append(item)
        self._save_schedule(schedule_mod.prune(schedule_mod.sort_items(items), self._clock()))
        return item

    def update_schedule(
        self,
        item_id: str,
        *,
        time_text: str | None = None,
        title: str | None = None,
        repeat: str | None = None,
        date: str | None = None,
        weekdays=None,
    ) -> schedule_mod.ScheduleItem:
        """改时间 / 改内容 / 改重复规则(不动完成记录)。"""
        items, item = self._find_schedule(item_id)
        if time_text is not None:
            normalized = schedule_mod.normalize_time(time_text)
            if not normalized:
                raise ValueError("时间格式看不懂,试试 20:00 或 2030")
            item.time = normalized
        if title is not None:
            name = schedule_mod.normalize_title(title)
            if not name:
                raise ValueError("内容不能为空")
            item.title = name
        if repeat is not None:
            if repeat not in schedule_mod.REPEAT_CHOICES:
                raise ValueError("未知的重复规则")
            item.repeat = repeat
            if repeat == schedule_mod.REPEAT_ONCE:
                item.date = date or item.date or schedule_mod.today(self._clock())
            if repeat != schedule_mod.REPEAT_WEEKLY:
                item.weekdays = ()
        if weekdays is not None:
            chosen = schedule_mod.normalize_weekdays(weekdays)
            if item.repeat == schedule_mod.REPEAT_WEEKLY and not chosen:
                raise ValueError("每周重复要选至少一天")
            item.weekdays = chosen
        self._save_schedule(schedule_mod.sort_items(items))
        return item

    def remove_schedule(self, item_id: str) -> bool:
        items = self.schedule_items()
        remaining = [item for item in items if item.id != item_id]
        if len(remaining) == len(items):
            return False
        self._save_schedule(remaining)
        return True

    def toggle_schedule_done(self, item_id: str, *, day: str | None = None) -> bool:
        """勾选/取消某条日程,返回勾选后的状态。只影响指定的那一天。"""
        items, item = self._find_schedule(item_id)
        target = day or schedule_mod.today(self._clock())
        done = not schedule_mod.is_done(item, target)
        schedule_mod.mark_done(item, target, done)
        self._save_schedule(items)
        return done

    def finish_schedule_for_video(self, video, *, day: str | None = None) -> int:
        """看完了某条视频 → 把它今天绑定的日程自动勾上,返回勾了多少条。"""
        key = getattr(video, "bvid", "")
        if not key:
            return 0
        items = self.schedule_items()
        target = day or schedule_mod.today(self._clock())
        touched = 0
        for item in items:
            if item.bvid == key and schedule_mod.applies_on(item, target):
                if not schedule_mod.is_done(item, target):
                    schedule_mod.mark_done(item, target, True)
                    touched += 1
        if touched:
            self._save_schedule(items)
        return touched

    def schedule_today(self) -> list[schedule_mod.ScheduleItem]:
        return schedule_mod.items_on(self.schedule_items(), schedule_mod.today(self._clock()))

    def schedule_next(self) -> schedule_mod.ScheduleItem | None:
        return schedule_mod.next_pending(self.schedule_items(), self._clock())

    def schedule_due(self, now: float | None = None) -> schedule_mod.ScheduleItem | None:
        """到点该提醒的那一条(应用内轻提示用)。"""
        moment = self._clock() if now is None else now
        dismissed = set(self.store.get_pref("schedule_dismissed", []) or [])
        return schedule_mod.due_now(self.schedule_items(), moment, dismissed=dismissed)

    def dismiss_schedule_reminder(self, item, *, now: float | None = None) -> None:
        """这次先不提醒(当天不再弹同一条)。"""
        moment = self._clock() if now is None else now
        key = f"{item.id}@{schedule_mod.today(moment)}"
        dismissed = list(self.store.get_pref("schedule_dismissed", []) or [])
        if key not in dismissed:
            dismissed.append(key)
        self.store.set_pref("schedule_dismissed", dismissed[-200:])

    def schedule_week(self) -> list[dict]:
        return schedule_mod.week_view(self.schedule_items(), schedule_mod.today(self._clock()))

    def schedule_week_done(self) -> tuple[int, int]:
        return schedule_mod.week_done(self.schedule_items(), schedule_mod.today(self._clock()))

    # -- 统计 -------------------------------------------------------------
    def stats(self, view: FavoritesView | Iterable[Video]) -> dict:
        """给前端用的汇总。前端不该自己算这些。"""
        videos = list(view.videos if isinstance(view, FavoritesView) else view)
        by_upper: dict[str, int] = {}
        total_seconds = 0
        for video in videos:
            by_upper[video.upper] = by_upper.get(video.upper, 0) + 1
            total_seconds += max(0, video.duration)
        top = sorted(by_upper.items(), key=lambda item: (-item[1], item[0]))[:5]
        return {
            "count": len(videos),
            "watched": self.watched_count(videos),
            "unwatched": len(videos) - self.watched_count(videos),
            "invalid": sum(1 for video in videos if not video.is_valid),
            "total_duration_seconds": total_seconds,
            "total_duration_text": _format_seconds(total_seconds),
            "in_progress": len(self.store.progress),
            "watch_seconds": self.total_watch_seconds(),
            "watch_text": _format_seconds(int(self.total_watch_seconds())),
            "top_uploaders": [{"upper": name, "count": count} for name, count in top],
        }

    # -- 导出 -------------------------------------------------------------
    def export_json(self, view: FavoritesView, path: str | Path) -> Path:
        target = Path(path)
        payload = {
            "fid": view.fid,
            "fetched_at": view.fetched_at,
            "count": len(view.videos),
            "from_cache": view.from_cache,
            "videos": [
                {**video.to_dict(), "watched": self.is_watched(video), "url": video.url}
                for video in view.videos
            ],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def export_csv(self, view: FavoritesView, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig:让 Excel 正确识别中文,不用用户手动选编码。
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["bvid", "aid", "标题", "UP主", "时长(秒)", "时长", "收藏时间", "已看", "链接"]
            )
            for video in view.videos:
                writer.writerow(
                    [
                        video.bvid,
                        video.aid,
                        video.title,
                        video.upper,
                        video.duration,
                        video.duration_text,
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(video.fav_time))
                        if video.fav_time
                        else "",
                        "是" if self.is_watched(video) else "",
                        video.url,
                    ]
                )
        return target


def _key_of(video: Video | str) -> str:
    if isinstance(video, Video):
        return video.key
    return str(video)


def _format_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "0 分钟"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟"
    return f"{sec} 秒"


def search(videos: Iterable[Video], keyword: str) -> list[Video]:
    """标题 / UP 主的不区分大小写子串匹配。前端不需要自己实现。"""
    needle = (keyword or "").strip().lower()
    if not needle:
        return list(videos)
    return [
        video
        for video in videos
        if needle in video.title.lower() or needle in video.upper.lower()
    ]
