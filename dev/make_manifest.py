#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成仓库根目录的 MANIFEST.txt(文件清单 + 一句话说明)。

在仓库根目录执行:

    python dev/make_manifest.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DESCRIPTIONS: dict[str, str] = {
    # 根目录
    "README.md": "项目说明:功能、截图、快速开始、设计说明、测试",
    "LICENSE": "MIT 许可证(含内置二维码库的署名)",
    "MANIFEST.txt": "本文件清单(由 dev/make_manifest.py 生成)",
    "pyproject.toml": "打包元数据(零第三方依赖,入口 bilifocus)",
    ".gitignore": "忽略缓存/本地数据/打包产物",
    ".gitattributes": "换行策略;.cmd 按二进制保存(GBK 编码)",
    "bili_focus.py": "旧版入口的兼容壳(GUI / --self-test)",
    "bilifocus.cmd": "图形界面启动器(GBK 编码,双击用)",
    "bilifocus-cli.cmd": "命令行启动器",
    "启动 BiliFocus.cmd": "中文名启动器包装",
    # 包
    "bilifocus/__init__.py": "公开导出",
    "bilifocus/__main__.py": "python -m bilifocus 入口",
    "bilifocus/wbi.py": "WBI 签名(纯函数,官方向量验证)",
    "bilifocus/http.py": "传输层:限速、重试、可注入替身",
    "bilifocus/models.py": "数据模型:Video(含封面地址)/ Folder / Page / LoginState",
    "bilifocus/client.py": "B 站只读 API 客户端(签名失败自动刷新重试)",
    "bilifocus/covers.py": "封面下载与缓存(要 PNG 后缀,避免依赖 Pillow)",
    "bilifocus/store.py": "持久化:state.json + cache,原子写、并发安全",
    "bilifocus/service.py": "服务门面:缓存、进度、统计、日程、导出",
    "bilifocus/browser_login.py": "独立浏览器登录 + CDP 取 Cookie(自带 WebSocket 客户端)",
    "bilifocus/qrlogin.py": "手机扫码登录:生成二维码、轮询状态、取 SESSDATA",
    "bilifocus/player.py": "播放器:自动全屏播放 + 采样观看进度 + 续播",
    "bilifocus/topics.py": "自动分类:本地关键词打分 + 分组排序",
    "bilifocus/schedule.py": "日程:时间解析、排序、下一项、跨天重置",
    "bilifocus/errors.py": "错误分层:把 B 站返回码翻译成可判断异常",
    "bilifocus/cli.py": "命令行入口",
    "bilifocus/theme.py": "配色 / 字体 / DPI 适配 / 卡片尺寸",
    "bilifocus/ui_tk.py": "桌面壳:工具栏、全屏、日程入口、退出小结",
    "bilifocus/ui_cards.py": "卡片网格:单画布绘制 + 虚拟化 + 平滑滚动",
    "bilifocus/ui_splash.py": "启动页:Canvas 矢量动画(无图片资源)",
    "bilifocus/ui_schedule.py": "日程面板:自己设定时间 + 内容",
    "bilifocus/ui_farewell.py": "退出小结卡(观看时长 + 已看完比例)",
    "bilifocus/install.py": "安装/卸载逻辑:注册表、快捷方式、覆盖安装",
    "bilifocus/winproc.py": "静默执行外部命令(不弹控制台窗口)",
    "bilifocus/vendor/__init__.py": "内置第三方代码说明",
    "bilifocus/vendor/qrcodegen.py": "二维码编码器(Project Nayuki,MIT,逐字保留)",
    "bilifocus/secrets.py": "凭证加密:SESSDATA 用 Windows DPAPI 加密存储",
    # 测试
    "tests/__init__.py": "测试包说明",
    "tests/fakes.py": "测试替身:假传输、响应构造器、可控时钟",
    "tests/test_wbi.py": "签名与编码测试(官方向量锚定)",
    "tests/test_models.py": "模型解析、封面地址、cover/pic 字段兼容",
    "tests/test_covers.py": "封面缓存:命中、过期、坏数据、非 PNG 拒绝",
    "tests/test_store.py": "持久化:原子写、损坏恢复、迁移、TTL、并发、进度",
    "tests/test_client.py": "客户端:签名参数、错误归因、重试、翻页",
    "tests/test_service.py": "服务:缓存、登录回滚、已看、统计、导出、进度",
    "tests/test_browser_login.py": "浏览器登录:Cookie 过滤、WebSocket 帧格式",
    "tests/test_qrlogin.py": "扫码登录:状态映射、SESSDATA 提取、二维码矩阵",
    "tests/test_player.py": "播放器:进度采样、启动参数、断连容错",
    "tests/test_topics.py": "自动分类:关键词命中、分组顺序、边界",
    "tests/test_schedule.py": "日程:时间解析、排序、下一项、跨天重置",
    "tests/test_secrets.py": "凭证加密:DPAPI 往返、明文迁移、坏密文容错",
    "tests/test_install.py": "安装/卸载辅助逻辑:路径、版本、快捷方式",
    "tests/test_winproc.py": "外部命令静默执行(不弹控制台窗口)",
    "tests/test_player_retry.py": "播放器启动失败重试、清理等待、日志",
    "tests/test_cli.py": "命令行:排版、子命令、输出",
    "tests/test_integration.py": "真实网络集成测试(默认跳过,需 BILI_FOCUS_INTEGRATION=1)",
    # 开发工具
    "dev/README.md": "开发与测试说明",
    "dev/smoke_ui.py": "界面冒烟测试(真实 Tk 窗口,约 1 秒)",
    "dev/test_startup.py": "启动链路测试:用无效凭证跑完整 bootstrap",
    "dev/check_imports.py": "无第三方 linter 时的未使用 import 检查",
    "dev/probe_network.py": "手动联调脚本:匿名打真实接口验证协议链路",
    "dev/lab_card.py": "卡片样式练习场(改它不影响主程序)",
    "dev/make_manifest.py": "生成本文件",
    "dev/demo_data.py": "示例数据:17 条示例视频 + 封面 + 日程(截图与演示用)",
    "dev/make_screenshots.py": "用示例数据重新生成 docs/screenshots(脱敏)",
    "dev/build_exe.py": "打包单文件 exe(需要 PyInstaller)",
    "dev/build_installer.py": "打 Windows 安装包(内嵌 exe + 安装向导)",
    "dev/installer_app.py": "安装器界面与安装/卸载流程",
    "dev/demo-covers/01.png": "示例封面(合成图,供截图使用)",
    "dev/run-tests.cmd": "一键跑离线测试",
    "dev/run-lab.cmd": "运行卡片实验室(出错时停住显示报错)",
    "dev/edit-lab.cmd": "用记事本打开卡片实验室",
    # 资源
    "assets/make_icon.py": "生成图标(米白底 + 取景框 + 播放三角)",
    "assets/bilifocus-icon.ico": "应用图标(16~256 七个尺寸)",
    "assets/icon-preview.png": "图标各尺寸预览图",
    "docs/screenshots/ui.png": "主界面截图",
    "docs/screenshots/grouped.png": "自动分类 + 收起截图",
    "docs/screenshots/schedule.png": "日程面板截图(绑定视频/重复规则/本周记录)",
    "docs/screenshots/reminder.png": "到点提醒条截图",
    ".github/workflows/tests.yml": "CI:push/PR 跑四套 Python 版本的测试",
    ".github/workflows/release.yml": "CI:推 v* 标签自动打 exe 并发布 Release",
    "docs/screenshots/farewell.png": "退出小结截图",
    "docs/screenshots/qr-login.png": "扫码登录截图",
}

HEADER = """BiliFocus 2.0.0 —— 文件清单
==============================================

用法:
  桌面       双击桌面上的 BiliFocus 图标
  登录       登录框点「手机扫码登录」,或用 bilifocus-cli.cmd login --qrcode
  命令行     bilifocus-cli.cmd list --fid <收藏夹id>
  跑测试     双击 dev\\run-tests.cmd
  样式试验场   双击 dev\\edit-lab.cmd 改颜色/尺寸,再双击 dev\\run-lab.cmd 看效果
  重做图标   <带 PIL 的 python> assets/make_icon.py

"""

SECTIONS: tuple[tuple[str, str], ...] = (
    ("", "根目录"),
    ("bilifocus", "包 bilifocus/"),
    ("bilifocus/vendor", "包 bilifocus/vendor/"),
    ("tests", "测试 tests/"),
    ("dev", "开发工具 dev/"),
    ("assets", "资源 assets/"),
    ("docs", "文档资源 docs/"),
    (".github/workflows", "CI .github/workflows/"),
)

EXCLUDE_DIRS = {
    "__pycache__", ".git", "build", "dist", ".venv", "venv",
    "_archive", "_screenshots",  # 本机归档,不列入清单
}


def collect() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def render(files: list[Path]) -> str:
    lines = [HEADER.rstrip("\n"), ""]
    used: set[str] = set()
    for prefix, title in SECTIONS:
        base = ROOT / prefix if prefix else ROOT
        group = [
            path
            for path in files
            if (path.parent == base if prefix else path.parent == ROOT)
        ]
        if not group:
            continue
        lines.append(f"— {title} " + "-" * max(0, 44 - len(title)))
        for path in group:
            relative = path.relative_to(ROOT).as_posix()
            used.add(relative)
            size = path.stat().st_size
            lines.append(f"{size:>10} B  {relative}")
            description = DESCRIPTIONS.get(relative)
            if description:
                lines.append(f"            {description}")
        lines.append("")

    unknown = [path for path in files if path.relative_to(ROOT).as_posix() not in used]
    if unknown:
        lines.append("— 其他 " + "-" * 40)
        for path in unknown:
            relative = path.relative_to(ROOT).as_posix()
            lines.append(f"{path.stat().st_size:>10} B  {relative}")
        lines.append("")

    lines.append(f"共 {len(files)} 个文件。")
    return "\n".join(lines) + "\n"


def main() -> int:
    files = collect()
    (ROOT / "MANIFEST.txt").write_text(render(files), encoding="utf-8")
    print(f"MANIFEST.txt 已更新:共 {len(files)} 个文件")
    missing = [
        path.relative_to(ROOT).as_posix()
        for path in files
        if path.relative_to(ROOT).as_posix() not in DESCRIPTIONS
        and path.suffix in {".py", ".md", ".cmd", ".toml"}
    ]
    if missing:
        print("还没有说明的源码文件:")
        for item in missing:
            print("  -", item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
