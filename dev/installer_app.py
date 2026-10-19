#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BiliFocus Windows 安装器(自带向导,不依赖 Inno Setup / NSIS)。

用法(开发时直接跑):

    python dev/installer_app.py              # 交互式安装
    python dev/installer_app.py --silent     # 静默安装(默认目录、建桌面快捷方式)
    python dev/installer_app.py --uninstall  # 交互式卸载

打包成安装包见 `dev/build_installer.py`。
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bilifocus import install, theme  # noqa: E402
from bilifocus.theme import ACCENT, BG, BG_ALT, BORDER, CARD_BG, FG, FG_DIM, font, px  # noqa: E402


def payload_path() -> Path:
    """打包后从临时解包目录读;开发时读 dist/。"""
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    bundled = base / "payload" / install.EXE_NAME
    if bundled.is_file():
        return bundled
    return ROOT / "dist" / install.EXE_NAME


class InstallerWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        theme.enable_dpi_awareness()
        theme.init_fonts(self.root)
        theme.init_display(self.root)
        self.root.title(f"{install.APP_NAME} {install.version()} 安装程序")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self._center_icon()
        self._build()
        self.root.update_idletasks()
        # 高度按内容来,别在下面留一大块空白
        width = max(px(560), self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 3
        self.root.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    def _center_icon(self) -> None:
        base = Path(getattr(sys, "_MEIPASS", ROOT))
        icon = base / "assets" / "bilifocus-icon.ico"
        if not icon.is_file():
            icon = ROOT / "assets" / "bilifocus-icon.ico"
        if icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=px(26), pady=(px(24), px(6)))
        tk.Label(header, text=install.APP_NAME, bg=BG, fg=FG, font=font(20, "bold")).pack(side="left")
        tk.Label(
            header,
            text=f"v{install.version()} · 只看收藏夹",
            bg=BG,
            fg=FG_DIM,
            font=font(10),
        ).pack(side="left", padx=(px(10), 0), pady=(px(6), 0))

        card = tk.Frame(self.root, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", padx=px(26), pady=(px(8), 0))
        self.target_label = tk.Label(
            card,
            text=str(install.install_dir()),
            bg=CARD_BG,
            fg=FG,
            font=font(9),
            anchor="w",
            justify="left",
        )
        self.target_label.pack(fill="x", padx=px(14), pady=(px(12), px(2)))
        tk.Label(
            card,
            text="安装到当前用户,不需要管理员权限;数据保存在 %APPDATA%\\bili-focus,升级不会丢。",
            bg=CARD_BG,
            fg=FG_DIM,
            font=font(8),
            anchor="w",
            justify="left",
            wraplength=px(470),
        ).pack(fill="x", padx=px(14), pady=(0, px(10)))
        if install.is_installed():
            tk.Label(
                card,
                text=f"检测到已安装版本 {install.installed_version() or '?'} → 本次为覆盖安装",
                bg=CARD_BG,
                fg=ACCENT,
                font=font(8),
                anchor="w",
            ).pack(fill="x", padx=px(14), pady=(0, px(10)))

        options = tk.Frame(self.root, bg=BG)
        options.pack(fill="x", padx=px(26), pady=(px(10), 0))
        self.desktop_var = tk.BooleanVar(value=True)
        self.launch_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            options,
            text="创建桌面快捷方式",
            variable=self.desktop_var,
            bg=BG,
            fg=FG,
            activebackground=BG,
            activeforeground=FG,
            selectcolor=BG_ALT,
            font=font(9),
        ).pack(side="left")
        tk.Checkbutton(
            options,
            text="安装完成后启动",
            variable=self.launch_var,
            bg=BG,
            fg=FG,
            activebackground=BG,
            activeforeground=FG,
            selectcolor=BG_ALT,
            font=font(9),
        ).pack(side="left", padx=(px(16), 0))

        self.status = tk.Label(self.root, text="", bg=BG, fg=FG_DIM, font=font(9), anchor="w")
        self.status.pack(fill="x", padx=px(26), pady=(px(12), 0))

        row = tk.Frame(self.root, bg=BG)
        row.pack(fill="x", padx=px(26), pady=(px(14), px(20)))
        self.install_btn = tk.Button(
            row,
            text="安装",
            command=self._install,
            bg=ACCENT,
            fg="#ffffff",
            relief="flat",
            activebackground="#0A6A8B",
            activeforeground="#ffffff",
            font=font(10, "bold"),
            padx=px(26),
            pady=px(6),
        )
        self.install_btn.pack(side="left")
        self.cancel_btn = tk.Button(
            row,
            text="取消",
            command=self.root.destroy,
            bg=BG_ALT,
            fg=FG,
            relief="flat",
            activebackground=BORDER,
            activeforeground=FG,
            font=font(10),
            padx=px(18),
            pady=px(6),
        )
        self.cancel_btn.pack(side="left", padx=(px(10), 0))
        tk.Label(
            row,
            text="MIT 许可 · 零第三方依赖 · 数据只存本机",
            bg=BG,
            fg=FG_DIM,
            font=font(8),
        ).pack(side="right")

    def _install(self) -> None:
        payload = payload_path()
        if not payload.is_file():
            messagebox.showerror("安装失败", f"找不到程序文件:\n{payload}")
            return
        self.install_btn.configure(state="disabled")
        self.cancel_btn.configure(state="disabled")
        try:
            directory = install.install(
                payload,
                desktop=self.desktop_var.get(),
                launch=self.launch_var.get(),
                progress=self._progress,
            )
        except OSError as error:
            messagebox.showerror("安装失败", f"{error}")
            self.install_btn.configure(state="normal")
            self.cancel_btn.configure(state="normal")
            return
        self.status.configure(text=f"安装完成:{directory}", fg=ACCENT)
        self.install_btn.configure(text="完成", command=self.root.destroy, state="normal")
        self.cancel_btn.pack_forget()

    def _progress(self, text: str) -> None:
        self.status.configure(text=text)
        self.root.update_idletasks()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_uninstall() -> int:
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
    try:
        install.uninstall(remove_data=not keep_data)
    except OSError as error:
        messagebox.showerror("卸载失败", str(error))
        return 1
    messagebox.showinfo("卸载完成", f"{install.APP_NAME} 已卸载。" + ("" if keep_data else "\n本机数据已删除。"))
    return 0


def main() -> int:
    args = set(sys.argv[1:])
    if "--uninstall" in args:
        if "--silent" in args:
            install.uninstall(remove_data=False)
            return 0
        return run_uninstall()
    if "--silent" in args:
        return install.silent_install(payload_path())
    return InstallerWindow().run()


if __name__ == "__main__":
    raise SystemExit(main())
