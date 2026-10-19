# 开发与测试

这个目录放**只在开发/调试时用**的脚本。运行主程序不需要它们。

## 常用命令

在**仓库根目录**执行:

```powershell
# 全部离线测试(272 项,约 4 秒)
python -m unittest discover -s tests -t .

# 真实网络集成测试(需登录后才有 SESSDATA;默认跳过)
$env:BILI_FOCUS_INTEGRATION='1'
python -m unittest discover -s tests -t .

# 界面冒烟(会真的开一个 Tk 窗口,约 1 秒)
python dev/smoke_ui.py

# 启动链路(用无效凭证跑完整 bootstrap)
python dev/test_startup.py

# 未使用 import 检查
python dev/check_imports.py bilifocus tests

# 匿名打真实接口,确认协议链路通畅
python dev/probe_network.py

# 重新生成根目录的 MANIFEST.txt
python dev/make_manifest.py
```

也可以直接双击 `dev/run-tests.cmd`(Windows)。

## 卡片样式练习场

`lab_card.py` 是给"改旋钮看效果"用的沙盒:改里面的 `CARD_WIDTH` / 颜色 / 标题字数,
双击 `edit-lab.cmd` 编辑、`run-lab.cmd` 运行。**改它不影响主程序**,试出满意的效果
再搬进 `bilifocus/ui_cards.py`。

## 重新生成 README 截图

```powershell
python dev/make_screenshots.py
```

它用 `dev/demo_data.py` 造一套**示例数据**(示例收藏夹、17 条示例视频、
示例日程),封面用 `dev/demo-covers/` 里的合成图,然后逐个开窗口,
用 Windows GDI 抓客户区、手写 PNG 写到 `docs/screenshots/`
(主界面、分类、日程、提醒条、退出小结、扫码登录共 6 张)。

全程不联网、不碰真实账号,所以截图可以安全公开。

## 凭证加密(DPAPI)

`bilifocus/secrets.py` 负责把 SESSDATA 加密后再写盘:Windows 上用 DPAPI
(`CryptProtectData`,当前用户作用域),其它平台退化为 base64。旧版本的明文
凭证在 `StateStore` 初始化时自动升级成密文。测试在 `tests/test_secrets.py`。

## CI

`.github/workflows/tests.yml` 在 Windows 上跑 3.10–3.13 四套测试;
`.github/workflows/release.yml` 在推 `v*` 标签时自动打包 exe 并发布。

## 打包成 exe

```powershell
python -m pip install pyinstaller
python dev/build_exe.py
```

- 入口:`bili_focus.py` → `bilifocus.ui_tk.main`
- `--onefile --windowed`:单个 exe,不弹控制台
- `--add-data assets`:图标打进包里(运行时从 `sys._MEIPASS` 读)
- `--collect-submodules bilifocus`:播放器/扫码这些懒加载模块也收进来
- 产物:`dist/BiliFocus.exe`(约 12 MB),`build/` 是中间文件,两者都已在 `.gitignore`

## 检查更新(调试)

```powershell
python -m bilifocus update                     # 查本仓库最新 Release
$env:BILI_FOCUS_REPO='git/git'; python -m bilifocus update   # 指向别的仓库验证网络链路
```

逻辑在 `bilifocus/update.py`:GitHub API → Atom 订阅兜底 → 按命名规则拼安装包地址。

## 升版本号

```powershell
python dev/bump_version.py patch   # 修 bug:2.1.0 -> 2.1.1
python dev/bump_version.py minor   # 加功能:2.1.1 -> 2.2.0
```

会同时改 `pyproject.toml` 和 `bilifocus/__init__.py`,测试里有一致性检查。

## 打安装包

```powershell
python dev/build_installer.py
```

- 先确认 `dist/BiliFocus.exe` 是最新的(脚本会提示先跑 `dev/build_exe.py`)
- 安装器本体在 `dev/installer_app.py`:Tk 向导 + 安装/卸载逻辑(`bilifocus/install.py`)
- 程序 exe 会作为 payload 内嵌,产物 `dist/BiliFocus-Setup-<版本>.exe`(约 24 MB)
- 静默安装(CI/测试用):`BiliFocus-Setup-x.y.z.exe --silent`
- 卸载:`"<安装目录>\BiliFocus.exe" --uninstall [--silent]`,或在 Windows 设置→应用里卸载

## 重做图标

```powershell
# 需要带 Pillow 的 Python(主程序本身不依赖 Pillow)
python assets/make_icon.py
```

生成 `assets/bilifocus-icon.ico`(16~256 共 7 个尺寸)和预览图。注意:Windows 按
**文件路径**缓存图标,换图标内容后如果桌面没更新,把文件名改一下再生成即可。

## 目录约定

| 目录 | 内容 |
|---|---|
| `bilifocus/` | 主程序(零第三方依赖) |
| `tests/` | 单元测试(不联网、不碰真实数据) |
| `dev/` | 开发脚本(本目录) |
| `assets/` | 图标与生成脚本 |
| `docs/` | 截图等文档资源 |
