"""兼容入口:旧版单文件脚本的位置。

真正实现在 `favfocus/` 包里。保留这个文件是为了让旧启动方式继续可用:

- `python favfocus.py`              -> 打开界面
- `python favfocus.py --fid 123`    -> 打开指定收藏夹
- `python favfocus.py --uninstall`  -> 卸载(Windows 设置里点卸载也会走这里)
- `python favfocus.py --self-test`  -> 旧版自检,现在等价于 `doctor --offline`
"""

from __future__ import annotations

import sys


def _uninstall(silent: bool) -> int:
    from favfocus import install

    if silent:
        install.uninstall(remove_data=False)
        print("已卸载,数据保留。")
        return 0

    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    if not messagebox.askyesno(
        f"卸载 {install.APP_NAME}",
        f"确定卸载 {install.APP_NAME} 吗?\n\n程序文件、快捷方式和卸载入口会被删除。",
    ):
        return 0
    keep_data = not messagebox.askyesno(
        "本机数据",
        "要不要同时删除观看记录、日程和登录信息?\n\n"
        "选「否」= 保留数据(下次装回来还能接着用);选「是」= 全部删除。",
    )
    install.uninstall(remove_data=not keep_data)
    messagebox.showinfo("卸载完成", f"{install.APP_NAME} 已卸载。")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--uninstall" in args:
        return _uninstall("--silent" in args)

    from favfocus import ui_tk

    if "--self-test" in args:
        from favfocus.cli import main as cli_main

        return cli_main(["doctor", "--offline"])
    return ui_tk.main(args)


if __name__ == "__main__":
    sys.exit(main())
