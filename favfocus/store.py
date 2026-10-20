"""本机持久化。

两个文件、两种职责,故意分开:

- `state.json` —— 小、常改、含登录凭证(SESSDATA)、已看标记、界面偏好。
- `cache/fav-<fid>.json` —— 大、可丢、随时能重新拉取的收藏夹缓存。

合在一起写会让"标记一条已看"顺带重写整个收藏夹列表,既慢又增加损坏风险。

所有写入走临时文件 + `os.replace`,避免断电时留下半个 JSON。损坏的文件不会
让程序崩:state 会被备份成 `state.corrupt-<时间戳>.json` 后重置,缓存直接丢弃。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import Any, Iterable

from . import secrets
from .models import Video

SCHEMA_VERSION = 4
STATE_FILENAME = "state.json"
CACHE_DIRNAME = "cache"

# 同一进程里多个线程都会落盘(界面在后台线程里取数据、主线程里标记已看),
# 所以写文件必须串行化,临时文件名也必须唯一。
_WRITE_LOCK = threading.Lock()
_TMP_COUNTER = count()


def default_home() -> Path:
    """默认数据目录。可用 `FAVFOCUS_HOME` 覆盖(测试和便携使用)。"""
    override = os.environ.get("FAVFOCUS_HOME") or os.environ.get("BILI_FOCUS_HOME")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    home = root / "favfocus"
    _migrate_legacy_home(root, home)
    return home


def _migrate_legacy_home(root: Path, home: Path) -> None:
    """从改名前的 `bili-focus` 目录复制一次数据(旧目录保留,不删)。"""
    legacy = root / "bili-focus"
    if home.exists() or not legacy.is_dir():
        return
    # cache / 浏览器配置都可再生(几百 MB),只迁移 state.json 等小文件
    ignore = shutil.ignore_patterns("cache", "browser-profile", "player-profile")
    try:
        shutil.copytree(legacy, home, ignore=ignore)
    except OSError:
        # 复制失败也要保证目录存在,让程序至少能起来
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{next(_TMP_COUNTER)}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)


class StateStore:
    """凭证 + 已看标记 + 偏好。每次改动立刻落盘(文件很小)。"""

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home).expanduser() if home is not None else default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / STATE_FILENAME
        self._lock = threading.RLock()
        self._data = self._load()
        # 老版本明文存的凭证:读到就顺手升级成密文(失败也不影响启动)
        if self._upgrade_plaintext_credentials():
            try:
                self.save()
            except OSError:
                pass

    # -- 读写 -------------------------------------------------------------
    def _upgrade_plaintext_credentials(self) -> bool:
        """旧版本直接把 SESSDATA 明文写在 state.json 里,读到就顺手加密。"""
        cookies = self._data.get("cookies")
        if not isinstance(cookies, dict):
            return False
        stored = cookies.get("SESSDATA")
        if not stored or secrets.is_encrypted(stored):
            return False
        plain = secrets.unprotect(stored)
        if not plain:
            return False
        cookies["SESSDATA"] = secrets.protect(plain)
        return True

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            raw = _read_json(self.path)
        except (OSError, ValueError):
            self._quarantine()
            return self._empty()
        if not isinstance(raw, dict):
            self._quarantine()
            return self._empty()
        return _migrate(raw)

    def _empty(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "cookies": {},
            "watched": {},
            "progress": {},
            "stats": {},
            "schedule": [],
            "ui": {},
        }

    def _quarantine(self) -> None:
        """损坏的文件留个备份,别让用户"莫名其妙丢了登录态"。"""
        try:
            backup = self.path.with_name(f"state.corrupt-{int(time.time())}.json")
            os.replace(self.path, backup)
        except OSError:
            pass

    def save(self) -> None:
        with self._lock:
            _write_json_atomic(self.path, self._data)

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    # -- 凭证 -------------------------------------------------------------
    @property
    def cookies(self) -> dict[str, str]:
        """给 HTTP 用的 Cookie(凭证已解密)。

        盘上存的是密文,这里必须还原成明文再交给客户端,
        否则请求会带着 `dpapi:...` 去访问 B 站,直接被判未登录。
        """
        raw = dict(self._data.get("cookies") or {})
        if raw.get("SESSDATA"):
            plain = secrets.unprotect(raw["SESSDATA"])
            if plain:
                raw["SESSDATA"] = plain
            else:
                raw.pop("SESSDATA", None)
        return {key: str(value) for key, value in raw.items()}

    @property
    def sessdata(self) -> str | None:
        """解密后的 SESSDATA(不落盘明文)。"""
        stored = (self._data.get("cookies") or {}).get("SESSDATA")
        if not stored:
            return None
        return secrets.unprotect(stored) or None

    def set_sessdata(self, value: str | None) -> None:
        with self._lock:
            cookies = dict(self._data.get("cookies") or {})
            if value:
                # 存的是密文:DPAPI 绑定当前 Windows 用户,拷走文件也解不开
                cookies["SESSDATA"] = secrets.protect(value)
            else:
                cookies.pop("SESSDATA", None)
            self._data["cookies"] = cookies
            self.save()

    def clear_cookies(self) -> None:
        with self._lock:
            self._data["cookies"] = {}
            self.save()

    # -- 已看标记 ---------------------------------------------------------
    @property
    def watched(self) -> dict[str, int]:
        return dict(self._data.get("watched") or {})

    def is_watched(self, key: str) -> bool:
        return key in (self._data.get("watched") or {})

    def mark_watched(self, key: str, *, when: float | None = None) -> bool:
        """返回是否发生了变化(已经标记过则 False)。"""
        with self._lock:
            watched = dict(self._data.get("watched") or {})
            if key in watched:
                return False
            watched[key] = int(when if when is not None else time.time())
            self._data["watched"] = watched
            self.save()
            return True

    def unmark_watched(self, key: str) -> bool:
        with self._lock:
            watched = dict(self._data.get("watched") or {})
            if key not in watched:
                return False
            watched.pop(key)
            self._data["watched"] = watched
            self.save()
            return True

    def prune_watched(self, keep: Iterable[str]) -> int:
        """丢掉不在 `keep` 里的已看记录(收藏夹里已经删掉的视频)。"""
        keep_set = set(keep)
        with self._lock:
            watched = dict(self._data.get("watched") or {})
            removed = [key for key in watched if key not in keep_set]
            for key in removed:
                watched.pop(key)
            if removed:
                self._data["watched"] = watched
                self.save()
            return len(removed)

    # -- 观看进度 ---------------------------------------------------------
    @property
    def progress(self) -> dict[str, dict]:
        return dict(self._data.get("progress") or {})

    def get_progress(self, key: str) -> dict | None:
        record = (self._data.get("progress") or {}).get(key)
        return dict(record) if isinstance(record, dict) else None

    def set_progress(
        self,
        key: str,
        *,
        position: float,
        duration: float,
        watched_seconds: float = 0.0,
        finished: bool = False,
        when: float | None = None,
    ) -> dict:
        """记录一条视频看到哪儿了。位置单位是秒。

        `finished` 单独存:看完的视频也要保留进度(否则退出小结里
        "已看完多少条"就没法统计了),只是下次不再从中间续播。
        """
        record = {
            "position": round(max(0.0, float(position)), 2),
            "duration": round(max(0.0, float(duration)), 2),
            "watched": round(max(0.0, float(watched_seconds)), 2),
            "finished": bool(finished),
            "updated_at": int(when if when is not None else time.time()),
        }
        with self._lock:
            progress = dict(self._data.get("progress") or {})
            progress[key] = record
            self._data["progress"] = progress
            self.save()
        return dict(record)

    def clear_progress(self, key: str) -> bool:
        with self._lock:
            progress = dict(self._data.get("progress") or {})
            if key not in progress:
                return False
            progress.pop(key)
            self._data["progress"] = progress
            self.save()
            return True

    def prune_progress(self, keep: Iterable[str]) -> int:
        keep_set = set(keep)
        with self._lock:
            progress = dict(self._data.get("progress") or {})
            removed = [key for key in progress if key not in keep_set]
            for key in removed:
                progress.pop(key)
            if removed:
                self._data["progress"] = progress
                self.save()
            return len(removed)

    # -- 日程 -------------------------------------------------------------
    @property
    def schedule(self) -> list[dict]:
        return list(self._data.get("schedule") or [])

    def set_schedule(self, items: list[dict]) -> None:
        """整表替换(日程条数很少,整写最简单也最不容易出错)。"""
        with self._lock:
            self._data["schedule"] = list(items)
            self.save()

    # -- 累计统计 ---------------------------------------------------------
    @property
    def stats(self) -> dict:
        return dict(self._data.get("stats") or {})

    def add_watch_seconds(self, seconds: float) -> float:
        """把一次播放的有效时长累加进总数(看完的也计入,不会随进度清理丢失)。"""
        if seconds is None or float(seconds) <= 0:
            return self.total_watch_seconds
        with self._lock:
            stats = dict(self._data.get("stats") or {})
            total = float(stats.get("total_watch_seconds") or 0) + float(seconds)
            stats["total_watch_seconds"] = round(total, 2)
            self._data["stats"] = stats
            self.save()
        return float(stats["total_watch_seconds"])

    @property
    def total_watch_seconds(self) -> float:
        return float((self._data.get("stats") or {}).get("total_watch_seconds") or 0)

    def add_session(self, *, watched_seconds: float = 0.0, plays: int = 1, finished: int = 0) -> None:
        """累计"一共播放过多少次、看完多少条"。"""
        with self._lock:
            stats = dict(self._data.get("stats") or {})
            stats["plays"] = int(stats.get("plays") or 0) + int(plays)
            stats["finished"] = int(stats.get("finished") or 0) + int(finished)
            self._data["stats"] = stats
            self.save()

    # -- 偏好 -------------------------------------------------------------
    @property
    def prefs(self) -> dict[str, Any]:
        return dict(self._data.get("ui") or {})

    def get_pref(self, name: str, default: Any = None) -> Any:
        return (self._data.get("ui") or {}).get(name, default)

    def set_pref(self, name: str, value: Any) -> None:
        with self._lock:
            prefs = dict(self._data.get("ui") or {})
            prefs[name] = value
            self._data["ui"] = prefs
            self.save()

    # -- 调试 -------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """不带敏感值的状态摘要。"""
        return {
            "home": str(self.home),
            "state_path": str(self.path),
            "schema": self._data.get("schema"),
            "has_sessdata": bool(self.sessdata),
            "watched_count": len(self.watched),
            "progress_count": len(self.progress),
            "watch_seconds": int(self.total_watch_seconds),
            "plays": int(self.stats.get("plays") or 0),
            "finished_count": sum(
                1
                for record in self.progress.values()
                if record.get("finished")
                or (
                    float(record.get("duration") or 0) > 0
                    and float(record.get("position") or 0)
                    / float(record.get("duration") or 1)
                    >= 0.90
                )
            ),
            "prefs": self.prefs,
        }


def _migrate_schedule_item(item: Any) -> dict[str, Any] | None:
    """把旧版日程条目补齐成 v4 形状(旧版的 done_day 转成 done_dates)。"""
    if not isinstance(item, dict):
        return None
    migrated = dict(item)
    migrated.setdefault("bvid", "")
    migrated.setdefault("repeat", "daily")
    migrated.setdefault("date", "")
    migrated.setdefault("weekdays", [])
    done = migrated.get("done_dates")
    if not isinstance(done, list):
        done = []
    legacy = migrated.pop("done_day", "")
    if legacy and legacy not in done:
        done.append(legacy)
    migrated["done_dates"] = done
    return migrated


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """把 v1(无 schema 字段)补齐到当前版本。未知字段原样保留。"""
    migrated = dict(raw)
    migrated.setdefault("cookies", {})
    migrated.setdefault("watched", {})
    migrated.setdefault("progress", {})
    migrated.setdefault("stats", {})
    migrated.setdefault("schedule", [])
    migrated.setdefault("ui", {})
    migrated["schema"] = SCHEMA_VERSION
    # v1 里 cookies 可能直接是 {"SESSDATA": "..."} 的扁平结构,已是目标形态。
    if not isinstance(migrated.get("cookies"), dict):
        migrated["cookies"] = {}
    if not isinstance(migrated.get("watched"), dict):
        migrated["watched"] = {}
    if not isinstance(migrated.get("progress"), dict):
        migrated["progress"] = {}
    if not isinstance(migrated.get("stats"), dict):
        migrated["stats"] = {}
    if not isinstance(migrated.get("schedule"), list):
        migrated["schedule"] = []
    # v4:日程从"纯文字 + 单日完成"升级为"绑定视频 + 重复规则 + 按日期记录完成"
    migrated["schedule"] = [_migrate_schedule_item(item) for item in migrated["schedule"]]
    migrated["schedule"] = [item for item in migrated["schedule"] if item]
    # 从旧版本升上来时还没有累计观看量:用已有进度里的 watched 当种子,
    # 这样升级前看过的那部分也不会凭空消失。
    if "total_watch_seconds" not in migrated["stats"]:
        seeded = sum(
            float(record.get("watched") or 0)
            for record in (migrated.get("progress") or {}).values()
            if isinstance(record, dict)
        )
        if seeded:
            migrated["stats"]["total_watch_seconds"] = round(seeded, 2)
    if not isinstance(migrated.get("ui"), dict):
        migrated["ui"] = {}
    return migrated


@dataclass(frozen=True)
class CachedFavorites:
    videos: tuple[Video, ...]
    total: int
    fetched_at: float

    def age_seconds(self, now: float | None = None) -> float:
        return max(0.0, (now if now is not None else time.time()) - self.fetched_at)


class CacheStore:
    """每个收藏夹一个缓存文件。过期或损坏就当没缓存。"""

    def __init__(self, home: Path | str | None = None):
        self.home = Path(home).expanduser() if home is not None else default_home()
        self.root = self.home / CACHE_DIRNAME

    def path_for(self, fid: int) -> Path:
        return self.root / f"fav-{int(fid)}.json"

    def get(self, fid: int, *, max_age: float | None = None, now: float | None = None) -> CachedFavorites | None:
        path = self.path_for(fid)
        if not path.exists():
            return None
        try:
            raw = _read_json(path)
        except (OSError, ValueError):
            self.clear(fid)
            return None
        try:
            videos = tuple(Video.from_dict(item) for item in raw["videos"])
            fetched_at = float(raw["fetched_at"])
            total = int(raw.get("total") or 0)
        except (KeyError, TypeError, ValueError):
            self.clear(fid)
            return None
        cached = CachedFavorites(videos=videos, total=total, fetched_at=fetched_at)
        if max_age is not None and cached.age_seconds(now) > max_age:
            return None
        return cached

    def put(self, fid: int, videos: Iterable[Video], *, total: int, now: float | None = None) -> CachedFavorites:
        collected = tuple(videos)
        fetched_at = now if now is not None else time.time()
        _write_json_atomic(
            self.path_for(fid),
            {
                "schema": SCHEMA_VERSION,
                "fid": int(fid),
                "total": int(total),
                "fetched_at": fetched_at,
                "videos": [video.to_dict() for video in collected],
            },
        )
        return CachedFavorites(videos=collected, total=int(total), fetched_at=fetched_at)

    def clear(self, fid: int | None = None) -> int:
        if fid is not None:
            path = self.path_for(fid)
            if path.exists():
                path.unlink(missing_ok=True)
                return 1
            return 0
        removed = 0
        if self.root.exists():
            for path in self.root.glob("fav-*.json"):
                path.unlink(missing_ok=True)
                removed += 1
        return removed
