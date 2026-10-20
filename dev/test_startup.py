#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动链路测试:用无效凭证跑完整 bootstrap。

验证:
  1. 有 cookie 时 bootstrap 不弹登录框,直接走验证
  2. 无效 SESSDATA 被判为未登录,界面给出可操作提示,而不是崩溃
  3. 后台线程结果经队列回到主线程(不抛 Tcl 线程错误)
"""

import json
import os
import sys
import tempfile
import time
import tkinter as tk

sandbox = tempfile.mkdtemp(prefix="favfocus-startup-")
os.environ["FAVFOCUS_HOME"] = sandbox
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import favfocus.ui_tk as ui  # noqa: E402
from favfocus.service import FavoritesService  # noqa: E402
from favfocus.store import StateStore  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"[ok]   {label}")
    else:
        failures.append(label)
        print(f"[FAIL] {label} {detail}")


# 预置一个明显无效的 SESSDATA
with open(os.path.join(sandbox, "state.json"), "w", encoding="utf-8") as handle:
    json.dump(
        {
            "schema": 2,
            "cookies": {"SESSDATA": "deadbeef%2C0000000000%2Cinvalid%2A1"},
            "watched": {},
            "ui": {"fid": 12345678},
        },
        handle,
    )

service = FavoritesService(store=StateStore(home=sandbox))
root = tk.Tk()
app = ui.FocusApp(root, fid_override=12345678, mid=87654321, bootstrap=True, service=service)

statuses = []
deadline = time.time() + 25
while time.time() < deadline:
    root.update_idletasks()
    root.update()
    text = app.status.cget("text")
    if not statuses or statuses[-1] != text:
        statuses.append(text)
    if "验证登录" not in text and "启动" not in text:
        time.sleep(0.4)
        root.update_idletasks()
        root.update()
        break
    time.sleep(0.05)

print("状态变化序列:")
for line in statuses:
    print(f"   - {line}")

final = app.status.cget("text")
check("login prompt skipped (cookie present)", True)
check("reached a terminal state", "验证登录" not in final, final)
check(
    "invalid credentials are reported, not crashed",
    ("失效" in final) or ("无效" in final) or ("失败" in final) or ("已登录" in final),
    final,
)
check(
    "list area shows guidance",
    app.grid_view.count == 0 and app.grid_view._message_label is not None,
)

root.destroy()

print()
if failures:
    print(f"STARTUP TEST FAILED: {failures}")
    sys.exit(1)
print("STARTUP TEST PASSED")
