"""数据模型:从 B 站响应到内部对象的唯一转换点。

所有 `from_api` 都对缺字段、脏数据做了兜底,并且**丢弃空壳条目**——收藏夹里
被删除的视频会以 `bvid=""` 的空记录出现,把它当成一条正常内容会让界面出现
无法播放的行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Folder:
    """一个收藏夹(媒体列表)。"""

    fid: int
    title: str
    media_count: int
    attr: int = 0

    @property
    def is_public(self) -> bool:
        """attr 为 0 表示公开;非 0 通常是私密(需要登录态才可见)。"""
        return self.attr == 0

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "Folder | None":
        if not isinstance(raw, Mapping):
            return None
        fid = _int(raw.get("id") or raw.get("media_id"))
        if fid <= 0:
            return None
        return cls(
            fid=fid,
            title=_text(raw.get("title")) or f"收藏夹 {fid}",
            media_count=_int(raw.get("media_count")),
            attr=_int(raw.get("attr")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"fid": self.fid, "title": self.title, "media_count": self.media_count, "attr": self.attr}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Folder":
        return cls(
            fid=_int(raw.get("fid")),
            title=_text(raw.get("title")),
            media_count=_int(raw.get("media_count")),
            attr=_int(raw.get("attr")),
        )


@dataclass(frozen=True)
class Video:
    """收藏夹里的一条视频。"""

    aid: int
    bvid: str
    title: str
    upper: str
    duration: int
    fav_time: int
    intro: str = ""
    attr: int = 0
    pic: str = ""

    @property
    def is_valid(self) -> bool:
        """B 站用 attr 的 bit0 表示视频已失效。"""
        return not bool(self.attr & 1)

    @property
    def key(self) -> str:
        """用于已看标记的稳定键。BV 号比 aid 更常见,优先用它。"""
        return self.bvid or f"av{self.aid}"

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}" if self.bvid else ""

    def play_url(self, start_at: float = 0) -> str:
        """带起播位置的播放地址(`?t=秒`),B 站播放器会从这个时间点开始。"""
        if not self.url:
            return ""
        seconds = max(0, int(start_at or 0))
        return f"{self.url}?t={seconds}" if seconds > 0 else self.url

    def cover_url(self, width: int = 320, height: int = 180) -> str:
        """封面地址。

        B 站图床支持用 URL 后缀在线裁剪和转码:`<原图>@320w_180h_1c.png`。
        转成 PNG 是关键——Tk 原生只能解 PNG/GIF,解不了 JPEG,这样就不用
        装 Pillow。`_1c` 表示居中裁剪,不会把图拉变形。
        """
        if not self.pic:
            return ""
        base = self.pic.replace("http://", "https://")
        if not base.startswith("https://"):
            return ""
        return f"{base}@{int(width)}w_{int(height)}h_1c.png"

    @property
    def is_openable(self) -> bool:
        return bool(self.bvid) and self.is_valid

    @property
    def duration_text(self) -> str:
        if self.duration <= 0:
            return "--:--"
        return format_seconds(self.duration)

    @classmethod
    def from_api(cls, raw: Mapping[str, Any]) -> "Video | None":
        if not isinstance(raw, Mapping):
            return None
        aid = _int(raw.get("id") or raw.get("aid"))
        bvid = _text(raw.get("bvid"))
        # 空壳:既没有 aid 也没有 BV 号,说明这条记录没有可用的视频。
        if aid <= 0 and not bvid:
            return None
        upper_block = raw.get("upper")
        upper = _text(upper_block.get("name")) if isinstance(upper_block, Mapping) else ""
        return cls(
            aid=aid,
            bvid=bvid,
            title=_text(raw.get("title")) or ("(已失效)" if not bvid else "(无标题)"),
            upper=upper or "未知UP",
            duration=_int(raw.get("duration")),
            fav_time=_int(raw.get("fav_time")),
            intro=_text(raw.get("intro")),
            attr=_int(raw.get("attr")),
            # 收藏夹接口用 cover,视频详情接口用 pic —— 两个都认
            pic=_text(raw.get("pic") or raw.get("cover")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "aid": self.aid,
            "bvid": self.bvid,
            "title": self.title,
            "upper": self.upper,
            "duration": self.duration,
            "fav_time": self.fav_time,
            "intro": self.intro,
            "attr": self.attr,
            "pic": self.pic,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Video":
        return cls(
            aid=_int(raw.get("aid")),
            bvid=_text(raw.get("bvid")),
            title=_text(raw.get("title")),
            upper=_text(raw.get("upper")),
            duration=_int(raw.get("duration")),
            fav_time=_int(raw.get("fav_time")),
            intro=_text(raw.get("intro")),
            attr=_int(raw.get("attr")),
            pic=_text(raw.get("pic")),
        )


@dataclass(frozen=True)
class Page:
    """收藏夹内容的一页。"""

    videos: tuple[Video, ...]
    page: int
    has_more: bool
    total: int

    @classmethod
    def from_api(cls, raw: Mapping[str, Any], page: int) -> "Page":
        if not isinstance(raw, Mapping):
            return cls(videos=(), page=page, has_more=False, total=0)
        collected: list[Video] = []
        for item in raw.get("medias") or ():
            video = Video.from_api(item)
            if video is not None:
                collected.append(video)
        info = raw.get("info")
        total = _int(info.get("media_count")) if isinstance(info, Mapping) else 0
        return cls(
            videos=tuple(collected),
            page=page,
            has_more=bool(raw.get("has_more")),
            total=total,
        )


@dataclass(frozen=True)
class FavoritesView:
    """一次"取收藏夹"的结果,带上数据新鲜度,前端据此决定要不要显示"缓存"。"""

    fid: int
    videos: tuple[Video, ...]
    total: int
    fetched_at: float
    from_cache: bool
    # 最近一次"联网核对"的时间。增量检查即使没发现新内容也会更新它,
    # 所以前端能区分"数据旧"和"很久没查过"。
    checked_at: float = 0.0
    # 这次同步新增了几条。0 表示没有变化(不是"没同步")。
    new_count: int = 0

    def __len__(self) -> int:
        return len(self.videos)

    def keys(self) -> set[str]:
        return {video.key for video in self.videos}


@dataclass(frozen=True)
class LoginState:
    """登录态快照。"""

    is_login: bool
    uname: str | None = None
    mid: int | None = None

    @classmethod
    def from_nav_data(cls, data: Mapping[str, Any] | None) -> "LoginState":
        if not isinstance(data, Mapping):
            return cls(is_login=False)
        is_login = bool(data.get("isLogin"))
        mid = _int(data.get("mid")) or None
        return cls(is_login=is_login, uname=_text(data.get("uname")) or None, mid=mid)

def format_seconds(seconds: float | int) -> str:
    """把秒数格式化成人看的时间:5:07 / 1:02:03。"""
    total = max(0, int(seconds or 0))
    minutes, sec = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"
